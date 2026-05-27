"""
Group upgrade-lifecycle workflow step executors.

Covers signing-key rotation and the upgrade state machine
(initiate -> poll status -> retry on failure).
"""

# PEP 563 deferred evaluation — required for `X | None` style annotations
# to work on Python 3.9 (merobox `requires-python = ">=3.9"`). Matches
# convention used by assertion.py, assert_log.py, json_assertion.py.
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console

# Cascade requires the calimero-client-py `cascade` kwarg on
# `upgrade_group` (added in 0.6.15). Resolved ONCE at import time so a
# version-mismatch is detected on the first cascade step rather than
# repeatedly inside execute(). Non-strict parsing: anything we can't
# split into 3 integers (e.g. "0.6.15rc1", "0.6.15.dev0", "0+local")
# is treated as unknown — we then defer to the RPC-level TypeError.
_CASCADE_MIN_CLIENT_VERSION = (0, 6, 15)


def _resolve_client_py_version() -> tuple[tuple[int, ...] | None, str]:
    try:
        installed = _pkg_version("calimero-client-py")
    except PackageNotFoundError:
        return None, "not installed"
    try:
        parts = tuple(int(p) for p in installed.split(".")[:3])
    except ValueError:
        return None, installed
    return parts, installed


_CLIENT_PY_VERSION, _CLIENT_PY_VERSION_STR = _resolve_client_py_version()


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
            # Redact exception details. The Rust FFI may format errors that
            # include the signing_key value (e.g., "Invalid key 'abcdef...'"),
            # and some server error paths echo request parameters. Capturing
            # the raw exception in result["exception"] would land that key
            # material in workflow artifacts and verbose logs. Record only
            # the exception type — no message, no traceback.
            result = fail(
                f"register_group_signing_key failed (exception type: {type(e).__name__})"
            )
        finally:
            # Drop the local reference to the resolved key. Python strings are
            # immutable so this doesn't zero the underlying buffer, but it
            # does remove this frame's reference so the GC can reclaim the
            # string as soon as any other holder releases it. Defense-in-depth
            # against a crash/debugger inspecting this stack frame.
            del signing_key

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


class CascadeNamespaceApplicationStep(BaseStep):
    """Cascade a target-application change across every descendant of a namespace.

    Wraps the same `upgrade_group` RPC as `UpgradeGroupStep`, but sets
    `cascade=True` so the emitter publishes `CascadeTargetApplicationSet`
    instead of a per-group upgrade. The core cascade engine then fans the
    op out to every matching descendant subgroup + context in a single
    sync round (calimero-network/core#2493).

    Requires calimero-client-py >= 0.6.15 for the `cascade` kwarg.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id", "target_application_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "namespace_id", "target_application_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        migrate_method = self.config.get("migrate_method")
        if migrate_method is not None and not isinstance(migrate_method, str):
            raise ValueError(
                f"Step '{step_name}': 'migrate_method' must be a string if provided"
            )

    def _get_exportable_variables(self):
        # Only export fields that are always populated. `total`,
        # `completed`, `failed` are Option<u32> in
        # UpgradeGroupApiResponseData — they're None on the Completed
        # branch — so exporting them would yield a confusing None
        # variable for workflow authors. Workflows that need the count
        # should poll via get_group_upgrade_status while status is
        # in_progress.
        return [
            (
                "groupId",
                "cascade_namespace_application_group_id_{node_name}",
                "Hex group ID returned by the cascade dispatch",
            ),
            (
                "status",
                "cascade_namespace_application_status_{node_name}",
                "Initial status string (in_progress | completed)",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]

        # Pre-flight: actionable error if the resolved client-py version
        # is below the minimum. Resolution happens once at module load
        # (see _resolve_client_py_version); None means we couldn't parse
        # the installed version string, in which case we fall through
        # and let the RPC-level TypeError surface. Honors
        # expected_failure so a workflow that intentionally tests the
        # version-check path (e.g., pinned-to-old-client regression
        # case) gets the same expected/unexpected accounting as any
        # other step-level failure.
        #
        # Runs before dynamic-value resolution so a version mismatch
        # short-circuits without resolving namespace_id /
        # target_application_id from workflow_results.
        if (
            _CLIENT_PY_VERSION is not None
            and _CLIENT_PY_VERSION < _CASCADE_MIN_CLIENT_VERSION
        ):
            min_str = ".".join(str(p) for p in _CASCADE_MIN_CLIENT_VERSION)
            msg = (
                f"cascade_namespace_application requires "
                f"calimero-client-py >= {min_str} "
                f"(installed: {_CLIENT_PY_VERSION_STR}) on {node_name}"
            )
            if self._is_expected_failure():
                self._report_expected_failure(msg)
                return True
            console.print(f"[red]{msg}[/red]")
            return False

        namespace_id = self._resolve_dynamic_value(
            self.config["namespace_id"], workflow_results, dynamic_values
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

        # Node resolution + client construction + RPC all share the
        # try/except so connection errors, auth failures, and RPC errors
        # all funnel through expected_failure handling consistently with
        # sibling steps (UpgradeGroupStep, RegisterGroupSigningKeyStep,
        # GetGroupUpgradeStatusStep).
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.upgrade_group(
                group_id=namespace_id,
                target_application_id=target_application_id,
                migrate_method=migrate_method,
                cascade=True,
            )
            result = ok(api_result)
        except Exception as e:
            result = fail("cascade_namespace_application failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]cascade_namespace_application failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"cascade_namespace_application_{node_name}"] = result["data"]
        # Mirror GetGroupUpgradeStatusStep: only export when the author
        # configured outputs, otherwise the base class prints a
        # "No outputs configured" warning for every caller that doesn't
        # use outputs.
        if "outputs" in self.config:
            self._export_variables(result["data"], node_name, dynamic_values)
        console.print(
            f"[green]✓ Cascaded namespace {namespace_id} to {target_application_id} on {node_name}[/green]"
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
        # Only export when the author configured outputs — otherwise the base
        # class prints "No outputs configured" warnings for every caller that
        # doesn't use outputs. Same guard as GetNamespaceIdentityStep.
        if "outputs" in self.config:
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
