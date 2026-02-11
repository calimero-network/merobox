"""
Authentication management for remote Calimero nodes.

This module provides AuthToken dataclass and AuthManager for handling:
- Username/password authentication via /auth/token endpoint
- Token refresh via /auth/refresh endpoint
- Disk-based token caching under ~/.merobox/auth_cache/

Connection Pooling:
    This module provides SessionManager for efficient HTTP connection pooling.
    Instead of creating a new aiohttp.ClientSession for each request, the
    SessionManager maintains a shared session with connection pooling to:
    - Reduce connection overhead
    - Improve performance through connection reuse
    - Support concurrent requests efficiently

Token Cache Integration:
    This module uses calimero-client-py's token cache helpers to ensure
    merobox and the Rust client use identical cache paths:
    - `calimero_client_py.get_token_cache_path(node_name)` for cache file paths
    - `calimero_client_py.get_token_cache_dir()` for the cache directory

    This ensures that:
    1. Merobox-written tokens are found by calimero-client-py's auto-refresh
    2. Tokens refreshed by the Rust client are visible to merobox
"""

import asyncio
import atexit
import base64
import json
import os
import threading
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import aiohttp
from calimero_client_py import get_token_cache_dir, get_token_cache_path
from rich.console import Console

from merobox.commands.constants import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)

console = Console()

# Connection pooling configuration
DEFAULT_POOL_CONNECTIONS = 100  # Maximum number of connections in the pool
DEFAULT_POOL_CONNECTIONS_PER_HOST = 10  # Maximum connections per host
DEFAULT_POOL_KEEPALIVE_TIMEOUT = 30  # Seconds to keep idle connections alive


class SessionManager:
    """Manages a shared aiohttp.ClientSession with connection pooling.

    This class provides efficient HTTP connection management by:
    - Maintaining a single shared session across multiple requests
    - Using a TCPConnector with connection pooling for better performance
    - Properly handling session lifecycle (creation, reuse, cleanup)
    - Thread-safe singleton creation and session access
    - Event loop aware: automatically recreates resources when loop changes
    - Cookie isolation: uses DummyCookieJar to prevent cookie leakage between nodes

    Usage:
        # Option 1: Use as async context manager (recommended)
        async with SessionManager() as session:
            async with session.get(url) as response:
                data = await response.json()

        # Option 2: Get shared session instance
        manager = SessionManager()
        session = await manager.get_session()
        try:
            async with session.get(url) as response:
                data = await response.json()
        finally:
            await manager.close()

        # Option 3: Use the global shared instance
        session = await get_shared_session()
        async with session.get(url) as response:
            data = await response.json()

    Note:
        When using the global shared instance via get_shared_session(),
        call close_shared_session() during application shutdown to
        properly release all HTTP connections.
    """

    _instance: Optional["SessionManager"] = None
    _instance_lock: threading.Lock = threading.Lock()  # Thread-safe singleton creation

    def __init__(
        self,
        pool_connections: int = DEFAULT_POOL_CONNECTIONS,
        pool_connections_per_host: int = DEFAULT_POOL_CONNECTIONS_PER_HOST,
        keepalive_timeout: int = DEFAULT_POOL_KEEPALIVE_TIMEOUT,
    ):
        """Initialize the SessionManager with connection pooling configuration.

        Args:
            pool_connections: Maximum number of connections in the pool.
            pool_connections_per_host: Maximum connections per host.
            keepalive_timeout: Seconds to keep idle connections alive.
        """
        self._pool_connections = pool_connections
        self._pool_connections_per_host = pool_connections_per_host
        self._keepalive_timeout = keepalive_timeout
        self._connector: Optional[aiohttp.TCPConnector] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._async_lock: Optional[asyncio.Lock] = None
        self._loop_id: Optional[int] = None  # Track which event loop owns resources
        self._sync_lock = threading.Lock()  # Protects _async_lock creation

    def _get_current_loop_id(self) -> int:
        """Get the ID of the current running event loop."""
        try:
            loop = asyncio.get_running_loop()
            return id(loop)
        except RuntimeError:
            # No running loop
            return 0

    def _is_same_loop(self) -> bool:
        """Check if we're running in the same event loop as when resources were created."""
        if self._loop_id is None:
            return False
        return self._loop_id == self._get_current_loop_id()

    def _get_async_lock(self) -> asyncio.Lock:
        """Get or create the asyncio.Lock for the current event loop.

        Thread-safe: uses a threading.Lock to protect creation.
        """
        current_loop_id = self._get_current_loop_id()
        with self._sync_lock:
            if self._async_lock is None or self._loop_id != current_loop_id:
                self._async_lock = asyncio.Lock()
            return self._async_lock

    def _create_connector(self) -> aiohttp.TCPConnector:
        """Create a TCPConnector with connection pooling settings."""
        return aiohttp.TCPConnector(
            limit=self._pool_connections,
            limit_per_host=self._pool_connections_per_host,
            keepalive_timeout=self._keepalive_timeout,
            enable_cleanup_closed=True,
        )

    def _create_session(self, connector: aiohttp.TCPConnector) -> aiohttp.ClientSession:
        """Create a ClientSession with disabled cookies.

        Uses DummyCookieJar to prevent cookies from being shared between
        different node authentications, which could cause auth state leakage.
        """
        return aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.DummyCookieJar(),
        )

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create the shared aiohttp.ClientSession.

        This method is thread-safe and uses a lock to prevent race conditions
        when multiple coroutines try to create a session concurrently.

        The session is automatically recreated if the event loop has changed,
        preventing cross-loop errors.

        Returns:
            The shared aiohttp.ClientSession instance with connection pooling.
        """
        current_loop_id = self._get_current_loop_id()

        # Always acquire lock to prevent race with close()
        lock = self._get_async_lock()
        async with lock:
            # Check if we need to recreate due to event loop change
            if self._loop_id is not None and self._loop_id != current_loop_id:
                # Event loop changed - old resources are orphaned
                # Log warning about orphaned resources
                if self._session is not None and not self._session.closed:
                    warnings.warn(
                        "SessionManager: Event loop changed, orphaning unclosed session. "
                        "This may cause 'Unclosed client session' warnings.",
                        ResourceWarning,
                        stacklevel=2,
                    )
                self._session = None
                self._connector = None

            # Create session if needed
            if self._session is None or self._session.closed:
                self._connector = self._create_connector()
                self._session = self._create_session(self._connector)
                self._loop_id = current_loop_id

            return self._session

    async def close(self) -> None:
        """Close the session and release all connections.

        This method is thread-safe and ensures proper cleanup of resources.
        """
        current_loop_id = self._get_current_loop_id()
        lock = self._get_async_lock()

        async with lock:
            # Only close if we're in the same loop
            if self._loop_id is not None and self._loop_id != current_loop_id:
                # Different loop - just clear references
                self._session = None
                self._connector = None
                self._loop_id = None
                return

            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None

            if self._connector is not None and not self._connector.closed:
                await self._connector.close()
            self._connector = None

            self._loop_id = None

    async def __aenter__(self) -> aiohttp.ClientSession:
        """Async context manager entry - returns the session."""
        return await self.get_session()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - closes the session."""
        await self.close()

    @classmethod
    def get_shared_instance(cls) -> "SessionManager":
        """Get the global shared SessionManager instance.

        Thread-safe: uses a threading.Lock to protect singleton creation.

        Returns:
            The global SessionManager instance (creates one if needed).
        """
        if cls._instance is None:
            with cls._instance_lock:
                # Double-check after acquiring lock
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    async def close_shared_instance(cls) -> None:
        """Close the global shared SessionManager instance.

        Call this during application shutdown to properly release all
        HTTP connections and prevent resource leaks.
        """
        with cls._instance_lock:
            if cls._instance is not None:
                await cls._instance.close()
                cls._instance = None


def _cleanup_shared_session() -> None:
    """Atexit handler to warn about unclosed shared session."""
    if SessionManager._instance is not None:
        instance = SessionManager._instance
        if instance._session is not None and not instance._session.closed:
            warnings.warn(
                "SessionManager: Shared session was not closed before exit. "
                "Call close_shared_session() during shutdown to avoid resource leaks.",
                ResourceWarning,
                stacklevel=2,
            )


# Register cleanup handler
atexit.register(_cleanup_shared_session)


async def get_shared_session() -> aiohttp.ClientSession:
    """Get the global shared aiohttp.ClientSession with connection pooling.

    This is a convenience function to access the shared session.
    The session is created on first call and reused for subsequent calls.

    Returns:
        The shared aiohttp.ClientSession instance.
    """
    return await SessionManager.get_shared_instance().get_session()


async def close_shared_session() -> None:
    """Close the global shared aiohttp.ClientSession.

    Call this when the application is shutting down to properly
    release all HTTP connections.
    """
    await SessionManager.close_shared_instance()


async def _get_session(
    session_manager: Optional[SessionManager] = None,
) -> aiohttp.ClientSession:
    """Get an aiohttp session from the provided manager or shared instance.

    This is a helper function to reduce duplication in authenticate/refresh methods.

    Args:
        session_manager: Optional SessionManager for connection pooling.
            If not provided, uses the global shared session.

    Returns:
        An aiohttp.ClientSession instance.
    """
    if session_manager is not None:
        return await session_manager.get_session()
    return await get_shared_session()


# Auth endpoints
AUTH_TOKEN_ENDPOINT = "/auth/token"
AUTH_REFRESH_ENDPOINT = "/auth/refresh"

# Auth methods
AUTH_METHOD_USER_PASSWORD = "user_password"
AUTH_METHOD_API_KEY = "api_key"
AUTH_METHOD_NONE = "none"

# Token expiry buffer (refresh if token expires within this many seconds)
TOKEN_EXPIRY_BUFFER_SECONDS = 60


@dataclass
class AuthToken:
    """Represents an authentication token for a Calimero node."""

    access_token: str
    node_url: str
    auth_method: str
    refresh_token: Optional[str] = None
    expires_at: Optional[int] = None
    username: Optional[str] = None

    def is_expired(self, buffer_seconds: int = TOKEN_EXPIRY_BUFFER_SECONDS) -> bool:
        """Check if the token is expired or will expire soon.

        Args:
            buffer_seconds: Consider token expired if it expires within this many seconds.

        Returns:
            True if token is expired or expires_at is not set, False otherwise.
        """
        if self.expires_at is None:
            # If we don't know when it expires, assume it might be expired
            # The server will tell us if it is via 401
            return False
        return time.time() + buffer_seconds >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Convert token to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuthToken":
        """Create AuthToken from dictionary."""
        return cls(
            access_token=data["access_token"],
            node_url=data["node_url"],
            auth_method=data["auth_method"],
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            username=data.get("username"),
        )


class AuthManager:
    """Manages authentication for remote Calimero nodes.

    Handles:
    - User/password authentication via POST /auth/token
    - Token refresh via POST /auth/refresh
    - Token caching on disk under ~/.merobox/auth_cache/

    Token Cache Integration:
        Uses calimero-client-py's cache helpers to ensure cache paths match
        exactly between merobox and the Rust client. This enables:
        - Tokens written by merobox to be auto-loaded by calimero-client-py
        - Tokens refreshed by calimero-client-py to be visible to merobox
    """

    def __init__(self):
        """Initialize AuthManager.

        Uses calimero-client-py's cache directory (~/.merobox/auth_cache/).
        """
        self.cache_dir = Path(get_token_cache_dir())

    def _ensure_cache_dir(self) -> None:
        """Ensure the cache directory exists with appropriate permissions."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Set restrictive permissions (owner only)
        try:
            os.chmod(self.cache_dir, 0o700)
        except OSError:
            # May fail on some platforms, continue anyway
            pass

    def _extract_jwt_expiry(self, token: str) -> Optional[int]:
        """Extract expiry timestamp from JWT token.

        Args:
            token: The JWT token string.

        Returns:
            The expiry timestamp (exp claim) or None if not found/invalid.
        """
        try:
            # JWT format: header.payload.signature
            parts = token.split(".")
            if len(parts) != 3:
                return None

            # Decode payload (middle part)
            payload_b64 = parts[1]
            # Add padding if needed
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding

            payload_json = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_json)

            exp = payload.get("exp")
            if exp is not None:
                return int(exp)
            return None
        except (ValueError, json.JSONDecodeError, KeyError, TypeError):
            # If parsing fails, return None
            return None

    def _parse_token_response(
        self, response_data: dict[str, Any], node_url: str, username: Optional[str]
    ) -> AuthToken:
        """Parse token response from auth service.

        Handles both wrapped ({"data": {...}}) and unwrapped responses.

        Args:
            response_data: The JSON response from the auth endpoint.
            node_url: The node URL this token is for.
            username: The username used for authentication (if any).

        Returns:
            An AuthToken instance.
        """
        # Try wrapped response first
        if "data" in response_data and isinstance(response_data["data"], dict):
            data = response_data["data"]
        else:
            data = response_data

        access_token = data.get("access_token") or data.get("accessToken", "")
        refresh_token = data.get("refresh_token") or data.get("refreshToken")

        # Extract expiry from JWT if present
        expires_at = self._extract_jwt_expiry(access_token)

        return AuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            node_url=node_url,
            auth_method=AUTH_METHOD_USER_PASSWORD,
            username=username,
        )

    async def authenticate(
        self,
        node_url: str,
        username: str,
        password: str,
        timeout: float = DEFAULT_READ_TIMEOUT,
        session_manager: Optional[SessionManager] = None,
    ) -> AuthToken:
        """Authenticate with a remote node using username/password.

        Posts to /auth/token with the required payload:
        - auth_method: "user_password"
        - public_key: username
        - client_name: normalized node URL
        - timestamp: current unix timestamp
        - provider_data: {"username": "...", "password": "..."}

        Args:
            node_url: The base URL of the node (e.g., "http://node1.example.com").
            username: The username for authentication.
            password: The password for authentication.
            timeout: Request timeout in seconds.
            session_manager: Optional SessionManager for connection pooling.
                If not provided, uses the global shared session.

        Returns:
            An AuthToken on success.

        Raises:
            AuthenticationError: If authentication fails.
        """
        # Normalize node URL (remove trailing slash)
        normalized_url = node_url.rstrip("/")

        # Build the authentication payload per server contract
        payload = {
            "auth_method": AUTH_METHOD_USER_PASSWORD,
            "public_key": username,
            "client_name": normalized_url,
            "timestamp": int(time.time()),
            "provider_data": {"username": username, "password": password},
        }

        auth_endpoint = f"{normalized_url}{AUTH_TOKEN_ENDPOINT}"

        try:
            session = await _get_session(session_manager)

            async with session.post(
                auth_endpoint,
                json=payload,
                timeout=aiohttp.ClientTimeout(
                    total=timeout, connect=DEFAULT_CONNECTION_TIMEOUT
                ),
            ) as response:
                response_text = await response.text()

                if response.status == 200:
                    try:
                        response_data = json.loads(response_text)
                        token = self._parse_token_response(
                            response_data, normalized_url, username
                        )
                        console.print(
                            f"[green]✓ Authenticated with {normalized_url}[/green]"
                        )
                        return token
                    except json.JSONDecodeError as e:
                        raise AuthenticationError(
                            f"Invalid JSON response from auth endpoint: {e}"
                        ) from e
                elif response.status == 401:
                    raise AuthenticationError(
                        "Invalid credentials: username or password incorrect"
                    )
                elif response.status == 403:
                    raise AuthenticationError(
                        "Access forbidden: account may be disabled or locked"
                    )
                else:
                    raise AuthenticationError(
                        f"Authentication failed with status {response.status}: {response_text}"
                    )

        except aiohttp.ClientError as e:
            raise AuthenticationError(
                f"Network error during authentication: {e}"
            ) from e

    async def refresh(
        self,
        node_url: str,
        token: AuthToken,
        timeout: float = DEFAULT_READ_TIMEOUT,
        session_manager: Optional[SessionManager] = None,
    ) -> AuthToken:
        """Refresh an authentication token.

        Posts to /auth/refresh with access_token and refresh_token.

        Args:
            node_url: The base URL of the node.
            token: The current AuthToken with refresh_token.
            timeout: Request timeout in seconds.
            session_manager: Optional SessionManager for connection pooling.
                If not provided, uses the global shared session.

        Returns:
            A new AuthToken on success.

        Raises:
            AuthenticationError: If refresh fails.
        """
        if not token.refresh_token:
            raise AuthenticationError("Cannot refresh: no refresh token available")

        normalized_url = node_url.rstrip("/")
        refresh_endpoint = f"{normalized_url}{AUTH_REFRESH_ENDPOINT}"

        payload = {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
        }

        try:
            session = await _get_session(session_manager)

            async with session.post(
                refresh_endpoint,
                json=payload,
                timeout=aiohttp.ClientTimeout(
                    total=timeout, connect=DEFAULT_CONNECTION_TIMEOUT
                ),
            ) as response:
                response_text = await response.text()

                if response.status == 200:
                    try:
                        response_data = json.loads(response_text)
                        new_token = self._parse_token_response(
                            response_data, normalized_url, token.username
                        )
                        console.print(
                            f"[green]✓ Token refreshed for {normalized_url}[/green]"
                        )
                        return new_token
                    except json.JSONDecodeError as e:
                        raise AuthenticationError(
                            f"Invalid JSON response from refresh endpoint: {e}"
                        ) from e
                elif response.status == 401:
                    raise AuthenticationError(
                        "Refresh token expired or invalid. Please re-authenticate."
                    )
                else:
                    raise AuthenticationError(
                        f"Token refresh failed with status {response.status}: {response_text}"
                    )

        except aiohttp.ClientError as e:
            raise AuthenticationError(f"Network error during token refresh: {e}") from e

    def get_cached_token(self, node_name: str) -> Optional[AuthToken]:
        """Get a cached token for a node.

        Args:
            node_name: The stable node name/identifier.

        Returns:
            The cached AuthToken if found, None otherwise.
        """
        cache_path = Path(get_token_cache_path(node_name))
        if not cache_path.exists():
            return None

        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            return AuthToken.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError) as e:
            console.print(
                f"[yellow]Warning: Could not read token cache for {node_name}: {e}[/yellow]"
            )
            return None

    def save_token(self, token: AuthToken, node_name: str) -> bool:
        """Save a token to the cache.

        Args:
            token: The AuthToken to save.
            node_name: The stable node name/identifier.

        Returns:
            True if saved successfully, False otherwise.
        """
        self._ensure_cache_dir()
        cache_path = Path(get_token_cache_path(node_name))

        try:
            # Write atomically using temp file
            temp_path = cache_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(token.to_dict(), f, indent=2)

            # Set restrictive permissions before moving into place
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass

            # Atomic rename
            temp_path.rename(cache_path)
            return True

        except OSError as e:
            console.print(f"[red]Error saving token cache for {node_name}: {e}[/red]")
            return False

    def delete_token(self, node_name: str) -> bool:
        """Delete a cached token.

        Args:
            node_name: The stable node name/identifier.

        Returns:
            True if deleted (or didn't exist), False on error.
        """
        cache_path = Path(get_token_cache_path(node_name))
        if not cache_path.exists():
            return True

        try:
            cache_path.unlink()
            return True
        except OSError as e:
            console.print(f"[red]Error deleting token cache for {node_name}: {e}[/red]")
            return False

    def delete_all_tokens(self) -> int:
        """Delete all cached tokens.

        Returns:
            The number of tokens deleted.
        """
        if not self.cache_dir.exists():
            return 0

        deleted = 0
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                cache_file.unlink()
                deleted += 1
            except OSError as e:
                console.print(
                    f"[yellow]Warning: Could not delete {cache_file}: {e}[/yellow]"
                )
        return deleted

    def list_cached_tokens(self) -> list[tuple[str, AuthToken]]:
        """List all cached tokens.

        Returns:
            List of (node_name, AuthToken) tuples.
        """
        if not self.cache_dir.exists():
            return []

        tokens = []
        for cache_file in self.cache_dir.glob("*.json"):
            node_name = cache_file.stem
            try:
                with open(cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                token = AuthToken.from_dict(data)
                tokens.append((node_name, token))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return tokens

    async def get_valid_token(
        self,
        node_url: str,
        node_name: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> Optional[AuthToken]:
        """Get a valid token for a node, refreshing or authenticating as needed.

        Args:
            node_url: The base URL of the node.
            node_name: The stable node name/identifier for caching.
            username: Username for authentication (if not cached).
            password: Password for authentication (if not cached).

        Returns:
            A valid AuthToken, or None if authentication info is missing.
        """
        # Try to get cached token
        cached_token = self.get_cached_token(node_name)

        if cached_token:
            if cached_token.auth_method == AUTH_METHOD_USER_PASSWORD:
                # Check if token is still valid
                if not cached_token.is_expired():
                    return cached_token

                # Try to refresh if we have a refresh token
                if cached_token.refresh_token:
                    try:
                        new_token = await self.refresh(node_url, cached_token)
                        self.save_token(new_token, node_name)
                        return new_token
                    except AuthenticationError as e:
                        console.print(f"[yellow]Token refresh failed: {e}[/yellow]")
                        # Fall through to re-authenticate

                # Get username from cached token if not provided
                if username is None and cached_token.username:
                    username = cached_token.username

        # Need to authenticate
        if username is None or password is None:
            return None

        try:
            token = await self.authenticate(node_url, username, password)
            self.save_token(token, node_name)
            return token
        except AuthenticationError:
            return None


class AuthenticationError(Exception):
    """Raised when authentication fails."""
