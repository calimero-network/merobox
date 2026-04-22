"""
Subgroup workflow step executors.
"""

import json
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class ReparentGroupStep(BaseStep):
    """Atomically move `child_group_id` to a new parent within the same namespace.

    Replaces the old NestGroupStep + UnnestGroupStep pair. The underlying
    server emits a single GroupReparented governance op; orphan group state
    is structurally impossible.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "child_group_id", "new_parent_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "child_group_id", "new_parent_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        child_group_id = self._resolve_dynamic_value(
            self.config["child_group_id"], workflow_results, dynamic_values
        )
        new_parent_id = self._resolve_dynamic_value(
            self.config["new_parent_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.reparent_group(
                group_id=child_group_id,
                new_parent_id=new_parent_id,
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("reparent_group failed", error=e)
        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False
            workflow_results[f"reparent_group_{node_name}"] = result["data"]
            console.print(
                f"[green]✓ Reparented group {child_group_id} to {new_parent_id} on {node_name}[/green]"
            )
            return True
        console.print(
            f"[red]reparent_group failed on {node_name}: {result.get('error', 'Unknown error')}[/red]"
        )
        return False


class ListSubgroupsStep(BaseStep):
    """Execute a list_subgroups step."""

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
        return [("subgroups", "subgroups_{node_name}", "List of subgroups")]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.list_subgroups(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("list_subgroups failed", error=e)
        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False
            workflow_results[f"subgroups_{node_name}"] = result["data"]
            self._export_variables(result["data"], node_name, dynamic_values)
            console.print(
                f"[green]✓ Listed subgroups for {group_id} on {node_name}[/green]"
            )
            return True
        console.print(
            f"[red]list_subgroups failed on {node_name}: {result.get('error', 'Unknown error')}[/red]"
        )
        return False


class AddGroupMembersStep(BaseStep):
    """Add members to a group."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "members"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        if not isinstance(self.config.get("group_id"), str):
            raise ValueError(f"Step '{step_name}': 'group_id' must be a string")
        if not isinstance(self.config.get("members"), list):
            raise ValueError(f"Step '{step_name}': 'members' must be a list")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        members = self.config["members"]

        # Resolve dynamic values in each member entry
        resolved_members = []
        for member in members:
            resolved = {}
            for key, value in member.items():
                if isinstance(value, str):
                    resolved[key] = self._resolve_dynamic_value(
                        value, workflow_results, dynamic_values
                    )
                else:
                    resolved[key] = value
            resolved_members.append(resolved)

        members_json = json.dumps(resolved_members)

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.add_group_members(
                group_id=group_id,
                members_json=members_json,
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("add_group_members failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]Failed to add group members on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False
        workflow_results[f"add_group_members_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Added {len(resolved_members)} member(s) to group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True
