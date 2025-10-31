"""
Join Open step executor - Join context using open invitation.
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console, get_node_rpc_url


async def join_context_via_open_invitation(
    rpc_url: str, invitation_dict: dict, new_member_public_key: str
) -> dict:
    """Join a context using an open invitation ."""
    try:
        client = get_client_for_rpc_url(rpc_url)
    except Exception as exc:
        return fail(
            "join_context_via_open_invitation failed during client creation",
            error=exc,
        )

    try:
        invitation_json = json_lib.dumps(invitation_dict)
    except (TypeError, ValueError) as exc:
        return fail(
            "join_context_via_open_invitation failed: invalid invitation", error=exc
        )

    try:
        result = client.join_context_by_open_invitation(
            invitation_json=invitation_json,
            new_member_public_key=new_member_public_key,
        )
    except Exception as exc:
        return fail(
            "join_context_via_open_invitation failed",
            error=exc,
            client_method="client.join_context_by_open_invitation",
            endpoint="calimero_client_py.join_context_by_open_invitation",
        )

    return ok(
        result,
        client_method="client.join_context_by_open_invitation",
        payload_format="json",
        endpoint="calimero_client_py.join_context_by_open_invitation",
    )


class JoinOpenStep(BaseStep):
    """Execute a join open step."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["node", "invitee_id", "invitation"]

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

        # Validate invitee_id is a string
        if not isinstance(self.config.get("invitee_id"), str):
            raise ValueError(f"Step '{step_name}': 'invitee_id' must be a string")

        # Validate invitation is a string
        if not isinstance(self.config.get("invitation"), str):
            raise ValueError(f"Step '{step_name}': 'invitation' must be a string")

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from join_open API response:
        - contextId: ID of the context joined
        - memberPublicKey: Public key of the member who joined
        """
        return [
            (
                "contextId",
                "join_open_context_id_{node_name}_{invitee_id}",
                "ID of the context joined",
            ),
            (
                "memberPublicKey",
                "join_open_member_public_key_{node_name}_{invitee_id}",
                "Public key of the member who joined",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        invitee_id = self._resolve_dynamic_value(
            self.config["invitee_id"], workflow_results, dynamic_values
        )
        invitation = self._resolve_dynamic_value(
            self.config["invitation"], workflow_results, dynamic_values
        )

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  JoinOpen step export configuration validation failed[/yellow]"
            )

        # Ensure invitation is a dict (not a string)
        if isinstance(invitation, str):
            invitation_dict = json_lib.loads(invitation)
        elif isinstance(invitation, dict):
            invitation_dict = invitation
        else:
            console.print(f"[red]Unexpected invitation type: {type(invitation)}[/red]")
            return False

        # Get node RPC URL
        try:
            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as exc:
            console.print(
                f"[red]Failed to get RPC URL for node {node_name}: {exc}[/red]"
            )
            return False

        # Execute join via open invitation
        result = await join_context_via_open_invitation(
            rpc_url, invitation_dict, invitee_id
        )

        if not result.get("success"):
            if "tried_payloads" in result:
                console.print(f"  Tried Payloads: {result['tried_payloads']}")
            if "errors" in result:
                console.print(f"  Detailed Errors: {result['errors']}")

        if result["success"]:
            # Check if the JSON-RPC response contains an error
            if self._check_jsonrpc_error(result["data"]):
                return False

            # Store result for later use
            step_key = f"join_open_{node_name}_{invitee_id}"
            workflow_results[step_key] = result["data"]

            # Export variables using the new standardized approach
            self._export_variables(result["data"], node_name, dynamic_values)

            return True
        else:
            console.print(
                f"[red]Join via open invitation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False
