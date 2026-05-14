"""
Join-subgroup-via-inheritance step executor.

Calls the `POST /admin-api/groups/:group_id/join-via-inheritance`
endpoint introduced in calimero-network/core#2357. Lets a namespace
member materialise their inherited Open-subgroup membership without
an admin-signed invitation and without first joining a child
context — the workflow-level equivalent of the explicit-invite
`JoinNamespaceStep`, but for the inherited path.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class JoinSubgroupInheritanceStep(BaseStep):
    """Execute a join-subgroup-via-inheritance step."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _get_exportable_variables(self):
        return [
            (
                "groupId",
                "subgroup_id_{node_name}",
                "Group ID that was joined via inheritance",
            ),
            (
                "memberPublicKey",
                "subgroup_member_public_key_{node_name}",
                "Public key of the joining member",
            ),
            (
                "wasInherited",
                "subgroup_was_inherited_{node_name}",
                "True if MemberJoinedOpen was published; "
                "false if caller was already a direct member",
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

        # Version-mismatch check sits OUTSIDE the API-call try/except so the
        # actionable "requires >= 0.6.11" message reaches the workflow author
        # directly rather than being swallowed as a generic
        # "join_subgroup_inheritance failed" failure.
        client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
        method = getattr(client, "join_subgroup_inheritance", None)
        if not callable(method):
            console.print(
                f"[red]join_subgroup_inheritance is not available in the current "
                f"calimero-client-py release on {node_name} "
                f"(requires >= 0.6.11)[/red]"
            )
            return False

        try:
            api_result = method(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("join_subgroup_inheritance failed", error=e)

        expected_failure = self._is_expected_failure()

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                if expected_failure:
                    self._report_expected_failure("JSON-RPC error returned")
                    return True
                return False

            step_key = f"join_subgroup_inheritance_{node_name}"
            workflow_results[step_key] = result["data"]
            self._export_variables(result["data"], node_name, dynamic_values)

            raw = result["data"]
            if isinstance(raw, dict):
                nested = raw.get("data", raw)
                if isinstance(nested, dict):
                    if f"subgroup_id_{node_name}" not in dynamic_values:
                        gid = nested.get("groupId")
                        if gid is not None:
                            dynamic_values[f"subgroup_id_{node_name}"] = gid
                    if f"subgroup_member_public_key_{node_name}" not in dynamic_values:
                        member_pk = nested.get("memberPublicKey")
                        if member_pk is not None:
                            dynamic_values[
                                f"subgroup_member_public_key_{node_name}"
                            ] = member_pk
                    if f"subgroup_was_inherited_{node_name}" not in dynamic_values:
                        was_inherited = nested.get("wasInherited")
                        if was_inherited is not None:
                            dynamic_values[f"subgroup_was_inherited_{node_name}"] = (
                                was_inherited
                            )

            console.print(
                f"[green]✓ Node {node_name} joined subgroup {group_id} "
                f"via inheritance[/green]"
            )
            if expected_failure:
                self._report_unexpected_success()
            return True
        else:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]join_subgroup_inheritance failed on {node_name}: "
                f"{result.get('error', 'Unknown error')}[/red]"
            )
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
