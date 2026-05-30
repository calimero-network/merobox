"""
Unit tests for the cascade-status workflow steps.

Covers:
- `_summarize_cascade_status` roll-up helper.
- `GetCascadeStatusStep` — validation + execute (success / export / failure).
- `AssertCascadeCompleteStep` — validation + execute (immediate complete,
  poll-then-complete, timeout, failed-descendant early exit, expected_failure).

The client (`calimero_client_py`) is mocked: conftest already stubs the import,
and each execute test patches `get_client_for_rpc_url` to return a MagicMock.
On a Python without calimero-client-py installed, `_CLIENT_PY_VERSION` resolves
to None, so the version pre-flight is skipped and never interferes here.
"""

import asyncio
import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.group_upgrade import (
    AssertCascadeCompleteStep,
    GetCascadeStatusStep,
    _summarize_cascade_status,
)

_MODULE = "merobox.commands.bootstrap.steps.group_upgrade"


def _resp(statuses):
    """Build a get_cascade_status response with the given per-group statuses."""
    return {
        "data": [
            {"groupId": f"g{i}", "upgrade": {"status": s}}
            for i, s in enumerate(statuses)
        ]
    }


def _run(coro):
    # Run on a private loop and never touch the process-wide "current loop".
    #
    # asyncio.run() would close the shared main-thread loop AND set the current
    # loop to None, so any later test using the suite's prevailing
    # `asyncio.get_event_loop().run_until_complete(...)` pattern would hit
    # "RuntimeError: no current event loop" on Python 3.11/3.12. Creating an
    # isolated loop (without set_event_loop) leaves the global loop state
    # untouched, so this file can't pollute sibling test modules.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# _summarize_cascade_status
# =============================================================================


class TestSummarizeCascadeStatus:
    def test_mixed_statuses_bucket_correctly(self):
        s = _summarize_cascade_status(
            _resp(["completed", "in_progress", "failed", "completed"])
        )
        assert s["total"] == 4
        assert s["completed"] == 2
        assert s["failed"] == 1
        assert s["pending"] == 1
        assert s["all_completed"] is False
        # buckets always sum to total
        assert s["completed"] + s["failed"] + s["pending"] == s["total"]

    def test_all_completed_flag(self):
        s = _summarize_cascade_status(_resp(["completed", "completed"]))
        assert s["all_completed"] is True
        assert s["pending"] == 0 and s["failed"] == 0

    def test_empty_subtree_is_not_complete(self):
        s = _summarize_cascade_status(_resp([]))
        assert s["total"] == 0
        assert s["all_completed"] is False

    def test_unknown_status_counts_as_pending(self):
        s = _summarize_cascade_status(_resp(["queued", "completed"]))
        assert s["completed"] == 1
        assert s["pending"] == 1
        assert s["all_completed"] is False

    def test_garbage_response_is_empty_summary(self):
        for bad in (None, [], {}, {"data": "nope"}, {"data": [1, 2, "x"]}):
            s = _summarize_cascade_status(bad)
            assert s["total"] == (3 if bad == {"data": [1, 2, "x"]} else 0)
            assert s["all_completed"] is False

    def test_raw_groups_are_reattached(self):
        resp = _resp(["completed"])
        s = _summarize_cascade_status(resp)
        # raw entries live under `groups`, not `data` (see helper docstring)
        assert s["groups"] == resp["data"]
        assert "data" not in s


# =============================================================================
# GetCascadeStatusStep — validation
# =============================================================================


class TestGetCascadeStatusValidation:
    def setup_method(self):
        self.base = {
            "type": "get_cascade_status",
            "name": "Status",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
        }

    def test_valid_config_passes(self):
        GetCascadeStatusStep(self.base)

    def test_missing_node_raises(self):
        cfg = {**self.base}
        del cfg["node"]
        with pytest.raises(ValueError, match="node"):
            GetCascadeStatusStep(cfg)

    def test_missing_namespace_id_raises(self):
        cfg = {**self.base}
        del cfg["namespace_id"]
        with pytest.raises(ValueError, match="namespace_id"):
            GetCascadeStatusStep(cfg)

    def test_namespace_id_not_string_raises(self):
        cfg = {**self.base, "namespace_id": 5}
        with pytest.raises(ValueError, match="'namespace_id' must be a string"):
            GetCascadeStatusStep(cfg)


# =============================================================================
# GetCascadeStatusStep — execute
# =============================================================================


class TestGetCascadeStatusExecute:
    def setup_method(self):
        self.config = {
            "type": "get_cascade_status",
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
        step = GetCascadeStatusStep(self.config)
        client = MagicMock()
        client.get_cascade_status.return_value = _resp(["completed", "in_progress"])
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        client.get_cascade_status.assert_called_once_with(namespace_id="ns123")
        summary = workflow_results["cascade_status_calimero-node-1"]
        assert summary["total"] == 2
        assert summary["completed"] == 1
        assert summary["pending"] == 1

    def test_outputs_export_summary_fields(self):
        cfg = {**self.config, "outputs": {"done": "all_completed", "n": "total"}}
        step = GetCascadeStatusStep(cfg)
        client = MagicMock()
        client.get_cascade_status.return_value = _resp(["completed", "completed"])
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("done") is True
        assert dynamic_values.get("n") == 2

    def test_client_error_fails_step(self):
        step = GetCascadeStatusStep(self.config)
        client = MagicMock()
        client.get_cascade_status.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_client_error_with_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = GetCascadeStatusStep(cfg)
        client = MagicMock()
        client.get_cascade_status.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is True

    def test_jsonrpc_error_body_fails_step(self):
        # transport OK but a JSON-RPC error body must not be summarised as an
        # all-zero status — the step should fail.
        step = GetCascadeStatusStep(self.config)
        client = MagicMock()
        client.get_cascade_status.return_value = {"error": {"type": "Internal"}}
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is False
        assert "cascade_status_calimero-node-1" not in workflow_results


# =============================================================================
# AssertCascadeCompleteStep — validation
# =============================================================================


class TestAssertCascadeCompleteValidation:
    def setup_method(self):
        self.base = {
            "type": "assert_cascade_complete",
            "name": "Assert",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
        }

    def test_valid_config_passes(self):
        AssertCascadeCompleteStep(self.base)

    def test_valid_with_optional_timing(self):
        AssertCascadeCompleteStep(
            {**self.base, "timeout_seconds": 10, "poll_interval": 0.5}
        )

    def test_missing_namespace_id_raises(self):
        cfg = {**self.base}
        del cfg["namespace_id"]
        with pytest.raises(ValueError, match="namespace_id"):
            AssertCascadeCompleteStep(cfg)

    def test_timeout_seconds_bool_raises(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            AssertCascadeCompleteStep({**self.base, "timeout_seconds": True})

    def test_timeout_seconds_string_raises(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            AssertCascadeCompleteStep({**self.base, "timeout_seconds": "30"})

    def test_poll_interval_non_positive_raises(self):
        with pytest.raises(ValueError, match="poll_interval"):
            AssertCascadeCompleteStep({**self.base, "poll_interval": 0})


# =============================================================================
# AssertCascadeCompleteStep — execute
# =============================================================================


class TestAssertCascadeCompleteExecute:
    def setup_method(self):
        self.config = {
            "type": "assert_cascade_complete",
            "name": "Assert",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
            "timeout_seconds": 5,
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
            # no-op sleep so poll loops don't burn wall-clock
            patch(f"{_MODULE}.asyncio.sleep", new=AsyncMock()),
        )

    def test_immediate_complete_passes_without_sleep(self):
        step = AssertCascadeCompleteStep(self.config)
        client = MagicMock()
        client.get_cascade_status.return_value = _resp(["completed", "completed"])
        workflow_results = {}
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4 as sleep_mock:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        assert client.get_cascade_status.call_count == 1
        sleep_mock.assert_not_called()
        assert workflow_results["cascade_status_calimero-node-1"]["all_completed"]

    def test_poll_then_complete_passes(self):
        step = AssertCascadeCompleteStep(self.config)
        client = MagicMock()
        client.get_cascade_status.side_effect = [
            _resp(["in_progress", "in_progress"]),
            _resp(["completed", "in_progress"]),
            _resp(["completed", "completed"]),
        ]
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4:
            result = _run(step.execute({}, {}))
        assert result is True
        assert client.get_cascade_status.call_count == 3

    def test_timeout_fails(self):
        step = AssertCascadeCompleteStep(
            {**self.config, "timeout_seconds": 10, "poll_interval": 1}
        )
        client = MagicMock()
        client.get_cascade_status.return_value = _resp(["completed", "in_progress"])
        workflow_results = {}
        p1, p2, p3, p4 = self._patched(step, client)
        # Drive the deadline deterministically rather than the wall clock:
        # monotonic returns 0,5,10,... so deadline=10 is crossed on the 2nd
        # poll regardless of CI runner speed.
        with (
            p1,
            p2,
            p3,
            p4,
            patch(f"{_MODULE}.time.monotonic", side_effect=itertools.count(0, 5)),
        ):
            result = _run(step.execute(workflow_results, {}))
        assert result is False
        assert client.get_cascade_status.call_count == 2
        # last-read summary is still recorded for downstream inspection
        assert workflow_results["cascade_status_calimero-node-1"]["pending"] == 1

    def test_failed_descendant_exits_early(self):
        step = AssertCascadeCompleteStep(self.config)
        client = MagicMock()
        client.get_cascade_status.return_value = _resp(["completed", "failed"])
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4 as sleep_mock:
            result = _run(step.execute({}, {}))
        assert result is False
        # bailed on the first read — no polling to timeout
        assert client.get_cascade_status.call_count == 1
        sleep_mock.assert_not_called()

    def test_timeout_with_expected_failure_passes(self):
        step = AssertCascadeCompleteStep(
            {
                **self.config,
                "timeout_seconds": 10,
                "poll_interval": 1,
                "expected_failure": True,
            }
        )
        client = MagicMock()
        client.get_cascade_status.return_value = _resp(["in_progress"])
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

    def test_transient_error_then_complete(self):
        step = AssertCascadeCompleteStep(self.config)
        client = MagicMock()
        client.get_cascade_status.side_effect = [
            RuntimeError("node booting"),
            _resp(["completed"]),
        ]
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4:
            result = _run(step.execute({}, {}))
        assert result is True
        assert client.get_cascade_status.call_count == 2

    def test_jsonrpc_error_body_retried_then_complete(self):
        # a JSON-RPC error body is treated like a transient read: keep polling.
        step = AssertCascadeCompleteStep(self.config)
        client = MagicMock()
        client.get_cascade_status.side_effect = [
            {"error": {"type": "Internal"}},
            _resp(["completed"]),
        ]
        p1, p2, p3, p4 = self._patched(step, client)
        with p1, p2, p3, p4:
            result = _run(step.execute({}, {}))
        assert result is True
        assert client.get_cascade_status.call_count == 2
