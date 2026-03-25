"""
Create group invitation step executor.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class CreateGroupInvitationStep(BaseStep):
    """Execute a create group invitation step."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        if not isinstance(self.config.get("group_id"), str):
            raise ValueError(f"Step '{step_name}': 'group_id' must be a string")

    def _get_exportable_variables(self):
        # The invitation object is stored as JSON string for use in join_group steps
        return [
            (
                "invitation",
                "group_invitation_{node_name}",
                "Invitation object (JSON) for joining the group",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        try:
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.create_group_invitation(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("create_group_invitation failed", error=e)

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False

            step_key = f"group_invitation_{node_name}"
            workflow_results[step_key] = result["data"]
            self._export_variables(result["data"], node_name, dynamic_values)

            # Fallback: extract and store the invitation object so
            # join_group steps can reference it via {{group_invitation_<node>}}
            raw_data = result["data"]
            if isinstance(raw_data, dict):
                nested = raw_data.get("data", raw_data)
                if isinstance(nested, dict):
                    # Store the full SignedGroupOpenInvitation (invitation + inviter_signature),
                    # NOT just the inner "invitation" field (GroupInvitationFromAdmin).
                    dynamic_values[f"group_invitation_{node_name}"] = nested
                    console.print(
                        f"[green]✓ Group invitation created on {node_name}[/green]"
                    )
                    return True

            console.print(
                f"[yellow]⚠️  Could not extract invitation from response on {node_name}[/yellow]"
            )
            return False
        else:
            console.print(
                f"[red]Group invitation creation failed on {node_name}: "
                f"{result.get('error', 'Unknown error')}[/red]"
            )
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
