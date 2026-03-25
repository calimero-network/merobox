"""
Create group step executor.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class CreateGroupStep(BaseStep):
    """Execute a create group step."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "application_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        if not isinstance(self.config.get("application_id"), str):
            raise ValueError(f"Step '{step_name}': 'application_id' must be a string")

    def _get_exportable_variables(self):
        return [
            (
                "groupId",
                "group_id_{node_name}",
                "Group ID - primary identifier for the created group",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        application_id = self._resolve_dynamic_value(
            self.config["application_id"], workflow_results, dynamic_values
        )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        try:
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.create_group(application_id=application_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("create_group failed", error=e)

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False

            step_key = f"group_{node_name}"
            workflow_results[step_key] = result["data"]
            self._export_variables(result["data"], node_name, dynamic_values)

            # Fallback: ensure group_id is captured
            if f"group_id_{node_name}" not in dynamic_values:
                if isinstance(result["data"], dict):
                    nested = result["data"].get("data", result["data"])
                    group_id = (
                        nested.get("groupId") if isinstance(nested, dict) else None
                    )
                    if group_id:
                        dynamic_values[f"group_id_{node_name}"] = group_id
                        console.print(
                            f"[blue]Captured group ID for {node_name}: {group_id}[/blue]"
                        )

            console.print(
                f"[green]✓ Group created on {node_name}: "
                f"{dynamic_values.get(f'group_id_{node_name}', 'unknown')}[/green]"
            )
            return True
        else:
            console.print(
                f"[red]Group creation failed on {node_name}: "
                f"{result.get('error', 'Unknown error')}[/red]"
            )
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
