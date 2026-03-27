"""
Invite Open step executor - Create open invitations for context.
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console


@with_retry(config=NETWORK_RETRY_CONFIG)
async def create_open_invitation_via_admin_api(
    rpc_url: str,
    context_id: str,
    granter_id: str,
    valid_for_seconds: int = 3600,
    node_name: str | None = None,
) -> dict:
    """Create an open invitation via raw HTTP POST.

    Uses direct HTTP to preserve all SignedOpenInvitation fields.
    """
    try:
        import requests

        from merobox.commands.result import fail, ok

        payload = {
            "contextId": context_id,
            "inviterId": granter_id,
            "validForSeconds": valid_for_seconds,
        }

        url = f"{rpc_url}/admin-api/contexts/invite_by_open_invitation"
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return ok(result, endpoint=url, payload_format=0)
    except Exception as e:
        from merobox.commands.result import fail

        return fail("create_open_invitation failed", error=e)


class InviteOpenStep(BaseStep):
    """Execute an invite open step."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["node", "context_id", "granter_id"]

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate node is a string
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        # Validate context_id is a string
        if not isinstance(self.config.get("context_id"), str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")
        # Validate granter_id is a string
        if not isinstance(self.config.get("granter_id"), str):
            raise ValueError(f"Step '{step_name}': 'granter_id' must be a string")
        # Validate valid_for_seconds is an integer if provided
        for key in ("valid_for_seconds", "valid_for_blocks"):
            if key in self.config and not isinstance(self.config.get(key), int):
                raise ValueError(f"Step '{step_name}': '{key}' must be an integer")

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from invite_open API response:
        - invitation: Signed open invitation data (JSON object)
        """
        return [
            (
                "invitation",
                "open_invitation_{node_name}_{context_id}",
                "Signed open invitation data",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        granter_id = self._resolve_dynamic_value(
            self.config["granter_id"], workflow_results, dynamic_values
        )
        valid_for_seconds = self.config.get(
            "valid_for_seconds", self.config.get("valid_for_blocks", 3600)
        )

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  InviteOpen step export configuration validation failed[/yellow]"
            )

        # Resolve node (gets URL and ensures authentication)
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        # Execute open invitation creation
        console.print(
            f"[blue]Creating open invitation for context {context_id} on {node_name}...[/blue]"
        )
        result = await create_open_invitation_via_admin_api(
            rpc_url,
            context_id,
            granter_id,
            valid_for_seconds,
            node_name=client_node_name,
        )

        console.print(f"[cyan]🔍 Open Invitation API Response for {node_name}:[/cyan]")
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

        console.print(f"  Endpoint: {result.get('endpoint', 'N/A')}")
        console.print(f"  Payload Format: {result.get('payload_format', 'N/A')}")
        if not result.get("success"):
            console.print(f"  Error: {result.get('error')}")
            if "tried_payloads" in result:
                console.print(f"  Tried Payloads: {result['tried_payloads']}")
            if "errors" in result:
                console.print(f"  Detailed Errors: {result['errors']}")

        if result["success"]:
            # Check if the JSON-RPC response contains an error
            if self._check_jsonrpc_error(result["data"]):
                return False

            # Store result for later use
            step_key = f"invite_open_{node_name}_{context_id}"
            workflow_results[step_key] = result["data"]

            # Export variables as dict - the join_open step will serialize it to JSON
            # Note: Don't serialize here because base class will auto-parse it back to dict
            # The response has structure: {"data": {"invitation": {...}, "inviter_signature": "..."}}
            # We need to export this entire object (invitation + signature) for join_open
            # Create a synthetic response where "invitation" field contains the complete signed invitation
            actual_data = result["data"].get("data", result["data"])
            synthetic_response = {"invitation": actual_data}
            self._export_variables(synthetic_response, node_name, dynamic_values)

            return True
        else:
            console.print(
                f"[red]Open invitation creation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False
