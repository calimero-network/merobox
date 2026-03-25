"""
Join group context step executor.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class JoinGroupContextStep(BaseStep):
    """Execute a join group context step (join an existing context via group membership)."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "context_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "context_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _get_exportable_variables(self):
        return [
            (
                "contextId",
                "context_id_{node_name}",
                "Context ID that was joined",
            ),
            (
                "memberPublicKey",
                "context_member_public_key_{node_name}",
                "Public key of the context member",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        try:
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.join_group_context(
                group_id=group_id, context_id=context_id
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("join_group_context failed", error=e)

        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False

            step_key = f"join_group_context_{node_name}"
            workflow_results[step_key] = result["data"]
            self._export_variables(result["data"], node_name, dynamic_values)

            # Fallback: ensure context_id and member_public_key are captured
            if f"context_id_{node_name}" not in dynamic_values:
                raw = result["data"]
                if isinstance(raw, dict):
                    nested = raw.get("data", raw)
                    if isinstance(nested, dict):
                        ctx_id = nested.get("contextId")
                        if ctx_id:
                            dynamic_values[f"context_id_{node_name}"] = ctx_id
                        member_pk = nested.get("memberPublicKey")
                        if member_pk:
                            dynamic_values[f"context_member_public_key_{node_name}"] = (
                                member_pk
                            )

            console.print(
                f"[green]✓ Node {node_name} joined context {context_id} "
                f"via group {group_id}[/green]"
            )
            return True
        else:
            console.print(
                f"[red]Join group context failed on {node_name}: "
                f"{result.get('error', 'Unknown error')}[/red]"
            )
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
