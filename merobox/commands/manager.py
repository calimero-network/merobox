"""
Calimero Manager - Core functionality for managing Calimero nodes in Docker containers.

This module provides the DockerManager class which serves as a facade for the
specialized manager classes following the Single Responsibility Principle:
- NodeManager: Handles node container operations
- AuthServiceManager: Handles auth service stack (Traefik + Auth)
- NetworkManager: Handles Docker network management
"""

import atexit
import signal
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from merobox.commands.config_utils import apply_near_devnet_config_to_file
from merobox.commands.managers import NodeManager

console = Console()


class DockerManager:
    """Manages Calimero nodes in Docker containers.

    This class serves as a facade that delegates to specialized manager classes:
    - NodeManager: Node container operations
    - AuthServiceManager: Auth service stack management
    - NetworkManager: Docker network management

    For direct access to specialized functionality, use the individual manager classes
    from merobox.commands.managers.
    """

    def __init__(self, enable_signal_handlers: bool = True):
        """Initialize the DockerManager.

        Args:
            enable_signal_handlers: If True, register signal handlers for graceful
                shutdown on SIGINT/SIGTERM. Set to False in tests or when managing
                signals externally.
        """
        # Initialize the node manager which also creates other managers
        self._node_manager = NodeManager()

        # Expose the shared Docker client
        self.client = self._node_manager.client

        # Expose sub-managers for direct access
        self.network_manager = self._node_manager.network_manager
        self.auth_service_manager = self._node_manager.auth_service_manager

        # Signal handling state
        self._shutting_down = False
        self._original_sigint_handler = None
        self._original_sigterm_handler = None

        if enable_signal_handlers:
            self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Register signal handlers for graceful shutdown."""
        self._original_sigint_handler = signal.signal(
            signal.SIGINT, self._signal_handler
        )
        self._original_sigterm_handler = signal.signal(
            signal.SIGTERM, self._signal_handler
        )
        atexit.register(self._cleanup_on_exit)

    def _signal_handler(self, signum, frame):
        """Handle SIGINT/SIGTERM signals for graceful shutdown."""
        if self._shutting_down:
            console.print("\n[red]Forced exit requested, terminating...[/red]")
            sys.exit(1)

        self._shutting_down = True
        sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        console.print(
            f"\n[yellow]Received {sig_name}, initiating graceful shutdown...[/yellow]"
        )

        self._cleanup_resources()
        sys.exit(0)

    def _cleanup_on_exit(self):
        """Cleanup handler for atexit - only runs if not already cleaned up."""
        if not self._shutting_down:
            self._cleanup_resources()

    def _cleanup_resources(self):
        """Stop all managed resources (containers)."""
        if self.nodes:
            console.print("[cyan]Stopping managed containers...[/cyan]")
            for node_name in list(self.nodes.keys()):
                try:
                    container = self.nodes[node_name]
                    container.stop(timeout=10)
                    container.remove()
                    console.print(f"[green]✓ Stopped container {node_name}[/green]")
                except Exception as e:
                    console.print(
                        f"[yellow]⚠️  Could not stop container {node_name}: {e}[/yellow]"
                    )
            self._node_manager.nodes.clear()
            self._node_manager.node_rpc_ports.clear()

    def remove_signal_handlers(self):
        """Remove signal handlers and restore original handlers."""
        if self._original_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._original_sigint_handler)
            self._original_sigint_handler = None
        if self._original_sigterm_handler is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm_handler)
            self._original_sigterm_handler = None

    # Properties for backward compatibility
    @property
    def nodes(self) -> dict:
        """Dict of managed node containers."""
        return self._node_manager.nodes

    @property
    def node_rpc_ports(self) -> dict[str, int]:
        """Dict mapping node names to RPC ports."""
        return self._node_manager.node_rpc_ports

    # Delegate image operations to base manager
    def _is_remote_image(self, image: str) -> bool:
        """Check if the image name indicates a remote registry."""
        return self._node_manager._is_remote_image(image)

    def force_pull_image(self, image: str) -> bool:
        """Force pull an image even if it exists locally."""
        return self._node_manager.force_pull_image(image)

    def _ensure_image_pulled(self, image: str) -> bool:
        """Ensure the specified Docker image is available locally, pulling if remote."""
        return self._node_manager._ensure_image_pulled(image)

    def _extract_host_port(self, container, container_port: str) -> Optional[int]:
        """Extract the published host port for a given container port."""
        return self._node_manager._extract_host_port(container, container_port)

    # Delegate node operations
    def get_node_rpc_port(self, node_name: str) -> Optional[int]:
        """Return the published RPC port for the given node, if available."""
        return self._node_manager.get_node_rpc_port(node_name)

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
        workflow_id: str = None,
        e2e_mode: bool = False,
        config_path: str = None,
        near_devnet_config: dict = None,
        bootstrap_nodes: list[str] = None,
        use_image_entrypoint: bool = False,
    ) -> bool:
        """Run a Calimero node container."""
        return self._node_manager.run_node(
            node_name=node_name,
            port=port,
            rpc_port=rpc_port,
            chain_id=chain_id,
            data_dir=data_dir,
            image=image,
            auth_service=auth_service,
            auth_image=auth_image,
            auth_use_cached=auth_use_cached,
            webui_use_cached=webui_use_cached,
            log_level=log_level,
            rust_backtrace=rust_backtrace,
            workflow_id=workflow_id,
            e2e_mode=e2e_mode,
            config_path=config_path,
            near_devnet_config=near_devnet_config,
            bootstrap_nodes=bootstrap_nodes,
            use_image_entrypoint=use_image_entrypoint,
        )

    def _find_available_ports(self, count: int, start_port: int = 2428) -> list[int]:
        """Find available ports starting from start_port."""
        return self._node_manager._find_available_ports(count, start_port)

    # Delegate network operations
    def _ensure_auth_networks(self):
        """Ensure the auth service networks exist for Traefik integration."""
        return self._node_manager.network_manager.ensure_auth_networks()

    # Delegate auth service operations
    def _start_auth_service_stack(
        self, auth_image: str = None, auth_use_cached: bool = False
    ):
        """Start the Traefik proxy and auth service containers."""
        return self._node_manager.auth_service_manager.start_auth_service_stack(
            auth_image, auth_use_cached
        )

    def _start_traefik_container(self):
        """Start the Traefik proxy container."""
        return self._node_manager.auth_service_manager._start_traefik_container()

    def _start_auth_container(
        self, auth_image: str = None, auth_use_cached: bool = False
    ):
        """Start the Auth service container."""
        return self._node_manager.auth_service_manager._start_auth_container(
            auth_image, auth_use_cached
        )

    def _is_container_running(self, container_name: str) -> bool:
        """Check if a container is running."""
        return self._node_manager._is_container_running(container_name)

    def stop_auth_service_stack(self):
        """Stop the Traefik proxy and auth service containers."""
        return self._node_manager.auth_service_manager.stop_auth_service_stack()

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
        workflow_id: str = None,
        e2e_mode: bool = False,
        near_devnet_config: dict = None,
        bootstrap_nodes: list[str] = None,
        use_image_entrypoint: bool = False,
    ) -> bool:
        """Run multiple Calimero nodes with automatic port allocation."""
        return self._node_manager.run_multiple_nodes(
            count=count,
            base_port=base_port,
            base_rpc_port=base_rpc_port,
            chain_id=chain_id,
            prefix=prefix,
            image=image,
            auth_service=auth_service,
            auth_image=auth_image,
            auth_use_cached=auth_use_cached,
            webui_use_cached=webui_use_cached,
            log_level=log_level,
            rust_backtrace=rust_backtrace,
            workflow_id=workflow_id,
            e2e_mode=e2e_mode,
            near_devnet_config=near_devnet_config,
            bootstrap_nodes=bootstrap_nodes,
            use_image_entrypoint=use_image_entrypoint,
        )

    def stop_node(self, node_name: str) -> bool:
        """Stop a Calimero node container."""
        return self._node_manager.stop_node(node_name)

    def stop_all_nodes(self) -> bool:
        """Stop all running Calimero nodes."""
        return self._node_manager.stop_all_nodes()

    def get_running_nodes(self) -> list[str]:
        """Return a list of names for running Calimero node containers."""
        return self._node_manager.get_running_nodes()

    def list_nodes(self) -> None:
        """List all running Calimero nodes and infrastructure."""
        return self._node_manager.list_nodes()

    def get_node_logs(self, node_name: str, tail: int = 100) -> None:
        """Get logs from a specific node."""
        return self._node_manager.get_node_logs(node_name, tail)

    def verify_admin_binding(self, node_name: str) -> bool:
        """Verify that the admin server is properly bound to localhost."""
        return self._node_manager.verify_admin_binding(node_name)

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
        return self._node_manager._fix_permissions(path)
