"""
Authentication management for remote Calimero nodes.

This module provides AuthToken dataclass and AuthManager for handling:
- Username/password authentication via /auth/token endpoint
- Token refresh via /auth/refresh endpoint
- Disk-based token caching under ~/.merobox/auth_cache/

Token Cache Integration:
    This module uses calimero-client-py's token cache helpers to ensure
    merobox and the Rust client use identical cache paths:
    - `calimero_client_py.get_token_cache_path(node_name)` for cache file paths
    - `calimero_client_py.get_token_cache_dir()` for the cache directory

    This ensures that:
    1. Merobox-written tokens are found by calimero-client-py's auto-refresh
    2. Tokens refreshed by the Rust client are visible to merobox
"""

import base64
import json
import os
import time
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
            async with aiohttp.ClientSession() as session:
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
    ) -> AuthToken:
        """Refresh an authentication token.

        Posts to /auth/refresh with access_token and refresh_token.

        Args:
            node_url: The base URL of the node.
            token: The current AuthToken with refresh_token.
            timeout: Request timeout in seconds.

        Returns:
            A new AuthToken on success.

        Raises:
            AuthenticationError: If refresh fails.
        """
        if not token.refresh_token:
            raise AuthenticationError(
                "Cannot refresh: no refresh token available")

        normalized_url = node_url.rstrip("/")
        refresh_endpoint = f"{normalized_url}{AUTH_REFRESH_ENDPOINT}"

        payload = {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
        }

        try:
            async with aiohttp.ClientSession() as session:
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
            raise AuthenticationError(
                f"Network error during token refresh: {e}") from e

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
            console.print(
                f"[red]Error saving token cache for {node_name}: {e}[/red]")
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
            console.print(
                f"[red]Error deleting token cache for {node_name}: {e}[/red]")
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
                    console.print(
                        f"[yellow]Token refresh failed: {e}[/yellow]")
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
