"""
Group governance workflow step executors.

Steps for group settings, context-group association, and manual sync.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class UpdateGroupSettingsStep(BaseStep):
    """Update group-level settings (currently: upgrade_policy)."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "upgrade_policy"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "upgrade_policy"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        upgrade_policy = self._resolve_dynamic_value(
            self.config["upgrade_policy"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.update_group_settings(
                group_id=group_id, upgrade_policy=upgrade_policy
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("update_group_settings failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]update_group_settings failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"update_group_settings_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Updated group {group_id} settings (upgrade_policy={upgrade_policy}) on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class DetachContextFromGroupStep(BaseStep):
    """Remove a context's association with a group without deleting the context."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "context_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "context_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

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
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.detach_context_from_group(
                group_id=group_id, context_id=context_id
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("detach_context_from_group failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]detach_context_from_group failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"detach_context_from_group_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Detached context {context_id} from group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class SyncGroupStep(BaseStep):
    """Explicitly trigger group-governance sync (diagnostic)."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

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
            api_result = client.sync_group(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("sync_group failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]sync_group failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"sync_group_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Triggered governance sync for group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True
