"""
Start node step executor - Start nodes during workflow execution.
"""

import asyncio
import socket
import time
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class StartNodeStep(BaseStep):
    """Start nodes during workflow execution."""

    def __init__(
        self,
        config: dict[str, Any],
        manager: object | None = None,
        resolver: object | None = None,
        auth_mode: str | None = None,
        workflow_config: dict[str, Any] | None = None,
        executor: object | None = None,
    ):
        super().__init__(config, manager, resolver, auth_mode=auth_mode)
        self.workflow_config = workflow_config or {}
        self.executor = executor  # Reference to executor for accessing node startup logic

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["nodes"]  # At least one node must be specified

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate nodes is a list or string
        nodes = self.config.get("nodes")
        if not isinstance(nodes, (list, str)):
            raise ValueError(
                f"Step '{step_name}': 'nodes' must be a list of node names or a single node name string"
            )

        # Validate wait_for_ready is a boolean if provided
        if "wait_for_ready" in self.config and not isinstance(
            self.config["wait_for_ready"], bool
        ):
            raise ValueError(
                f"Step '{step_name}': 'wait_for_ready' must be a boolean"
            )

        # Validate wait_timeout is an integer if provided
        if "wait_timeout" in self.config and not isinstance(
            self.config["wait_timeout"], int
        ):
            raise ValueError(
                f"Step '{step_name}': 'wait_timeout' must be an integer"
            )

        # Validate wait_timeout is positive if provided
        if "wait_timeout" in self.config and self.config["wait_timeout"] <= 0:
            raise ValueError(
                f"Step '{step_name}': 'wait_timeout' must be positive"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        """
        Execute the start node step.

        Args:
            workflow_results: Results from previous workflow steps
            dynamic_values: Dynamic values captured during workflow execution

        Returns:
            True if successful, False otherwise
        """
        if not self.manager:
            console.print(
                "[red]❌ Cannot start nodes: no manager available (remote-only mode)[/red]"
            )
            return False

        # Get node names (can be a single string or list)
        nodes_config = self.config.get("nodes")
        if isinstance(nodes_config, str):
            # Resolve dynamic values in node name
            node_names = [
                self._resolve_dynamic_value(nodes_config, workflow_results, dynamic_values)
            ]
        else:
            # Resolve dynamic values in each node name
            node_names = [
                self._resolve_dynamic_value(node, workflow_results, dynamic_values)
                for node in nodes_config
            ]

        console.print(
            f"[yellow]🚀 Starting {len(node_names)} node(s): {', '.join(node_names)}[/yellow]"
        )

        # Get node configuration from workflow config
        workflow_nodes_config = self.workflow_config.get("nodes", {})

        started_nodes = []
        failed_to_start = []

        for node_name in node_names:
            # Try to get node-specific config
            node_config = None
            if isinstance(workflow_nodes_config, dict):
                # Check if this is a node-specific config entry
                if node_name in workflow_nodes_config:
                    node_config = workflow_nodes_config[node_name]
                # Otherwise, use the base config for count-based nodes
                elif "count" in workflow_nodes_config:
                    # Extract index from node name (e.g., "calimero-node-1" -> 1)
                    try:
                        prefix = workflow_nodes_config.get("prefix", "calimero-node")
                        if node_name.startswith(prefix):
                            index = int(node_name.split("-")[-1]) - 1
                            base_port = workflow_nodes_config.get("base_port", 2428)
                            base_rpc_port = workflow_nodes_config.get("base_rpc_port", 2528)
                            node_config = {
                                "port": base_port + index if base_port else None,
                                "rpc_port": base_rpc_port + index if base_rpc_port else None,
                                "chain_id": workflow_nodes_config.get("chain_id", "testnet-1"),
                            }
                    except (ValueError, IndexError):
                        pass

            # Use executor's _start_single_node method if available
            if self.executor and hasattr(self.executor, "_start_single_node"):
                # Use executor's method which has access to all node config
                success = await self.executor._start_single_node(
                    node_name, node_config, workflow_nodes_config
                )
            else:
                console.print(
                    f"[red]❌ Cannot start node {node_name}: executor not available[/red]"
                )
                success = False

            if success:
                started_nodes.append(node_name)
            else:
                failed_to_start.append(node_name)

        if failed_to_start:
            console.print(
                f"[red]❌ Failed to start nodes: {', '.join(failed_to_start)}[/red]"
            )
            return False

        console.print(f"[green]✓ Started {len(started_nodes)} node(s)[/green]")

        # Wait for nodes to be ready (optional)
        wait_for_ready = self.config.get("wait_for_ready", True)
        if wait_for_ready:
            wait_timeout = self.config.get("wait_timeout", 30)
            console.print(
                f"[cyan]Waiting up to {wait_timeout} seconds for nodes to be ready...[/cyan]"
            )

            start_time = time.time()
            all_ready = False

            while time.time() - start_time < wait_timeout:
                ready_count = 0
                for node_name in started_nodes:
                    # Try to get RPC port from manager
                    rpc_port = None
                    if hasattr(self.manager, "get_node_rpc_port"):
                        rpc_port = self.manager.get_node_rpc_port(node_name)
                    elif hasattr(self.manager, "node_rpc_ports"):
                        rpc_port = self.manager.node_rpc_ports.get(node_name)

                    if rpc_port:
                        try:
                            with socket.create_connection(
                                ("127.0.0.1", rpc_port), timeout=1
                            ):
                                ready_count += 1
                        except Exception:
                            pass

                if ready_count == len(started_nodes):
                    all_ready = True
                    break

                await asyncio.sleep(1)

            if all_ready:
                console.print("[green]✓ All nodes are ready[/green]")
            else:
                console.print(
                    "[yellow]⚠ Some nodes may not be ready yet, continuing...[/yellow]"
                )

        return True
