"""
Identity management step executors.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.identity import (
    generate_identity_via_admin_api,
    invite_identity_via_admin_api,
)
from merobox.commands.utils import console, get_node_rpc_url


class CreateIdentityStep(BaseStep):
    """Execute a create identity step."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["node"]

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

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from generate_identity API response:
        - publicKey: Public key of the generated identity (this is what the API actually returns)
        """
        return [
            (
                "publicKey",
                "public_key_{node_name}",
                "Public key of the generated identity",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  CreateIdentity step export configuration validation failed[/yellow]"
            )

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

        # Execute identity creation
        result = await generate_identity_via_admin_api(rpc_url)

        # Log detailed API response
        import json as json_lib

        console.print(
            f"[cyan]🔍 Identity Creation API Response for {node_name}:[/cyan]"
        )
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
            # Check if the JSON-RPC response contains an error
            if self._check_jsonrpc_error(result["data"]):
                return False

            # Store result for later use
            step_key = f"identity_{node_name}"
            workflow_results[step_key] = result["data"]

            # Export variables using the new standardized approach
            self._export_variables(result["data"], node_name, dynamic_values)

            # Legacy support: ensure public_key is always available for backward compatibility
            if f"public_key_{node_name}" not in dynamic_values:
                # Try to extract from the raw response as fallback
                if isinstance(result["data"], dict):
                    public_key = result["data"].get(
                        "publicKey",
                        result["data"].get("id", result["data"].get("name")),
                    )
                    if public_key:
                        dynamic_values[f"public_key_{node_name}"] = public_key
                        console.print(
                            f"[blue]📝 Fallback: Captured public key for {node_name}: {public_key}[/blue]"
                        )
                    else:
                        console.print(
                            f"[yellow]⚠️  No public key found in response. Available keys: {list(result['data'].keys())}[/yellow]"
                        )
                else:
                    console.print(
                        f"[yellow]⚠️  Identity result is not a dict: {type(result['data'])}[/yellow]"
                    )

            return True
        else:
            console.print(
                f"[red]Identity creation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False


class InviteIdentityStep(BaseStep):
    """Execute an invite identity step."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["node", "context_id", "granter_id", "grantee_id"]

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
        # Validate grantee_id is a string
        if not isinstance(self.config.get("grantee_id"), str):
            raise ValueError(f"Step '{step_name}': 'grantee_id' must be a string")

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from invite_identity API response:
        - invitation: Invitation data for joining the context (this is what the API actually returns)
        """
        return [
            (
                "invitation",
                "invitation_data_{node_name}_{invitee_id}",
                "Invitation data for joining the context",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        inviter_id = self._resolve_dynamic_value(
            self.config["granter_id"], workflow_results, dynamic_values
        )
        invitee_id = self._resolve_dynamic_value(
            self.config["grantee_id"], workflow_results, dynamic_values
        )
        capability = self.config.get("capability", "member")

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  InviteIdentity step export configuration validation failed[/yellow]"
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

        # Execute invitation
        result = await invite_identity_via_admin_api(
            rpc_url, context_id, inviter_id, invitee_id, capability
        )

        import json as json_lib

        console.print(f"[cyan]🔍 Invitation API Response for {node_name}:[/cyan]")
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
            step_key = f"invite_{node_name}_{invitee_id}"
            # Extract the actual invitation data from the nested response
            invitation_data = (
                result["data"].get("data")
                if isinstance(result["data"], dict)
                else result["data"]
            )
            workflow_results[step_key] = invitation_data

            # Special handling for invitation export since the data field IS the invitation
            # The result structure is {'success': True, 'data': {'data': 'invitation_string'}}
            # The _export_variables method expects to work with the response['data'] part
            # So we need to create: {'invitation': 'invitation_string'} as the response_data
            actual_invitation = (
                result["data"].get("data")
                if isinstance(result["data"], dict)
                else result["data"]
            )
            synthetic_response_data = {"invitation": actual_invitation}
            self._export_variables(synthetic_response_data, node_name, dynamic_values)

            return True
        else:
            console.print(
                f"[red]Invitation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False
