"""
Join namespace step executor (invitation-based).

Unified step that handles invitation-based namespace joining.
Step types 'join' and 'join_open' route here as deprecated aliases.
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.join import join_namespace_via_admin_api
from merobox.commands.utils import console


class JoinNamespaceStep(BaseStep):
    """Execute a join step via namespace invitation."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "invitation"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        if not isinstance(self.config.get("invitation"), str):
            raise ValueError(f"Step '{step_name}': 'invitation' must be a string")

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from join_namespace API response:
        - namespaceId: ID of the namespace joined
        - memberIdentity: Member identity public key after joining
        """
        return [
            (
                "namespaceId",
                "join_namespace_id_{node_name}",
                "ID of the namespace joined",
            ),
            (
                "memberIdentity",
                "join_member_identity_{node_name}",
                "Member identity after joining the namespace",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        namespace_id = self._resolve_dynamic_value(
            self.config.get("namespace_id", self.config.get("group_id")),
            workflow_results,
            dynamic_values,
        )
        invitation = self._resolve_dynamic_value(
            self.config["invitation"], workflow_results, dynamic_values
        )

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Join step export configuration validation failed[/yellow]"
            )

        # Normalize invitation to JSON string
        if isinstance(invitation, dict):
            invitation_json = json_lib.dumps(invitation)
        elif isinstance(invitation, str):
            try:
                json_lib.loads(invitation)
                invitation_json = invitation
            except json_lib.JSONDecodeError:
                console.print(
                    f"[red]Join step on {node_name}: 'invitation' is not valid JSON[/red]"
                )
                return False
        else:
            console.print(
                f"[red]Join step on {node_name}: 'invitation' must be a dict or JSON string[/red]"
            )
            return False

        console.print("[blue]Debug: Resolved values for join step:[/blue]")
        console.print(f"  namespace_id: {namespace_id}")
        console.print(
            f"  invitation: {invitation_json[:80] if len(invitation_json) > 80 else invitation_json}"
        )
        console.print(f"  invitation type: {type(invitation)}")

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        console.print("[blue]About to call join_namespace function...[/blue]")
        result = await join_namespace_via_admin_api(
            rpc_url,
            namespace_id,
            invitation_json,
            node_name=client_node_name,
        )
        console.print(f"[blue]Join namespace function returned: {result}[/blue]")

        console.print(f"[cyan]🔍 Join Namespace API Response for {node_name}:[/cyan]")
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

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False

            step_key = f"join_namespace_{node_name}"
            workflow_results[step_key] = result["data"]

            self._export_variables(result["data"], node_name, dynamic_values)

            return True
        else:
            console.print(
                f"[red]Join namespace failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False


# Deprecated aliases kept for backward compatibility.
JoinContextStep = JoinNamespaceStep
JoinInvitationStep = JoinNamespaceStep
