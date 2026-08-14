"""
Unit tests for the migrations-v2 workflow steps (calimero-client-py 0.6.19,
wrapping calimero-network/core#2768): `get_migration_status`,
`assert_migration_complete`, `resync_context`, and `list_application_versions`.

Mirrors `test_cascade_status_steps.py` / `test_abort_migration_step.py`: the
conftest stubs the `calimero_client_py` import, and each execute test patches
`get_client_for_rpc_url` to return a MagicMock. On a Python without
calimero-client-py installed, `_CLIENT_PY_VERSION` resolves to None so the
version pre-flight is skipped and never interferes here.
"""

import asyncio
import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.group_upgrade import (
    AssertMigrationCompleteStep,
    GetMigrationStatusStep,
    ListApplicationVersionsStep,
    ResyncContextStep,
    _summarize_migration_status,
)

_MODULE = "merobox.commands.bootstrap.steps.group_upgrade"


def _run(coro):
    # Isolated loop so this file can't pollute sibling test modules' global
    # loop state (see test_cascade_status_steps.py for the rationale).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _rollup(**overrides):
    base = {
        "migrated": 0,
        "inProgress": 0,
        "unknown": 0,
        "failed": 0,
        "total": 0,
        "allMigrated": False,
        "membersPendingSignature": 0,
    }
    base.update(overrides)
    return base


# =============================================================================
# _summarize_migration_status — pure rollup flattening
# =============================================================================


class TestSummarizeMigrationStatus:
    def test_full_rollup_passthrough(self):
        response = {
            "targetVersion": 3,
            "expectedMembers": 2,
            "rollup": _rollup(migrated=2, total=2, allMigrated=True),
            "members": [
                {"peer": "peerA", "state": "migrated"},
                {"peer": "peerB", "state": "migrated"},
            ],
        }
        s = _summarize_migration_status(response)
        assert s["target_version"] == 3
        assert s["expected_members"] == 2
        assert s["migrated"] == 2
        assert s["total"] == 2
        assert s["all_migrated"] is True
        assert [m["peer"] for m in s["members"]] == ["peerA", "peerB"]

    def test_failed_member_surfaces_reason(self):
        response = {
            "rollup": _rollup(failed=1, total=1),
            "members": [
                {
                    "peer": "peerX",
                    "state": "failed",
                    "report": {"migrationFailed": "no_migration_path"},
                }
            ],
        }
        s = _summarize_migration_status(response)
        assert s["failed"] == 1
        assert s["all_migrated"] is False
        assert s["members"][0]["state"] == "failed"
        assert s["members"][0]["migration_failed"] == "no_migration_path"

    def test_missing_rollup_degrades_to_all_zero(self):
        s = _summarize_migration_status(
            {"members": [{"peer": "p", "state": "unknown"}]}
        )
        assert s["total"] == 1  # falls back to len(members)
        assert s["migrated"] == 0
        assert s["all_migrated"] is False
        assert s["members"][0]["migration_failed"] is None

    def test_garbage_response_is_empty_summary(self):
        s = _summarize_migration_status("not-a-dict")
        assert s["total"] == 0
        assert s["all_migrated"] is False
        assert s["members"] == []
        assert s["fleet_completed_at"] is None
        assert s["cohort_pinned_at_hlc"] is None

    def test_fleet_completed_at_is_surfaced(self):
        # The durable evidence that convergence happened, which allMigrated
        # cannot provide: it is TTL-derived and lapses when a member goes quiet.
        s = _summarize_migration_status(
            {
                "fleetCompletedAt": 1_700_002_000,
                "cohortPinnedAtHlc": "hlc-abc",
                "rollup": _rollup(total=1),
            }
        )
        assert s["fleet_completed_at"] == 1_700_002_000
        assert s["cohort_pinned_at_hlc"] == "hlc-abc"

    def test_fleet_completed_at_outlives_a_lapsed_all_migrated(self):
        # A converged fleet whose members went quiet: allMigrated is back to
        # false, and the timestamp is the only remaining evidence.
        s = _summarize_migration_status(
            {
                "fleetCompletedAt": 1_700_002_000,
                "rollup": _rollup(unknown=2, total=2, allMigrated=False),
            }
        )
        assert s["all_migrated"] is False
        assert s["fleet_completed_at"] == 1_700_002_000

    def test_an_unconverged_namespace_reports_none_not_zero(self):
        # Core omits the key entirely rather than sending 0, and 0 is a valid
        # unix timestamp, so absent must not collapse into it.
        s = _summarize_migration_status({"rollup": _rollup(total=2)})
        assert s["fleet_completed_at"] is None
        assert s["cohort_pinned_at_hlc"] is None

    def test_a_zero_fleet_timestamp_is_preserved(self):
        s = _summarize_migration_status(
            {"fleetCompletedAt": 0, "rollup": _rollup(total=1)}
        )
        assert s["fleet_completed_at"] == 0

    def test_bool_counters_coerced_to_zero(self):
        # A bool is an int subclass — it must NOT be treated as a count.
        s = _summarize_migration_status({"rollup": {"migrated": True, "total": 4}})
        assert s["migrated"] == 0
        assert s["total"] == 4

    def test_non_dict_member_entries_skipped(self):
        s = _summarize_migration_status(
            {"members": ["junk", {"peer": "p", "state": "MIGRATED"}]}
        )
        assert len(s["members"]) == 1
        # state is lowercased
        assert s["members"][0]["state"] == "migrated"

    def test_failed_reconciled_from_members_when_rollup_missing(self):
        # Bugbot #1: a failed member must surface in `failed` even when the
        # rollup omits the counter, so the assert fast-exit still fires.
        s = _summarize_migration_status({"members": [{"peer": "p", "state": "failed"}]})
        assert s["failed"] == 1

    def test_failed_takes_max_of_rollup_and_members(self):
        s = _summarize_migration_status(
            {
                "rollup": _rollup(failed=2, total=3),
                "members": [{"peer": "p", "state": "failed"}],
            }
        )
        # rollup is authoritative when larger; reconciliation never undercounts.
        assert s["failed"] == 2

    def test_explicit_zero_total_preserved(self):
        # An explicit empty cohort (total: 0) must NOT fall back to len(members).
        s = _summarize_migration_status(
            {
                "rollup": _rollup(total=0),
                "members": [{"peer": "p", "state": "unknown"}],
            }
        )
        assert s["total"] == 0

    def test_all_migrated_false_when_member_failed(self):
        # Bugbot HIGH: a (contradictory) response claiming allMigrated while a
        # member is failed must NOT report all_migrated — the assert poll loop
        # checks all_migrated before failed.
        s = _summarize_migration_status(
            {
                "rollup": _rollup(migrated=1, total=2, allMigrated=True),
                "members": [
                    {"peer": "a", "state": "migrated"},
                    {"peer": "b", "state": "failed"},
                ],
            }
        )
        assert s["failed"] == 1
        assert s["all_migrated"] is False

    def test_all_migrated_false_when_a_member_is_unknown(self):
        # `unknown` is where a member that never reported sits; trusting core's
        # flag would report a cohort with a silent member as converged.
        s = _summarize_migration_status(
            {
                "rollup": _rollup(migrated=1, unknown=1, total=2, allMigrated=True),
                "members": [
                    {"peer": "a", "state": "migrated"},
                    {"peer": "b", "state": "unknown"},
                ],
            }
        )
        assert s["unknown"] == 1
        assert s["all_migrated"] is False

    def test_all_migrated_false_when_a_member_is_in_progress(self):
        s = _summarize_migration_status(
            {
                "rollup": _rollup(migrated=1, inProgress=1, total=2, allMigrated=True),
                "members": [
                    {"peer": "a", "state": "migrated"},
                    {"peer": "b", "state": "in_progress"},
                ],
            }
        )
        assert s["in_progress"] == 1
        assert s["all_migrated"] is False

    def test_the_in_progress_state_is_counted_whichever_way_core_spells_it(self):
        # The rollup spells its own counter `inProgress`, so the member state
        # may well be camelCase too. Reconciliation must not depend on which.
        for spelling in ("in_progress", "inProgress", "IN_PROGRESS", "in-progress"):
            s = _summarize_migration_status(
                {
                    "rollup": _rollup(migrated=1, total=2, allMigrated=True),
                    "members": [
                        {"peer": "a", "state": "migrated"},
                        {"peer": "b", "state": spelling},
                    ],
                }
            )
            assert s["in_progress"] == 1, spelling
            assert s["all_migrated"] is False, spelling

    def test_unknown_and_in_progress_reconciled_from_members(self):
        # The rollup zeroes both counters while the member rows contradict it,
        # the same shape the `failed` reconciliation was written for.
        s = _summarize_migration_status(
            {
                "rollup": _rollup(migrated=2, total=2, allMigrated=True),
                "members": [
                    {"peer": "a", "state": "unknown"},
                    {"peer": "b", "state": "in_progress"},
                ],
            }
        )
        assert s["unknown"] == 1
        assert s["in_progress"] == 1
        assert s["all_migrated"] is False

    def test_all_migrated_false_on_a_state_no_counter_names(self):
        # A state core adds or spells differently is still not `migrated`, so
        # it must not be waved through as converged.
        s = _summarize_migration_status(
            {
                "rollup": _rollup(migrated=2, total=2, allMigrated=True),
                "members": [
                    {"peer": "a", "state": "migrated"},
                    {"peer": "b", "state": "quarantined"},
                ],
            }
        )
        assert s["all_migrated"] is False

    def test_all_migrated_survives_a_fully_converged_cohort(self):
        s = _summarize_migration_status(
            {
                "rollup": _rollup(migrated=2, total=2, allMigrated=True),
                "members": [
                    {"peer": "a", "state": "migrated"},
                    {"peer": "b", "state": "migrated"},
                ],
            }
        )
        assert s["all_migrated"] is True


# =============================================================================
# _summarize_migration_status - the per-member report and the aggregates
# recomputed from it, the only evidence that a member STATE-migrated.
# =============================================================================


def _member(peer, state="migrated", **report):
    return {"peer": peer, "state": state, "report": {**report}}


class TestReportedSchemaVersions:
    def test_report_fields_are_passed_through(self):
        s = _summarize_migration_status(
            {
                "targetVersion": 2,
                "rollup": _rollup(migrated=1, total=1, allMigrated=True),
                "members": [
                    _member(
                        "peerA",
                        schemaVersion=2,
                        residueAuto=0,
                        residueIdentity=0,
                        syncedUpToHlc="hlc-9",
                        reportedAt=1_700_000_000,
                        authoredRemaining=0,
                    )
                ],
            }
        )
        m = s["members"][0]
        assert m["schema_version"] == 2
        assert m["residue_auto"] == 0
        assert m["synced_up_to_hlc"] == "hlc-9"
        assert m["reported_at"] == 1_700_000_000
        assert m["authored_remaining"] == 0

    def test_a_member_below_target_is_counted_even_when_core_claims_converged(self):
        # The load-bearing case: a rollup comparing the wrong version axis can
        # answer allMigrated while a member still holds pre-migration state.
        s = _summarize_migration_status(
            {
                "targetVersion": 2,
                "rollup": _rollup(migrated=2, total=2, allMigrated=True),
                "members": [
                    _member("peerA", schemaVersion=2),
                    _member("peerB", schemaVersion=1),
                ],
            }
        )
        assert s["all_migrated"] is True
        assert s["reported_below_target"] == 1
        assert s["reported_at_target"] == 1
        assert s["min_reported_schema_version"] == 1

    def test_a_converged_cohort_is_zero_below_target(self):
        s = _summarize_migration_status(
            {
                "targetVersion": 2,
                "rollup": _rollup(migrated=2, total=2, allMigrated=True),
                "members": [
                    _member("peerA", schemaVersion=2),
                    _member("peerB", schemaVersion=3),
                ],
            }
        )
        assert s["reported_below_target"] == 0
        assert s["reported_at_target"] == 2
        assert s["reported_missing"] == 0
        assert s["min_reported_schema_version"] == 2

    def test_no_report_is_missing_not_below_target(self):
        # "did not report" and "reported the wrong version" are different
        # failures, and only the latter is evidence the migration ran wrong.
        s = _summarize_migration_status(
            {
                "targetVersion": 2,
                "rollup": _rollup(migrated=1, unknown=1, total=2),
                "members": [
                    _member("peerA", schemaVersion=2),
                    {"peer": "peerB", "state": "unknown"},
                ],
            }
        )
        assert s["reported_missing"] == 1
        assert s["reported_below_target"] == 0
        assert s["reported_at_target"] == 1
        assert s["members"][1]["schema_version"] is None

    def test_absent_target_leaves_the_target_relative_counters_none(self):
        # Defaulting the target to 0 would classify every member as at-target
        # and hand back a green summary for a namespace that never migrated.
        s = _summarize_migration_status(
            {
                "rollup": _rollup(total=1),
                "members": [_member("peerA", schemaVersion=1)],
            }
        )
        assert s["reported_at_target"] is None
        assert s["reported_below_target"] is None
        # The target-independent aggregates are still computed.
        assert s["min_reported_schema_version"] == 1
        assert s["reported_missing"] == 0

    def test_absent_target_still_reports_a_silent_cohort_as_missing(self):
        s = _summarize_migration_status(
            {"rollup": _rollup(total=2), "members": [{"peer": "a"}, {"peer": "b"}]}
        )
        assert s["reported_missing"] == 2
        assert s["min_reported_schema_version"] is None

    def test_a_bool_schema_version_counts_as_missing(self):
        # bool is an int subclass. Counting True as version 1 would silently
        # manufacture a reported version out of a malformed report.
        s = _summarize_migration_status(
            {
                "targetVersion": 2,
                "members": [_member("peerA", schemaVersion=True)],
            }
        )
        assert s["reported_missing"] == 1
        assert s["reported_below_target"] == 0

    def test_residue_is_summed_and_zero_stays_zero(self):
        s = _summarize_migration_status(
            {
                "targetVersion": 2,
                "members": [
                    _member("peerA", schemaVersion=2, residueAuto=3),
                    _member("peerB", schemaVersion=2, residueAuto=0),
                    _member("peerC", schemaVersion=2),
                ],
            }
        )
        assert s["residue_total"] == 3

    def test_failure_reasons_are_sorted_and_order_independent(self):
        # A sorted list can be asserted whole; members.N.migration_failed
        # depends on an ordering the API does not guarantee.
        s = _summarize_migration_status(
            {
                "members": [
                    _member(
                        "peerA", state="failed", migrationFailed="no_migration_path"
                    ),
                    _member("peerB", state="failed", migrationFailed="check_aborted"),
                    _member("peerC", schemaVersion=2),
                ]
            }
        )
        assert s["failure_reasons"] == ["check_aborted", "no_migration_path"]

    def test_no_failures_is_an_empty_reason_list(self):
        s = _summarize_migration_status(
            {"members": [_member("peerA", schemaVersion=2)]}
        )
        assert s["failure_reasons"] == []
        assert s["stuck_members"] == []

    def test_stuck_members_name_the_peers_behind_the_reasons(self):
        # The reason list says WHAT went wrong; a count plus a reason still
        # does not say WHICH member is holding the fleet back.
        s = _summarize_migration_status(
            {
                "members": [
                    _member("peerB", state="failed", migrationFailed="check_aborted"),
                    _member(
                        "peerA", state="failed", migrationFailed="no_migration_path"
                    ),
                    _member("peerC", schemaVersion=2),
                ]
            }
        )
        assert s["stuck_members"] == ["peerA", "peerB"]


# =============================================================================
# GetMigrationStatusStep
# =============================================================================


class TestGetMigrationStatusValidation:
    def setup_method(self):
        self.base = {
            "type": "get_migration_status",
            "name": "Status",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
        }

    def test_valid_config_passes(self):
        GetMigrationStatusStep(self.base)

    def test_missing_namespace_id_raises(self):
        cfg = {**self.base}
        del cfg["namespace_id"]
        with pytest.raises(ValueError, match="namespace_id"):
            GetMigrationStatusStep(cfg)

    def test_namespace_id_not_string_raises(self):
        cfg = {**self.base, "namespace_id": 7}
        with pytest.raises(ValueError, match="'namespace_id' must be a string"):
            GetMigrationStatusStep(cfg)


class TestGetMigrationStatusExecute:
    def setup_method(self):
        self.config = {
            "type": "get_migration_status",
            "name": "Status",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
        }

    def _patched(self, step, client):
        return (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "calimero-node-1"),
            ),
            patch(f"{_MODULE}.get_client_for_rpc_url", return_value=client),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
        )

    def test_success_stores_summary(self):
        step = GetMigrationStatusStep(self.config)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "rollup": _rollup(migrated=1, total=1, allMigrated=True),
            "members": [{"peer": "p", "state": "migrated"}],
        }
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        client.get_migration_status.assert_called_once_with(namespace_id="ns123")
        summary = workflow_results["migration_status_calimero-node-1"]
        assert summary["all_migrated"] is True
        assert summary["migrated"] == 1

    def test_outputs_export_summary_fields(self):
        cfg = {**self.config, "outputs": {"done": "all_migrated"}}
        step = GetMigrationStatusStep(cfg)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "rollup": _rollup(migrated=1, total=1, allMigrated=True),
            "members": [],
        }
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("done") is True

    def test_client_error_fails_step(self):
        step = GetMigrationStatusStep(self.config)
        client = MagicMock()
        client.get_migration_status.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_client_error_with_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = GetMigrationStatusStep(cfg)
        client = MagicMock()
        client.get_migration_status.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is True

    def test_jsonrpc_error_body_fails_step(self):
        step = GetMigrationStatusStep(self.config)
        client = MagicMock()
        client.get_migration_status.return_value = {"error": {"code": -1}}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3, patch.object(step, "_check_jsonrpc_error", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False

    def test_version_guard_old_client_fails(self):
        step = GetMigrationStatusStep(self.config)
        with patch(f"{_MODULE}._client_py_below", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False


# =============================================================================
# AssertMigrationCompleteStep
# =============================================================================


class TestAssertMigrationCompleteValidation:
    def setup_method(self):
        self.base = {
            "type": "assert_migration_complete",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
        }

    def test_valid_defaults(self):
        AssertMigrationCompleteStep(self.base)

    def test_valid_with_optional_timing(self):
        AssertMigrationCompleteStep(
            {**self.base, "timeout_seconds": 60, "poll_interval": 3.0}
        )

    def test_bool_poll_interval_raises(self):
        with pytest.raises(ValueError, match="poll_interval"):
            AssertMigrationCompleteStep({**self.base, "poll_interval": True})

    def test_non_positive_timeout_raises(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            AssertMigrationCompleteStep({**self.base, "timeout_seconds": 0})


class TestAssertMigrationCompleteExecute:
    def setup_method(self):
        self.config = {
            "type": "assert_migration_complete",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
            "poll_interval": 0.01,
        }

    def _patched(self, step, client):
        return (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "calimero-node-1"),
            ),
            patch(f"{_MODULE}.get_client_for_rpc_url", return_value=client),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
            patch(f"{_MODULE}.asyncio.sleep", new=AsyncMock()),
        )

    def test_immediate_complete_passes(self):
        step = AssertMigrationCompleteStep(self.config)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "rollup": _rollup(migrated=2, total=2, allMigrated=True),
            "members": [],
        }
        workflow_results = {}
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        assert workflow_results["migration_status_calimero-node-1"]["all_migrated"]

    def test_poll_then_complete_passes(self):
        step = AssertMigrationCompleteStep(self.config)
        client = MagicMock()
        client.get_migration_status.side_effect = [
            {"rollup": _rollup(inProgress=1, total=1), "members": []},
            {"rollup": _rollup(migrated=1, total=1, allMigrated=True), "members": []},
        ]
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4:
            result = _run(step.execute({}, {}))
        assert result is True
        # Both polls must run — the first (in-progress) should not short-circuit.
        assert client.get_migration_status.call_count == 2

    def test_failed_member_exits_early(self):
        step = AssertMigrationCompleteStep(self.config)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "rollup": _rollup(failed=1, total=1),
            "members": [
                {
                    "peer": "p",
                    "state": "failed",
                    "report": {"migrationFailed": "no_migration_path"},
                }
            ],
        }
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_failed_member_exits_early_without_rollup_counter(self):
        # Bugbot #1: the fast-exit must fire on a failed member even when the
        # rollup omits the `failed` counter (reconciled via member states).
        step = AssertMigrationCompleteStep(self.config)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "members": [{"peer": "p", "state": "failed"}],
        }
        p1, p2, p3, p4 = self._patched(step, client)
        with (
            p1,
            p2,
            p3,
            p4,
            # If the fast-exit failed to fire, monotonic would advance past the
            # deadline and the step would still fail — but via timeout, not the
            # failed-member branch. Keep time frozen so only the fast-exit can
            # end the single poll; a hang would surface as a test timeout.
            patch(f"{_MODULE}.time.monotonic", side_effect=itertools.count(0, 0)),
        ):
            result = _run(step.execute({}, {}))
        assert result is False

    def test_outputs_exported_on_failure_exit(self):
        # meroreviewer 🟡: under expected_failure the step returns True and
        # downstream runs, so the final summary must be exported even on the
        # fail-fast path.
        cfg = {
            **self.config,
            "expected_failure": True,
            "outputs": {"failed_count": "failed"},
        }
        step = AssertMigrationCompleteStep(cfg)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "rollup": _rollup(failed=1, total=1),
            "members": [{"peer": "p", "state": "failed"}],
        }
        dynamic_values = {}
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("failed_count") == 1

    def test_timeout_fails(self):
        step = AssertMigrationCompleteStep(self.config)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "rollup": _rollup(inProgress=1, total=1),
            "members": [],
        }
        p1, p2, p3, p4 = self._patched(step, client)
        # monotonic jumps past the deadline after the first poll.
        with (
            p1,
            p2,
            p3,
            p4,
            patch(f"{_MODULE}.time.monotonic", side_effect=itertools.count(0, 5)),
        ):
            result = _run(step.execute({}, {}))
        assert result is False

    def test_timeout_message_names_the_below_target_member(self, capsys):
        # The bare counters cannot separate "a member is slow" from "a member is
        # on the wrong state version".
        step = AssertMigrationCompleteStep(self.config)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "targetVersion": 2,
            "rollup": _rollup(migrated=1, inProgress=1, total=2),
            "members": [
                _member("peerA", schemaVersion=2),
                _member("peerB", state="in_progress", schemaVersion=1),
            ],
        }
        p1, p2, p3, p4 = self._patched(step, client)
        with (
            p1,
            p2,
            p3,
            p4,
            patch(f"{_MODULE}.time.monotonic", side_effect=itertools.count(0, 5)),
        ):
            assert _run(step.execute({}, {})) is False
        # Rich hard-wraps to the terminal width, so compare on collapsed space.
        out = " ".join(capsys.readouterr().out.split())
        assert "1 member(s) below target state version 2" in out
        assert "min reported: 1" in out

    def test_all_polls_jsonrpc_error_times_out(self):
        # Every poll returns a JSON-RPC error body → summary always None →
        # last_summary stays None → exit via timeout, no migration_status stored.
        step = AssertMigrationCompleteStep(self.config)
        client = MagicMock()
        client.get_migration_status.return_value = {"error": {"code": -1}}
        workflow_results = {}
        p1, p2, p3, p4 = self._patched(step, client)
        with (
            p1,
            p2,
            p3,
            p4,
            patch.object(step, "_check_jsonrpc_error", return_value=True),
            patch(f"{_MODULE}.time.monotonic", side_effect=itertools.count(0, 5)),
        ):
            result = _run(step.execute(workflow_results, {}))
        assert result is False
        assert "migration_status_calimero-node-1" not in workflow_results

    def test_timeout_with_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = AssertMigrationCompleteStep(cfg)
        client = MagicMock()
        client.get_migration_status.return_value = {
            "rollup": _rollup(unknown=1, total=1),
            "members": [],
        }
        p1, p2, p3, p4 = self._patched(step, client)
        with (
            p1,
            p2,
            p3,
            p4,
            patch(f"{_MODULE}.time.monotonic", side_effect=itertools.count(0, 5)),
        ):
            result = _run(step.execute({}, {}))
        assert result is True

    def test_version_guard_old_client_fails(self):
        step = AssertMigrationCompleteStep(self.config)
        with patch(f"{_MODULE}._client_py_below", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False

    def test_version_guard_old_client_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = AssertMigrationCompleteStep(cfg)
        with patch(f"{_MODULE}._client_py_below", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is True


# =============================================================================
# ResyncContextStep
# =============================================================================


class TestResyncContextValidation:
    def setup_method(self):
        self.base = {
            "type": "resync_context",
            "node": "calimero-node-2",
            "context_id": "ctx123",
        }

    def test_valid_config_passes(self):
        ResyncContextStep(self.base)

    def test_valid_with_force(self):
        ResyncContextStep({**self.base, "force": True})

    def test_missing_context_id_raises(self):
        cfg = {**self.base}
        del cfg["context_id"]
        with pytest.raises(ValueError, match="context_id"):
            ResyncContextStep(cfg)

    def test_force_not_bool_raises(self):
        with pytest.raises(ValueError, match="'force' must be a boolean"):
            ResyncContextStep({**self.base, "force": "yes"})


class TestResyncContextExecute:
    def setup_method(self):
        self.config = {
            "type": "resync_context",
            "node": "calimero-node-2",
            "context_id": "ctx123",
            "force": True,
        }

    def _patched(self, step, client):
        return (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "calimero-node-2"),
            ),
            patch(f"{_MODULE}.get_client_for_rpc_url", return_value=client),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
        )

    def test_success_stores_response_and_forwards_force(self):
        step = ResyncContextStep(self.config)
        client = MagicMock()
        client.resync_context.return_value = {
            "contextId": "ctx123",
            "resyncStarted": True,
        }
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        client.resync_context.assert_called_once_with(context_id="ctx123", force=True)
        stored = workflow_results["resync_context_calimero-node-2"]
        assert stored["resyncStarted"] is True

    def test_force_defaults_false(self):
        cfg = {**self.config}
        del cfg["force"]
        step = ResyncContextStep(cfg)
        client = MagicMock()
        client.resync_context.return_value = {"resyncStarted": False}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            _run(step.execute({}, {}))
        client.resync_context.assert_called_once_with(context_id="ctx123", force=False)

    def test_outputs_export_fields(self):
        cfg = {**self.config, "outputs": {"started": "resyncStarted"}}
        step = ResyncContextStep(cfg)
        client = MagicMock()
        client.resync_context.return_value = {
            "contextId": "ctx123",
            "resyncStarted": True,
        }
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("started") is True

    def test_non_dict_response_warns_and_stores_empty(self):
        # A non-dict body must not be silently dropped: the step warns and
        # stores {} (so `outputs:` fields resolve to None, not stale data).
        step = ResyncContextStep(self.config)
        client = MagicMock()
        client.resync_context.return_value = ["unexpected"]
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        assert workflow_results["resync_context_calimero-node-2"] == {}

    def test_client_error_fails_step(self):
        step = ResyncContextStep(self.config)
        client = MagicMock()
        client.resync_context.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_version_guard_old_client_fails(self):
        step = ResyncContextStep(self.config)
        with patch(f"{_MODULE}._client_py_below", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False

    def test_version_guard_old_client_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = ResyncContextStep(cfg)
        with patch(f"{_MODULE}._client_py_below", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is True


# =============================================================================
# ListApplicationVersionsStep
# =============================================================================


class TestListApplicationVersionsValidation:
    def setup_method(self):
        self.base = {
            "type": "list_application_versions",
            "node": "calimero-node-1",
            "application_id": "app123",
        }

    def test_valid_config_passes(self):
        ListApplicationVersionsStep(self.base)

    def test_missing_application_id_raises(self):
        cfg = {**self.base}
        del cfg["application_id"]
        with pytest.raises(ValueError, match="application_id"):
            ListApplicationVersionsStep(cfg)


class TestListApplicationVersionsExecute:
    def setup_method(self):
        self.config = {
            "type": "list_application_versions",
            "node": "calimero-node-1",
            "application_id": "app123",
        }

    def _patched(self, step, client):
        return (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "calimero-node-1"),
            ),
            patch(f"{_MODULE}.get_client_for_rpc_url", return_value=client),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
        )

    def test_success_stores_summary(self):
        step = ListApplicationVersionsStep(self.config)
        client = MagicMock()
        client.list_application_versions.return_value = {
            "data": [
                {"version": "0.1.0", "blob_id": "aa", "size": 10, "package": "p"},
                {"version": "0.2.0", "blob_id": "bb", "size": 11, "package": "p"},
            ]
        }
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        client.list_application_versions.assert_called_once_with(
            application_id="app123"
        )
        # The list is re-attached under `versions` (not `data`) so the export
        # unwrap can't strip it; `count` is the convenience length.
        stored = workflow_results["list_application_versions_calimero-node-1"]
        assert stored["count"] == 2
        assert len(stored["versions"]) == 2

    def test_outputs_export_versions_list(self):
        # Mapping outputs to `versions` must actually export the list — under
        # the old `{data: [...]}` shape the export unwrap stripped `data` and
        # this resolved to None (Bugbot #2).
        cfg = {**self.config, "outputs": {"vs": "versions"}}
        step = ListApplicationVersionsStep(cfg)
        client = MagicMock()
        client.list_application_versions.return_value = {
            "data": [
                {"version": "0.1.0", "blob_id": "aa", "size": 10, "package": "p"},
            ]
        }
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert isinstance(dynamic_values.get("vs"), list)
        assert len(dynamic_values["vs"]) == 1

    def test_outputs_export_nested_blob_id(self):
        # A specific version's blob_id (the app_key) is reachable by dotted path.
        cfg = {**self.config, "outputs": {"v2_key": "versions.1.blob_id"}}
        step = ListApplicationVersionsStep(cfg)
        client = MagicMock()
        client.list_application_versions.return_value = {
            "data": [
                {"version": "0.1.0", "blob_id": "aa", "size": 10, "package": "p"},
                {"version": "0.2.0", "blob_id": "bb", "size": 11, "package": "p"},
            ]
        }
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("v2_key") == "bb"

    def test_non_list_data_warns_and_empties(self):
        # A non-list `data` body must not masquerade as zero installed versions:
        # warn and fall back to an empty list.
        step = ListApplicationVersionsStep(self.config)
        client = MagicMock()
        client.list_application_versions.return_value = {"data": {"oops": 1}}
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        stored = workflow_results["list_application_versions_calimero-node-1"]
        assert stored["count"] == 0
        assert stored["versions"] == []

    def test_bare_list_body_warns_and_empties(self):
        # A non-dict top-level body (e.g. a bare list, not `{data: [...]}`) is
        # also a shape mismatch and must warn rather than silently empty.
        step = ListApplicationVersionsStep(self.config)
        client = MagicMock()
        client.list_application_versions.return_value = [{"version": "0.1.0"}]
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        stored = workflow_results["list_application_versions_calimero-node-1"]
        assert stored["count"] == 0
        assert stored["versions"] == []

    def test_client_error_fails_step(self):
        step = ListApplicationVersionsStep(self.config)
        client = MagicMock()
        client.list_application_versions.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_version_guard_old_client_fails(self):
        step = ListApplicationVersionsStep(self.config)
        with patch(f"{_MODULE}._client_py_below", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False
