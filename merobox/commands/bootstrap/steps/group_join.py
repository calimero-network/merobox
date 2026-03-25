"""
Join group step executor.
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class JoinGroupStep(BaseStep):
    """Execute a join group step using a previously created invitation."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "invitation"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

    def _get_exportable_variables(self):
        return [
            (
                "groupId",
                "group_id_{node_name}",
                "Group ID that was joined",
            ),
            (
                "memberIdentity",
                "group_member_identity_{node_name}",
                "Member identity public key after joining",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        invitation = self._resolve_dynamic_value(
            self.config["invitation"], workflow_results, dynamic_values
        )

        # invitation may be a dict (captured from create_group_invitation step)
        # or a JSON string — normalise to a JSON string for the client
        if isinstance(invitation, dict):
            invitation_json = json_lib.dumps(invitation)
        elif isinstance(invitation, str):
            # Validate it's parseable JSON
            try:
                json_lib.loads(invitation)
                invitation_json = invitation
            except json_lib.JSONDecodeError as e:
                console.print(
                    f"[red]Step 'join_group' on {node_name}: "
                    f"'invitation' is not valid JSON: {e}[/red]"
                )
                return False
        else:
            console.print(
                f"[red]Step 'join_group' on {node_name}: "
                f"'invitation' must be a dict or JSON string[/red]"
            )
            return False

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        try:
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.join_group(invitation_json=invitation_json)
            result = ok(api_result)
        except Exception as e:
            result = fail("join_group failed", error=e)

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False

            step_key = f"join_group_{node_name}"
            workflow_results[step_key] = result["data"]
            self._export_variables(result["data"], node_name, dynamic_values)

            # Fallback extraction
            if f"group_id_{node_name}" not in dynamic_values:
                raw = result["data"]
                if isinstance(raw, dict):
                    nested = raw.get("data", raw)
                    group_id = (
                        nested.get("groupId") if isinstance(nested, dict) else None
                    )
                    if group_id:
                        dynamic_values[f"group_id_{node_name}"] = group_id

            console.print(
                f"[green]✓ Node {node_name} joined group successfully[/green]"
            )
            return True
        else:
            console.print(
                f"[red]Join group failed on {node_name}: "
                f"{result.get('error', 'Unknown error')}[/red]"
            )
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
