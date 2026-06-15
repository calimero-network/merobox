"""
Group upgrade & migration-lifecycle workflow step executors.

Covers signing-key rotation, the upgrade state machine (initiate -> poll
status -> retry on failure), the cascade/migration status rollups, and the
migrations-v2 recovery surface (resync a stranded context, list installed
bytecode versions).
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

# `abort_migration` needs the `abort_migration(namespace_id)` binding, added in
# calimero-client-py 0.6.18 (calimero-network/calimero-client-py#61, wrapping
# the admin route from calimero-network/core#2681).
_ABORT_MIGRATION_MIN_CLIENT_VERSION = (0, 6, 18)

# The migrations-v2 recovery surface — `resync_context`, `get_migration_status`
# (+ the `assert_migration_complete` helper built on it), and
# `list_application_versions` — all landed together in calimero-client-py 0.6.19
# (calimero-network/calimero-client-py#63), wrapping the admin routes from
# calimero-network/core#2768.
_MIGRATIONS_V2_MIN_CLIENT_VERSION = (0, 6, 19)


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
    `all_completed` flag, and re-attaches the raw per-group entries (under
    `groups`, see below) so callers can still reach them via `outputs:`.

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


def _summarize_migration_status(response: Any) -> dict[str, Any]:
    """Flatten a `get_migration_status` response into a flat, exportable summary.

    The RPC (calimero-network/core#2768) returns the pinned-cohort rollup
    (`rollup`: migrated / in_progress / unknown / failed / total +
    `all_migrated`) alongside one `members` row per cohort member
    (`{peer, report?, state}`). This lifts the rollup counters to the top level
    (so `outputs:` can address them without an `all_migrated` envelope), faithfully
    passes through core's authoritative `all_migrated` flag, and re-attaches a
    compact per-member list under `members` carrying each member's `state` plus
    the `migration_failed` reason from its report (a stranded member surfaces as
    `state:"failed"`). Counter keys are coerced to ints with a 0 default so a
    missing/partial rollup degrades to an all-zero (never-complete) summary
    rather than raising.

    `failed` is reconciled against the per-member states (`max` of the rollup
    counter and the count of members in `state:"failed"`) so the
    `assert_migration_complete` fast-exit still honours a failed member even
    when the rollup is missing or carries a non-int counter — without it the
    documented fail-fast would silently degrade to a poll-to-timeout. `total`
    falls back to the member count only when the rollup omits it entirely (an
    explicit `0` cohort is preserved).
    """
    rollup = response.get("rollup") if isinstance(response, dict) else None
    rollup = rollup if isinstance(rollup, dict) else {}
    raw_members = response.get("members") if isinstance(response, dict) else None
    raw_members = raw_members if isinstance(raw_members, list) else []

    members: list[dict[str, Any]] = []
    members_failed = 0
    for entry in raw_members:
        if not isinstance(entry, dict):
            continue
        report = entry.get("report")
        report = report if isinstance(report, dict) else None
        state = str(entry.get("state", "")).lower()
        if state == "failed":
            members_failed += 1
        members.append(
            {
                "peer": entry.get("peer"),
                "state": state,
                "migration_failed": (
                    report.get("migrationFailed") if report is not None else None
                ),
            }
        )

    def _as_int(value: Any) -> int:
        return (
            int(value) if isinstance(value, int) and not isinstance(value, bool) else 0
        )

    # `or len(members)` would mask a legitimate `total: 0` (empty cohort), so
    # only fall back when the key is absent entirely.
    total_raw = rollup.get("total")
    total = _as_int(total_raw) if total_raw is not None else len(members)

    return {
        "target_version": (
            response.get("targetVersion") if isinstance(response, dict) else None
        ),
        "expected_members": (
            response.get("expectedMembers") if isinstance(response, dict) else None
        ),
        "total": total,
        "migrated": _as_int(rollup.get("migrated")),
        "in_progress": _as_int(rollup.get("inProgress")),
        "unknown": _as_int(rollup.get("unknown")),
        # Reconcile with member states so a failed member is never missed when
        # the rollup counter is absent/malformed (see docstring).
        "failed": max(_as_int(rollup.get("failed")), members_failed),
        # core computes this directly: true iff every cohort member reported a
        # converged schema with zero residue. An empty/no-record response yields
        # false, so it can never falsely satisfy assert_migration_complete.
        "all_migrated": bool(rollup.get("allMigrated", False)),
        "members_pending_signature": _as_int(rollup.get("membersPendingSignature")),
        "members": members,
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
        # `.get(key, default)` returns the default only when the key is
        # ABSENT; an explicit `timeout_seconds: null` in YAML yields a present
        # key with value None, which float() would crash on. _validate_field_types
        # permits None (treats it as "not provided"), so collapse None to the
        # default here too. An explicit `is None` test (rather than `or`) so a
        # value of 0 is NOT silently swapped for the default — 0 is already
        # rejected by _validate_field_types, and `or` would mask that.
        timeout_raw = self.config.get("timeout_seconds")
        timeout_seconds = float(
            self._DEFAULT_TIMEOUT_SECONDS if timeout_raw is None else timeout_raw
        )
        poll_raw = self.config.get("poll_interval")
        poll_interval = float(
            self._DEFAULT_POLL_INTERVAL if poll_raw is None else poll_raw
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
                    # Support `outputs:` on the success path, same summary shape
                    # as get_cascade_status (total/completed/.../all_completed +
                    # the raw `groups` list), so authors can capture counts.
                    if "outputs" in self.config:
                        self._export_variables(summary, node_name, dynamic_values)
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


class AbortMigrationStep(BaseStep):
    """Logically abort an in-flight namespace migration.

    Calls the `abort_migration` binding (calimero-client-py 0.6.18, wrapping
    calimero-network/core#2681's
    `POST admin-api/groups/{namespace_id}/migration/abort`). Flips the
    namespace's pending migration target back to the pre-migration app id and
    drops the pending marker, cascading to descendant subgroups. Idempotent
    (nothing pending => no-op). The `{namespace_id, aborted}` response is stored
    under `abort_migration_{node}` and reachable from an `outputs:` block.

    Requires calimero-client-py >= 0.6.18.
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

        # Pre-flight version guard, same policy as the cascade steps: only block
        # when client-py is *known* to predate the binding. Honors
        # expected_failure for regression workflows pinning an old client.
        if _client_py_below(_ABORT_MIGRATION_MIN_CLIENT_VERSION):
            min_str = ".".join(str(p) for p in _ABORT_MIGRATION_MIN_CLIENT_VERSION)
            msg = (
                f"abort_migration requires calimero-client-py >= {min_str} "
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
            api_result = client.abort_migration(namespace_id=namespace_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("abort_migration failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]abort_migration failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        # A transport-level success can still carry a JSON-RPC error body.
        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        # The abort route returns a `{namespace_id, aborted}` object; coerce any
        # non-dict body to {} (rather than `or {}`, which would also swallow a
        # falsy-but-valid dict) so export/lookup below stay well-typed.
        raw = result["data"]
        data = raw if isinstance(raw, dict) else {}
        workflow_results[f"abort_migration_{node_name}"] = data
        if "outputs" in self.config:
            self._export_variables(data, node_name, dynamic_values)
        aborted = data.get("aborted")
        console.print(
            f"[green]✓ abort_migration on namespace {namespace_id} ({node_name}): "
            f"aborted={aborted}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class GetMigrationStatusStep(BaseStep):
    """Read the pinned-cohort migration-status rollup for a namespace.

    Calls the `get_migration_status` RPC (calimero-network/core#2768), which
    returns the cohort rollup (migrated / in_progress / unknown / failed / total
    + `all_migrated`) plus one row per cohort member (`{peer, report?, state}`).
    The response is flattened by `_summarize_migration_status` and stored under
    `migration_status_{node}`; the counter fields, `all_migrated`, and the raw
    per-member `members` list are reachable from an `outputs:` block. A stranded
    member surfaces as `state:"failed"` with its `migration_failed` reason.
    Observability only — this never gates a write or apply.

    Requires calimero-client-py >= 0.6.19 for the `get_migration_status`
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

        # Pre-flight version guard, same policy as the cascade-status step: only
        # block when client-py is *known* to predate the binding. Runs before
        # dynamic-value resolution and honors expected_failure.
        if _client_py_below(_MIGRATIONS_V2_MIN_CLIENT_VERSION):
            min_str = ".".join(str(p) for p in _MIGRATIONS_V2_MIN_CLIENT_VERSION)
            msg = (
                f"get_migration_status requires calimero-client-py >= {min_str} "
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
            api_result = client.get_migration_status(namespace_id=namespace_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("get_migration_status failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]get_migration_status failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        # A transport-level success can still carry a JSON-RPC error body; mirror
        # the cascade-status step so it isn't silently summarised as empty.
        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        summary = _summarize_migration_status(result["data"])
        workflow_results[f"migration_status_{node_name}"] = summary
        if "outputs" in self.config:
            self._export_variables(summary, node_name, dynamic_values)
        console.print(
            f"[green]✓ Migration status for namespace {namespace_id} on {node_name}: "
            f"{summary['migrated']}/{summary['total']} migrated, "
            f"{summary['in_progress']} in-progress, {summary['unknown']} unknown, "
            f"{summary['failed']} failed[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class AssertMigrationCompleteStep(BaseStep):
    """Poll `get_migration_status` until the whole pinned cohort has migrated.

    Saves workflow authors from hand-rolling a `wait`-loop around
    `get_migration_status`. Polls every `poll_interval` seconds (default `2.0`)
    until the cohort is fully migrated (core's `all_migrated` — every member
    reported a converged schema with zero residue) or `timeout_seconds`
    (default `30`) elapses. A member entering the `failed` state aborts the
    wait immediately: a cohort with a failed member can never reach
    `all_migrated`, so there is no point burning the rest of the timeout.

    On the happy path the final summary is stored under
    `migration_status_{node}` (same shape as `get_migration_status`). A timeout
    or a failed member fails the step unless `expected_failure` is set.

    Requires calimero-client-py >= 0.6.19 for the `get_migration_status`
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

        if _client_py_below(_MIGRATIONS_V2_MIN_CLIENT_VERSION):
            min_str = ".".join(str(p) for p in _MIGRATIONS_V2_MIN_CLIENT_VERSION)
            msg = (
                f"assert_migration_complete requires calimero-client-py >= {min_str} "
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
        # `.get(key, default)` returns the default only when the key is ABSENT;
        # an explicit `timeout_seconds: null` yields a present key with value
        # None, which float() would crash on. _validate_field_types permits None
        # (treats it as "not provided"), so collapse None to the default here
        # too. An explicit `is None` test (not `or`) so a value of 0 is NOT
        # silently swapped for the default — 0 is already rejected above.
        timeout_raw = self.config.get("timeout_seconds")
        timeout_seconds = float(
            self._DEFAULT_TIMEOUT_SECONDS if timeout_raw is None else timeout_raw
        )
        poll_raw = self.config.get("poll_interval")
        poll_interval = float(
            self._DEFAULT_POLL_INTERVAL if poll_raw is None else poll_raw
        )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
        except Exception as e:
            if expected_failure:
                self._report_expected_failure(str(e))
                return True
            console.print(
                f"[red]assert_migration_complete: failed to reach {node_name}: {e}[/red]"
            )
            return False

        console.print(
            f"[blue]⏳ Waiting for migration on namespace {namespace_id} to complete "
            f"(timeout {timeout_seconds:g}s, poll {poll_interval:g}s) on {node_name}[/blue]"
        )

        # monotonic() so a wall-clock adjustment mid-wait can't extend or
        # truncate the timeout. The first poll always runs; thereafter the
        # deadline is checked BEFORE committing to a sleep, and the final sleep
        # is clamped to the time remaining, so total wall-time stays within
        # timeout_seconds.
        deadline = time.monotonic() + timeout_seconds
        last_summary: dict[str, Any] | None = None
        attempt = 0
        failure_reason = "timed out"

        while True:
            attempt += 1
            try:
                api_result = client.get_migration_status(namespace_id=namespace_id)
                if self._check_jsonrpc_error(api_result):
                    # JSON-RPC error body on an otherwise-OK transport: treat
                    # like a transient read and retry until the deadline.
                    summary = None
                else:
                    summary = _summarize_migration_status(api_result)
                    last_summary = summary
            except Exception as e:
                # Transient RPC error (node still booting, sync in flight). Keep
                # polling until the deadline rather than failing the whole
                # assertion on one bad read.
                vprint(
                    f"[yellow]  attempt {attempt}: get_migration_status errored "
                    f"({type(e).__name__}); retrying[/yellow]",
                    level=LOG_LEVEL_VERBOSE,
                )
                summary = None

            if summary is not None:
                vprint(
                    f"[blue]  attempt {attempt}: {summary['migrated']}/{summary['total']} "
                    f"migrated, {summary['in_progress']} in-progress, "
                    f"{summary['unknown']} unknown, {summary['failed']} failed[/blue]",
                    level=LOG_LEVEL_VERBOSE,
                )
                if summary["all_migrated"]:
                    workflow_results[f"migration_status_{node_name}"] = summary
                    if "outputs" in self.config:
                        self._export_variables(summary, node_name, dynamic_values)
                    console.print(
                        f"[green]✓ Migration on namespace {namespace_id} complete: "
                        f"all {summary['total']} cohort members migrated on {node_name}[/green]"
                    )
                    if expected_failure:
                        self._report_unexpected_success()
                    return True
                if summary["failed"] > 0:
                    # Unrecoverable: a failed member means all_migrated is
                    # unreachable. Stop early instead of polling to timeout.
                    failure_reason = (
                        f"{summary['failed']} of {summary['total']} members failed"
                    )
                    break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))

        if last_summary is not None:
            workflow_results[f"migration_status_{node_name}"] = last_summary
            # Export on the failure/timeout exit too (not just the success
            # path): under expected_failure the step returns True and
            # downstream steps run, so they should see the final migration
            # state rather than an unset variable.
            if "outputs" in self.config:
                self._export_variables(last_summary, node_name, dynamic_values)
            detail = (
                f"{last_summary['migrated']}/{last_summary['total']} migrated, "
                f"{last_summary['in_progress']} in-progress, "
                f"{last_summary['unknown']} unknown, {last_summary['failed']} failed"
            )
        else:
            detail = "no successful status read"
        msg = (
            f"assert_migration_complete on namespace {namespace_id} ({node_name}): "
            f"{failure_reason} after {attempt} poll(s) — {detail}"
        )

        if expected_failure:
            self._report_expected_failure(msg)
            return True
        console.print(f"[red]✗ {msg}[/red]")
        return False


class ResyncContextStep(BaseStep):
    """Resync a stranded context by adopting a peer's full state.

    Calls the `resync_context` binding (calimero-client-py 0.6.19, wrapping
    calimero-network/core#2768's `POST admin-api/contexts/{context_id}/resync`).
    Recovers a context that can no longer replay its upgrade ladder (an
    intermediate bytecode blob is unobtainable from every reachable peer) by
    discarding local DAG heads and pulling a peer's full-state snapshot.

    Destructive: `force` (default `false`) must be `true` when the context still
    holds local DAG heads, which the resync discards. The `{context_id,
    resync_started}` response is stored under `resync_context_{node}` and
    reachable from an `outputs:` block.

    Requires calimero-client-py >= 0.6.19.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "context_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        force = self.config.get("force")
        if force is not None and not isinstance(force, bool):
            raise ValueError(
                f"Step '{step_name}': 'force' must be a boolean if provided"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]

        if _client_py_below(_MIGRATIONS_V2_MIN_CLIENT_VERSION):
            min_str = ".".join(str(p) for p in _MIGRATIONS_V2_MIN_CLIENT_VERSION)
            msg = (
                f"resync_context requires calimero-client-py >= {min_str} "
                f"(installed: {_CLIENT_PY_VERSION_STR}) on {node_name}"
            )
            if self._is_expected_failure():
                self._report_expected_failure(msg)
                return True
            console.print(f"[red]{msg}[/red]")
            return False

        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        force = bool(self.config.get("force", False))

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.resync_context(context_id=context_id, force=force)
            result = ok(api_result)
        except Exception as e:
            result = fail("resync_context failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]resync_context failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        raw = result["data"]
        # Warn (don't silently drop) on an unexpected non-dict body so an
        # operator can diagnose why `outputs:` fields resolve to None.
        if not isinstance(raw, dict):
            console.print(
                f"[yellow]resync_context: unexpected response type "
                f"{type(raw).__name__} on {node_name}, expected dict[/yellow]"
            )
        data = raw if isinstance(raw, dict) else {}
        workflow_results[f"resync_context_{node_name}"] = data
        if "outputs" in self.config:
            self._export_variables(data, node_name, dynamic_values)
        resync_started = data.get("resyncStarted")
        console.print(
            f"[green]✓ resync_context on {context_id} ({node_name}, force={force}): "
            f"resync_started={resync_started}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class ListApplicationVersionsStep(BaseStep):
    """List every locally-retained bytecode version of an application.

    Calls the `list_application_versions` binding (calimero-client-py 0.6.19,
    wrapping calimero-network/core#2768's
    `GET admin-api/applications/{id}/versions`). Returns one entry per retained
    version (`{version, blob_id, size, package}`) — the row's latest install
    plus any older blobs still referenced by groups or context activation
    markers. The `blob_id` doubles as the `app_key` accepted by the
    `create_namespace` step to pin a namespace to a specific version.

    The response is summarised into `{count, versions}` and stored under
    `list_application_versions_{node}`. The list is re-attached under `versions`
    (NOT `data`): the export machinery (`_export_custom_outputs`) unwraps a
    top-level `data` key before resolving `outputs:` paths, which would leave
    `outputs: {x: "data"}` resolving against the inner list and break `app_key`
    chaining. With `versions`, authors capture the whole list
    (`outputs: {vs: "versions"}`) or pick a specific blob id
    (`outputs: {v3_key: "versions.2.blob_id"}`).

    Requires calimero-client-py >= 0.6.19.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "application_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "application_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]

        if _client_py_below(_MIGRATIONS_V2_MIN_CLIENT_VERSION):
            min_str = ".".join(str(p) for p in _MIGRATIONS_V2_MIN_CLIENT_VERSION)
            msg = (
                f"list_application_versions requires calimero-client-py >= {min_str} "
                f"(installed: {_CLIENT_PY_VERSION_STR}) on {node_name}"
            )
            if self._is_expected_failure():
                self._report_expected_failure(msg)
                return True
            console.print(f"[red]{msg}[/red]")
            return False

        application_id = self._resolve_dynamic_value(
            self.config["application_id"], workflow_results, dynamic_values
        )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.list_application_versions(application_id=application_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("list_application_versions failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]list_application_versions failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        data = result["data"]
        raw_versions = data.get("data") if isinstance(data, dict) else None
        versions = raw_versions if isinstance(raw_versions, list) else []
        # Re-attach under `versions` (not `data`) so the export unwrap doesn't
        # strip the list before resolving `outputs:` paths — see class docstring.
        summary = {"count": len(versions), "versions": versions}
        workflow_results[f"list_application_versions_{node_name}"] = summary
        if "outputs" in self.config:
            self._export_variables(summary, node_name, dynamic_values)
        console.print(
            f"[green]✓ list_application_versions for {application_id} on {node_name}: "
            f"{summary['count']} version(s)[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True
