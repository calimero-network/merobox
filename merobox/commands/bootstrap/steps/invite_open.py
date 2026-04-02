"""
Invite step executor - Create group invitations.

Unified step that handles invitation creation via groups.
Step types 'invite', 'invite_open', and 'invite_identity' all route here.

The old context-based invitation flow (POST /contexts/invite) has been replaced
by group-based invitations (POST /groups/:id/invite).
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.identity import create_group_invitation_via_admin_api
from merobox.commands.utils import console


class InviteOpenStep(BaseStep):
    """Execute an invite step using group invitations.

    Accepts either 'group_id' (new flow) or 'context_id' + 'granter_id' (legacy config).
    When legacy fields are provided, 'group_id' must also be present or resolvable.
    """

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
        """
        Define which variables this step can export.

        Available variables from group invitation API response:
        - invitation: Signed group invitation data (JSON object)
        """
        return [
            (
                "invitation",
                "group_invitation_{node_name}",
                "Signed group invitation data",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Invite step export configuration validation failed[/yellow]"
            )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        console.print(
            f"[blue]Creating group invitation for group {group_id} on {node_name}...[/blue]"
        )
        result = await create_group_invitation_via_admin_api(
            rpc_url,
            group_id,
            node_name=client_node_name,
        )

        console.print(f"[cyan]🔍 Group Invitation API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")

        data = result.get("data")
        if isinstance(data, dict):
            try:
                formatted_data = json_lib.dumps(data, indent=2)
                console.print(f"  Data:\n{formatted_data}")
            except Exception:
                console.print(f"  Data: {data}")
        else:
            console.print(f"  Data: {data}")

        if not result.get("success"):
            console.print(f"  Error: {result.get('error')}")
            console.print(
                f"[red]Group invitation creation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False

            step_key = f"invite_{node_name}_{group_id}"
            workflow_results[step_key] = result["data"]

            # The response contains the full SignedGroupOpenInvitation.
            # Export it so join_group steps can reference it.
            actual_data = result["data"].get("data", result["data"])
            synthetic_response = {"invitation": actual_data}
            self._export_variables(synthetic_response, node_name, dynamic_values)

            return True
        else:
            console.print(
                f"[red]Group invitation creation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False
