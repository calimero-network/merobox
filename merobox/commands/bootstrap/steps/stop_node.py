"""
Stop node step executor - Stop nodes during workflow execution.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class StopNodeStep(BaseStep):
    """Stop nodes during workflow execution."""

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

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        """
        Execute the stop node step.

        Args:
            workflow_results: Results from previous workflow steps
            dynamic_values: Dynamic values captured during workflow execution

        Returns:
            True if successful, False otherwise
        """
        if not self.manager:
            console.print(
                "[red]❌ Cannot stop nodes: no manager available (remote-only mode)[/red]"
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
            f"[yellow]🛑 Stopping {len(node_names)} node(s): {', '.join(node_names)}[/yellow]"
        )

        stopped_nodes = []
        failed_to_stop = []

        for node_name in node_names:
            if hasattr(self.manager, "stop_node"):
                if self.manager.stop_node(node_name):
                    stopped_nodes.append(node_name)
                else:
                    failed_to_stop.append(node_name)
            else:
                console.print(
                    f"[yellow]⚠ Manager does not support stop_node for {node_name}[/yellow]"
                )
                failed_to_stop.append(node_name)

        if failed_to_stop:
            console.print(
                f"[red]❌ Failed to stop nodes: {', '.join(failed_to_stop)}[/red]"
            )
            return False

        console.print(f"[green]✓ Stopped {len(stopped_nodes)} node(s)[/green]")
        return True
