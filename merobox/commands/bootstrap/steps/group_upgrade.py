"""
Group upgrade-lifecycle workflow step executors.

Covers signing-key rotation and the upgrade state machine
(initiate -> poll status -> retry on failure).
"""

# PEP 563 deferred evaluation — required for `X | None` style annotations
# to work on Python 3.9 (merobox `requires-python = ">=3.9"`). Matches
# convention used by assertion.py, assert_log.py, json_assertion.py.
from __future__ import annotations

import asyncio
import math
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import LOG_LEVEL_VERBOSE, console, vprint

# Cascade requires the calimero-client-py `cascade` kwarg on
# `upgrade_group` (added in 0.6.15). Resolved ONCE at import time so a
# version-mismatch is detected on the first cascade step rather than
# repeatedly inside execute(). Non-strict parsing: anything we can't
# split into 3 integers (e.g. "0.6.15rc1", "0.6.15.dev0", "0+local")
# is treated as unknown — we then defer to the RPC-level TypeError.
_CASCADE_MIN_CLIENT_VERSION = (0, 6, 15)

# `get_cascade_status` / `assert_cascade_complete` need the
# `get_cascade_status(namespace_id)` binding, added in
# calimero-client-py 0.6.17 (calimero-network/calimero-client-py#59,
# wrapping the RPC from calimero-network/core#2524).
_CASCADE_STATUS_MIN_CLIENT_VERSION = (0, 6, 17)


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


def _client_py_below(min_version: tuple[int, int, int]) -> bool:
    """True only when the installed client-py is *known* to be too old.

    Returns False when the version couldn't be resolved (`None`): we then
    defer to the RPC-level error rather than blocking on a guess. Mirrors the
    non-strict policy documented on `_resolve_client_py_version`.
    """
    return _CLIENT_PY_VERSION is not None and _CLIENT_PY_VERSION < min_version


def _summarize_cascade_status(response: Any) -> dict[str, Any]:
    """Roll a `get_cascade_status` response up into per-status counts.

    The RPC (calimero-network/core#2524) returns one entry per group in the
    namespace subtree (root included) under `data`, each carrying an
    `upgrade.status` of `completed` / `in_progress` / `failed`. This collapses
    that list into `total` / `completed` / `failed` / `pending` counts plus an
    `all_completed` flag, and re-attaches the raw `data` so callers can still
    reach individual entries via `outputs:`.

    `pending` is everything that is neither completed nor failed, so the three
    buckets always sum to `total` regardless of any future status spellings.
    `all_completed` is only true for a non-empty subtree where every group is
    `completed` — an empty response is never "complete" (there is nothing to
    have completed, and the subtree may simply not have propagated yet).

    The raw per-group entries are re-attached under `groups` (NOT `data`): the
    export machinery (`_export_custom_outputs`) treats a top-level `data` key
    as an envelope to unwrap, which would hide the count fields from
    `outputs:`. Keeping the list under `groups` leaves the whole summary
    addressable.
    """
    entries: list[Any] = []
    if isinstance(response, dict) and isinstance(response.get("data"), list):
        entries = response["data"]

    completed = 0
    failed = 0
    for entry in entries:
        status = ""
        if isinstance(entry, dict) and isinstance(entry.get("upgrade"), dict):
            status = str(entry["upgrade"].get("status", "")).lower()
        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1

    total = len(entries)
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "pending": total - completed - failed,
        "all_completed": total > 0 and completed == total,
        "groups": entries,
    }


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
        cascade = self.config.get("cascade")
        if cascade is not None and not isinstance(cascade, bool):
            raise ValueError(
                f"Step '{step_name}': 'cascade' must be a boolean if provided"
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
        cascade = bool(self.config.get("cascade", False))

        # Pre-flight: only enforced when cascade=True. The non-cascade
        # path (the historical default) does not need the kwarg at all,
        # so on older client-py versions a workflow that doesn't ask for
        # cascade keeps working unchanged. Mirrors the version-guard
        # pattern in CascadeNamespaceApplicationStep — see that step's
        # comment for the rationale.
        if cascade and (
            _CLIENT_PY_VERSION is not None
            and _CLIENT_PY_VERSION < _CASCADE_MIN_CLIENT_VERSION
        ):
            min_str = ".".join(str(p) for p in _CASCADE_MIN_CLIENT_VERSION)
            msg = (
                f"upgrade_group(cascade=true) requires "
                f"calimero-client-py >= {min_str} "
                f"(installed: {_CLIENT_PY_VERSION_STR}) on {node_name}"
            )
            if self._is_expected_failure():
                self._report_expected_failure(msg)
                return True
            console.print(f"[red]{msg}[/red]")
            return False

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            # `cascade` is only forwarded when truthy so callers running
            # against pre-0.6.15 client-py (where the kwarg doesn't exist)
            # don't trip a TypeError on the default `cascade=false` path.
            upgrade_kwargs: dict[str, Any] = {
                "group_id": group_id,
                "target_application_id": target_application_id,
                "migrate_method": migrate_method,
            }
            if cascade:
                upgrade_kwargs["cascade"] = True
            api_result = client.upgrade_group(**upgrade_kwargs)
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


class GetCascadeStatusStep(BaseStep):
    """Read per-descendant cascade migration status across a namespace subtree.

    Calls the `get_cascade_status` RPC (calimero-network/core#2524), which
    returns one entry per group in the namespace tree — the root included —
    carrying that group's upgrade snapshot plus the sticky `cascade_hlc` fence
    the atomic `CascadeUpgrade` op stamped on it. The raw list is rolled up
    into `total` / `completed` / `pending` / `failed` counts and an
    `all_completed` flag (see `_summarize_cascade_status`), stored under
    `cascade_status_{node}`. The summary fields — and the raw per-group
    `groups` list — are reachable from an `outputs:` block.

    Requires calimero-client-py >= 0.6.17 for the `get_cascade_status`
    binding.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "namespace_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]

        # Pre-flight version guard, same policy as the cascade steps: only
        # block when client-py is *known* to predate the binding. Runs
        # before dynamic-value resolution so a mismatch short-circuits
        # cleanly, and honors expected_failure for regression workflows
        # that intentionally pin an old client.
        if _client_py_below(_CASCADE_STATUS_MIN_CLIENT_VERSION):
            min_str = ".".join(str(p) for p in _CASCADE_STATUS_MIN_CLIENT_VERSION)
            msg = (
                f"get_cascade_status requires calimero-client-py >= {min_str} "
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

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.get_cascade_status(namespace_id=namespace_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("get_cascade_status failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]get_cascade_status failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        # A transport-level success can still carry a JSON-RPC error body;
        # mirror GetGroupUpgradeStatusStep so it isn't silently summarised as
        # an empty (all-zero) status.
        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        summary = _summarize_cascade_status(result["data"])
        workflow_results[f"cascade_status_{node_name}"] = summary
        # Only export when the author configured outputs — otherwise the base
        # class emits a verbose "no outputs configured" advisory. The summary
        # exposes total/completed/pending/failed/all_completed plus the raw
        # per-group `groups` list, all addressable by `outputs:`.
        if "outputs" in self.config:
            self._export_variables(summary, node_name, dynamic_values)
        console.print(
            f"[green]✓ Cascade status for namespace {namespace_id} on {node_name}: "
            f"{summary['completed']}/{summary['total']} completed, "
            f"{summary['pending']} pending, {summary['failed']} failed[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class AssertCascadeCompleteStep(BaseStep):
    """Poll `get_cascade_status` until every group in a namespace has migrated.

    Saves workflow authors from hand-rolling a `wait`-loop around
    `get_cascade_status`. Polls every `poll_interval` seconds (default `2.0`)
    until the subtree is fully migrated (`all_completed` — every group's
    upgrade status is `completed`) or `timeout_seconds` (default `30`)
    elapses. A group entering the `failed` status aborts the wait immediately:
    a cascade with a failed descendant can never reach `all_completed`, so
    there is no point burning the rest of the timeout.

    On the happy path the final summary is stored under
    `cascade_status_{node}` (same shape as `get_cascade_status`). A timeout or
    a failed descendant fails the step unless `expected_failure` is set.

    Requires calimero-client-py >= 0.6.17 for the `get_cascade_status`
    binding.
    """

    _DEFAULT_TIMEOUT_SECONDS = 30.0
    _DEFAULT_POLL_INTERVAL = 2.0

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "namespace_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        for field in ("timeout_seconds", "poll_interval"):
            value = self.config.get(field)
            if value is None:
                continue
            # bool is an int subclass — reject it explicitly so `true`/`false`
            # don't masquerade as 1/0 timeouts.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"Step '{step_name}': '{field}' must be a number if provided"
                )
            # NaN/inf would make `deadline` non-finite so the poll loop's
            # `time.monotonic() >= deadline` never trips — the step would hang
            # forever instead of timing out. Reject them up front.
            if not math.isfinite(value):
                raise ValueError(
                    f"Step '{step_name}': '{field}' must be a finite number"
                )
            if value <= 0:
                raise ValueError(
                    f"Step '{step_name}': '{field}' must be greater than 0"
                )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        expected_failure = self._is_expected_failure()

        if _client_py_below(_CASCADE_STATUS_MIN_CLIENT_VERSION):
            min_str = ".".join(str(p) for p in _CASCADE_STATUS_MIN_CLIENT_VERSION)
            msg = (
                f"assert_cascade_complete requires calimero-client-py >= {min_str} "
                f"(installed: {_CLIENT_PY_VERSION_STR}) on {node_name}"
            )
            if expected_failure:
                self._report_expected_failure(msg)
                return True
            console.print(f"[red]{msg}[/red]")
            return False

        namespace_id = self._resolve_dynamic_value(
            self.config["namespace_id"], workflow_results, dynamic_values
        )
        timeout_seconds = float(
            self.config.get("timeout_seconds", self._DEFAULT_TIMEOUT_SECONDS)
        )
        poll_interval = float(
            self.config.get("poll_interval", self._DEFAULT_POLL_INTERVAL)
        )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
        except Exception as e:
            if expected_failure:
                self._report_expected_failure(str(e))
                return True
            console.print(
                f"[red]assert_cascade_complete: failed to reach {node_name}: {e}[/red]"
            )
            return False

        console.print(
            f"[blue]⏳ Waiting for cascade on namespace {namespace_id} to complete "
            f"(timeout {timeout_seconds:g}s, poll {poll_interval:g}s) on {node_name}[/blue]"
        )

        # monotonic() so a wall-clock adjustment mid-wait can't extend or
        # truncate the timeout. The first poll always runs; thereafter the
        # deadline is checked BEFORE committing to a sleep, and the final
        # sleep is clamped to the time remaining, so total wall-time stays
        # within timeout_seconds (no extra poll past the deadline, no skipped
        # poll while time remains).
        deadline = time.monotonic() + timeout_seconds
        last_summary: dict[str, Any] | None = None
        attempt = 0
        failure_reason = "timed out"

        while True:
            attempt += 1
            try:
                api_result = client.get_cascade_status(namespace_id=namespace_id)
                if self._check_jsonrpc_error(api_result):
                    # JSON-RPC error body on an otherwise-OK transport: treat
                    # like a transient read and retry until the deadline.
                    summary = None
                else:
                    summary = _summarize_cascade_status(api_result)
                    last_summary = summary
            except Exception as e:
                # Transient RPC error (node still booting, sync in flight).
                # Keep polling until the deadline rather than failing the
                # whole assertion on one bad read.
                vprint(
                    f"[yellow]  attempt {attempt}: get_cascade_status errored "
                    f"({type(e).__name__}); retrying[/yellow]",
                    level=LOG_LEVEL_VERBOSE,
                )
                summary = None

            if summary is not None:
                vprint(
                    f"[blue]  attempt {attempt}: {summary['completed']}/{summary['total']} "
                    f"completed, {summary['pending']} pending, {summary['failed']} failed[/blue]",
                    level=LOG_LEVEL_VERBOSE,
                )
                if summary["all_completed"]:
                    workflow_results[f"cascade_status_{node_name}"] = summary
                    console.print(
                        f"[green]✓ Cascade on namespace {namespace_id} complete: "
                        f"all {summary['total']} groups migrated on {node_name}[/green]"
                    )
                    if expected_failure:
                        self._report_unexpected_success()
                    return True
                if summary["failed"] > 0:
                    # Unrecoverable: a failed descendant means all_completed
                    # is unreachable. Stop early instead of polling to timeout.
                    failure_reason = (
                        f"{summary['failed']} of {summary['total']} groups failed"
                    )
                    break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))

        if last_summary is not None:
            workflow_results[f"cascade_status_{node_name}"] = last_summary
            detail = (
                f"{last_summary['completed']}/{last_summary['total']} completed, "
                f"{last_summary['pending']} pending, {last_summary['failed']} failed"
            )
        else:
            detail = "no successful status read"
        msg = (
            f"assert_cascade_complete on namespace {namespace_id} ({node_name}): "
            f"{failure_reason} after {attempt} poll(s) — {detail}"
        )

        if expected_failure:
            self._report_expected_failure(msg)
            return True
        console.print(f"[red]✗ {msg}[/red]")
        return False
