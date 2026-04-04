"""
Subgroup workflow step executors.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class NestGroupStep(BaseStep):
    """Execute a nest_group step."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "parent_group_id", "child_group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "parent_group_id", "child_group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        parent_group_id = self._resolve_dynamic_value(
            self.config["parent_group_id"], workflow_results, dynamic_values
        )
        child_group_id = self._resolve_dynamic_value(
            self.config["child_group_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.nest_group(
                parent_group_id=parent_group_id,
                child_group_id=child_group_id,
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("nest_group failed", error=e)
        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False
            workflow_results[f"nest_group_{node_name}"] = result["data"]
            console.print(
                f"[green]✓ Nested group {child_group_id} under {parent_group_id} on {node_name}[/green]"
            )
            return True
        console.print(
            f"[red]nest_group failed on {node_name}: {result.get('error', 'Unknown error')}[/red]"
        )
        return False


class UnnestGroupStep(BaseStep):
    """Execute an unnest_group step."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "parent_group_id", "child_group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "parent_group_id", "child_group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        parent_group_id = self._resolve_dynamic_value(
            self.config["parent_group_id"], workflow_results, dynamic_values
        )
        child_group_id = self._resolve_dynamic_value(
            self.config["child_group_id"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.unnest_group(
                parent_group_id=parent_group_id,
                child_group_id=child_group_id,
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("unnest_group failed", error=e)
        if result["success"]:
            if self._check_jsonrpc_error(result["data"]):
                return False
            workflow_results[f"unnest_group_{node_name}"] = result["data"]
            console.print(
                f"[green]✓ Unnested group {child_group_id} from {parent_group_id} on {node_name}[/green]"
            )
            return True
        console.print(
            f"[red]unnest_group failed on {node_name}: {result.get('error', 'Unknown error')}[/red]"
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
            console.print(f"[green]✓ Listed subgroups for {group_id} on {node_name}[/green]")
            return True
        console.print(
            f"[red]list_subgroups failed on {node_name}: {result.get('error', 'Unknown error')}[/red]"
        )
        return False
