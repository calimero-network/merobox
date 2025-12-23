"""
Calimero Manager - Core functionality for managing Calimero nodes in Docker containers.
"""

import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import docker
import toml
from rich.console import Console
from rich.table import Table

from merobox.commands.config_utils import apply_near_devnet_config_to_file
from merobox.commands.constants import (
    ANVIL_DEFAULT_PORT,
    DFX_DEFAULT_PORT,
    ETHEREUM_LOCAL_ACCOUNT_ID,
    ETHEREUM_LOCAL_CONTRACT_ID,
    ETHEREUM_LOCAL_SECRET_KEY,
    ICP_LOCAL_CONTRACT_ID,
    NETWORK_LOCAL,
)

console = Console()

MOCK_RELAYER_IMAGE = "ghcr.io/calimero-network/mero-relayer:8ee178e"
MOCK_RELAYER_PORT = 63529
MOCK_RELAYER_NAME = "mock-relayer"


class DockerManager:
    """Manages Calimero nodes in Docker containers."""

    def __init__(self):
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
        self.mock_relayer_url: Optional[str] = None

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
        except Exception:
            return None

        return None

    def _ensure_mock_relayer(self) -> Optional[str]:
        """Ensure a mock relayer container is running and return its host URL."""
        # Validate cached URL by checking if container is still running
        if self.mock_relayer_url:
            try:
                existing = self.client.containers.get(MOCK_RELAYER_NAME)
                existing.reload()
                if existing.status == "running":
                    return self.mock_relayer_url
                # Container stopped - clear cached URL and continue to restart
                console.print(
                    "[yellow]Mock relayer container stopped, restarting...[/yellow]"
                )
                self.mock_relayer_url = None
            except docker.errors.NotFound:
                # Container removed - clear cached URL and continue to restart
                console.print(
                    "[yellow]Mock relayer container not found, starting new one...[/yellow]"
                )
                self.mock_relayer_url = None
            except Exception as e:
                # Unexpected error - clear cached URL and continue
                console.print(
                    f"[yellow]Error checking mock relayer status: {e}, will attempt restart...[/yellow]"
                )
                self.mock_relayer_url = None

        try:
            existing = self.client.containers.get(MOCK_RELAYER_NAME)
            existing.reload()
            if existing.status == "running":
                host_port = self._extract_host_port(
                    existing, f"{MOCK_RELAYER_PORT}/tcp"
                )
                if host_port is None:
                    console.print(
                        "[red]✗ Mock relayer is running but could not determine host port[/red]"
                    )
                    return None
                self.mock_relayer_url = f"http://host.docker.internal:{host_port}"
                console.print(
                    f"[cyan]✓ Mock relayer already running at {self.mock_relayer_url}[/cyan]"
                )
                return self.mock_relayer_url

            console.print(
                f"[yellow]Found stopped mock relayer container '{MOCK_RELAYER_NAME}', removing...[/yellow]"
            )
            try:
                existing.remove(force=True)
            except Exception as remove_err:
                console.print(
                    f"[red]✗ Failed to clean up existing mock relayer: {remove_err}[/red]"
                )
                return None
        except docker.errors.NotFound:
            pass
        except Exception as e:
            console.print(f"[red]✗ Error inspecting mock relayer: {e}[/red]")
            return None

        # Pull image if needed
        if not self._ensure_image_pulled(MOCK_RELAYER_IMAGE):
            return None

        # Try preferred host port first, fall back to random if it's taken
        port_binding: Optional[int] = MOCK_RELAYER_PORT
        for attempt in range(2):
            try:
                container = self.client.containers.run(
                    name=MOCK_RELAYER_NAME,
                    image=MOCK_RELAYER_IMAGE,
                    detach=True,
                    ports={f"{MOCK_RELAYER_PORT}/tcp": port_binding},
                    command=["--enable-mock-relayer"],
                    environment={
                        "ENABLE_NEAR": "false",
                        "ENABLE_STARKNET": "false",
                        "ENABLE_ICP": "false",
                        "ENABLE_ETHEREUM": "false",
                    },
                    labels={"calimero.mock_relayer": "true"},
                )
                container.reload()
                host_port = self._extract_host_port(
                    container, f"{MOCK_RELAYER_PORT}/tcp"
                )
                if host_port is None:
                    if port_binding is not None:
                        # Fallback to requested port only if we explicitly requested it
                        host_port = port_binding
                    else:
                        # Random port was requested but we couldn't determine it
                        console.print(
                            "[red]✗ Failed to determine mock relayer host port[/red]"
                        )
                        container.remove(force=True)
                        return None
                self.mock_relayer_url = f"http://host.docker.internal:{host_port}"
                console.print(
                    f"[green]✓ Mock relayer started ({container.short_id}) at {self.mock_relayer_url}[/green]"
                )
                return self.mock_relayer_url
            except docker.errors.APIError as e:
                if attempt == 0 and "port is already allocated" in str(e).lower():
                    console.print(
                        f"[yellow]Port {MOCK_RELAYER_PORT} is in use, starting mock relayer on a random host port...[/yellow]"
                    )
                    port_binding = None
                    continue
                console.print(
                    f"[red]✗ Failed to start mock relayer container: {str(e)}[/red]"
                )
                return None
            except Exception as e:
                console.print(
                    f"[red]✗ Unexpected error starting mock relayer: {str(e)}[/red]"
                )
                return None

        # Loop exhausted without success (should not normally reach here)
        return None

    def get_node_rpc_port(self, node_name: str) -> Optional[int]:
        """Return the published RPC port for the given node, if available."""
        if node_name in self.node_rpc_ports:
            return self.node_rpc_ports[node_name]

        try:
            container = self.client.containers.get(node_name)
            container.reload()
            host_port = self._extract_host_port(container, "2528/tcp")
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
        port: int = 2428,
        rpc_port: int = 2528,
        chain_id: str = "testnet-1",
        data_dir: str = None,
        image: str = None,
        auth_service: bool = False,
        auth_image: str = None,
        auth_use_cached: bool = False,
        webui_use_cached: bool = False,
        log_level: str = "debug",
        rust_backtrace: str = "0",
        mock_relayer: bool = False,
        workflow_id: str = None,  # for test isolation
        e2e_mode: bool = False,  # enable e2e-style defaults
        config_path: str = None,  # custom config.toml path
        near_devnet_config: dict = None,
        bootstrap_nodes: list[str] = None,  # bootstrap nodes to connect to
        use_image_entrypoint: bool = False,  # preserve Docker image's entrypoint
    ) -> bool:
        """Run a Calimero node container."""
        try:
            # Determine the image to use
            image_to_use = image or "ghcr.io/calimero-network/merod:edge"

            # Ensure the image is available
            if not self._ensure_image_pulled(image_to_use):
                console.print(
                    f"[red]✗ Cannot proceed without image: {image_to_use}[/red]"
                )
                return False

            relayer_url = None
            if mock_relayer:
                relayer_url = self._ensure_mock_relayer()
                if not relayer_url:
                    console.print(
                        "[red]✗ Mock relayer requested but failed to start[/red]"
                    )
                    return False
                console.print(
                    f"[cyan]Using mock relayer for node {node_name}: {relayer_url}[/cyan]"
                )

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

            # Set permissions to be world-writable since container runs as root
            os.chmod(data_dir, 0o777)
            os.chmod(node_data_dir, 0o777)

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
                "privileged": True,  # Run in privileged mode to avoid permission issues
                "environment": node_env,
                "ports": {
                    # Map external P2P port to internal P2P port (0.0.0.0:2428)
                    "2428/tcp": port,
                    # Map external RPC port to internal admin server port (127.0.0.1:2528)
                    "2528/tcp": rpc_port,
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

            # Near Devnet, Mock relayer, and E2E mode support
            if near_devnet_config or mock_relayer or e2e_mode:
                # Add host gateway so container can reach services on the host machine
                if "extra_hosts" not in container_config:
                    container_config["extra_hosts"] = {}
                container_config["extra_hosts"]["host.docker.internal"] = "host-gateway"

            # Add auth service configuration if enabled
            if auth_service:
                console.print(
                    f"[cyan]Configuring {node_name} for auth service integration...[/cyan]"
                )

                # Ensure auth service stack is running
                if not self._start_auth_service_stack(auth_image, auth_use_cached):
                    console.print(
                        "[yellow]⚠️  Warning: Auth service stack failed to start, but continuing with node setup[/yellow]"
                    )

                # Add Traefik labels for auth service integration
                auth_labels = {
                    "traefik.enable": "true",
                    # API routes (protected when auth is available)
                    f"traefik.http.routers.{node_name}-api.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && (PathPrefix(`/jsonrpc`) || PathPrefix(`/admin-api/`))",
                    f"traefik.http.routers.{node_name}-api.entrypoints": "web",
                    f"traefik.http.routers.{node_name}-api.service": f"{node_name}-core",
                    f"traefik.http.routers.{node_name}-api.middlewares": f"cors,auth-{node_name}",
                    # WebSocket (protected when auth is available)
                    f"traefik.http.routers.{node_name}-ws.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && PathPrefix(`/ws`)",
                    f"traefik.http.routers.{node_name}-ws.entrypoints": "web",
                    f"traefik.http.routers.{node_name}-ws.service": f"{node_name}-core",
                    f"traefik.http.routers.{node_name}-ws.middlewares": f"cors,auth-{node_name}",
                    # SSE (Server-Sent Events) routes (protected when auth is available)
                    f"traefik.http.routers.{node_name}-sse.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && PathPrefix(`/sse`)",
                    f"traefik.http.routers.{node_name}-sse.entrypoints": "web",
                    f"traefik.http.routers.{node_name}-sse.service": f"{node_name}-core",
                    f"traefik.http.routers.{node_name}-sse.middlewares": f"cors-sse-{node_name},auth-{node_name}",
                    # Admin dashboard (publicly accessible)
                    f"traefik.http.routers.{node_name}-dashboard.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && PathPrefix(`/admin-dashboard`)",
                    f"traefik.http.routers.{node_name}-dashboard.entrypoints": "web",
                    f"traefik.http.routers.{node_name}-dashboard.service": f"{node_name}-core",
                    f"traefik.http.routers.{node_name}-dashboard.middlewares": "cors",
                    # Auth service route for this node's subdomain (both /auth/ and /admin/)
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.rule": f"Host(`{node_name.replace('calimero-', '').replace('-', '')}.127.0.0.1.nip.io`) && (PathPrefix(`/auth/`) || PathPrefix(`/admin/`))",
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.entrypoints": "web",
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.service": "auth-service",
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.middlewares": "cors,auth-headers",
                    f"traefik.http.routers.{node_name.replace('calimero-', '')}-auth.priority": "200",
                    # Forward Auth middleware
                    f"traefik.http.middlewares.auth-{node_name}.forwardauth.address": "http://auth:3001/auth/validate",
                    f"traefik.http.middlewares.auth-{node_name}.forwardauth.trustForwardHeader": "true",
                    f"traefik.http.middlewares.auth-{node_name}.forwardauth.authResponseHeaders": "X-Auth-User,X-Auth-Permissions",
                    # Define the service
                    f"traefik.http.services.{node_name}-core.loadbalancer.server.port": "2528",
                    # Shared middlewares (from docker-compose)
                    "traefik.http.middlewares.cors.headers.accesscontrolallowmethods": "GET,OPTIONS,PUT,POST,DELETE",
                    "traefik.http.middlewares.cors.headers.accesscontrolallowheaders": "*",
                    "traefik.http.middlewares.cors.headers.accesscontrolalloworiginlist": "*",
                    "traefik.http.middlewares.cors.headers.accesscontrolmaxage": "100",
                    "traefik.http.middlewares.cors.headers.addvaryheader": "true",
                    "traefik.http.middlewares.cors.headers.accesscontrolexposeheaders": "X-Auth-Error",
                    # SSE-specific CORS middleware
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolallowmethods": "GET,OPTIONS",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolallowheaders": "Cache-Control,Last-Event-ID,Accept,Accept-Language,Content-Language,Content-Type,Authorization",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolalloworiginlist": "*",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolmaxage": "86400",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.addvaryheader": "true",
                    f"traefik.http.middlewares.cors-sse-{node_name}.headers.accesscontrolexposeheaders": "X-Auth-Error",
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
                        "--node-name",
                        node_name,
                        "init",
                        "--server-host",
                        "0.0.0.0",
                        "--server-port",
                        str(2528),
                        "--swarm-port",
                        str(2428),
                    ]
                    if mock_relayer and relayer_url:
                        init_config["command"].extend(
                            ["--relayer-url", relayer_url, "--protocol", "mock-relayer"]
                        )
                    # Note: Don't set entrypoint - use image default
                else:
                    # Original behavior - bypass entrypoint for direct merod control
                    init_config["entrypoint"] = ""
                    init_config["command"] = [
                        "merod",
                        "--home",
                        "/app/data",
                        "--node-name",
                        node_name,
                        "init",
                        "--server-host",
                        "0.0.0.0",
                        "--server-port",
                        str(2528),
                        "--swarm-port",
                        str(2428),
                    ]
                    if mock_relayer and relayer_url:
                        init_config["command"].extend(
                            ["--relayer-url", relayer_url, "--protocol", "mock-relayer"]
                        )
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
                    self._apply_e2e_defaults(config_file, node_name, workflow_id)

                # Apply bootstrap nodes configuration (works regardless of e2e_mode)
                if bootstrap_nodes:
                    self._apply_bootstrap_nodes(config_file, node_name, bootstrap_nodes)

            except Exception:
                if e2e_mode:
                    console.print(
                        f"[cyan]Applying e2e defaults to {node_name} for test isolation...[/cyan]"
                    )
                    self._apply_e2e_defaults(config_file, node_name, workflow_id)

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
                    "--node-name",
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
                    "--node-name",
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
            time.sleep(3)
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
            host_rpc_port = self._extract_host_port(container, "2528/tcp")
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

    def _find_available_ports(self, count: int, start_port: int = 2428) -> list[int]:
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
        self, auth_image: str = None, auth_use_cached: bool = False
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
                if not self._start_auth_container(auth_image, auth_use_cached):
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
        self, auth_image: str = None, auth_use_cached: bool = False
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
                    "traefik.http.routers.auth-public.middlewares": "cors,auth-headers",
                    "traefik.http.routers.auth-public.priority": "100",
                    # Add Node ID header for auth service
                    "traefik.http.middlewares.auth-headers.headers.customrequestheaders.X-Node-ID": "auth",
                    # Define the service
                    "traefik.http.services.auth-service.loadbalancer.server.port": "3001",
                    # CORS middleware
                    "traefik.http.middlewares.cors.headers.accesscontrolallowmethods": "GET,OPTIONS,PUT,POST,DELETE",
                    "traefik.http.middlewares.cors.headers.accesscontrolallowheaders": "*",
                    "traefik.http.middlewares.cors.headers.accesscontrolalloworiginlist": "*",
                    "traefik.http.middlewares.cors.headers.accesscontrolmaxage": "100",
                    "traefik.http.middlewares.cors.headers.addvaryheader": "true",
                    "traefik.http.middlewares.cors.headers.accesscontrolexposeheaders": "X-Auth-Error",
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
        mock_relayer: bool = False,
        workflow_id: str = None,  # for test isolation
        e2e_mode: bool = False,  # enable e2e-style defaults
        near_devnet_config: dict = None,
        bootstrap_nodes: list[str] = None,  # bootstrap nodes to connect to
        use_image_entrypoint: bool = False,  # preserve Docker image's entrypoint
    ) -> bool:
        """Run multiple Calimero nodes with automatic port allocation."""
        console.print(f"[bold]Starting {count} Calimero nodes...[/bold]")

        # Generate a single shared workflow_id for all nodes if none provided
        if workflow_id is None:
            workflow_id = str(uuid.uuid4())[:8]
            console.print(f"[cyan]Generated shared workflow_id: {workflow_id}[/cyan]")

        # Find available ports automatically if not specified
        if base_port is None:
            p2p_ports = self._find_available_ports(count, 2428)
        else:
            p2p_ports = [base_port + i for i in range(count)]

        if base_rpc_port is None:
            # Use a different range for RPC ports to avoid conflicts
            rpc_ports = self._find_available_ports(count, 2528)
        else:
            rpc_ports = [base_rpc_port + i for i in range(count)]

        success_count = 0
        for i in range(count):
            node_name = f"{prefix}-{i+1}"
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
                mock_relayer=mock_relayer,
                workflow_id=workflow_id,
                e2e_mode=e2e_mode,
                near_devnet_config=node_specific_near_config,
                bootstrap_nodes=bootstrap_nodes,
                use_image_entrypoint=use_image_entrypoint,
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

    def stop_node(self, node_name: str) -> bool:
        """Stop a Calimero node container."""
        try:
            if node_name in self.nodes:
                container = self.nodes[node_name]
                container.stop(timeout=10)
                container.remove()
                del self.nodes[node_name]
                console.print(f"[green]✓ Stopped and removed node {node_name}[/green]")
                self.node_rpc_ports.pop(node_name, None)
                return True
            else:
                # Try to find container by name
                try:
                    container = self.client.containers.get(node_name)
                    container.stop(timeout=10)
                    container.remove()
                    console.print(
                        f"[green]✓ Stopped and removed node {node_name}[/green]"
                    )
                    self.node_rpc_ports.pop(node_name, None)
                    return True
                except docker.errors.NotFound:
                    console.print(f"[yellow]Node {node_name} not found[/yellow]")
                    return False
        except Exception as e:
            console.print(f"[red]✗ Failed to stop node {node_name}: {str(e)}[/red]")
            return False

    def stop_all_nodes(self) -> bool:
        """Stop all running Calimero nodes."""
        try:
            containers = self.client.containers.list(
                filters={"label": "calimero.node=true"}
            )

            success = True
            success_count = 0
            failed_nodes = []

            if not containers:
                console.print(
                    "[yellow]No Calimero nodes are currently running[/yellow]"
                )
            else:
                console.print(
                    f"[bold]Stopping {len(containers)} Calimero nodes...[/bold]"
                )

                for container in containers:
                    try:
                        container.stop(timeout=10)
                        container.remove()
                        console.print(
                            f"[green]✓ Stopped and removed {container.name}[/green]"
                        )
                        success_count += 1
                        self.node_rpc_ports.pop(container.name, None)

                        # Remove from nodes dict if present
                        if container.name in self.nodes:
                            del self.nodes[container.name]

                    except Exception as e:
                        console.print(
                            f"[red]✗ Failed to stop {container.name}: {str(e)}[/red]"
                        )
                        failed_nodes.append(container.name)

                console.print(
                    f"\n[bold]Stop Summary: {success_count}/{len(containers)} nodes stopped successfully[/bold]"
                )

                if failed_nodes:
                    console.print(
                        f"[red]Failed to stop: {', '.join(failed_nodes)}[/red]"
                    )
                    success = False

            # Stop mock relayer if it's running
            try:
                relayer = self.client.containers.get(MOCK_RELAYER_NAME)
                if relayer.status == "running":
                    console.print("[cyan]Stopping mock relayer container...[/cyan]")
                    relayer.stop(timeout=10)
                relayer.remove()
                console.print("[green]✓ Mock relayer stopped[/green]")
                self.mock_relayer_url = None
            except docker.errors.NotFound:
                pass
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Warning: Failed to stop mock relayer: {e}[/yellow]"
                )
                success = False

            return success

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
                "sh -c 'timeout 3 bash -c \"</dev/tcp/127.0.0.1/2528\"' 2>&1 || echo 'Connection failed'"
            )

            if "Connection failed" in result.output.decode():
                console.print(
                    f"[red]✗ Admin server not accessible on localhost:2528 for {node_name}[/red]"
                )
                return False
            else:
                console.print(
                    f"[green]✓ Admin server accessible on localhost:2528 for {node_name}[/green]"
                )
                return True

        except Exception as e:
            console.print(
                f"[red]Failed to verify admin binding for {node_name}: {str(e)}[/red]"
            )
            return False

    def _apply_bootstrap_nodes(
        self,
        config_file: str,
        node_name: str,
        bootstrap_nodes: list[str],
    ):
        """Apply bootstrap nodes configuration."""
        try:
            from pathlib import Path

            import toml

            config_path = Path(config_file)
            if not config_path.exists():
                console.print(f"[yellow]Config file not found: {config_file}[/yellow]")
                return

            with open(config_path) as f:
                config = toml.load(f)

            self._set_nested_config(config, "bootstrap.nodes", bootstrap_nodes)

            with open(config_path, "w") as f:
                toml.dump(config, f)

            console.print(
                f"[green]✓ Applied bootstrap nodes to {node_name} ({len(bootstrap_nodes)} nodes)[/green]"
            )

        except ImportError:
            console.print(
                "[red]✗ toml package not found. Install with: pip install toml[/red]"
            )
        except Exception as e:
            console.print(
                f"[red]✗ Failed to apply bootstrap nodes to {node_name}: {e}[/red]"
            )

    def _get_docker_host_url(self, port: int) -> str:
        """Get Docker host URL for a given port.

        When nodes run in Docker containers (via --image flag), they need to use
        host.docker.internal to reach services on the host machine.
        This works on Mac/Windows Docker Desktop.
        On Linux, Docker will handle the resolution or fall back to gateway IP.
        """
        # When merobox uses --image flag, nodes run in Docker
        # Use host.docker.internal to reach host services
        return f"http://host.docker.internal:{port}"

    def _apply_e2e_defaults(
        self,
        config_file: str,
        node_name: str,
        workflow_id: str,
    ):
        """Apply e2e-style defaults for reliable testing."""
        try:
            # Generate unique workflow ID if not provided
            if not workflow_id:
                workflow_id = str(uuid.uuid4())[:8]

            config_path = Path(config_file)
            if not config_path.exists():
                console.print(f"[yellow]Config file not found: {config_file}[/yellow]")
                return

            # Load existing config
            with open(config_path) as f:
                config = toml.load(f)

            # Use Docker host URLs when nodes run in Docker (they always do with --image flag)
            eth_rpc_url = self._get_docker_host_url(ANVIL_DEFAULT_PORT)
            icp_rpc_url = self._get_docker_host_url(DFX_DEFAULT_PORT)

            # Apply e2e-style defaults for reliable testing
            e2e_config = {
                # Disable bootstrap nodes for test isolation
                "bootstrap.nodes": [],
                # Use unique rendezvous namespace per workflow (like e2e tests)
                "discovery.rendezvous.namespace": f"calimero/merobox-tests/{workflow_id}",
                # Keep mDNS as backup (like e2e tests)
                "discovery.mdns": True,
                # Aggressive sync settings from e2e tests for reliable testing
                "sync.timeout_ms": 30000,  # 30s timeout (matches production)
                # 500ms between syncs (very aggressive for tests)
                "sync.interval_ms": 500,
                # 1s periodic checks (ensures rapid sync in tests)
                "sync.frequency_ms": 1000,
                # Ethereum local devnet configuration (uses Anvil default account #0)
                "context.config.ethereum.network": NETWORK_LOCAL,
                "context.config.ethereum.contract_id": ETHEREUM_LOCAL_CONTRACT_ID,
                "context.config.ethereum.signer": "self",
                "context.config.signer.self.ethereum.local.rpc_url": eth_rpc_url,
                "context.config.signer.self.ethereum.local.account_id": ETHEREUM_LOCAL_ACCOUNT_ID,
                "context.config.signer.self.ethereum.local.secret_key": ETHEREUM_LOCAL_SECRET_KEY,
                # ICP local devnet configuration (for consistency)
                "context.config.icp.network": NETWORK_LOCAL,
                "context.config.icp.contract_id": ICP_LOCAL_CONTRACT_ID,
                "context.config.icp.signer": "self",
                "context.config.signer.self.icp.local.rpc_url": icp_rpc_url,
            }

            # Apply each configuration
            for key, value in e2e_config.items():
                self._set_nested_config(config, key, value)

            # Write back to file
            with open(config_path, "w") as f:
                toml.dump(config, f)

            console.print(
                f"[green]✓ Applied e2e-style defaults to {node_name} (workflow: {workflow_id})[/green]"
            )

        except ImportError:
            console.print(
                "[red]✗ toml package not found. Install with: pip install toml[/red]"
            )
        except Exception as e:
            console.print(
                f"[red]✗ Failed to apply e2e defaults to {node_name}: {e}[/red]"
            )

    def _set_nested_config(self, config: dict, key: str, value):
        """Set nested configuration value using dot notation."""
        keys = key.split(".")
        current = config
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]

        current[keys[-1]] = value
        console.print(f"[cyan]  {key} = {value}[/cyan]")

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
