"""
Node resolver for merobox.

This module provides NodeResolver for unified node resolution across:
- Registered remote nodes
- Direct URL references
- Docker-based nodes
- Binary-based nodes

Resolution order (as specified in the plan):
1. Registered remote node
2. Direct URL
3. Docker node
4. Binary node
"""

import asyncio
import concurrent.futures
import os
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp
from rich.console import Console
from rich.prompt import Prompt

from merobox.commands.auth import (
    AUTH_METHOD_API_KEY,
    AUTH_METHOD_NONE,
    AUTH_METHOD_USER_PASSWORD,
    AuthManager,
    AuthToken,
    run_with_shared_session_cleanup,
)
from merobox.commands.constants import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RPC_PORT,
)
from merobox.commands.errors import AuthenticationError, NodeResolutionError
from merobox.commands.remote_nodes import RemoteNodeManager

console = Console()

# Health endpoint for auth detection
ADMIN_HEALTH_ENDPOINT = "/admin-api/health"

# Environment variable names for credentials
ENV_MEROBOX_USERNAME = "MEROBOX_USERNAME"
ENV_MEROBOX_PASSWORD = "MEROBOX_PASSWORD"
ENV_MEROBOX_API_KEY = "MEROBOX_API_KEY"


@dataclass
class ResolvedNode:
    """Result of node resolution."""

    url: str
    node_name: str
    auth_required: bool
    auth_method: str
    token: Optional[AuthToken] = None
    source: str = "unknown"  # remote, url, docker, binary


class NodeResolver:
    """
    Resolves node references to their URLs and handles authentication.

    Resolution order:
    1. Registered remote node (by name)
    2. Direct URL reference
    3. Docker node (by container name)
    4. Binary node (by process name)
    """

    def __init__(
        self,
        remote_manager: Optional[RemoteNodeManager] = None,
        auth_manager: Optional[AuthManager] = None,
        docker_manager: Optional[Any] = None,
        binary_manager: Optional[Any] = None,
    ):
        """Initialize NodeResolver.

        Args:
            remote_manager: Manager for remote node registry.
            auth_manager: Manager for authentication.
            docker_manager: Manager for Docker nodes.
            binary_manager: Manager for binary nodes.
        """
        self.remote_manager = remote_manager or RemoteNodeManager()
        self.auth_manager = auth_manager or AuthManager()
        self.docker_manager = docker_manager
        self.binary_manager = binary_manager

    async def resolve(
        self,
        node_ref: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        prompt_for_credentials: bool = True,
        skip_auth: bool = False,
    ) -> ResolvedNode:
        """Resolve a node reference to its URL and handle authentication.

        Args:
            node_ref: Node reference (registered name, URL, docker name, or binary name).
            username: Username for authentication (overrides env/registry).
            password: Password for authentication (overrides env).
            api_key: API key for authentication (overrides env).
            prompt_for_credentials: If True, prompt user for missing credentials.
            skip_auth: If True, skip authentication entirely.

        Returns:
            ResolvedNode with URL, stable node name, auth status, and token if authenticated.

        Raises:
            NodeResolutionError: If the node cannot be resolved.
            AuthenticationError: If authentication fails.
        """
        resolved = await self._resolve_node_url(node_ref)

        if skip_auth:
            return resolved

        # Check if auth is required for remote URLs
        if resolved.source in ("remote", "url"):
            # If registry already specifies auth is required, trust it
            # Only use health check detection for unregistered URLs or when registry says no auth
            if not resolved.auth_required:
                auth_required, detected_method = await self._detect_auth_requirement(
                    resolved.url
                )
                resolved.auth_required = auth_required
            else:
                # Registry says auth is required - use registry's auth method
                detected_method = resolved.auth_method

            if resolved.auth_required:
                # Determine auth method
                resolved.auth_method = self._determine_auth_method(
                    node_ref, detected_method, api_key
                )

                # Get credentials and authenticate
                resolved.token = await self._handle_authentication(
                    resolved.url,
                    resolved.node_name,
                    resolved.auth_method,
                    username=username,
                    password=password,
                    api_key=api_key,
                    prompt_for_credentials=prompt_for_credentials,
                    node_ref=node_ref,
                )

        return resolved

    async def _resolve_node_url(self, node_ref: str) -> ResolvedNode:
        """Resolve a node reference to its URL using priority order.

        Resolution order:
        1. Registered remote node
        2. Direct URL
        3. Docker node
        4. Binary node

        Args:
            node_ref: Node reference to resolve.

        Returns:
            ResolvedNode with basic resolution info (no auth yet).

        Raises:
            NodeResolutionError: If the node cannot be resolved.
        """
        # 1. Check registered remote nodes first
        entry = self.remote_manager.get(node_ref)
        if entry:
            return ResolvedNode(
                url=entry.url,
                node_name=entry.name,
                auth_required=entry.auth.method != AUTH_METHOD_NONE,
                auth_method=entry.auth.method,
                source="remote",
            )

        # 2. Check if it's a direct URL
        if self.remote_manager.is_url(node_ref):
            # Check if URL is registered under a different name
            entry = self.remote_manager.get_by_url(node_ref)
            if entry:
                return ResolvedNode(
                    url=entry.url,
                    node_name=entry.name,
                    auth_required=entry.auth.method != AUTH_METHOD_NONE,
                    auth_method=entry.auth.method,
                    source="remote",
                )

            # Direct URL not registered
            normalized_url = node_ref.rstrip("/")
            return ResolvedNode(
                url=normalized_url,
                node_name=self.remote_manager.get_stable_node_name(node_ref),
                auth_required=False,  # Will be detected later
                auth_method=AUTH_METHOD_NONE,
                source="url",
            )

        # 3. Check Docker nodes
        if self.docker_manager:
            docker_url = self._resolve_docker_node(node_ref)
            if docker_url:
                return ResolvedNode(
                    url=docker_url,
                    node_name=node_ref,
                    auth_required=False,  # Local nodes typically don't require auth
                    auth_method=AUTH_METHOD_NONE,
                    source="docker",
                )

        # 4. Check binary nodes
        if self.binary_manager:
            binary_url = self._resolve_binary_node(node_ref)
            if binary_url:
                return ResolvedNode(
                    url=binary_url,
                    node_name=node_ref,
                    auth_required=False,  # Local nodes typically don't require auth
                    auth_method=AUTH_METHOD_NONE,
                    source="binary",
                )

        # Node not found anywhere
        raise NodeResolutionError(
            f"Node '{node_ref}' not found. "
            f"It's not a registered remote node, valid URL, running Docker container, "
            f"or running binary process."
        )

    def _resolve_docker_node(self, node_name: str) -> Optional[str]:
        """Resolve a Docker node to its URL.

        Args:
            node_name: Docker container name.

        Returns:
            URL if found, None otherwise.
        """
        if not self.docker_manager:
            return None

        try:
            # Check if we have a cached RPC port
            if hasattr(self.docker_manager, "get_node_rpc_port"):
                port = self.docker_manager.get_node_rpc_port(node_name)
                if port:
                    return f"http://localhost:{port}"

            # Try to get the container
            if hasattr(self.docker_manager, "client"):
                try:
                    container = self.docker_manager.client.containers.get(node_name)
                    container.reload()
                    if container.status == "running":
                        # Extract port from container
                        port_mappings = (
                            container.attrs.get("NetworkSettings", {}).get("Ports")
                            or {}
                        )
                        host_bindings = port_mappings.get("2528/tcp") or []
                        for binding in host_bindings:
                            host_port = binding.get("HostPort")
                            if host_port and host_port.isdigit():
                                return f"http://localhost:{host_port}"
                        # Fallback to default port
                        return f"http://localhost:{DEFAULT_RPC_PORT}"
                except Exception:
                    return None

            return None
        except Exception:
            return None

    def _resolve_binary_node(self, node_name: str) -> Optional[str]:
        """Resolve a binary node to its URL.

        Args:
            node_name: Binary process node name.

        Returns:
            URL if found, None otherwise.
        """
        if not self.binary_manager:
            return None

        try:
            # Check if node is running
            if hasattr(self.binary_manager, "is_node_running"):
                if not self.binary_manager.is_node_running(node_name):
                    return None

            # Get RPC port
            if hasattr(self.binary_manager, "get_node_rpc_port"):
                port = self.binary_manager.get_node_rpc_port(node_name)
                if port:
                    return f"http://localhost:{port}"

            # Fallback to default port
            return f"http://localhost:{DEFAULT_RPC_PORT}"
        except Exception:
            return None

    async def _detect_auth_requirement(self, url: str) -> tuple[bool, str]:
        """Detect if a node requires authentication.

        Calls GET /admin-api/health and checks the response:
        - 200: No auth required
        - 401/403: Auth required

        Args:
            url: The node URL.

        Returns:
            Tuple of (auth_required, detected_auth_method).
        """
        health_url = f"{url.rstrip('/')}{ADMIN_HEALTH_ENDPOINT}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    health_url,
                    timeout=aiohttp.ClientTimeout(
                        total=DEFAULT_READ_TIMEOUT,
                        connect=DEFAULT_CONNECTION_TIMEOUT,
                    ),
                ) as response:
                    if response.status == 200:
                        return False, AUTH_METHOD_NONE
                    elif response.status in (401, 403):
                        # Check for auth hints in response headers or body
                        auth_header = response.headers.get("WWW-Authenticate", "")
                        if "Bearer" in auth_header:
                            return True, AUTH_METHOD_USER_PASSWORD
                        return (
                            True,
                            AUTH_METHOD_USER_PASSWORD,
                        )  # Default to user_password
                    else:
                        # Other status codes - assume no auth but warn
                        console.print(
                            f"[yellow]Warning: Health check returned status {response.status}[/yellow]"
                        )
                        return False, AUTH_METHOD_NONE

        except aiohttp.ClientError as e:
            console.print(
                f"[yellow]Warning: Could not reach {url} for auth detection: {e}[/yellow]"
            )
            return False, AUTH_METHOD_NONE
        except asyncio.TimeoutError:
            console.print(
                f"[yellow]Warning: Timeout checking auth requirement for {url}[/yellow]"
            )
            return False, AUTH_METHOD_NONE

    def _determine_auth_method(
        self, node_ref: str, detected_method: str, api_key: Optional[str]
    ) -> str:
        """Determine the auth method to use.

        Priority:
        1. Explicit API key provided -> api_key
        2. Registered node auth config
        3. Environment variable API key -> api_key
        4. Detected method from server response

        Args:
            node_ref: Node reference.
            detected_method: Method detected from server response.
            api_key: Explicitly provided API key.

        Returns:
            The auth method to use.
        """
        # If API key is explicitly provided, use it
        if api_key:
            return AUTH_METHOD_API_KEY

        # Check registered node config
        entry = self.remote_manager.get(node_ref)
        if entry and entry.auth.method != AUTH_METHOD_NONE:
            return entry.auth.method

        # Check for URL match in registry
        if self.remote_manager.is_url(node_ref):
            entry = self.remote_manager.get_by_url(node_ref)
            if entry and entry.auth.method != AUTH_METHOD_NONE:
                return entry.auth.method

        # Check environment for API key
        if os.getenv(ENV_MEROBOX_API_KEY):
            return AUTH_METHOD_API_KEY

        # Use detected method
        return detected_method or AUTH_METHOD_USER_PASSWORD

    async def _handle_authentication(
        self,
        url: str,
        node_name: str,
        auth_method: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        prompt_for_credentials: bool = True,
        node_ref: Optional[str] = None,
    ) -> Optional[AuthToken]:
        """Handle authentication for a node.

        Credential resolution order:
        1. Explicit parameters
        2. Environment variables
        3. Registered node config (username, password, api_key)
        4. Interactive prompt

        Args:
            url: Node URL.
            node_name: Stable node name for caching.
            auth_method: Authentication method to use.
            username: Explicitly provided username.
            password: Explicitly provided password.
            api_key: Explicitly provided API key.
            prompt_for_credentials: If True, prompt for missing credentials.
            node_ref: Original node reference (for registry lookup).

        Returns:
            AuthToken if authenticated, None if credentials not available.
        """
        # Handle API key auth
        if auth_method == AUTH_METHOD_API_KEY:
            # Try cached token first (same as user_password path)
            cached = self.auth_manager.get_cached_token(node_name)
            if (
                cached
                and cached.auth_method == AUTH_METHOD_API_KEY
                and not cached.is_expired()
            ):
                return cached

            resolved_api_key = self._resolve_api_key(api_key, node_ref)
            if resolved_api_key:
                # Store API key as an AuthToken for consistent handling
                token = AuthToken(
                    access_token=resolved_api_key,
                    refresh_token=None,
                    expires_at=None,
                    node_url=url,
                    auth_method=AUTH_METHOD_API_KEY,
                    username=None,
                )
                self.auth_manager.save_token(token, node_name)
                console.print(f"[green]✓ Using API key for {url}[/green]")
                return token
            elif prompt_for_credentials:
                resolved_api_key = Prompt.ask(
                    f"[cyan]Enter API key for {url}[/cyan]",
                    password=True,
                )
                if resolved_api_key:
                    token = AuthToken(
                        access_token=resolved_api_key,
                        refresh_token=None,
                        expires_at=None,
                        node_url=url,
                        auth_method=AUTH_METHOD_API_KEY,
                        username=None,
                    )
                    self.auth_manager.save_token(token, node_name)
                    console.print(f"[green]✓ API key saved for {url}[/green]")
                    return token
            return None

        # Handle user_password auth
        if auth_method == AUTH_METHOD_USER_PASSWORD:
            resolved_username, resolved_password = self._resolve_credentials(
                username, password, node_ref
            )

            # Try cached token first
            token = await self.auth_manager.get_valid_token(
                url, node_name, resolved_username, resolved_password
            )
            if token:
                return token

            # Need credentials
            if not resolved_username or not resolved_password:
                if prompt_for_credentials:
                    if not resolved_username:
                        resolved_username = Prompt.ask(
                            f"[cyan]Username for {url}[/cyan]"
                        )
                    if not resolved_password:
                        resolved_password = Prompt.ask(
                            f"[cyan]Password for {url}[/cyan]",
                            password=True,
                        )
                else:
                    console.print(
                        f"[yellow]Authentication required for {url} but no credentials provided[/yellow]"
                    )
                    return None

            # Authenticate
            try:
                token = await self.auth_manager.authenticate(
                    url, resolved_username, resolved_password
                )
                self.auth_manager.save_token(token, node_name)
                return token
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed: {e}[/red]")
                raise

        return None

    def _resolve_api_key(
        self, explicit_key: Optional[str], node_ref: Optional[str] = None
    ) -> Optional[str]:
        """Resolve API key from explicit value, registered node config, or environment.

        Priority:
        1. Explicit parameter
        2. Registered node config
        3. Environment variable

        Args:
            explicit_key: Explicitly provided API key.
            node_ref: Node reference for registry lookup.

        Returns:
            Resolved API key or None.
        """
        if explicit_key:
            return explicit_key

        # Check registered node config
        if node_ref:
            entry = self.remote_manager.get(node_ref)
            if entry and entry.auth and entry.auth.api_key:
                return entry.auth.api_key
            elif self.remote_manager.is_url(node_ref):
                entry = self.remote_manager.get_by_url(node_ref)
                if entry and entry.auth and entry.auth.api_key:
                    return entry.auth.api_key

        return os.getenv(ENV_MEROBOX_API_KEY)

    def _resolve_credentials(
        self,
        explicit_username: Optional[str],
        explicit_password: Optional[str],
        node_ref: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        """Resolve username and password from various sources.

        Priority:
        1. Explicit parameters
        2. Environment variables
        3. Registered node config (username and password)

        Args:
            explicit_username: Explicitly provided username.
            explicit_password: Explicitly provided password.
            node_ref: Node reference for registry lookup.

        Returns:
            Tuple of (username, password).
        """
        # Start with explicit values
        username = explicit_username
        password = explicit_password

        # Fall back to environment variables
        if not username:
            username = os.getenv(ENV_MEROBOX_USERNAME)
        if not password:
            password = os.getenv(ENV_MEROBOX_PASSWORD)

        # Fall back to registered node config (username and password)
        if node_ref:
            entry = self.remote_manager.get(node_ref)
            if entry and entry.auth:
                if not username and entry.auth.username:
                    username = entry.auth.username
                if not password and entry.auth.password:
                    password = entry.auth.password
            elif self.remote_manager.is_url(node_ref):
                entry = self.remote_manager.get_by_url(node_ref)
                if entry and entry.auth:
                    if not username and entry.auth.username:
                        username = entry.auth.username
                    if not password and entry.auth.password:
                        password = entry.auth.password

        return username, password

    def resolve_sync(
        self,
        node_ref: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        prompt_for_credentials: bool = True,
        skip_auth: bool = False,
    ) -> ResolvedNode:
        """Synchronous wrapper for resolve().

        Args:
            node_ref: Node reference.
            username: Username for authentication.
            password: Password for authentication.
            api_key: API key for authentication.
            prompt_for_credentials: If True, prompt for missing credentials.
            skip_auth: If True, skip authentication.

        Returns:
            ResolvedNode.
        """
        # Create the resolve coroutine wrapped with session cleanup
        coro = run_with_shared_session_cleanup(
            self.resolve(
                node_ref,
                username=username,
                password=password,
                api_key=api_key,
                prompt_for_credentials=prompt_for_credentials,
                skip_auth=skip_auth,
            )
        )

        try:
            asyncio.get_running_loop()
            # Already in an async context - run in thread to avoid blocking
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop - safe to use run_until_complete
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            return loop.run_until_complete(coro)

    def get_node_url(self, node_ref: str) -> str:
        """Get the URL for a node reference without authentication.

        This is a simple utility for getting URLs when auth isn't needed.

        Args:
            node_ref: Node reference.

        Returns:
            The node URL.

        Raises:
            NodeResolutionError: If the node cannot be resolved.
        """
        resolved = self.resolve_sync(node_ref, skip_auth=True)
        return resolved.url

    def is_registered_remote(self, node_ref: str) -> bool:
        """Check if a node reference is registered in the remote node registry.

        Args:
            node_ref: Node reference (name or URL).

        Returns:
            True if the node is registered, False otherwise.
        """
        # Check by name
        if self.remote_manager.get(node_ref) is not None:
            return True
        # Check by URL
        if self.remote_manager.is_url(node_ref):
            return self.remote_manager.get_by_url(node_ref) is not None
        return False

    def is_url(self, node_ref: str) -> bool:
        """Check if a node reference is a URL.

        Args:
            node_ref: Node reference.

        Returns:
            True if the reference is a URL (starts with http:// or https://).
        """
        return self.remote_manager.is_url(node_ref)

    def register_remote(
        self,
        name: str,
        url: str,
        auth_method: str = AUTH_METHOD_NONE,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """Register a remote node in the registry.

        Args:
            name: Friendly name for the node.
            url: Node URL.
            auth_method: Authentication method (user_password, api_key, none).
            username: Default username for user_password auth.
            password: Password for user_password auth.
            api_key: API key for api_key auth.
            description: Human-readable description.

        Returns:
            True if registered successfully.
        """
        return self.remote_manager.register(
            name=name,
            url=url,
            auth_method=auth_method,
            username=username,
            password=password,
            api_key=api_key,
            description=description,
        )


# NodeResolutionError is now imported from merobox.commands.errors
# Keeping this comment for backward compatibility reference


def get_resolver(
    docker_manager: Optional[Any] = None,
    binary_manager: Optional[Any] = None,
) -> NodeResolver:
    """Create a NodeResolver with appropriate managers.

    Args:
        docker_manager: Docker manager (or None to skip Docker resolution).
        binary_manager: Binary manager (or None to skip binary resolution).

    Returns:
        Configured NodeResolver instance.
    """
    return NodeResolver(
        remote_manager=RemoteNodeManager(),
        auth_manager=AuthManager(),
        docker_manager=docker_manager,
        binary_manager=binary_manager,
    )
