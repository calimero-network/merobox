"""
Start node step executor - Start nodes during workflow execution.
"""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.health import check_node_health
from merobox.commands.utils import console

# Coroutine that starts one node: (node_name, node_config, nodes_config) -> bool
StartNodeFn = Callable[[str, Optional[dict], Optional[dict]], Awaitable[bool]]


class StartNodeStep(BaseStep):
    """Start nodes during workflow execution."""

    def __init__(
        self,
        config: dict[str, Any],
        manager: object | None = None,
        resolver: object | None = None,
        auth_mode: str | None = None,
        workflow_config: dict[str, Any] | None = None,
        start_node_fn: Optional[StartNodeFn] = None,
    ):
        super().__init__(config, manager, resolver, auth_mode=auth_mode)
        self.workflow_config = workflow_config or {}
        # Callable that actually starts a node (the executor's node-startup
        # logic); kept as a plain callable so the step doesn't hold a back-
        # reference to the whole executor.
        self.start_node_fn = start_node_fn

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
            raise ValueError(f"Step '{step_name}': 'wait_for_ready' must be a boolean")

        # Validate wait_timeout is an integer if provided (bool is an int
        # subclass in Python, so reject it explicitly).
        if "wait_timeout" in self.config:
            wait_timeout = self.config["wait_timeout"]
            if isinstance(wait_timeout, bool) or not isinstance(wait_timeout, int):
                raise ValueError(
                    f"Step '{step_name}': 'wait_timeout' must be an integer"
                )

        # Validate wait_timeout is positive if provided
        if "wait_timeout" in self.config and self.config["wait_timeout"] <= 0:
            raise ValueError(f"Step '{step_name}': 'wait_timeout' must be positive")

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
                self._resolve_dynamic_value(
                    nodes_config, workflow_results, dynamic_values
                )
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

        # No node-start function -> nothing we can do; fail fast (it would be
        # None for every node anyway).
        if self.start_node_fn is None:
            console.print(
                "[red]❌ Cannot start nodes: no node-start function available[/red]"
            )
            return False

        # Get node configuration from workflow config
        workflow_nodes_config = self.workflow_config.get("nodes", {})

        started_nodes = []
        failed_to_start = []

        for node_name in node_names:
            # Node-specific config only exists for individually-defined nodes;
            # for count-based nodes pass None and let the start fn resolve it.
            node_config = (
                workflow_nodes_config.get(node_name)
                if isinstance(workflow_nodes_config, dict)
                else None
            )
            success = await self.start_node_fn(
                node_name, node_config, workflow_nodes_config
            )
            (started_nodes if success else failed_to_start).append(node_name)

        if failed_to_start:
            console.print(
                f"[red]❌ Failed to start nodes: {', '.join(failed_to_start)}[/red]"
            )
            return False

        console.print(f"[green]✓ Started {len(started_nodes)} node(s)[/green]")

        # wait_for_ready defaults to True: callers expect a started node to be
        # serving requests before the workflow continues, so a readiness
        # timeout fails the step. Set it to false to return as soon as the
        # node process/container has been launched.
        if self.config.get("wait_for_ready", True):
            return await self._wait_for_ready(
                started_nodes, self.config.get("wait_timeout", 30)
            )
        return True

    async def _wait_for_ready(self, node_names: list[str], timeout: int) -> bool:
        """Wait for each node to accept RPC connections and pass a health check.

        Returns True if every node became ready (or if no RPC port could be
        resolved for any node, in which case the check is skipped). Returns
        False if the deadline passed with nodes still not responding.
        """
        console.print(
            f"[cyan]Waiting up to {timeout} seconds for nodes to be ready...[/cyan]"
        )

        node_ports: dict[str, int] = {}
        for node_name in node_names:
            rpc_port = None
            if hasattr(self.manager, "get_node_rpc_port"):
                rpc_port = self.manager.get_node_rpc_port(node_name)
            elif hasattr(self.manager, "node_rpc_ports"):
                rpc_port = self.manager.node_rpc_ports.get(node_name)
            if rpc_port:
                node_ports[node_name] = rpc_port

        if not node_ports:
            console.print(
                "[yellow]⚠ Could not determine RPC ports for the started "
                "node(s); skipping readiness check.[/yellow]"
            )
            return True

        deadline = time.monotonic() + timeout
        pending = set(node_ports)
        while pending and time.monotonic() < deadline:
            for node_name in list(pending):
                if await self._node_ready(node_ports[node_name]):
                    pending.discard(node_name)
                    console.print(f"[green]✓ Node {node_name} is ready[/green]")
            if pending:
                await asyncio.sleep(1)

        if pending:
            console.print(
                f"[red]❌ Node(s) not ready within {timeout}s: "
                f"{', '.join(sorted(pending))}[/red]"
            )
            return False
        console.print("[green]✓ All nodes are ready[/green]")
        return True

    async def _node_ready(self, rpc_port: int) -> bool:
        """A node is ready once its RPC port accepts a connection *and* the
        admin health endpoint responds (a bound port alone doesn't mean the
        node is serving requests)."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", rpc_port), timeout=1
            )
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            return False
        try:
            result = await check_node_health(f"http://localhost:{rpc_port}")
            return bool(result.get("success"))
        except Exception:
            return False
