"""
Join Open step executor - Join context using open invitation.
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url


@with_retry(config=NETWORK_RETRY_CONFIG)
async def join_context_via_open_invitation(
    rpc_url: str, invitation_dict: dict, new_member_public_key: str
) -> dict:
    """Join a context using an open invitation via calimero-client-py."""
    try:
        import json as json_lib

        from merobox.commands.client import get_client_for_rpc_url
        from merobox.commands.result import fail, ok
        from merobox.commands.utils import console

        client = get_client_for_rpc_url(rpc_url)

        # Serialize the SignedOpenInvitation to JSON
        # invitation_dict contains {"invitation": {...}, "inviterSignature": "..."}
        invitation_json = json_lib.dumps(invitation_dict)

        console.print("[dim]Calling join_context_by_open_invitation:[/dim]")
        console.print(f"[dim]  RPC URL: {rpc_url}[/dim]")
        console.print(f"[dim]  Member key: {new_member_public_key}[/dim]")
        console.print(f"[dim]  Invitation JSON length: {len(invitation_json)}[/dim]")

        result = client.join_context_by_open_invitation(
            invitation_json=invitation_json, new_member_public_key=new_member_public_key
        )

        return ok(
            result,
            endpoint=f"{rpc_url}/admin-api/dev/contexts/join-open",
            payload_format=0,
        )
    except Exception as e:
        import traceback

        from merobox.commands.result import fail
        from merobox.commands.utils import console

        console.print(f"[red]Exception: {e}[/red]")
        console.print(f"[red]Type: {type(e).__name__}[/red]")
        console.print(f"[red]Traceback:\n{traceback.format_exc()}[/red]")
        return fail(f"join_context_via_open_invitation failed: {str(e)}", error=e)


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
            console.print("[blue]Parsing invitation from JSON string...[/blue]")
            invitation_dict = json_lib.loads(invitation)
        elif isinstance(invitation, dict):
            invitation_dict = invitation
        else:
            console.print(f"[red]Unexpected invitation type: {type(invitation)}[/red]")
            return False

        # Debug: Show resolved values
        console.print("[blue]Debug: Resolved values for join_open step:[/blue]")
        console.print(f"  invitee_id: {invitee_id}")
        console.print(f"  invitation type: {type(invitation_dict)}")
        console.print(
            f"  invitation keys: {list(invitation_dict.keys()) if isinstance(invitation_dict, dict) else 'N/A'}"
        )

        # Get node RPC URL
        try:
            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(
                f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]"
            )
            return False

        # Execute join via open invitation
        console.print(
            f"[blue]Joining context via open invitation on {node_name}...[/blue]"
        )
        result = await join_context_via_open_invitation(
            rpc_url, invitation_dict, invitee_id
        )

        # Log detailed API response
        console.print(f"[cyan]🔍 Join Open API Response for {node_name}:[/cyan]")
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
