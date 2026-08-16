"""
Unit tests for drift detection on the group-upgrade status summarizers.

`_summarize_cascade_status` and `_summarize_migration_status` flatten core's
JSON into curated allowlists, so any field core adds upstream is dropped. These
tests pin the mapped-key sets (a core-side addition has to be reviewed here
rather than discovered weeks later in a workflow whose `outputs:` resolved to
nothing) and cover the runtime warning that makes the drop visible on the first
run against a newer merod.
"""

import pytest

from merobox.commands.bootstrap.steps.group_upgrade import (
    _CASCADE_STATUS_MAPPED_KEYS,
    _MIGRATION_ROLLUP_MAPPED_KEYS,
    _MIGRATION_STATUS_MAPPED_KEYS,
    _summarize_cascade_status,
    _summarize_migration_status,
    _warn_once,
)
from merobox.commands.utils import console

# Substring every drift warning carries, and nothing else prints.
_MARKER = "not mapped by merobox's summary"


@pytest.fixture(autouse=True)
def _fresh_console():
    # `_warn_once` dedupes for the life of the process, so each test needs a
    # clean cache; the wide console keeps rich from wrapping a key name across
    # lines and breaking the substring assertions.
    original_width = console.width
    console.width = 400
    _warn_once.cache_clear()
    yield
    console.width = original_width
    _warn_once.cache_clear()


def _rollup(**overrides):
    base = {
        "migrated": 1,
        "inProgress": 0,
        "unknown": 0,
        "failed": 0,
        "total": 1,
        "allMigrated": True,
        "membersPendingSignature": 0,
    }
    base.update(overrides)
    return base


# =============================================================================
# The pinned allowlists
# =============================================================================


class TestMappedKeysArePinned:
    """Change these together with the summary that surfaces the new field.

    The sets are the summarizers' contract with core's response shape: this is
    the checkpoint where a core-side addition has to be either mapped into the
    summary or consciously left out.
    """

    def test_cascade_status_mapped_keys(self):
        assert set(_CASCADE_STATUS_MAPPED_KEYS) == {"data"}

    def test_migration_status_mapped_keys(self):
        assert set(_MIGRATION_STATUS_MAPPED_KEYS) == {
            "targetVersion",
            "expectedMembers",
            "rollup",
            "members",
        }

    def test_migration_rollup_mapped_keys(self):
        assert set(_MIGRATION_ROLLUP_MAPPED_KEYS) == {
            "migrated",
            "inProgress",
            "unknown",
            "failed",
            "total",
            "allMigrated",
            "membersPendingSignature",
        }


# =============================================================================
# The runtime warning
# =============================================================================


class TestDriftWarning:
    def test_cascade_unmapped_key_warns(self, capsys):
        summary = _summarize_cascade_status({"data": [], "cursor": "abc"})
        out = capsys.readouterr().out
        assert _MARKER in out
        assert "get_cascade_status" in out and "cursor" in out
        # The summary itself is unaffected: the warning is the only new output.
        assert summary["total"] == 0

    def test_migration_unmapped_top_level_key_warns(self, capsys):
        _summarize_migration_status(
            {"rollup": _rollup(), "members": [], "fleetCompletedAt": 1_700_002_000}
        )
        out = capsys.readouterr().out
        assert _MARKER in out
        assert "get_migration_status" in out and "fleetCompletedAt" in out

    def test_migration_unmapped_rollup_counter_warns(self, capsys):
        # The rollup counters are lifted to the top level of the summary, so a
        # counter core adds there is dropped exactly like a top-level field.
        _summarize_migration_status({"rollup": _rollup(membersPendingProof=2)})
        out = capsys.readouterr().out
        assert _MARKER in out
        assert "get_migration_status.rollup" in out and "membersPendingProof" in out

    def test_fully_mapped_responses_are_silent(self, capsys):
        _summarize_cascade_status({"data": [{"groupId": "g0"}]})
        _summarize_migration_status(
            {
                "targetVersion": 3,
                "expectedMembers": 1,
                "rollup": _rollup(),
                "members": [{"peer": "p", "state": "migrated"}],
            }
        )
        assert _MARKER not in capsys.readouterr().out

    def test_deliberately_omitted_member_report_fields_do_not_warn(self, capsys):
        # Only `migrationFailed` is surfaced per member; the rest of the report
        # is left out on purpose, and a warning about it would be pure noise.
        _summarize_migration_status(
            {
                "rollup": _rollup(),
                "members": [
                    {
                        "peer": "p",
                        "state": "migrated",
                        "report": {
                            "schemaVersion": 3,
                            "residueAuto": 0,
                            "syncedUpToHlc": "hlc-1",
                            "reportedAt": 1_700_000_000,
                            "authoredRemaining": 0,
                            "migrationFailed": None,
                        },
                    }
                ],
            }
        )
        assert _MARKER not in capsys.readouterr().out

    def test_garbage_response_does_not_warn(self, capsys):
        # A non-dict body is already handled (and warned about) by the steps;
        # the summarizers stay silent rather than blaming core for it.
        for bad in (None, "not-a-dict", []):
            _summarize_cascade_status(bad)
            _summarize_migration_status(bad)
        assert _MARKER not in capsys.readouterr().out

    def test_repeated_drift_warns_once(self, capsys):
        # assert_migration_complete re-summarizes on every poll; one warning per
        # poll is one nobody reads.
        for _ in range(3):
            _summarize_migration_status({"rollup": _rollup(), "cohortPinnedAtHlc": "x"})
        assert capsys.readouterr().out.count(_MARKER) == 1
