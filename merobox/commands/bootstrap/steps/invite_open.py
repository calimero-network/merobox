"""
Invite step executor - Create namespace invitations.

Unified step that handles invitation creation via namespaces.
Step types 'invite', 'invite_open', and 'invite_identity' all route here.
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.identity import create_namespace_invitation_via_admin_api
from merobox.commands.utils import console


class InviteOpenStep(BaseStep):
    """Execute an invite step using namespace invitations."""

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        namespace_id = self.config.get("namespace_id", self.config.get("group_id"))
        if not isinstance(namespace_id, str):
            raise ValueError(
                f"Step '{step_name}': 'namespace_id' (or deprecated 'group_id') must be a string"
            )

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from namespace invitation API response:
        - invitation: Signed namespace invitation data (JSON object)
        """
        return [
            (
                "invitation",
                "namespace_invitation_{node_name}",
                "Signed namespace invitation data",
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
            f"[blue]Creating namespace invitation for namespace {namespace_id} on {node_name}...[/blue]"
        )
        result = await create_namespace_invitation_via_admin_api(
            rpc_url,
            namespace_id,
            recursive=bool(self.config.get("recursive", False)),
            node_name=client_node_name,
        )

        console.print(f"[cyan]🔍 Namespace Invitation API Response for {node_name}:[/cyan]")
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
                f"[red]Namespace invitation creation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False

            step_key = f"invite_{node_name}_{namespace_id}"
            workflow_results[step_key] = result["data"]

            # The response contains the full invitation.
            # Export it so join_namespace steps can reference it.
            actual_data = result["data"].get("data", result["data"])
            synthetic_response = {"invitation": actual_data}
            self._export_variables(synthetic_response, node_name, dynamic_values)

            return True
        else:
            console.print(
                f"[red]Namespace invitation creation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False
