"""
Join namespace step executor.
"""

import json as json_lib
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class JoinNamespaceStep(BaseStep):
    """Execute a join namespace step using a previously created invitation."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "invitation"]

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
        return [
            (
                "namespaceId",
                "namespace_id_{node_name}",
                "Namespace ID that was joined",
            ),
            (
                "memberIdentity",
                "namespace_member_identity_{node_name}",
                "Member identity public key after joining",
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

        # invitation is a SignedGroupOpenInvitation dict or JSON string from
        # create_group_invitation. The Rust client parses invitation_json as
        # JoinGroupApiRequest { invitation: SignedGroupOpenInvitation }, so we
        # must wrap the invitation in {"invitation": ...}.
        if isinstance(invitation, dict):
            if "inviter_signature" in invitation:
                invitation_json = json_lib.dumps({"invitation": invitation})
            elif "invitation" in invitation and isinstance(
                invitation.get("invitation"), dict
            ):
                invitation_json = json_lib.dumps(invitation)
            else:
                invitation_json = json_lib.dumps({"invitation": invitation})
        elif isinstance(invitation, str):
            # Validate it's parseable JSON
            try:
                json_lib.loads(invitation)
                invitation_json = invitation
            except json_lib.JSONDecodeError as e:
                console.print(
                    f"[red]Step 'join_namespace' on {node_name}: "
                    f"'invitation' is not valid JSON: {e}[/red]"
                )
                return False
        else:
            console.print(
                f"[red]Step 'join_namespace' on {node_name}: "
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
            join_namespace = getattr(client, "join_namespace", None)
            if callable(join_namespace):
                api_result = join_namespace(
                    namespace_id=namespace_id,
                    invitation_json=invitation_json,
                )
            else:
                # Backward compatibility for older client versions.
                api_result = client.join_group(
                    namespace_id=namespace_id, invitation_json=invitation_json
                )
            result = ok(api_result)
        except Exception as e:
            result = fail("join_namespace failed", error=e)

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False

            step_key = f"join_namespace_{node_name}"
            workflow_results[step_key] = result["data"]
            self._export_variables(result["data"], node_name, dynamic_values)

            # Fallback extraction
            if f"namespace_id_{node_name}" not in dynamic_values:
                raw = result["data"]
                if isinstance(raw, dict):
                    nested = raw.get("data", raw)
                    joined_namespace_id = (
                        nested.get("namespaceId") if isinstance(nested, dict) else None
                    )
                    if joined_namespace_id:
                        dynamic_values[f"namespace_id_{node_name}"] = (
                            joined_namespace_id
                        )

            # Namespace governance model: no relay needed. The joining node
            # publishes a MemberJoined op directly on the namespace topic.
            # The relay_to config key is ignored (kept for backward compat).
            console.print(
                f"[green]✓ Node {node_name} joined namespace successfully[/green]"
            )
            return True
        else:
            exception = result.get("exception", {})
            detail = exception.get("message", result.get("error", "Unknown error"))
            console.print(f"[red]Join namespace failed on {node_name}: {detail}[/red]")
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False


# Deprecated alias kept for backward compatibility.
JoinGroupStep = JoinNamespaceStep
