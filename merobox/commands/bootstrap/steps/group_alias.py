"""
Group alias workflow step executors.

Steps for renaming groups and members via the admin API.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class SetGroupAliasStep(BaseStep):
    """Rename a group via the admin API (authoritative rename)."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "alias"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "alias"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        alias = self._resolve_dynamic_value(
            self.config["alias"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.set_group_alias(group_id=group_id, alias=alias)
            result = ok(api_result)
        except Exception as e:
            result = fail("set_group_alias failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]set_group_alias failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"set_group_alias_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Renamed group {group_id} to '{alias}' on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class SetMemberAliasStep(BaseStep):
    """Rename a member's display identity within a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "member_id", "alias"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "member_id", "alias"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        member_id = self._resolve_dynamic_value(
            self.config["member_id"], workflow_results, dynamic_values
        )
        alias = self._resolve_dynamic_value(
            self.config["alias"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.set_member_alias(
                group_id=group_id, member_id=member_id, alias=alias
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("set_member_alias failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]set_member_alias failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"set_member_alias_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Renamed member {member_id} to '{alias}' in group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True
