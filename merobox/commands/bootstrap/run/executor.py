"""
Main workflow executor - Orchestrates workflow execution and manages the overall process.
"""

import asyncio
import os
import sys
import time
import uuid
from typing import Any, Optional

import docker
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from merobox.commands.constants import RESERVED_NODE_CONFIG_KEYS
from merobox.commands.manager import DockerManager
from merobox.commands.near.sandbox import SandboxManager
from merobox.commands.utils import console


class WorkflowExecutor:
    """Executes Calimero workflows based on YAML configuration."""

    def __init__(
        self,
        config: dict[str, Any],
        manager: DockerManager,
        image: Optional[str] = None,
        auth_service: bool = False,
        auth_image: str = None,
        auth_use_cached: bool = False,
        webui_use_cached: bool = False,
        log_level: str = "debug",
        rust_backtrace: str = "0",
        mock_relayer: bool = False,
        e2e_mode: bool = False,
        workflow_dir: str = None,
        near_devnet: bool = False,
        contracts_dir: str = None,
    ):
        self.config = config
        self.manager = manager
        self.workflow_dir = workflow_dir or "."
        self.sandbox = None
        self.near_config = {}

        # Determine if we're in binary mode
        self.is_binary_mode = (
            hasattr(manager, "binary_path") and manager.binary_path is not None
        )

        # Auth service can be enabled by CLI flag or workflow config (CLI takes precedence)
        self.auth_service = auth_service or config.get("auth_service", False)
        # Auth image can be set by CLI flag or workflow config (CLI takes precedence)
        self.auth_image = (
            auth_image if auth_image is not None else config.get("auth_image", None)
        )
        # Auth use cached can be enabled by CLI flag or workflow config (CLI takes precedence)
        self.auth_use_cached = auth_use_cached or config.get("auth_use_cached", False)
        # WebUI use cached can be enabled by CLI flag or workflow config (CLI takes precedence)
        self.webui_use_cached = webui_use_cached or config.get(
            "webui_use_cached", False
        )
        # Log level can be set by CLI flag or workflow config (CLI takes precedence)
        self.log_level = (
            log_level if log_level is not None else config.get("log_level", "debug")
        )
        # Log level can be set by CLI flag or workflow config (CLI takes precedence)
        self.rust_backtrace = (
            rust_backtrace
            if rust_backtrace is not None
            else config.get("rust_backtrace", "0")
        )
        # Mock relayer can be enabled by CLI flag or workflow config (CLI takes precedence)
        self.mock_relayer = mock_relayer or config.get("mock_relayer", False)

        # E2E mode can be enabled by CLI flag or workflow config (CLI takes precedence)
        self.e2e_mode = e2e_mode or config.get("e2e_mode", False)

        # Bootstrap nodes can be specified in workflow config to override e2e_mode's empty default
        self.bootstrap_nodes = config.get("bootstrap_nodes", None)

        # Generate unique workflow ID for test isolation (like e2e tests)
        self.workflow_id = str(uuid.uuid4())[:8]

        self.near_devnet = near_devnet or config.get("near_devnet", False)
        self.contracts_dir = contracts_dir or config.get("contracts_dir", None)

        # Forbid having Near Devnet (sandbox) configuration and mock relayer at the same time
        if self.mock_relayer and self.near_devnet:
            console.print(
                "[red]Configuration Error: --mock-relayer and --near-devnet cannot be enabled simultaneously.[/red]"
            )
            sys.exit(1)

        if self.near_devnet and not self.contracts_dir:
            console.print(
                "[red] Config Error: near_devnet requires contracts_dir to be specified[/red]"
            )
            sys.exit(1)

        try:
            console.print(
                f"[cyan]WorkflowExecutor: resolved log_level='{self.log_level}', binary_mode={self.is_binary_mode}[/cyan]"
            )
            console.print(
                f"[cyan]WorkflowExecutor: workflow_id='{self.workflow_id}' (for test isolation)[/cyan]"
            )
        except Exception:
            pass
        try:
            console.print(
                f"[cyan]WorkflowExecutor: resolved rust_backtrace='{self.rust_backtrace}', binary_mode={self.is_binary_mode}[/cyan]"
            )
        except Exception:
            pass
        try:
            if self.mock_relayer:
                console.print("[cyan]WorkflowExecutor: mock relayer enabled[/cyan]")
        except Exception:
            pass
        self.workflow_results = {}
        self.dynamic_values = {}  # Store dynamic values for later use
        # Node image can be overridden by CLI flag; otherwise from config; else default in manager
        self.image = image

    async def execute_workflow(self) -> bool:
        """Execute the complete workflow."""
        workflow_name = self.config.get("name", "Unnamed Workflow")
        console.print(
            f"\n[bold blue]🚀 Executing Workflow: {workflow_name}[/bold blue]"
        )

        try:
            # Check if we should nuke on start
            nuke_on_start = self.config.get("nuke_on_start", False)
            if nuke_on_start:
                console.print(
                    "\n[bold red]💥 Nuking all data before workflow ...[/bold red]"
                )
                if not self._nuke_data():
                    console.print(
                        "[yellow]⚠️  Warning: Nuke operation encountered issues, continuing anyway...[/yellow]"
                    )
                else:
                    console.print("[green]✓ Nuke on start completed[/green]")
                time.sleep(2)  # Give time for cleanup

            # Check if we should force pull images
            force_pull_images = self.config.get("force_pull_image", False)
            if force_pull_images:
                console.print(
                    "\n[bold red]💥 Nuking all data before workflow ...[/bold red]"
                )
                if not self._nuke_data():
                    console.print(
                        "[yellow]⚠️  Warning: Nuke operation encountered issues, continuing anyway...[/yellow]"
                    )
                else:
                    console.print("[green]✓ Nuke on start completed[/green]")
                time.sleep(2)  # Give time for cleanup

            # Check if we should force pull images (only for Docker mode)
            force_pull_images = self.config.get("force_pull_image", False)
            if force_pull_images:
                if self.is_binary_mode:
                    console.print(
                        "\n[cyan]Skipping image pull in binary (no-docker) mode[/cyan]"
                    )
                else:
                    console.print(
                        "\n[bold yellow]🔄 Force pulling workflow images (force_pull_image=true)...[/bold yellow]"
                    )
                    await self._force_pull_workflow_images()
                    console.print("[green]✓ Image force pull completed[/green]")

            # Start NEAR Devnet if requested
            if self.near_devnet:
                console.print(
                    "\n[bold yellow]Step 0: Initializing NEAR Sandbox...[/bold yellow]"
                )
                self.sandbox = SandboxManager()
                try:
                    self.sandbox.start()

                    ctx_path = os.path.join(
                        self.contracts_dir, "calimero_context_config_near.wasm"
                    )
                    proxy_path = os.path.join(
                        self.contracts_dir, "calimero_context_proxy_near.wasm"
                    )

                    console.print(
                        f"[cyan]Context Config contract path: {ctx_path}[/cyan]"
                    )
                    console.print(
                        f"[cyan]Context Proxy contract path: {proxy_path}[/cyan]"
                    )

                    if not os.path.exists(ctx_path) or not os.path.exists(proxy_path):
                        raise Exception(
                            f"Contract files missing in {self.contracts_dir}. Expected calimero_context_config_near.wasm and calimero_context_proxy_near.wasm"
                        )

                    contract_id = await self.sandbox.setup_calimero(
                        ctx_path, proxy_path
                    )

                    # Determine RPC URL accessible from Docker containers
                    # Not applicable in binary mode, but kept generic
                    rpc_url = self.sandbox.get_rpc_url(
                        for_docker=(not self.is_binary_mode)
                    )

                    self.near_config = {"contract_id": contract_id, "rpc_url": rpc_url}
                except Exception as e:
                    console.print(f"[red]Sandbox setup failed: {e}[/red]")
                    if self.sandbox:
                        await self.sandbox.stop()
                    return False

            # Check if we should restart nodes at the beginning
            restart_nodes = self.config.get("restart", False)
            stop_all_nodes = self.config.get("stop_all_nodes", False)
            nuke_on_end = self.config.get("nuke_on_end", False)

            # Step 1: Restart nodes if requested (at beginning)
            if restart_nodes:
                console.print(
                    "\n[bold yellow]Step 1: Restarting workflow nodes (restart=true)...[/bold yellow]"
                )
                if not self.manager.stop_all_nodes():
                    console.print(
                        "[red]❌ Failed to stop workflow nodes - stopping workflow[/red]"
                    )
                    if stop_all_nodes:
                        self._stop_nodes_on_failure()
                    return False
                console.print("[green]✓ Workflow nodes stopped[/green]")
                time.sleep(2)  # Give time for cleanup
            else:
                console.print(
                    "\n[bold blue]Step 1: Checking workflow nodes (restart=false)...[/bold blue]"
                )
                console.print(
                    "[cyan]Will reuse existing nodes if they're running...[/cyan]"
                )

            # Step 2: Manage nodes
            console.print("\n[bold yellow]Step 2: Managing nodes...[/bold yellow]")
            if not await self._start_nodes(restart_nodes):
                console.print(
                    "[red]❌ Node management failed - stopping workflow[/red]"
                )
                if stop_all_nodes:
                    self._stop_nodes_on_failure()
                return False

            # Step 3: Wait for nodes to be ready
            console.print(
                "\n[bold yellow]Step 3: Waiting for nodes to be ready...[/bold yellow]"
            )
            if not await self._wait_for_nodes_ready():
                console.print("[red]❌ Nodes not ready - stopping workflow[/red]")
                if stop_all_nodes:
                    self._stop_nodes_on_failure()
                return False

            # Step 4: Execute workflow steps
            console.print(
                "\n[bold yellow]Step 4: Executing workflow steps...[/bold yellow]"
            )
            if not await self._execute_workflow_steps():
                console.print("[red]❌ Workflow steps failed - stopping workflow[/red]")
                if stop_all_nodes:
                    self._stop_nodes_on_failure()
                return False

            # Step 5: Stop all nodes if requested (at end)
            if stop_all_nodes:
                console.print(
                    "\n[bold yellow]Step 5: Stopping all nodes (stop_all_nodes=true)...[/bold yellow]"
                )
                if not self.manager.stop_all_nodes():
                    console.print("[red]Failed to stop all nodes[/red]")
                    # Don't return False here as workflow completed successfully
                else:
                    console.print("[green]✓ All nodes stopped[/green]")
            else:
                console.print(
                    "\n[bold blue]Step 5: Leaving nodes running (stop_all_nodes=false)...[/bold blue]"
                )
                console.print(
                    "[cyan]Nodes will continue running for future workflows[/cyan]"
                )

            # Step 6: Nuke on end if requested
            if nuke_on_end:
                console.print(
                    "\n[bold red]💥 Nuking all data after workflow ...[/bold red]"
                )
                if not self._nuke_data():
                    console.print(
                        "[yellow]⚠️  Warning: Nuke operation encountered issues[/yellow]"
                    )
                else:
                    console.print("[green]✓ Nuke on end completed[/green]")

            console.print(
                f"\n[bold green]🎉 Workflow '{workflow_name}' completed successfully![/bold green]"
            )

            # Display captured dynamic values
            if self.dynamic_values:
                console.print("\n[bold]📋 Captured Dynamic Values:[/bold]")
                for key, value in self.dynamic_values.items():
                    console.print(f"  {key}: {value}")

            return True

        except Exception as e:
            console.print(f"\n[red]❌ Workflow failed with error: {str(e)}[/red]")
            stop_all_nodes = self.config.get("stop_all_nodes", False)
            if stop_all_nodes:
                self._stop_nodes_on_failure()
            return False
        finally:
            if self.sandbox:
                await self.sandbox.stop()

    def _stop_nodes_on_failure(self) -> None:
        """
        Stop all nodes when workflow fails, if stop_all_nodes is configured.
        This ensures nodes are cleaned up even on failure.
        """
        console.print(
            "\n[bold yellow]Stopping all nodes due to workflow failure (stop_all_nodes=true)...[/bold yellow]"
        )
        if not self.manager.stop_all_nodes():
            console.print("[red]Failed to stop all nodes[/red]")
        else:
            console.print("[green]✓ All nodes stopped[/green]")

    def _nuke_data(self, prefix: str = None) -> bool:
        """
        Execute nuke operation to clean all data.

        Args:
            prefix: Optional prefix to filter which nodes to nuke

        Returns:
            bool: True if nuke succeeded, False otherwise
        """
        try:
            from merobox.commands.nuke import execute_nuke

            # If no prefix specified, derive from workflow nodes config
            if prefix is None:
                nodes_config = self.config.get("nodes", {})
                prefix = nodes_config.get("prefix", None)

            return execute_nuke(
                manager=self.manager,
                prefix=prefix,
                verbose=False,
                silent=False,
            )
        except Exception as e:
            console.print(f"[red]Nuke operation failed: {str(e)}[/red]")
            return False

    async def _force_pull_workflow_images(self) -> None:
        """Force pull all Docker images specified in the workflow configuration."""
        # Only applicable in Docker mode
        if self.is_binary_mode:
            return

        try:
            # Get image from nodes configuration
            nodes_config = self.config.get("nodes", {})
            if isinstance(nodes_config, dict):
                image = nodes_config.get("image")
                if image:
                    console.print(
                        f"[yellow]Force pulling workflow image: {image}[/yellow]"
                    )
                    try:
                        if not self.manager.force_pull_image(image):
                            console.print(
                                f"[red]Warning: Failed to force pull image: {image}[/red]"
                            )
                            console.print(
                                "[yellow]Workflow will continue with existing image[/yellow]"
                            )
                    except Exception as e:
                        console.print(
                            f"[red]Warning: force_pull_image failed for image: {image} - {e}[/red]"
                        )

                # Check for images in individual node configurations
                for node_name, node_config in nodes_config.items():
                    if isinstance(node_config, dict) and "image" in node_config:
                        image = node_config["image"]
                        console.print(
                            f"[yellow]Force pulling image for node {node_name}: {image}[/yellow]"
                        )
                        try:
                            if not self.manager.force_pull_image(image):
                                console.print(
                                    f"[red]Warning: Failed to force pull image for {node_name}: {image}[/red]"
                                )
                                console.print(
                                    "[yellow]Workflow will continue with existing image[/yellow]"
                                )
                        except Exception as e:
                            console.print(
                                f"[red]Warning: force_pull_image failed for node {node_name}: {image} - {e}[/red]"
                            )

        except Exception as e:
            console.print(f"[red]Error during force pull: {str(e)}[/red]")
            console.print(
                "[yellow]Workflow will continue with existing images[/yellow]"
            )

    def _is_node_running(self, node_name: str) -> bool:
        """Check if a node is running (works for both binary and Docker mode)."""
        try:
            if hasattr(self.manager, "is_node_running"):
                return self.manager.is_node_running(node_name)

            # Fallback to Docker client (Docker mode only)
            if not self.is_binary_mode and hasattr(self.manager, "client"):
                try:
                    container = self.manager.client.containers.get(node_name)
                    return container.status == "running"
                except docker.errors.NotFound:
                    return False
                except Exception:
                    return False

            return False
        except Exception:
            return False

    def _resolve_config_path(self, config_path: Optional[str]) -> Optional[str]:
        """
        Resolve config path relative to workflow YAML file location.

        Args:
            config_path: Path to config file (can be relative or absolute)

        Returns:
            Absolute path to config file, or None if config_path is None
        """
        if config_path is None:
            return None
        if os.path.isabs(config_path):
            return config_path
        resolved = os.path.join(self.workflow_dir, config_path)
        return os.path.abspath(resolved)

    async def _start_nodes(self, restart: bool) -> bool:
        """Start the configured nodes."""
        nodes_config = self.config.get("nodes", {})

        if not nodes_config:
            console.print("[red]No nodes configuration found[/red]")
            return False

        base_port = nodes_config.get("base_port", 2428)
        base_rpc_port = nodes_config.get("base_rpc_port", 2528)

        chain_id = nodes_config.get("chain_id", "testnet-1")
        image = self.image if self.image is not None else nodes_config.get("image")
        prefix = nodes_config.get("prefix", "calimero-node")
        config_path = self._resolve_config_path(nodes_config.get("config_path"))
        use_image_entrypoint = nodes_config.get("use_image_entrypoint", False)

        # Ensure nodes are restarted when Near Devnet or Mock Relayer is requested so wiring is fresh
        if (self.near_devnet or self.mock_relayer) and not restart:
            feature = "NEAR Devnet" if self.near_devnet else "Mock Relayer"
            console.print(
                f"[yellow]{feature} requested; forcing restart to wire nodes to the relayer[/yellow]"
            )
            restart = True

        # If workflow declares a count, delegate to manager to handle bulk creation
        if "count" in nodes_config:
            # Check for incompatible config_path option
            if config_path is not None:
                console.print(
                    "[red]❌ config_path is not supported with 'count' mode[/red]"
                )
                console.print(
                    "[yellow]Please define nodes individually to use custom config paths[/yellow]"
                )
                return False

            count = nodes_config["count"]
            if restart:
                console.print(
                    f"Starting {count} nodes with prefix '{prefix}' (restart mode)..."
                )

                # NEAR Devnet Config Logic
                node_near_config = {}
                if self.near_devnet:
                    console.print("[green]✓ Using Near Devnet config [/green]")
                    for i in range(count):
                        node_name = f"{prefix}-{i+1}"
                        console.print(
                            f"[green]✓ Creating account '{node_name}' using Near Devnet config [/green]"
                        )
                        creds = await self.sandbox.create_node_account(node_name)
                        node_near_config[node_name] = {
                            "rpc_url": self.near_config["rpc_url"],
                            "contract_id": self.near_config["contract_id"],
                            **creds,
                        }

                # Build arguments for run_multiple_nodes
                run_multiple_kwargs = {
                    "count": count,
                    "base_port": base_port,
                    "base_rpc_port": base_rpc_port,
                    "chain_id": chain_id,
                    "prefix": prefix,
                    "image": image,
                    "auth_service": self.auth_service,
                    "auth_image": self.auth_image,
                    "auth_use_cached": self.auth_use_cached,
                    "webui_use_cached": self.webui_use_cached,
                    "log_level": self.log_level,
                    "rust_backtrace": self.rust_backtrace,
                    "mock_relayer": self.mock_relayer,
                    "workflow_id": self.workflow_id,
                    "e2e_mode": self.e2e_mode,
                    "near_devnet_config": node_near_config,
                    "bootstrap_nodes": self.bootstrap_nodes,
                }
                if not self.is_binary_mode:
                    run_multiple_kwargs["use_image_entrypoint"] = use_image_entrypoint

                if not self.manager.run_multiple_nodes(**run_multiple_kwargs):
                    return False
            else:
                console.print(
                    f"Checking {count} nodes with prefix '{prefix}' (no restart mode)..."
                )
                for i in range(count):
                    node_name = f"{prefix}-{i+1}"
                    is_running = self._is_node_running(node_name)

                    if is_running:
                        console.print(
                            f"[green]✓ Node '{node_name}' is already running[/green]"
                        )
                        continue

                    # NEAR Devnet Config Logic
                    node_near_config = None
                    if self.near_devnet:
                        creds = await self.sandbox.create_node_account(node_name)
                        node_near_config = {
                            "rpc_url": self.near_config["rpc_url"],
                            "contract_id": self.near_config["contract_id"],
                            **creds,
                        }

                    # Not running -> start (allow manager to allocate ports if base_* is None)
                    port = base_port + i if base_port is not None else None
                    rpc_port = base_rpc_port + i if base_rpc_port is not None else None
                    # Build arguments for run_node
                    run_node_kwargs = {
                        "node_name": node_name,
                        "port": port,
                        "rpc_port": rpc_port,
                        "chain_id": chain_id,
                        "data_dir": None,
                        "image": image,
                        "auth_service": self.auth_service,
                        "auth_image": self.auth_image,
                        "auth_use_cached": self.auth_use_cached,
                        "webui_use_cached": self.webui_use_cached,
                        "log_level": self.log_level,
                        "rust_backtrace": self.rust_backtrace,
                        "mock_relayer": self.mock_relayer,
                        "workflow_id": self.workflow_id,
                        "e2e_mode": self.e2e_mode,
                        "near_devnet_config": node_near_config,
                        "bootstrap_nodes": self.bootstrap_nodes,
                    }
                    if not self.is_binary_mode:
                        run_node_kwargs["use_image_entrypoint"] = use_image_entrypoint

                    if not self.manager.run_node(**run_node_kwargs):
                        return False

            console.print("[green]✓ Node management completed[/green]")
            return True

        # Otherwise handle individually defined nodes (dict or list)
        if isinstance(nodes_config, dict):
            # Filter out reserved configuration keys from node definitions
            items = [
                (k, v)
                for k, v in nodes_config.items()
                if k not in RESERVED_NODE_CONFIG_KEYS
            ]
        else:
            # list of node names
            items = [(n, None) for n in nodes_config]

        for node_name, node_cfg in items:
            # Resolve per-node settings
            if isinstance(node_cfg, dict):
                port = node_cfg.get("port", base_port)
                rpc_port = node_cfg.get("rpc_port", base_rpc_port)
                node_chain_id = node_cfg.get("chain_id", chain_id)
                node_image = (
                    self.image
                    if self.image is not None
                    else node_cfg.get("image", image)
                )
                data_dir = node_cfg.get("data_dir")
                node_config_path = self._resolve_config_path(
                    node_cfg.get("config_path", nodes_config.get("config_path"))
                )
                node_use_image_entrypoint = node_cfg.get(
                    "use_image_entrypoint", use_image_entrypoint
                )
            else:
                port = base_port
                rpc_port = base_rpc_port
                node_chain_id = chain_id
                node_image = image
                data_dir = None
                node_config_path = config_path
                node_use_image_entrypoint = use_image_entrypoint

            # Check if node is running
            is_running = self._is_node_running(node_name)

            node_near_config = None
            if self.near_devnet:
                # Create unique account for this node
                creds = await self.sandbox.create_node_account(node_name)

                node_near_config = {
                    "rpc_url": self.near_config["rpc_url"],
                    "contract_id": self.near_config["contract_id"],
                    **creds,
                }

            if is_running:
                if restart:
                    console.print(
                        f"[yellow]Node '{node_name}' is running but restart requested, stopping...[/yellow]"
                    )
                    try:
                        if hasattr(self.manager, "stop_node"):
                            self.manager.stop_node(node_name)
                        elif not self.is_binary_mode and hasattr(
                            self.manager, "client"
                        ):
                            container = self.manager.client.containers.get(node_name)
                            container.stop()
                            container.remove()
                    except Exception as e:
                        console.print(
                            f"[yellow]Warning: Failed to stop node: {e}[/yellow]"
                        )

                    console.print(f"Starting node '{node_name}'...")
                    # Build arguments for run_node
                    run_node_kwargs = {
                        "node_name": node_name,
                        "port": port,
                        "rpc_port": rpc_port,
                        "chain_id": node_chain_id,
                        "data_dir": data_dir,
                        "image": node_image,
                        "auth_service": self.auth_service,
                        "auth_image": self.auth_image,
                        "auth_use_cached": self.auth_use_cached,
                        "webui_use_cached": self.webui_use_cached,
                        "log_level": self.log_level,
                        "rust_backtrace": self.rust_backtrace,
                        "mock_relayer": self.mock_relayer,
                        "workflow_id": self.workflow_id,
                        "e2e_mode": self.e2e_mode,
                        "config_path": node_config_path,
                        "near_devnet_config": node_near_config,
                        "bootstrap_nodes": self.bootstrap_nodes,
                    }
                    if not self.is_binary_mode:
                        run_node_kwargs["use_image_entrypoint"] = (
                            node_use_image_entrypoint
                        )

                    if not self.manager.run_node(**run_node_kwargs):
                        return False
                else:
                    console.print(
                        f"[green]✓ Node '{node_name}' is already running[/green]"
                    )
                    continue
            else:
                console.print(f"Starting node '{node_name}'...")
                # Build arguments for run_node
                run_node_kwargs = {
                    "node_name": node_name,
                    "port": port,
                    "rpc_port": rpc_port,
                    "chain_id": node_chain_id,
                    "data_dir": data_dir,
                    "image": node_image,
                    "auth_service": self.auth_service,
                    "auth_image": self.auth_image,
                    "auth_use_cached": self.auth_use_cached,
                    "webui_use_cached": self.webui_use_cached,
                    "log_level": self.log_level,
                    "rust_backtrace": self.rust_backtrace,
                    "mock_relayer": self.mock_relayer,
                    "workflow_id": self.workflow_id,
                    "e2e_mode": self.e2e_mode,
                    "config_path": node_config_path,
                    "near_devnet_config": node_near_config,
                    "bootstrap_nodes": self.bootstrap_nodes,
                }
                if not self.is_binary_mode:
                    run_node_kwargs["use_image_entrypoint"] = node_use_image_entrypoint

                if not self.manager.run_node(**run_node_kwargs):
                    return False

        console.print("[green]✓ Node management completed[/green]")
        return True

    async def _wait_for_nodes_ready(self) -> bool:
        """Wait for all nodes to be ready and accessible."""
        wait_timeout = self.config.get("wait_timeout", 60)  # Default 60 seconds

        # Use the validated node names (filters out reserved config keys)
        node_names = list(self._get_valid_node_names())

        console.print(
            f"Waiting up to {wait_timeout} seconds for {len(node_names)} nodes to be ready..."
        )

        start_time = time.time()
        ready_nodes = set()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Waiting for nodes...", total=len(node_names))

            while (
                len(ready_nodes) < len(node_names)
                and (time.time() - start_time) < wait_timeout
            ):
                for node_name in node_names:
                    if node_name not in ready_nodes:
                        try:
                            is_running = self._is_node_running(node_name)

                            if is_running:
                                if self.manager.verify_admin_binding(node_name):
                                    ready_nodes.add(node_name)
                                    progress.update(task, completed=len(ready_nodes))
                                    console.print(
                                        f"[green]✓ Node {node_name} is ready[/green]"
                                    )
                        except Exception:
                            pass

                if len(ready_nodes) < len(node_names):
                    await asyncio.sleep(2)

        if len(ready_nodes) == len(node_names):
            console.print("[green]✓ All nodes are ready[/green]")
            return True
        else:
            missing_nodes = set(node_names) - ready_nodes
            console.print(f"[red]❌ Nodes not ready: {', '.join(missing_nodes)}[/red]")
            return False

    def _get_valid_node_names(self) -> set[str]:
        """Get the set of valid node names based on nodes configuration."""
        nodes_config = self.config.get("nodes", {})

        if not nodes_config:
            return set()

        if isinstance(nodes_config, dict) and "count" in nodes_config:
            count = nodes_config["count"]
            if isinstance(count, int) and count >= 0:
                prefix = nodes_config.get("prefix", "calimero-node")
                return {f"{prefix}-{i+1}" for i in range(count)}
            else:
                return set()
        else:
            if isinstance(nodes_config, dict):
                # Filter out reserved configuration keys
                return {
                    k for k in nodes_config.keys() if k not in RESERVED_NODE_CONFIG_KEYS
                }
            elif isinstance(nodes_config, list):
                return set(nodes_config)
            else:
                return set()

    def _extract_node_references_from_step(self, step: dict[str, Any]) -> set[str]:
        """Extract all node references from a step, including nested steps."""
        node_refs = set()

        if "node" in step:
            node_value = step["node"]
            if (
                isinstance(node_value, str)
                and "{{" not in node_value
                and "}}" not in node_value
            ):
                node_refs.add(node_value)

        if step.get("type") == "repeat":
            for nested_step in step.get("steps") or []:
                node_refs.update(self._extract_node_references_from_step(nested_step))
        elif step.get("type") == "parallel":
            for group in step.get("groups") or []:
                for nested_step in group.get("steps") or []:
                    node_refs.update(
                        self._extract_node_references_from_step(nested_step)
                    )

        return node_refs

    def _validate_node_references(self) -> bool:
        """Validate that all node references in workflow steps exist."""
        steps = self.config.get("steps", [])

        if not steps:
            return True

        valid_nodes = self._get_valid_node_names()

        referenced_nodes = set()
        for step in steps:
            referenced_nodes.update(self._extract_node_references_from_step(step))

        if not valid_nodes:
            if referenced_nodes:
                console.print(
                    f"[red]❌ Workflow references nodes but no nodes are configured: {', '.join(sorted(referenced_nodes))}[/red]"
                )
                return False
            return True

        invalid_nodes = referenced_nodes - valid_nodes

        if invalid_nodes:
            console.print(
                f"[red]❌ Workflow references non-existent nodes: {', '.join(sorted(invalid_nodes))}[/red]"
            )
            console.print(
                f"[yellow]Valid nodes based on configuration: {', '.join(sorted(valid_nodes))}[/yellow]"
            )
            nodes_config = self.config.get("nodes", {})
            if "count" in nodes_config:
                count = nodes_config["count"]
                prefix = nodes_config.get("prefix", "calimero-node")
                console.print(
                    f"[yellow]Configuration specifies count={count} with prefix='{prefix}', "
                    f"so only nodes {prefix}-1 through {prefix}-{count} exist[/yellow]"
                )
            return False

        return True

    async def _execute_workflow_steps(self) -> bool:
        """Execute the configured workflow steps."""
        steps = self.config.get("steps", [])

        if not steps:
            console.print("[yellow]No workflow steps configured[/yellow]")
            return True

        if not self._validate_node_references():
            return False

        for i, step in enumerate(steps, 1):
            step_type = step.get("type")
            step_name = step.get("name", f"Step {i}")

            console.print(
                f"\n[bold cyan]Executing {step_name} ({step_type})...[/bold cyan]"
            )

            try:
                # Create appropriate step executor
                step_executor = self._create_step_executor(step_type, step)
                if not step_executor:
                    console.print(f"[red]Unknown step type: {step_type}[/red]")
                    return False

                # Execute the step
                success = await step_executor.execute(
                    self.workflow_results, self.dynamic_values
                )

                if not success:
                    console.print(f"[red]❌ Step '{step_name}' failed[/red]")
                    return False

                console.print(f"[green]✓ Step '{step_name}' completed[/green]")

            except Exception as e:
                console.print(
                    f"[red]❌ Step '{step_name}' failed with error: {str(e)}[/red]"
                )
                return False

        return True

    def _create_step_executor(self, step_type: str, step_config: dict[str, Any]):
        """Create a step executor based on the step type."""
        if step_type == "install_application":
            from merobox.commands.bootstrap.steps import InstallApplicationStep

            return InstallApplicationStep(step_config, manager=self.manager)
        elif step_type == "create_context":
            from merobox.commands.bootstrap.steps import CreateContextStep

            return CreateContextStep(step_config, manager=self.manager)
        elif step_type == "create_identity":
            from merobox.commands.bootstrap.steps import CreateIdentityStep

            return CreateIdentityStep(step_config, manager=self.manager)
        elif step_type == "invite_identity":
            from merobox.commands.bootstrap.steps import InviteIdentityStep

            return InviteIdentityStep(step_config, manager=self.manager)
        elif step_type == "join_context":
            from merobox.commands.bootstrap.steps import JoinContextStep

            return JoinContextStep(step_config, manager=self.manager)
        elif step_type == "invite_open":
            from merobox.commands.bootstrap.steps import InviteOpenStep

            return InviteOpenStep(step_config, manager=self.manager)
        elif step_type == "join_open":
            from merobox.commands.bootstrap.steps import JoinOpenStep

            return JoinOpenStep(step_config, manager=self.manager)
        elif step_type == "call":
            from merobox.commands.bootstrap.steps import ExecuteStep

            return ExecuteStep(step_config, manager=self.manager)
        elif step_type == "wait":
            from merobox.commands.bootstrap.steps import WaitStep

            return WaitStep(step_config, manager=self.manager)
        elif step_type == "wait_for_sync":
            from merobox.commands.bootstrap.steps import WaitForSyncStep

            return WaitForSyncStep(step_config, manager=self.manager)
        elif step_type == "repeat":
            from merobox.commands.bootstrap.steps import RepeatStep

            return RepeatStep(step_config, manager=self.manager)
        elif step_type == "parallel":
            from merobox.commands.bootstrap.steps import ParallelStep

            return ParallelStep(step_config, manager=self.manager)
        elif step_type == "script":
            from merobox.commands.bootstrap.steps import ScriptStep

            return ScriptStep(step_config, manager=self.manager)
        elif step_type == "assert":
            from merobox.commands.bootstrap.steps.assertion import AssertStep

            return AssertStep(step_config, manager=self.manager)
        elif step_type == "json_assert":
            from merobox.commands.bootstrap.steps.json_assertion import JsonAssertStep

            return JsonAssertStep(step_config)
        elif step_type == "get_proposal":
            from merobox.commands.bootstrap.steps.proposals import GetProposalStep

            return GetProposalStep(step_config, manager=self.manager)
        elif step_type == "list_proposals":
            from merobox.commands.bootstrap.steps.proposals import ListProposalsStep

            return ListProposalsStep(step_config, manager=self.manager)
        elif step_type == "get_proposal_approvers":
            from merobox.commands.bootstrap.steps.proposals import (
                GetProposalApproversStep,
            )

            return GetProposalApproversStep(step_config, manager=self.manager)
        elif step_type == "upload_blob":
            from merobox.commands.bootstrap.steps import UploadBlobStep

            return UploadBlobStep(step_config, manager=self.manager)
        elif step_type == "create_mesh":
            from merobox.commands.bootstrap.steps.mesh import CreateMeshStep

            return CreateMeshStep(step_config, manager=self.manager)
        elif step_type == "fuzzy_test":
            from merobox.commands.bootstrap.steps.fuzzy_test import FuzzyTestStep

            return FuzzyTestStep(step_config, manager=self.manager)
        else:
            console.print(f"[red]Unknown step type: {step_type}[/red]")
            return None
