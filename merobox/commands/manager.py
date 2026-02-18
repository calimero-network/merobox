"""
Calimero Manager - Core functionality for managing Calimero nodes in Docker containers.
"""

import atexit
import logging
import os
import shutil
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import docker
from rich.console import Console
from rich.table import Table

from merobox.commands.config_utils import (
    apply_bootstrap_nodes,
    apply_e2e_defaults,
    apply_near_devnet_config_to_file,
)
from merobox.commands.constants import (
    CONTAINER_STOP_TIMEOUT,
    DEFAULT_P2P_PORT,
    DEFAULT_RPC_PORT,
    NODE_STARTUP_DELAY,
    P2P_PORT_BINDING,
    RPC_PORT_BINDING,
)

logger = logging.getLogger(__name__)
console = Console()

# Default CORS origins for localhost development
DEFAULT_CORS_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
]

# Explicit headers allowed in CORS requests (required when credentials are enabled)
# Note: wildcard '*' doesn't work with credentials, so we list headers explicitly
CORS_ALLOWED_HEADERS = (
    "Accept,Accept-Language,Content-Language,Content-Type,"
    "Authorization,X-Requested-With,X-Auth-Token,Cache-Control"
)


def _validate_cors_origins(origins: list[str]) -> list[str]:
    """
    Validate and sanitize CORS origins list.

    Rejects wildcard '*' and origins containing commas to prevent CORS injection.

    Args:
        origins: List of origin URLs to validate

    Returns:
        The validated origins list

    Raises:
        ValueError: If an origin is invalid (wildcard or contains comma)
    """
    validated = []
    for origin in origins:
        if origin == "*":
            raise ValueError(
                "Wildcard '*' is not allowed in cors_allowed_origins. "
                "Please specify explicit origins."
            )
        if "," in origin:
            raise ValueError(
                f"Origin '{origin}' contains a comma which is not allowed. "
                "Please specify origins as separate list items."
            )
        validated.append(origin.strip())
    return validated


def _get_node_hostname(node_name: str) -> str:
    """
    Transform node name into a hostname for nip.io.

    Transforms 'calimero-foo-bar' into 'foobar' for use in nip.io domains.
    The 'calimero-' prefix is removed and hyphens are stripped to create
    a simple hostname suitable for subdomain use.

    Args:
        node_name: The node name (e.g., 'calimero-node-1')

    Returns:
        The transformed hostname (e.g., 'node1')
    """
    return node_name.replace("calimero-", "").replace("-", "")


class DockerManager:
    """Manages Calimero nodes in Docker containers."""

    def __init__(self, enable_signal_handlers: bool = True):
        """
        Initialize the DockerManager.

        Args:
            enable_signal_handlers: If True, register signal handlers for graceful
                shutdown on SIGINT/SIGTERM. Set to False in tests or when managing
                signals externally.
        """
        try:
            self.client = docker.from_env()
        except Exception as e:
            console.print(f"[red]Failed to connect to Docker: {str(e)}[/red]")
            console.print(
                "[yellow]Make sure Docker is running and you have permission to access it.[/yellow]"
            )
            sys.exit(1)
        self.nodes = {}
        self.node_rpc_ports: dict[str, int] = {}
        self._shutting_down = False
        self._cleanup_lock = threading.Lock()
        self._cleanup_done = False
        self._original_sigint_handler = None
        self._original_sigterm_handler = None

        if enable_signal_handlers:
            self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Register signal handlers for graceful shutdown."""
        # Store original handlers so we can restore them if needed
        self._original_sigint_handler = signal.signal(
            signal.SIGINT, self._signal_handler
        )
        self._original_sigterm_handler = signal.signal(
            signal.SIGTERM, self._signal_handler
        )
        # Also register atexit handler for cleanup on normal exit
        atexit.register(self._cleanup_on_exit)

    def _signal_handler(self, signum, frame):
        """Handle SIGINT/SIGTERM signals for graceful shutdown.

        Uses os._exit() instead of sys.exit() to avoid raising SystemExit
        which can corrupt state if async tasks are running. This also
        prevents atexit handlers from being called again after cleanup.
        """
        if self._shutting_down:
            # Already shutting down, force exit on second signal
            console.print("\n[red]Forced exit requested, terminating...[/red]")
            os._exit(1)

        self._shutting_down = True
        sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        console.print(
            f"\n[yellow]Received {sig_name}, initiating graceful shutdown...[/yellow]"
        )

        self._cleanup_resources()

        # Use os._exit() to avoid triggering atexit handlers (already cleaned up)
        # and to prevent raising SystemExit which can corrupt async state
        os._exit(0)

    def _cleanup_on_exit(self):
        """Cleanup handler for atexit - only runs if not already cleaned up."""
        if not self._shutting_down:
            self._cleanup_resources()

    def _cleanup_resources(
        self, drain_timeout: int = 3, stop_timeout: int = CONTAINER_STOP_TIMEOUT
    ):
        """Stop all managed resources (containers) with graceful shutdown.

        Thread-safe cleanup that ensures resources are only cleaned once,
        preventing double-cleanup races between signal handlers and atexit.

        Uses a shorter drain_timeout (3s) than normal operations (5s) because
        cleanup scenarios (SIGTERM handler, atexit) need faster completion to
        avoid blocking process termination or triggering forced kills.

        Args:
            drain_timeout: Seconds to wait for connection draining (default 3s).
            stop_timeout: Seconds to wait for container stop (default CONTAINER_STOP_TIMEOUT)
        """
        with self._cleanup_lock:
            if self._cleanup_done:
                return
            self._cleanup_done = True

        if self.nodes:
            console.print(
                "[cyan]Stopping managed containers with graceful shutdown...[/cyan]"
            )
            containers_to_stop = [
                (name, container) for name, container in self.nodes.items()
            ]
            self._graceful_stop_containers_batch(
                containers_to_stop, drain_timeout, stop_timeout
            )
            self.nodes.clear()
            self.node_rpc_ports.clear()

    def remove_signal_handlers(self):
        """Remove signal handlers and restore original handlers."""
        if self._original_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._original_sigint_handler)
            self._original_sigint_handler = None
        if self._original_sigterm_handler is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm_handler)
            self._original_sigterm_handler = None

    def _is_remote_image(self, image: str) -> bool:
        """Check if the image name indicates a remote registry."""
        # Check if image contains a registry (has slashes and a tag)
        return "/" in image and ":" in image

    def force_pull_image(self, image: str) -> bool:
        """Force pull an image even if it exists locally."""
        try:
            console.print(f"[yellow]Force pulling image: {image}[/yellow]")

            # Remove local image if it exists
            try:
                self.client.images.get(image)
                console.print(f"[cyan]Removing local image: {image}[/cyan]")
                self.client.images.remove(image, force=True)
            except docker.errors.ImageNotFound:
                pass

            # Pull the fresh image
            return self._ensure_image_pulled(image)

        except Exception as e:
            console.print(f"[red]✗ Error force pulling image {image}: {str(e)}[/red]")
            return False

    def _ensure_image_pulled(self, image: str) -> bool:
        """Ensure the specified Docker image is available locally, pulling if remote."""
        try:
            # Check if image exists locally
            try:
                self.client.images.get(image)
                console.print(f"[cyan]✓ Image {image} already available locally[/cyan]")
                return True
            except docker.errors.ImageNotFound:
                pass

            # Image not found locally, attempt to pull it
            console.print(f"[yellow]Pulling image: {image}[/yellow]")
            try:
                # Pull the image
                self.client.images.pull(image)

                console.print(f"[green]✓ Successfully pulled image: {image}[/green]")
                return True

            except docker.errors.NotFound:
                console.print(f"[red]✗ Image {image} not found in registry[/red]")
                return False
            except docker.errors.APIError as e:
                console.print(
                    f"[red]✗ Docker API error pulling {image}: {str(e)}[/red]"
                )
                return False
            except Exception as e:
                console.print(f"[red]✗ Failed to pull image {image}: {str(e)}[/red]")
                return False

        except Exception as e:
            console.print(
                f"[red]✗ Error checking/pulling image {image}: {str(e)}[/red]"
            )
            return False

    def _extract_host_port(self, container, container_port: str) -> Optional[int]:
        """Extract the published host port for a given container port."""
        try:
            ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
            host_bindings = ports.get(container_port)
            if host_bindings:
                for binding in host_bindings:
                    host_port = binding.get("HostPort")
                    if host_port and host_port.isdigit():
                        return int(host_port)

            port_bindings = (
                container.attrs.get("HostConfig", {}).get("PortBindings") or {}
            )
            host_bindings = port_bindings.get(container_port)
            if host_bindings:
                for binding in host_bindings:
                    host_port = binding.get("HostPort")
                    if host_port and host_port.isdigit():
                        return int(host_port)

            env_vars = container.attrs.get("Config", {}).get("Env") or []
            for env_entry in env_vars:
                if isinstance(env_entry, str) and env_entry.startswith(
                    "HOST_RPC_PORT="
                ):
                    value = env_entry.split("=", 1)[1]
                    if value.isdigit():
                        return int(value)
        except (KeyError, TypeError, AttributeError) as e:
            logger.debug("Failed to extract host port: %s", e)
            return None
        except docker.errors.DockerException as e:
            logger.debug("Docker error while extracting host port: %s", e)
            return None

        return None

    def get_node_rpc_port(self, node_name: str) -> Optional[int]:
        """Return the published RPC port for the given node, if available."""
        if node_name in self.node_rpc_ports:
            return self.node_rpc_ports[node_name]

        try:
            container = self.client.containers.get(node_name)
            container.reload()
            host_port = self._extract_host_port(container, RPC_PORT_BINDING)
            if host_port is not None:
                self.node_rpc_ports[node_name] = host_port
            return host_port
        except docker.errors.NotFound:
            return None
        except Exception:
            return None

    def run_node(
        self,
        node_name: str,
        port: int = DEFAULT_P2P_PORT,
        rpc_port: int = DEFAULT_RPC_PORT,
        chain_id: str = "testnet-1",
        data_dir: str = None,
        image: str = None,
        auth_service: bool = False,
        auth_image: str = None,
        auth_use_cached: bool = False,
        webui_use_cached: bool = False,
        log_level: str = "debug",
        rust_backtrace: str = "0",
        workflow_id: str = None,  # for test isolation
        e2e_mode: bool = False,  # enable e2e-style defaults
        config_path: str = None,  # custom config.toml path
        near_devnet_config: dict = None,
        bootstrap_nodes: list[str] = None,  # bootstrap nodes to connect to
        use_image_entrypoint: bool = False,  # preserve Docker image's entrypoint
        cors_allowed_origins: list[str] = None,  # explicit CORS origin allowlist
    ) -> bool:
        """Run a Calimero node container."""
        try:
            # Determine the image to use
            image_to_use = image or "ghcr.io/calimero-network/merod:prerelease"

            # Ensure the image is available
            if not self._ensure_image_pulled(image_to_use):
                console.print(
                    f"[red]✗ Cannot proceed without image: {image_to_use}[/red]"
                )
                return False

            # Check if containers already exist and clean them up
            for container_name in [node_name, f"{node_name}-init"]:
                try:
                    existing_container = self.client.containers.get(container_name)
                    if existing_container.status == "running":
                        console.print(
                            f"[yellow]Container {container_name} is already running, stopping it...[/yellow]"
                        )
                        try:
                            existing_container.stop()
                            existing_container.remove()
                            console.print(
                                f"[green]✓ Cleaned up existing container {container_name}[/green]"
                            )
                        except Exception as stop_error:
                            console.print(
                                f"[yellow]⚠️  Could not stop container {container_name}: {str(stop_error)}[/yellow]"
                            )
                            console.print("[yellow]Trying to force remove...[/yellow]")
                            try:
                                # Try to force remove the container
                                existing_container.remove(force=True)
                                console.print(
                                    f"[green]✓ Force removed container {container_name}[/green]"
                                )
                            except Exception as force_error:
                                console.print(
                                    f"[red]✗ Could not remove container {container_name}: {str(force_error)}[/red]"
                                )
                                console.print(
                                    "[yellow]Container may need manual cleanup. Continuing with deployment...[/yellow]"
                                )
                                # Continue anyway - the new container will have a different name
                    else:
                        # Container exists but not running, just remove it
                        existing_container.remove()
                        console.print(
                            f"[green]✓ Cleaned up existing container {container_name}[/green]"
                        )
                except docker.errors.NotFound:
                    pass

            # Set container names (using standard names since we've cleaned up)
            container_name = node_name
            init_container_name = f"{node_name}-init"

            # Prepare data directory
            if data_dir is None:
                data_dir = f"./data/{node_name}"

            # Create data directory if it doesn't exist
            os.makedirs(data_dir, exist_ok=True)

            # Create the node-specific subdirectory that merod expects
            node_data_dir = os.path.join(data_dir, node_name)
            os.makedirs(node_data_dir, exist_ok=True)

            # Set restrictive permissions (owner only) for sensitive node data
            # Root in the container has full access regardless, and _fix_permissions
            # handles ownership when the host user needs to access files created by Docker
            os.chmod(data_dir, 0o700)
            os.chmod(node_data_dir, 0o700)

            # Handle custom config if provided
            skip_init = False
            if config_path is not None:
                config_source = Path(config_path)
                if not config_source.exists():
                    console.print(
                        f"[red]✗ Custom config file not found: {config_path}[/red]"
                    )
                    return False

                config_dest = os.path.join(node_data_dir, "config.toml")
                try:
                    shutil.copy2(config_source, config_dest)
                    console.print(
                        f"[green]✓ Copied custom config from {config_path} to {config_dest}[/green]"
                    )
                    skip_init = True
                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to copy custom config: {str(e)}[/red]"
                    )
                    return False

            # Prepare container configuration
            # Prepare environment variables for node
            node_env = {
                "CALIMERO_HOME": "/app/data",
                "NODE_NAME": node_name,
                "RUST_LOG": log_level,
                "RUST_BACKTRACE": rust_backtrace,
            }

            # Debug: Print the RUST_LOG value being set
            console.print(
                f"[cyan]Setting RUST_LOG for node {node_name}: {log_level}[/cyan]"
            )
            # Debug: Print the RUST_BACKTRACE value being set
            console.print(
                f"[cyan]Setting RUST_BACKTRACE for node {node_name}: {rust_backtrace}[/cyan]"
            )

            # Also print all environment variables being set for debugging
            console.print(f"[yellow]Environment variables for {node_name}:[/yellow]")
            for key, value in node_env.items():
                console.print(f"  {key}={value}")

            # By default, fetch fresh WebUI unless explicitly disabled
            env_webui_fetch = os.getenv("CALIMERO_WEBUI_FETCH", "1")
            should_use_cached = webui_use_cached or env_webui_fetch == "0"

            if not should_use_cached:
                node_env["CALIMERO_WEBUI_FETCH"] = "1"
                if env_webui_fetch == "1" and not webui_use_cached:
                    console.print(
                        f"[cyan]Using default fresh WebUI fetch for node {node_name}[/cyan]"
                    )
                else:
                    console.print(
                        f"[cyan]Setting CALIMERO_WEBUI_FETCH=1 for node {node_name}[/cyan]"
                    )
            else:
                if webui_use_cached:
                    console.print(
                        f"[cyan]Using cached WebUI frontend for node {node_name} (--webui-use-cached flag)[/cyan]"
                    )
                else:
                    console.print(
                        f"[cyan]Environment variable CALIMERO_WEBUI_FETCH=0 detected, using cached WebUI for node {node_name}[/cyan]"
                    )

            container_config = {
                "name": container_name,
                "image": image_to_use,
                "detach": True,
                "user": "root",  # Override the default user in the image
                # Use specific capabilities instead of privileged mode for security
                # These capabilities are needed for file permission handling with volumes
                "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
                "environment": node_env,
                "ports": {
                    # Map external P2P port to internal P2P port
                    P2P_PORT_BINDING: port,
                    # Map external RPC port to internal admin server port
                    RPC_PORT_BINDING: rpc_port,
                },
                "volumes": {
                    os.path.abspath(data_dir): {"bind": "/app/data", "mode": "rw"}
                },
                "labels": {
                    "calimero.node": "true",
                    "node.name": node_name,
                    "chain.id": chain_id,
                },
            }

            # Near Devnet and E2E mode support
            if near_devnet_config or e2e_mode:
                # Add host gateway so container can reach services on the host machine
                if "extra_hosts" not in container_config:
                    container_config["extra_hosts"] = {}
                container_config["extra_hosts"]["host.docker.internal"] = "host-gateway"

            # Add auth service configuration if enabled
            if auth_service:
                console.print(
                    f"[cyan]Configuring {node_name} for auth service integration...[/cyan]"
                )

                # Configure CORS allowed origins with sensible localhost defaults
                hostname = _get_node_hostname(node_name)
                nip_io_origin = f"http://{hostname}.127.0.0.1.nip.io"
                if cors_allowed_origins is None:
                    cors_allowed_origins = DEFAULT_CORS_ORIGINS.copy()
                    cors_allowed_origins.append(nip_io_origin)

                # Validate origins to prevent CORS injection attacks
                cors_allowed_origins = _validate_cors_origins(cors_allowed_origins)
                cors_origins_str = ",".join(cors_allowed_origins)

                # Ensure auth service stack is running
                if not self._start_auth_service_stack(
                    auth_image, auth_use_cached, cors_allowed_origins
                ):
                    console.print(
                        "[yellow]⚠️  Warning: Auth service stack failed to start, but continuing with node setup[/yellow]"
                    )

                # Use per-node CORS middleware to avoid conflicts when multiple nodes run
                cors_middleware_name = f"cors-{node_name}"

                # Add Traefik labels for auth service integration
                auth_labels = {
                    "traefik.enable": "true",
                    # API routes (protected when auth is available)
                    f"traefik.http.routers.{node_name}-api.rule": f"Host(`{hostname}.127.0.0.1.nip.io`) && (PathPrefix(`/jsonrpc`) || PathPrefix(`/admin-api/`))",
                    f"traefik.http.routers.{node_name}-api.entrypoints": "web",
                    f"traefik.http.routers.{node_name}-api.service": f"{node_name}-core",
                    f"traefik.http.routers.{node_name}-api.middlewares": f"{cors_middleware_name},auth-{node_name}",
                    # WebSocket (protected when auth is available)
                    f"traefik.http.routers.{node_name}-ws.rule": f"Host(`{hostname}.127.0.0.1.nip.io`) && PathPrefix(`/ws`)",
                    f"traefik.http.routers.{node_name}-ws.entrypoints": "web",
                    f"traefik.http.routers.{node_name}-ws.service": f"{node_name}-core",
                    f"traefik.http.routers.{node_name}-ws.middlewares": f"{cors_middleware_name},auth-{node_name}",
                    # SSE (Server-Sent Events) routes (protected when auth is available)
                    f"traefik.http.routers.{node_name}-sse.rule": f"Host(`{hostname}.127.0.0.1.nip.io`) && PathPrefix(`/sse`)",
                    f"traefik.http.routers.{node_name}-sse.entrypoints": "web",
                    f"traefik.http.routers.{node_name}-sse.service": f"{node_name}-core",
                    f"traefik.http.routers.{node_name}-sse.middlewares": f"cors-sse-{node_name},auth-{node_name}",
                    # Admin dashboard (publicly accessible)
                    f"traefik.http.routers.{node_name}-dashboard.rule": f"Host(`{hostname}.127.0.0.1.nip.io`) && PathPrefix(`/admin-dashboard`)",
                    f"traefik.http.routers.{node_name}-dashboard.entrypoints": "web",
                    f"traefik.http.routers.{node_name}-dashboard.service": f"{node_name}-core",
                    f"traefik.http.routers.{node_name}-dashboard.middlewares": cors_middleware_name,
                    # Auth service route for this node's subdomain (both /auth/ and /admin/)
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.rule": f"Host(`{hostname}.127.0.0.1.nip.io`) && (PathPrefix(`/auth/`) || PathPrefix(`/admin/`))",
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.entrypoints": "web",
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.service": "auth-service",
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.middlewares": f"{cors_middleware_name},auth-headers",
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.priority": "200",
                    # Forward Auth middleware
                    f"traefik.http.middlewares.auth-{node_name}.forwardauth.address": "http://auth:3001/auth/validate",
                    f"traefik.http.middlewares.auth-{node_name}.forwardauth.trustForwardHeader": "true",
                    f"traefik.http.middlewares.auth-{node_name}.forwardauth.authResponseHeaders": "X-Auth-User,X-Auth-Permissions",
                    # Define the service
                    f"traefik.http.services.{node_name}-core.loadbalancer.server.port": str(
                        DEFAULT_RPC_PORT
                    ),
                    # Per-node CORS middleware (explicit headers required for credentials)
                    f"traefik.http.middlewares.{cors_middleware_name}.headers.accesscontrolallowmethods": "GET,OPTIONS,PUT,POST,DELETE",
                    f"traefik.http.middlewares.{cors_middleware_name}.headers.accesscontrolallowheaders": CORS_ALLOWED_HEADERS,
                    f"traefik.http.middlewares.{cors_middleware_name}.headers.accesscontrolalloworiginlist": cors_origins_str,
                    f"traefik.http.middlewares.{cors_middleware_name}.headers.accesscontrolmaxage": "100",
                    f"traefik.http.middlewares.{cors_middleware_name}.headers.addvaryheader": "true",
                    f"traefik.http.middlewares.{cors_middleware_name}.headers.accesscontrolexposeheaders": "X-Auth-Error",
                    f"traefik.http.middlewares.{cors_middleware_name}.headers.accesscontrolallowcredentials": "true",
                    # SSE-specific CORS middleware (per-node)
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolallowmethods": "GET,OPTIONS",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolallowheaders": "Cache-Control,Last-Event-ID,Accept,Accept-Language,Content-Language,Content-Type,Authorization",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolalloworiginlist": cors_origins_str,
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolmaxage": "86400",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.addvaryheader": "true",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolexposeheaders": "X-Auth-Error",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolallowcredentials": "true",
                }

                # Add auth labels to container config
                container_config["labels"].update(auth_labels)

                # Try to ensure the auth service networks exist and connect to them
                self._ensure_auth_networks()

            # Initialize the node (unless using custom config)
            if not skip_init:
                console.print(f"[yellow]Initializing node {node_name}...[/yellow]")

                # Create a temporary container for initialization
                init_config = container_config.copy()
                init_config["name"] = init_container_name
                if use_image_entrypoint:
                    # Preserve image's entrypoint
                    # Pass full merod command as CMD - entrypoint will handle it
                    init_config["command"] = [
                        "merod",
                        "--home",
                        "/app/data",
                        "--node",
                        node_name,
                        "init",
                        "--server-host",
                        "0.0.0.0",
                        "--server-port",
                        str(DEFAULT_RPC_PORT),
                        "--swarm-port",
                        str(DEFAULT_P2P_PORT),
                    ]
                    # Note: Don't set entrypoint - use image default
                else:
                    # Original behavior - bypass entrypoint for direct merod control
                    init_config["entrypoint"] = ""
                    init_config["command"] = [
                        "merod",
                        "--home",
                        "/app/data",
                        "--node",
                        node_name,
                        "init",
                        "--server-host",
                        "0.0.0.0",
                        "--server-port",
                        str(DEFAULT_RPC_PORT),
                        "--swarm-port",
                        str(DEFAULT_P2P_PORT),
                    ]
                init_config["detach"] = False

                try:
                    init_container = self.client.containers.run(**init_config)
                    console.print(
                        f"[green]✓ Node {node_name} initialized successfully[/green]"
                    )

                except Exception as e:
                    console.print(
                        f"[red]✗ Failed to initialize node {node_name}: {str(e)}[/red]"
                    )
                    return False
                finally:
                    # Clean up init container
                    try:
                        init_container.remove()
                    except Exception:
                        pass
            else:
                console.print(
                    f"[cyan]Skipping initialization for {node_name} (using custom config)[/cyan]"
                )

            config_file = os.path.join(node_data_dir, "config.toml")

            try:
                if near_devnet_config:
                    # Docker might creates files as root; we need to own them to modify config.toml
                    self._fix_permissions(node_data_dir)

                    console.print(
                        "[green]✓ Applying Near Devnet config for the node [/green]"
                    )
                    # Calculate the config path here, using the resolved data_dir/node_data_dir
                    actual_config_file = Path(node_data_dir) / "config.toml"

                    if not self._apply_near_devnet_config(
                        actual_config_file,
                        node_name,
                        near_devnet_config["rpc_url"],
                        near_devnet_config["contract_id"],
                        near_devnet_config["account_id"],
                        near_devnet_config["public_key"],
                        near_devnet_config["secret_key"],
                    ):
                        console.print("[red]✗ Failed to apply NEAR Devnet config[/red]")
                        return False

                # Apply e2e-style configuration for reliable testing (only if e2e_mode is enabled)
                if e2e_mode:
                    # Docker might create files as root; we need to own them to modify config.toml
                    # Only fix permissions if not already fixed (when near_devnet_config is provided)
                    if not near_devnet_config:
                        self._fix_permissions(node_data_dir)
                    apply_e2e_defaults(config_file, node_name, workflow_id)

                # Apply bootstrap nodes configuration (works regardless of e2e_mode)
                if bootstrap_nodes:
                    apply_bootstrap_nodes(config_file, node_name, bootstrap_nodes)

            except Exception:
                if e2e_mode:
                    console.print(
                        f"[cyan]Applying e2e defaults to {node_name} for test isolation...[/cyan]"
                    )
                    apply_e2e_defaults(config_file, node_name, workflow_id)

            # Now start the actual node
            console.print(f"[yellow]Starting node {node_name}...[/yellow]")
            run_config = container_config.copy()
            if use_image_entrypoint:
                # Preserve image's entrypoint
                # Pass full merod command as CMD - entrypoint will handle it
                run_config["command"] = [
                    "merod",
                    "--home",
                    "/app/data",
                    "--node",
                    node_name,
                    "run",
                ]
                # Note: Don't set entrypoint - use image default
            else:
                # Original behavior - bypass entrypoint for direct merod control
                run_config["entrypoint"] = ""
                run_config["command"] = [
                    "merod",
                    "--home",
                    "/app/data",
                    "--node",
                    node_name,
                    "run",
                ]

            # Set primary network for auth service
            if auth_service:
                run_config["network"] = "calimero_web"

            container = self.client.containers.run(**run_config)
            self.nodes[node_name] = container

            # Connect to auth service networks if enabled
            if auth_service:
                try:
                    # Connect to internal network for secure backend communication
                    internal_network = self.client.networks.get("calimero_internal")
                    internal_network.connect(container)
                    console.print(
                        f"[cyan]✓ {node_name} connected to internal network (secure backend)[/cyan]"
                    )
                    console.print(
                        f"[cyan]✓ {node_name} connected to web network (Traefik routing)[/cyan]"
                    )
                except Exception as e:
                    console.print(
                        f"[yellow]⚠️  Warning: Could not connect {node_name} to auth networks: {str(e)}[/yellow]"
                    )

            # Wait a moment and check if container is still running
            time.sleep(NODE_STARTUP_DELAY)
            container.reload()

            if container.status != "running":
                # Container failed to start, get logs
                logs = container.logs().decode("utf-8")
                container.remove()
                console.print(f"[red]✗ Node {node_name} failed to start[/red]")
                console.print("[yellow]Container logs:[/yellow]")
                console.print(logs)

                # Check for common issues
                if "GLIBC" in logs:
                    console.print("\n[red]GLIBC Compatibility Issue Detected[/red]")
                    console.print(
                        "[yellow]The Calimero binary requires newer GLIBC versions.[/yellow]"
                    )
                    console.print("[yellow]Try one of these solutions:[/yellow]")
                    console.print("  1. Use a different base image (--image option)")
                    console.print("  2. Build from source")
                    console.print("  3. Use a compatible Docker base image")

                return False

            console.print(
                f"[green]✓ Started Calimero node {node_name} (ID: {container.short_id})[/green]"
            )
            console.print(f"  - P2P Port: {port}")
            console.print(f"  - RPC/Admin Port: {rpc_port}")
            console.print(f"  - Chain ID: {chain_id}")
            console.print(f"  - Data Directory: {data_dir}")
            host_rpc_port = self._extract_host_port(container, RPC_PORT_BINDING)
            if host_rpc_port is None and rpc_port is not None:
                try:
                    host_rpc_port = int(rpc_port)
                except (TypeError, ValueError):
                    host_rpc_port = None
            if host_rpc_port is not None:
                self.node_rpc_ports[node_name] = host_rpc_port

            display_rpc_port = host_rpc_port if host_rpc_port is not None else rpc_port
            console.print(
                f"  - Non Auth Node URL: [link]http://localhost:{display_rpc_port}[/link]"
            )

            if auth_service:
                # Generate the hostname for nip.io URLs
                hostname = node_name.replace("calimero-", "").replace("-", "")
                console.print(
                    f"  - Auth Node URL: [link]http://{hostname}.127.0.0.1.nip.io[/link]"
                )
            return True

        except Exception as e:
            console.print(f"[red]✗ Failed to start node {node_name}: {str(e)}[/red]")
            return False

    def _find_available_ports(
        self, count: int, start_port: int = DEFAULT_P2P_PORT
    ) -> list[int]:
        """Find available ports starting from start_port."""
        import socket

        available_ports = []
        current_port = start_port

        while len(available_ports) < count:
            try:
                # Try to bind to the port to check if it's available
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("localhost", current_port))
                    available_ports.append(current_port)
            except OSError:
                # Port is in use, try next
                pass
            current_port += 1

            # Safety check to prevent infinite loop
            if current_port > start_port + 1000:
                raise RuntimeError(
                    f"Could not find {count} available ports starting from {start_port}"
                )

        return available_ports

    def _ensure_auth_networks(self):
        """Ensure the auth service networks exist for Traefik integration."""
        try:
            networks_to_create = [
                {"name": "calimero_web", "driver": "bridge"},
                {"name": "calimero_internal", "driver": "bridge", "internal": True},
            ]

            for network_spec in networks_to_create:
                network_name = network_spec["name"]
                try:
                    # Check if network already exists
                    self.client.networks.get(network_name)
                    console.print(
                        f"[cyan]✓ Network {network_name} already exists[/cyan]"
                    )
                except docker.errors.NotFound:
                    # Create the network
                    console.print(f"[yellow]Creating network: {network_name}[/yellow]")
                    network_config = {
                        "name": network_name,
                        "driver": network_spec["driver"],
                    }
                    if network_spec.get("internal"):
                        network_config["internal"] = True

                    self.client.networks.create(**network_config)
                    console.print(f"[green]✓ Created network: {network_name}[/green]")

        except Exception as e:
            console.print(
                f"[yellow]⚠️  Warning: Could not ensure auth networks: {str(e)}[/yellow]"
            )

    def _start_auth_service_stack(
        self,
        auth_image: str = None,
        auth_use_cached: bool = False,
        cors_allowed_origins: list[str] = None,
    ):
        """Start the Traefik proxy and auth service containers."""
        try:
            console.print(
                "[yellow]Starting auth service stack (Traefik + Auth)...[/yellow]"
            )

            # Check if auth service and traefik are already running
            auth_running = self._is_container_running("auth")
            traefik_running = self._is_container_running("proxy")

            if auth_running and traefik_running:
                console.print("[green]✓ Auth service stack is already running[/green]")
                return True

            # Ensure networks exist first
            self._ensure_auth_networks()

            # Start Traefik proxy first
            if not traefik_running:
                if not self._start_traefik_container():
                    return False

            # Start Auth service
            if not auth_running:
                if not self._start_auth_container(
                    auth_image, auth_use_cached, cors_allowed_origins
                ):
                    return False

            # Wait a bit for services to be ready
            console.print("[yellow]Waiting for services to be ready...[/yellow]")
            time.sleep(5)

            # Verify services are running
            if self._is_container_running("auth") and self._is_container_running(
                "proxy"
            ):
                console.print("[green]✓ Auth service stack is healthy[/green]")
                return True
            else:
                console.print(
                    "[yellow]⚠️  Auth service stack started but may not be fully ready[/yellow]"
                )
                return True

        except Exception as e:
            console.print(f"[red]✗ Error starting auth service stack: {str(e)}[/red]")
            return False

    def _start_traefik_container(self):
        """Start the Traefik proxy container."""
        try:
            console.print("[yellow]Starting Traefik proxy...[/yellow]")

            # Remove existing container if it exists
            try:
                existing = self.client.containers.get("proxy")
                existing.remove(force=True)
            except docker.errors.NotFound:
                pass

            # Pull Traefik image
            if not self._ensure_image_pulled("traefik:v2.10"):
                return False

            # Create and start Traefik container
            traefik_config = {
                "name": "proxy",
                "image": "traefik:v2.10",
                "detach": True,
                "command": [
                    "--api.insecure=true",
                    "--providers.docker=true",
                    "--entrypoints.web.address=:80",
                    "--accesslog=true",
                    "--log.level=DEBUG",
                    "--providers.docker.exposedByDefault=false",
                    "--providers.docker.network=calimero_web",
                    "--serversTransport.forwardingTimeouts.dialTimeout=30s",
                    "--serversTransport.forwardingTimeouts.responseHeaderTimeout=30s",
                    "--serversTransport.forwardingTimeouts.idleConnTimeout=30s",
                ],
                "ports": {"80/tcp": 80, "8080/tcp": 8080},
                "volumes": {
                    "/var/run/docker.sock": {
                        "bind": "/var/run/docker.sock",
                        "mode": "ro",
                    }
                },
                "network": "calimero_web",
                "restart_policy": {"Name": "unless-stopped"},
                "labels": {
                    "traefik.enable": "true",
                    "traefik.http.routers.proxy-dashboard.rule": "Host(`proxy.127.0.0.1.nip.io`)",
                    "traefik.http.routers.proxy-dashboard.entrypoints": "web",
                    "traefik.http.routers.proxy-dashboard.service": "api@internal",
                },
            }

            self.client.containers.run(**traefik_config)
            console.print("[green]✓ Traefik proxy started[/green]")
            return True

        except Exception as e:
            console.print(f"[red]✗ Failed to start Traefik proxy: {str(e)}[/red]")
            return False

    def _start_auth_container(
        self,
        auth_image: str = None,
        auth_use_cached: bool = False,
        cors_allowed_origins: list[str] = None,
    ):
        """Start the Auth service container."""
        try:
            console.print("[yellow]Starting Auth service...[/yellow]")

            # Remove existing container if it exists
            try:
                existing = self.client.containers.get("auth")
                existing.remove(force=True)
            except docker.errors.NotFound:
                pass

            # Pull Auth service image
            auth_image_to_use = auth_image or "ghcr.io/calimero-network/mero-auth:edge"

            # Ensure auth image is available
            if not self._ensure_image_pulled(auth_image_to_use):
                console.print(
                    "[yellow]⚠️  Warning: Could not pull auth image, trying with local image[/yellow]"
                )

            # Create volume for auth data if it doesn't exist
            try:
                self.client.volumes.get("calimero_auth_data")
            except docker.errors.NotFound:
                self.client.volumes.create("calimero_auth_data")

            # Create and start Auth service container
            # Prepare environment variables for auth service
            auth_env = ["RUST_LOG=debug"]

            # By default, fetch fresh auth frontend unless explicitly disabled
            env_auth_fetch = os.getenv("CALIMERO_AUTH_FRONTEND_FETCH", "1")
            should_use_cached = auth_use_cached or env_auth_fetch == "0"

            if not should_use_cached:
                auth_env.append("CALIMERO_AUTH_FRONTEND_FETCH=1")
                if env_auth_fetch == "1" and not auth_use_cached:
                    console.print(
                        "[cyan]Using default fresh auth frontend fetch for auth service[/cyan]"
                    )
                else:
                    console.print(
                        "[cyan]Setting CALIMERO_AUTH_FRONTEND_FETCH=1 for auth service[/cyan]"
                    )
            else:
                if auth_use_cached:
                    console.print(
                        "[cyan]Using cached auth frontend (--auth-use-cached flag)[/cyan]"
                    )
                else:
                    console.print(
                        "[cyan]Environment variable CALIMERO_AUTH_FRONTEND_FETCH=0 detected, using cached auth frontend[/cyan]"
                    )

            # Configure CORS allowed origins with sensible localhost defaults
            if cors_allowed_origins is None:
                cors_allowed_origins = DEFAULT_CORS_ORIGINS.copy()

            # Validate origins to prevent CORS injection attacks
            cors_allowed_origins = _validate_cors_origins(cors_allowed_origins)
            cors_origins_str = ",".join(cors_allowed_origins)

            auth_config = {
                "name": "auth",
                "image": auth_image_to_use,
                "detach": True,
                "user": "root",
                "volumes": {"calimero_auth_data": {"bind": "/data", "mode": "rw"}},
                "environment": auth_env,
                "network": "calimero_web",  # Connect to web network first
                "restart_policy": {"Name": "unless-stopped"},
                "labels": {
                    "traefik.enable": "true",
                    # Auth service on localhost (both /auth/ and /admin/)
                    "traefik.http.routers.auth-public.rule": "Host(`localhost`) && (PathPrefix(`/auth/`) || PathPrefix(`/admin/`))",
                    "traefik.http.routers.auth-public.entrypoints": "web",
                    "traefik.http.routers.auth-public.service": "auth-service",
                    "traefik.http.routers.auth-public.middlewares": "cors-auth,auth-headers",
                    "traefik.http.routers.auth-public.priority": "100",
                    # Add Node ID header for auth service
                    "traefik.http.middlewares.auth-headers.headers.customrequestheaders.X-Node-ID": "auth",
                    # Define the service
                    "traefik.http.services.auth-service.loadbalancer.server.port": "3001",
                    # CORS middleware for auth service (explicit headers required for credentials)
                    "traefik.http.middlewares.cors-auth.headers.accesscontrolallowmethods": "GET,OPTIONS,PUT,POST,DELETE",
                    "traefik.http.middlewares.cors-auth.headers.accesscontrolallowheaders": CORS_ALLOWED_HEADERS,
                    "traefik.http.middlewares.cors-auth.headers.accesscontrolalloworiginlist": cors_origins_str,
                    "traefik.http.middlewares.cors-auth.headers.accesscontrolmaxage": "100",
                    "traefik.http.middlewares.cors-auth.headers.addvaryheader": "true",
                    "traefik.http.middlewares.cors-auth.headers.accesscontrolexposeheaders": "X-Auth-Error",
                    "traefik.http.middlewares.cors-auth.headers.accesscontrolallowcredentials": "true",
                },
            }

            container = self.client.containers.run(**auth_config)

            # Connect to the internal network as well
            try:
                internal_network = self.client.networks.get("calimero_internal")
                internal_network.connect(container)
                console.print(
                    "[cyan]✓ Auth service connected to internal network[/cyan]"
                )
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Warning: Could not connect auth to internal network: {str(e)}[/yellow]"
                )
            console.print("[green]✓ Auth service started[/green]")
            return True

        except Exception as e:
            console.print(f"[red]✗ Failed to start Auth service: {str(e)}[/red]")
            return False

    def _is_container_running(self, container_name: str) -> bool:
        """Check if a container is running."""
        try:
            container = self.client.containers.get(container_name)
            return container.status == "running"
        except docker.errors.NotFound:
            return False
        except Exception:
            return False

    def stop_auth_service_stack(self):
        """Stop the Traefik proxy and auth service containers."""
        try:
            console.print("[yellow]Stopping auth service stack...[/yellow]")

            success = True
            # Stop auth service
            try:
                auth_container = self.client.containers.get("auth")
                auth_container.stop()
                auth_container.remove()
                console.print("[green]✓ Auth service stopped[/green]")
            except docker.errors.NotFound:
                console.print("[cyan]• Auth service was not running[/cyan]")
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Warning: Could not stop auth service: {str(e)}[/yellow]"
                )
                success = False

            # Stop Traefik proxy
            try:
                proxy_container = self.client.containers.get("proxy")
                proxy_container.stop()
                proxy_container.remove()
                console.print("[green]✓ Traefik proxy stopped[/green]")
            except docker.errors.NotFound:
                console.print("[cyan]• Traefik proxy was not running[/cyan]")
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Warning: Could not stop Traefik proxy: {str(e)}[/yellow]"
                )
                success = False

            if success:
                console.print(
                    "[green]✓ Auth service stack stopped successfully[/green]"
                )

            return success

        except Exception as e:
            console.print(f"[red]✗ Error stopping auth service stack: {str(e)}[/red]")
            return False

    def run_multiple_nodes(
        self,
        count: int,
        base_port: int = None,
        base_rpc_port: int = None,
        chain_id: str = "testnet-1",
        prefix: str = "calimero-node",
        image: str = None,
        auth_service: bool = False,
        auth_image: str = None,
        auth_use_cached: bool = False,
        webui_use_cached: bool = False,
        log_level: str = "debug",
        rust_backtrace: str = "0",
        workflow_id: str = None,  # for test isolation
        e2e_mode: bool = False,  # enable e2e-style defaults
        near_devnet_config: dict = None,
        bootstrap_nodes: list[str] = None,  # bootstrap nodes to connect to
        use_image_entrypoint: bool = False,  # preserve Docker image's entrypoint
        cors_allowed_origins: list[str] = None,  # explicit CORS origin allowlist
    ) -> bool:
        """Run multiple Calimero nodes with automatic port allocation."""
        console.print(f"[bold]Starting {count} Calimero nodes...[/bold]")

        # Generate a single shared workflow_id for all nodes if none provided
        if workflow_id is None:
            workflow_id = str(uuid.uuid4())[:8]
            console.print(f"[cyan]Generated shared workflow_id: {workflow_id}[/cyan]")

        # Find available ports automatically if not specified
        if base_port is None:
            p2p_ports = self._find_available_ports(count, DEFAULT_P2P_PORT)
        else:
            p2p_ports = [base_port + i for i in range(count)]

        if base_rpc_port is None:
            # Use a different range for RPC ports to avoid conflicts
            rpc_ports = self._find_available_ports(count, DEFAULT_RPC_PORT)
        else:
            rpc_ports = [base_rpc_port + i for i in range(count)]

        success_count = 0
        for i in range(count):
            node_name = f"{prefix}-{i + 1}"
            port = p2p_ports[i]
            rpc_port = rpc_ports[i]

            # Resolve specific config for this node if a map is provided
            node_specific_near_config = None
            if near_devnet_config:
                if node_name in near_devnet_config:
                    node_specific_near_config = near_devnet_config[node_name]

            if self.run_node(
                node_name,
                port,
                rpc_port,
                chain_id,
                image=image,
                auth_service=auth_service,
                auth_image=auth_image,
                auth_use_cached=auth_use_cached,
                webui_use_cached=webui_use_cached,
                log_level=log_level,
                rust_backtrace=rust_backtrace,
                workflow_id=workflow_id,
                e2e_mode=e2e_mode,
                near_devnet_config=node_specific_near_config,
                bootstrap_nodes=bootstrap_nodes,
                use_image_entrypoint=use_image_entrypoint,
                cors_allowed_origins=cors_allowed_origins,
            ):
                success_count += 1
            else:
                console.print(
                    f"[red]Failed to start node {node_name}, stopping deployment[/red]"
                )
                break

        console.print(
            f"\n[bold]Deployment Summary: {success_count}/{count} nodes started successfully[/bold]"
        )
        return success_count == count

    def _graceful_stop_container(
        self,
        container,
        container_name: str,
        drain_timeout: int = 5,
        stop_timeout: int = CONTAINER_STOP_TIMEOUT,
    ) -> bool:
        """Gracefully stop a single container with connection draining.

        Delegates to _graceful_stop_containers_batch with a single-element list
        to avoid duplicating the three-phase shutdown logic.

        Args:
            container: Docker container object
            container_name: Name of the container (for logging)
            drain_timeout: Seconds to wait for connection draining (default 5s)
            stop_timeout: Seconds to wait for container stop (default CONTAINER_STOP_TIMEOUT)

        Returns:
            True if container was stopped successfully, False otherwise
        """
        success_count, failed = self._graceful_stop_containers_batch(
            [(container_name, container)], drain_timeout, stop_timeout
        )
        return success_count == 1 and not failed

    def _graceful_stop_containers_batch(
        self,
        containers: list[tuple[str, any]],
        drain_timeout: int = 5,
        stop_timeout: int = CONTAINER_STOP_TIMEOUT,
    ) -> tuple[int, list[str]]:
        """Gracefully stop multiple containers with O(timeout) complexity.

        This method implements batch graceful shutdown:
        1. Send SIGTERM to ALL containers first (parallel signal phase)
        2. Wait ONCE for drain_timeout (shared drain period)
        3. Stop and remove all containers

        This is more efficient than sequential graceful stops which would
        take O(n * drain_timeout) time.

        Args:
            containers: List of (container_name, container) tuples to stop
            drain_timeout: Seconds to wait for connection draining (default 5s)
            stop_timeout: Seconds to wait for each container stop (default CONTAINER_STOP_TIMEOUT)

        Returns:
            Tuple of (success_count, failed_names)
        """
        if not containers:
            return 0, []

        # Phase 1: Send SIGTERM to all containers (parallel signal)
        console.print(
            f"[cyan]Initiating graceful shutdown for {len(containers)} containers...[/cyan]"
        )
        for _container_name, container in containers:
            try:
                container.kill(signal="SIGTERM")
            except docker.errors.APIError:
                # Container may have already stopped or doesn't support signals
                pass

        # Phase 2: Single shared drain period for all containers
        if drain_timeout > 0:
            console.print(
                f"[cyan]Waiting {drain_timeout}s for connection draining...[/cyan]"
            )
            time.sleep(drain_timeout)

        # Phase 3: Stop and remove all containers
        success_count = 0
        failed_names = []

        for container_name, container in containers:
            try:
                container.stop(timeout=stop_timeout)
                container.remove()
                console.print(
                    f"[green]✓ Gracefully stopped and removed {container_name}[/green]"
                )
                success_count += 1
            except Exception as e:
                console.print(f"[red]✗ Failed to stop {container_name}: {str(e)}[/red]")
                failed_names.append(container_name)

        return success_count, failed_names

    def stop_node(
        self,
        node_name: str,
        drain_timeout: int = 5,
        stop_timeout: int = CONTAINER_STOP_TIMEOUT,
    ) -> bool:
        """Stop a Calimero node container with graceful shutdown.

        Implements graceful shutdown with connection draining:
        1. Sends SIGTERM to disable health checks and stop accepting new connections
        2. Waits for drain_timeout to allow in-flight requests to complete
        3. Stops the container with stop_timeout for final cleanup

        Args:
            node_name: Name of the node to stop
            drain_timeout: Seconds to wait for connection draining (default 5s)
            stop_timeout: Seconds to wait for container stop (default CONTAINER_STOP_TIMEOUT)

        Returns:
            True if node was stopped successfully, False otherwise
        """
        try:
            if node_name in self.nodes:
                container = self.nodes[node_name]
                success = self._graceful_stop_container(
                    container, node_name, drain_timeout, stop_timeout
                )
                if success:
                    del self.nodes[node_name]
                    self.node_rpc_ports.pop(node_name, None)
                return success
            else:
                # Try to find container by name
                try:
                    container = self.client.containers.get(node_name)
                    success = self._graceful_stop_container(
                        container, node_name, drain_timeout, stop_timeout
                    )
                    if success:
                        self.node_rpc_ports.pop(node_name, None)
                    return success
                except docker.errors.NotFound:
                    console.print(f"[yellow]Node {node_name} not found[/yellow]")
                    return False
        except Exception as e:
            console.print(f"[red]✗ Failed to stop node {node_name}: {str(e)}[/red]")
            return False

    def stop_all_nodes(
        self,
        drain_timeout: int = 5,
        stop_timeout: int = CONTAINER_STOP_TIMEOUT,
    ) -> bool:
        """Stop all running Calimero nodes with graceful shutdown.

        Uses batch graceful shutdown for O(timeout) complexity instead of
        O(n * timeout) that sequential stops would require.

        Args:
            drain_timeout: Seconds to wait for connection draining (default 5s)
            stop_timeout: Seconds to wait for each container stop (default CONTAINER_STOP_TIMEOUT)

        Returns:
            True if all nodes were stopped successfully, False otherwise
        """
        try:
            containers = self.client.containers.list(
                filters={"label": "calimero.node=true"}
            )

            if not containers:
                console.print(
                    "[yellow]No Calimero nodes are currently running[/yellow]"
                )
                return True

            console.print(
                f"[bold]Stopping {len(containers)} Calimero nodes with graceful shutdown...[/bold]"
            )

            # Build list of (name, container) tuples for batch shutdown
            containers_to_stop = [(c.name, c) for c in containers]

            success_count, failed_nodes = self._graceful_stop_containers_batch(
                containers_to_stop, drain_timeout, stop_timeout
            )

            # Clean up internal tracking for successfully stopped containers
            for container_name, _ in containers_to_stop:
                if container_name not in failed_nodes:
                    self.node_rpc_ports.pop(container_name, None)
                    if container_name in self.nodes:
                        del self.nodes[container_name]

            console.print(
                f"\n[bold]Stop Summary: {success_count}/{len(containers)} nodes stopped successfully[/bold]"
            )

            if failed_nodes:
                console.print(f"[red]Failed to stop: {', '.join(failed_nodes)}[/red]")
                return False

            return True

        except Exception as e:
            console.print(f"[red]Failed to stop all nodes: {str(e)}[/red]")
            return False

    def get_running_nodes(self) -> list[str]:
        """Return a list of names for running Calimero node containers."""
        try:
            containers = self.client.containers.list(
                filters={"label": "calimero.node=true", "status": "running"}
            )
            return [c.name for c in containers]
        except Exception:
            return []

    def list_nodes(self) -> None:
        """List all running Calimero nodes and infrastructure."""
        try:
            # Get Calimero nodes
            node_containers = self.client.containers.list(
                filters={"label": "calimero.node=true"}
            )

            # Get auth service and proxy containers
            auth_containers = []
            try:
                auth_container = self.client.containers.get("auth")
                auth_containers.append(auth_container)
            except docker.errors.NotFound:
                pass

            try:
                proxy_container = self.client.containers.get("proxy")
                auth_containers.append(proxy_container)
            except docker.errors.NotFound:
                pass

            # Check if anything is running
            if not node_containers and not auth_containers:
                console.print(
                    "[yellow]No Calimero nodes or services are currently running[/yellow]"
                )
                return

            # Display nodes table if nodes exist
            if node_containers:
                table = Table(title="Running Calimero Nodes")
                table.add_column("Name", style="cyan")
                table.add_column("Status", style="green")
                table.add_column("Image", style="blue")
                table.add_column("P2P Port", style="yellow")
                table.add_column("RPC/Admin Port", style="yellow")
                table.add_column("Chain ID", style="magenta")
                table.add_column("Created", style="white")

                for container in node_containers:
                    # Extract ports from container attributes
                    p2p_port = "N/A"
                    rpc_port = "N/A"

                    # Get port mappings from container attributes
                    if container.attrs.get("NetworkSettings", {}).get("Ports"):
                        port_mappings = container.attrs["NetworkSettings"]["Ports"]
                        port_list = []

                        for _container_port, host_bindings in port_mappings.items():
                            if host_bindings:
                                for binding in host_bindings:
                                    if "HostPort" in binding:
                                        port_list.append(int(binding["HostPort"]))

                        # Remove duplicates and sort ports
                        port_list = sorted(set(port_list))

                        # Assign P2P and RPC ports
                        if len(port_list) >= 2:
                            p2p_port = str(port_list[0])
                            rpc_port = str(port_list[1])
                        elif len(port_list) == 1:
                            p2p_port = str(port_list[0])

                    # Extract chain ID from labels
                    chain_id = container.labels.get("chain.id", "N/A")

                    table.add_row(
                        container.name,
                        container.status,
                        (
                            container.image.tags[0]
                            if container.image.tags
                            else container.image.id[:12]
                        ),
                        p2p_port,
                        rpc_port,
                        chain_id,
                        container.attrs["Created"][:19].replace("T", " "),
                    )

                console.print(table)

            # Display auth services table if auth containers exist
            if auth_containers:
                auth_table = Table(title="Running Auth Infrastructure")
                auth_table.add_column("Service", style="cyan")
                auth_table.add_column("Status", style="green")
                auth_table.add_column("Image", style="blue")
                auth_table.add_column("Ports", style="yellow")
                auth_table.add_column("Networks", style="magenta")
                auth_table.add_column("Created", style="white")

                for container in auth_containers:
                    # Extract port mappings
                    ports = []
                    if container.attrs.get("NetworkSettings", {}).get("Ports"):
                        port_mappings = container.attrs["NetworkSettings"]["Ports"]
                        for container_port, host_bindings in port_mappings.items():
                            if host_bindings:
                                for binding in host_bindings:
                                    if "HostPort" in binding:
                                        ports.append(
                                            f"{binding['HostPort']}:{container_port}"
                                        )
                            else:
                                ports.append(container_port)

                    ports_str = ", ".join(ports) if ports else "N/A"

                    # Extract networks
                    networks = []
                    if container.attrs.get("NetworkSettings", {}).get("Networks"):
                        networks = list(
                            container.attrs["NetworkSettings"]["Networks"].keys()
                        )

                    networks_str = ", ".join(networks) if networks else "N/A"

                    # Service type based on container name
                    service_type = (
                        "Auth Service" if container.name == "auth" else "Traefik Proxy"
                    )

                    auth_table.add_row(
                        service_type,
                        container.status,
                        (
                            container.image.tags[0]
                            if container.image.tags
                            else container.image.id[:12]
                        ),
                        ports_str,
                        networks_str,
                        container.attrs["Created"][:19].replace("T", " "),
                    )

                if node_containers:
                    console.print()  # Add spacing between tables
                console.print(auth_table)

            # Show auth volume information
            try:
                auth_volume = self.client.volumes.get("calimero_auth_data")
                console.print(
                    f"\n[cyan]Auth Data Volume:[/cyan] calimero_auth_data (created: {auth_volume.attrs.get('CreatedAt', 'N/A')[:19]})"
                )
            except docker.errors.NotFound:
                pass

        except Exception as e:
            console.print(f"[red]Failed to list infrastructure: {str(e)}[/red]")

    def get_node_logs(self, node_name: str, tail: int = 100) -> None:
        """Get logs from a specific node."""
        try:
            if node_name in self.nodes:
                container = self.nodes[node_name]
            else:
                container = self.client.containers.get(node_name)

            logs = container.logs(tail=tail, timestamps=True).decode("utf-8")
            console.print(f"\n[bold]Logs for {node_name}:[/bold]")
            console.print(logs)

        except Exception as e:
            console.print(f"[red]Failed to get logs for {node_name}: {str(e)}[/red]")

    def verify_admin_binding(self, node_name: str) -> bool:
        """Verify that the admin server is properly bound to localhost."""
        try:
            if node_name in self.nodes:
                container = self.nodes[node_name]
            else:
                container = self.client.containers.get(node_name)

            # Check if admin server is listening on localhost
            result = container.exec_run(
                f"sh -c 'timeout 3 bash -c \"</dev/tcp/127.0.0.1/{DEFAULT_RPC_PORT}\"' 2>&1 || echo 'Connection failed'"
            )

            if "Connection failed" in result.output.decode():
                console.print(
                    f"[red]✗ Admin server not accessible on localhost:{DEFAULT_RPC_PORT} for {node_name}[/red]"
                )
                return False
            else:
                console.print(
                    f"[green]✓ Admin server accessible on localhost:{DEFAULT_RPC_PORT} for {node_name}[/green]"
                )
                return True

        except Exception as e:
            console.print(
                f"[red]Failed to verify admin binding for {node_name}: {str(e)}[/red]"
            )
            return False

    def _apply_near_devnet_config(
        self,
        config_file: Path,
        node_name: str,
        rpc_url: str,
        contract_id: str,
        account_id: str,
        pub_key: str,
        secret_key: str,
    ):
        """Wrapper for shared config utility."""

        return apply_near_devnet_config_to_file(
            config_file,
            node_name,
            rpc_url,
            contract_id,
            account_id,
            pub_key,
            secret_key,
        )

    def _fix_permissions(self, path: str):
        """Fix ownership and write permissions of files created by Docker."""
        if not hasattr(os, "getuid"):
            return

        try:
            uid = os.getuid()
            gid = os.getgid()

            # Use Alpine to chown AND chmod the directory
            # We add 'chmod -R u+w' to ensure we can write to the files even if they were created read-only
            self.client.containers.run(
                "alpine:latest",
                command=f"sh -c 'chown -R {uid}:{gid} /data && chmod -R u+w /data'",
                volumes={os.path.abspath(path): {"bind": "/data", "mode": "rw"}},
                remove=True,
            )
        except Exception as e:
            console.print(
                f"[yellow]⚠️  Warning: Failed to fix permissions for {path}: {e}[/yellow]"
            )
