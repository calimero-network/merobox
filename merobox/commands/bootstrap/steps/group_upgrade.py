"""
Group upgrade-lifecycle workflow step executors.

Covers signing-key rotation and the upgrade state machine
(initiate -> poll status -> retry on failure).
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class RegisterGroupSigningKeyStep(BaseStep):
    """Register (or rotate) the group's signing key."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "signing_key"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "signing_key"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        signing_key = self._resolve_dynamic_value(
            self.config["signing_key"], workflow_results, dynamic_values
        )
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.register_group_signing_key(
                group_id=group_id, signing_key=signing_key
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("register_group_signing_key failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]register_group_signing_key failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        # Deliberately do NOT store the raw API response: if the server ever
        # echoes back the signing key, or if a future change makes the step
        # configure outputs that pull sensitive fields out, we don't want that
        # key material landing in workflow_results (which executor.py dumps in
        # verbose mode). Record only a redacted success marker.
        workflow_results[f"register_group_signing_key_{node_name}"] = {
            "registered": True
        }
        console.print(
            f"[green]✓ Registered signing key for group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class UpgradeGroupStep(BaseStep):
    """Initiate a group-application upgrade."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "target_application_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", "target_application_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        migrate_method = self.config.get("migrate_method")
        if migrate_method is not None and not isinstance(migrate_method, str):
            raise ValueError(
                f"Step '{step_name}': 'migrate_method' must be a string if provided"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        target_application_id = self._resolve_dynamic_value(
            self.config["target_application_id"], workflow_results, dynamic_values
        )
        migrate_method_raw = self.config.get("migrate_method")
        migrate_method = (
            self._resolve_dynamic_value(
                migrate_method_raw, workflow_results, dynamic_values
            )
            if migrate_method_raw is not None
            else None
        )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.upgrade_group(
                group_id=group_id,
                target_application_id=target_application_id,
                migrate_method=migrate_method,
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("upgrade_group failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]upgrade_group failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"upgrade_group_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Initiated upgrade of group {group_id} to {target_application_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class GetGroupUpgradeStatusStep(BaseStep):
    """Read the current upgrade status for a group."""

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
            ("status", "upgrade_status_{node_name}", "Upgrade status string"),
            (
                "target_application_id",
                "upgrade_target_{node_name}",
                "Application ID targeted by the upgrade",
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
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.get_group_upgrade_status(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("get_group_upgrade_status failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]get_group_upgrade_status failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"upgrade_status_{node_name}"] = result["data"]
        self._export_variables(result["data"], node_name, dynamic_values)
        console.print(
            f"[green]✓ Read upgrade status for group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class RetryGroupUpgradeStep(BaseStep):
    """Retry a previously-failed group upgrade."""

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
            api_result = client.retry_group_upgrade(group_id=group_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("retry_group_upgrade failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]retry_group_upgrade failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"retry_group_upgrade_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Retried upgrade for group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True
