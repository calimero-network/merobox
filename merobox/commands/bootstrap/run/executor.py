"""
Main workflow executor - Orchestrates workflow execution and manages the overall process.
"""

import asyncio
import time
from typing import Any, Optional

import docker
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from merobox.commands.manager import CalimeroManager
from merobox.commands.utils import console


class WorkflowExecutor:
    """Executes Calimero workflows based on YAML configuration."""

    def __init__(
        self,
        config: dict[str, Any],
        manager: CalimeroManager,
        image: Optional[str] = None,
        auth_service: bool = False,
        auth_image: str = None,
        auth_use_cached: bool = False,
        webui_use_cached: bool = False,
        log_level: str = "debug",
    ):
        self.config = config
        self.manager = manager
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
        # If CLI provided a value (including complex RUST_LOG patterns), use it; otherwise fall back to config
        self.log_level = log_level if log_level is not None else config.get("log_level", "debug")
        try:
            console.print(f"[cyan]WorkflowExecutor: resolved log_level='{self.log_level}'[/cyan]")
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
            # Check if we should force pull images
            force_pull_images = self.config.get("force_pull_image", False)
            if force_pull_images:
                console.print(
                    "\n[bold yellow]🔄 Force pulling workflow images (force_pull_image=true)...[/bold yellow]"
                )
                await self._force_pull_workflow_images()
                console.print("[green]✓ Image force pull completed[/green]")

            # Check if we should restart nodes at the beginning
            restart_nodes = self.config.get("restart", False)
            stop_all_nodes = self.config.get("stop_all_nodes", False)

            # Step 1: Restart nodes if requested (at beginning)
            if restart_nodes:
                console.print(
                    "\n[bold yellow]Step 1: Restarting workflow nodes (restart=true)...[/bold yellow]"
                )
                if not self.manager.stop_all_nodes():
                    console.print(
                        "[red]❌ Failed to stop workflow nodes - stopping workflow[/red]"
                    )
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
                return False

            # Step 3: Wait for nodes to be ready
            console.print(
                "\n[bold yellow]Step 3: Waiting for nodes to be ready...[/bold yellow]"
            )
            if not await self._wait_for_nodes_ready():
                console.print("[red]❌ Nodes not ready - stopping workflow[/red]")
                return False

            # Step 4: Execute workflow steps
            console.print(
                "\n[bold yellow]Step 4: Executing workflow steps...[/bold yellow]"
            )
            if not await self._execute_workflow_steps():
                console.print("[red]❌ Workflow steps failed - stopping workflow[/red]")
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
            return False

    async def _force_pull_workflow_images(self) -> None:
        """Force pull all Docker images specified in the workflow configuration."""
        try:
            # Get image from nodes configuration
            nodes_config = self.config.get("nodes", {})
            if isinstance(nodes_config, dict):
                image = nodes_config.get("image")
                if image:
                    console.print(
                        f"[yellow]Force pulling workflow image: {image}[/yellow]"
                    )
                    if not self.manager.force_pull_image(image):
                        console.print(
                            f"[red]Warning: Failed to force pull image: {image}[/red]"
                        )
                        console.print(
                            "[yellow]Workflow will continue with existing image[/yellow]"
                        )

                # Check for images in individual node configurations
                for node_name, node_config in nodes_config.items():
                    if isinstance(node_config, dict) and "image" in node_config:
                        image = node_config["image"]
                        console.print(
                            f"[yellow]Force pulling image for node {node_name}: {image}[/yellow]"
                        )
                        if not self.manager.force_pull_image(image):
                            console.print(
                                f"[red]Warning: Failed to force pull image for {node_name}: {image}[/red]"
                            )
                            console.print(
                                "[yellow]Workflow will continue with existing image[/yellow]"
                            )

        except Exception as e:
            console.print(f"[red]Error during force pull: {str(e)}[/red]")
            console.print(
                "[yellow]Workflow will continue with existing images[/yellow]"
            )

    async def _start_nodes(self, restart: bool) -> bool:
        """Start the configured nodes."""
        nodes_config = self.config.get("nodes", {})

        if not nodes_config:
            console.print("[red]No nodes configuration found[/red]")
            return False

        # Get base ports from configuration (available for all node types)
        base_port = nodes_config.get("base_port", 2428)
        base_rpc_port = nodes_config.get("base_rpc_port", 2528)
        chain_id = nodes_config.get("chain_id", "testnet-1")
        image = self.image if self.image is not None else nodes_config.get("image")
        prefix = nodes_config.get("prefix", "calimero-node")

        # Handle multiple nodes
        if "count" in nodes_config:
            count = nodes_config["count"]

            if restart:
                console.print(
                    f"Starting {count} nodes with prefix '{prefix}' (restart mode)..."
                )
                if not self.manager.run_multiple_nodes(
                    count,
                    base_port,
                    base_rpc_port,
                    chain_id,
                    prefix,
                    image,
                    self.auth_service,
                    self.auth_image,
                    self.auth_use_cached,
                    self.webui_use_cached,
                    self.log_level,
                ):
                    return False
            else:
                console.print(
                    f"Checking {count} nodes with prefix '{prefix}' (no restart mode)..."
                )
                # Check if nodes are already running
                running_nodes = 0
                for i in range(count):
                    node_name = f"{prefix}-{i+1}"
                    try:
                        existing_container = self.manager.client.containers.get(
                            node_name
                        )
                        if existing_container.status == "running":
                            console.print(
                                f"[green]✓ Node '{node_name}' is already running[/green]"
                            )
                            running_nodes += 1
                        else:
                            console.print(
                                f"[yellow]Node '{node_name}' exists but not running, starting...[/yellow]"
                            )
                            # Start the specific node
                            if not self.manager.run_node(
                                node_name,
                                base_port + i,
                                base_rpc_port + i,
                                chain_id,
                                None,
                                image,
                                self.auth_service,
                                self.auth_image,
                                self.auth_use_cached,
                                self.webui_use_cached,
                                self.log_level,
                            ):
                                return False
                    except docker.errors.NotFound:
                        console.print(
                            f"[cyan]Node '{node_name}' doesn't exist, creating...[/cyan]"
                        )
                        if not self.manager.run_node(
                            node_name,
                            base_port + i,
                            base_rpc_port + i,
                            chain_id,
                            None,
                            image,
                            self.auth_service,
                            self.auth_image,
                            self.auth_use_cached,
                            self.webui_use_cached,
                            self.log_level,
                        ):
                            return False

                if running_nodes == count:
                    console.print(
                        f"[green]✓ All {count} nodes are already running[/green]"
                    )
                elif running_nodes > 0:
                    console.print(
                        f"[green]✓ {running_nodes}/{count} nodes were already running, {count - running_nodes} started[/green]"
                    )
                else:
                    console.print(f"[green]✓ All {count} nodes started[/green]")
        else:
            # Handle individual node configurations
            for node_name, node_config in nodes_config.items():
                if isinstance(node_config, dict):
                    # Check if node already exists and is running
                    try:
                        existing_container = self.manager.client.containers.get(
                            node_name
                        )
                        if existing_container.status == "running":
                            if restart:
                                console.print(
                                    f"[yellow]Node '{node_name}' is running but restart requested, stopping...[/yellow]"
                                )
                                existing_container.stop()
                                existing_container.remove()
                                # Start fresh
                                port = node_config.get("port", base_port)
                                rpc_port = node_config.get("rpc_port", base_rpc_port)
                                node_chain_id = node_config.get("chain_id", chain_id)
                                node_image = node_config.get("image", image)
                                data_dir = node_config.get("data_dir")

                                console.print(f"Starting node '{node_name}'...")
                                if not self.manager.run_node(
                                    node_name,
                                    port,
                                    rpc_port,
                                    node_chain_id,
                                    data_dir,
                                    node_image,
                                    self.auth_service,
                                    self.auth_image,
                                    self.auth_use_cached,
                                    self.webui_use_cached,
                                ):
                                    return False
                            else:
                                console.print(
                                    f"[green]✓ Node '{node_name}' is already running[/green]"
                                )
                                continue
                        else:
                            console.print(
                                f"[yellow]Node '{node_name}' exists but not running, attempting to start...[/yellow]"
                            )
                    except docker.errors.NotFound:
                        # Node doesn't exist, create it
                        port = node_config.get("port", base_port)
                        rpc_port = node_config.get("rpc_port", base_rpc_port)
                        node_chain_id = node_config.get("chain_id", chain_id)
                        node_image = (
                            self.image
                            if self.image is not None
                            else node_config.get("image", image)
                        )
                        data_dir = node_config.get("data_dir")

                        console.print(f"Starting node '{node_name}'...")
                        if not self.manager.run_node(
                            node_name,
                            port,
                            rpc_port,
                            node_chain_id,
                            data_dir,
                            node_image,
                            self.auth_service,
                            self.auth_image,
                            self.auth_use_cached,
                            self.webui_use_cached,
                        ):
                            return False
                else:
                    # Simple string configuration (just node name)
                    # Check if node already exists and is running
                    try:
                        existing_container = self.manager.client.containers.get(
                            node_config
                        )
                        if existing_container.status == "running":
                            if restart:
                                console.print(
                                    f"[yellow]Node '{node_config}' is running but restart requested, stopping...[/yellow]"
                                )
                                existing_container.stop()
                                existing_container.remove()
                                # Start fresh
                                console.print(f"Starting node '{node_config}'...")
                                if not self.manager.run_node(
                                    node_config,
                                    base_port,
                                    base_rpc_port,
                                    chain_id,
                                    None,
                                    image,
                                    self.auth_service,
                                    self.auth_image,
                                    self.auth_use_cached,
                                    self.webui_use_cached,
                                ):
                                    return False
                            else:
                                console.print(
                                    f"[green]✓ Node '{node_config}' is already running[/green]"
                                )
                                continue
                        else:
                            console.print(
                                f"[yellow]Node '{node_config}' exists but not running, attempting to start...[/yellow]"
                            )
                    except docker.errors.NotFound:
                        # Node doesn't exist, create it
                        console.print(f"Starting node '{node_config}'...")
                        if not self.manager.run_node(
                            node_config,
                            base_port,
                            base_rpc_port,
                            chain_id,
                            None,
                            image,
                            self.auth_service,
                            self.auth_image,
                            self.auth_use_cached,
                            self.webui_use_cached,
                        ):
                            return False

        console.print("[green]✓ Node management completed[/green]")
        return True

    async def _wait_for_nodes_ready(self) -> bool:
        """Wait for all nodes to be ready and accessible."""
        nodes_config = self.config.get("nodes", {})
        wait_timeout = self.config.get("wait_timeout", 60)  # Default 60 seconds

        if "count" in nodes_config:
            count = nodes_config["count"]
            prefix = nodes_config.get("prefix", "calimero-node")
            node_names = [f"{prefix}-{i+1}" for i in range(count)]
        else:
            node_names = (
                list(nodes_config.keys())
                if isinstance(nodes_config, dict)
                else list(nodes_config)
            )

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
                            # Check if node is running
                            container = self.manager.client.containers.get(node_name)
                            if container.status == "running":
                                # Try to verify admin binding
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

    async def _execute_workflow_steps(self) -> bool:
        """Execute the configured workflow steps."""
        steps = self.config.get("steps", [])

        if not steps:
            console.print("[yellow]No workflow steps configured[/yellow]")
            return True

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

            return InstallApplicationStep(step_config)
        elif step_type == "create_context":
            from merobox.commands.bootstrap.steps import CreateContextStep

            return CreateContextStep(step_config)
        elif step_type == "create_identity":
            from merobox.commands.bootstrap.steps import CreateIdentityStep

            return CreateIdentityStep(step_config)
        elif step_type == "invite_identity":
            from merobox.commands.bootstrap.steps import InviteIdentityStep

            return InviteIdentityStep(step_config)
        elif step_type == "join_context":
            from merobox.commands.bootstrap.steps import JoinContextStep

            return JoinContextStep(step_config)
        elif step_type == "call":
            from merobox.commands.bootstrap.steps import ExecuteStep

            return ExecuteStep(step_config)
        elif step_type == "wait":
            from merobox.commands.bootstrap.steps import WaitStep

            return WaitStep(step_config)
        elif step_type == "repeat":
            from merobox.commands.bootstrap.steps import RepeatStep

            return RepeatStep(step_config)
        elif step_type == "script":
            from merobox.commands.bootstrap.steps import ScriptStep

            return ScriptStep(step_config)
        elif step_type == "assert":
            from merobox.commands.bootstrap.steps.assertion import AssertStep

            return AssertStep(step_config)
        elif step_type == "json_assert":
            from merobox.commands.bootstrap.steps.json_assertion import JsonAssertStep

            return JsonAssertStep(step_config)
        else:
            console.print(f"[red]Unknown step type: {step_type}[/red]")
            return None
