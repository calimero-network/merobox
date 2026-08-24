"""`expected_failure` on a `call` does not fail a run — the error export does.

Alone among the step types, `call` only *warns* when a step marked
`expected_failure: true` succeeds anyway: it prints "Expected failure but call
succeeded" and returns True. That is deliberate — workflows use `call` as a soft
"may not have propagated yet" probe (`workflow-propagation-monitoring.yml`) and
`workflow-negative-testing-example.yml` pins the leniency on purpose — but it
means a scenario written to prove that something is *refused* proves nothing if
the flag is all it has. The whole run goes green on the exact defect it exists
to catch.

The leave/rejoin workflows depend on precisely that property ("node 2 cannot
read the channel it left"), so each of their `expected_failure` calls captures
the error and a following `assert` step's `is_set` turns an unexpected success
into a red run. These tests pin both halves:

  * the export/`is_set` round trip in all four directions, including the
    `error` vs `error_message` choice — a JSON-RPC error carrying neither
    `message` nor `data` leaves `error_message` None, which would read as "the
    leave did not take effect" on a leave that worked;
  * that every `expected_failure` `call` in the leave workflows is actually
    guarded, so the guard cannot be dropped in a later edit without a test
    going red.
"""

import glob
import os
import re

import pytest
import yaml

from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_LEAVE_WORKFLOW_GLOB = os.path.join(
    _REPO_ROOT, "workflow-examples", "*leave-rejoin-example.yml"
)


def _execute_step(**extra):
    config = {
        "type": "call",
        "name": "node 2 cannot read the context it left",
        "node": "calimero-node-1",
        "context_id": "ctx1",
        "method": "get",
        "args": {"key": "msg_1"},
        "expected_failure": True,
        "outputs": {"left_err": "error"},
    }
    config.update(extra)
    return ExecuteStep(config, manager=None)


def _is_set(dynamic_values):
    step = AssertStep(
        {"type": "assert", "name": "guard", "statements": ["is_set({{left_err}})"]},
        manager=None,
    )
    passed, _detail = step._eval_statement("is_set({{left_err}})", {}, dynamic_values)
    return passed


def _export(error_info):
    dynamic_values = {}
    _execute_step()._export_error_variables(
        error_info, "calimero-node-1", dynamic_values
    )
    return dynamic_values


class TestLenientCallStillAsserts:
    """The captured error is what carries the verdict, not the step's own."""

    def test_jsonrpc_failure_binds_the_error_and_the_guard_passes(self):
        step = _execute_step()
        info = step._extract_error_info(
            {
                "error": {
                    "type": "FunctionCallError",
                    "code": -32000,
                    "message": "no identity",
                }
            },
            expected=True,
        )
        dynamic_values = _export(info)
        assert dynamic_values["left_err"] is not None
        assert _is_set(dynamic_values) is True

    def test_transport_failure_binds_the_error_too(self):
        """A refused read may never reach JSON-RPC; the guard must still pass."""
        step = _execute_step()
        info = step._extract_error_info(
            {"success": False, "error": "HTTP 500 Internal Server Error"}, expected=True
        )
        dynamic_values = _export(info)
        assert dynamic_values["left_err"] == "HTTP 500 Internal Server Error"
        assert _is_set(dynamic_values) is True

    def test_error_survives_a_jsonrpc_error_with_no_message(self):
        """Why the capture is `error` and not `error_message`.

        `_extract_jsonrpc_error_details` leaves `error_message` None when the
        error object carries neither `message` nor `data`. Guarding on that
        field would fail the run on a leave that worked — a false red reading
        as "the leave did not take effect". `error` is seeded in every failure
        branch, so it is the field that can carry the verdict.
        """
        step = _execute_step()
        info = step._extract_error_info(
            {"error": {"type": "FunctionCallError"}}, expected=True
        )
        assert info["error_message"] is None, "premise of this test no longer holds"
        assert info["error"] == {"type": "FunctionCallError"}
        assert _is_set(_export(info)) is True

    def test_unexpected_success_binds_none_and_the_guard_fails(self):
        """The case the whole file exists for.

        `call` returns True here — it merely warns — so the guard is the only
        thing that can fail the run.
        """
        lenient_success = {
            "success": False,
            "expected": True,
            "error_code": None,
            "error_type": None,
            "error_message": None,
            "error": None,
        }
        dynamic_values = _export(lenient_success)
        assert "left_err" in dynamic_values, "the capture must bind even when None"
        assert dynamic_values["left_err"] is None
        assert _is_set(dynamic_values) is False


# =============================================================================
# The workflows keep their guards
# =============================================================================


def _leave_workflows():
    return sorted(glob.glob(_LEAVE_WORKFLOW_GLOB))


def test_there_are_leave_workflows_to_check():
    """Guard the guard: a bad glob would make the checks below vacuous."""
    assert len(_leave_workflows()) >= 2


def _steps(workflow_path):
    with open(workflow_path) as handle:
        return yaml.safe_load(handle).get("steps", [])


@pytest.mark.parametrize(
    "workflow_path", _leave_workflows(), ids=lambda p: os.path.basename(p)
)
def test_every_expected_failure_call_is_guarded_by_an_assertion(workflow_path):
    """Each `expected_failure` call captures an error a later `assert` checks.

    Without this, deleting the `assert` step (or the `outputs` block that feeds
    it) leaves a workflow that still reads as if it proves the leave took
    effect, and still passes.
    """
    steps = _steps(workflow_path)
    guarded = {
        name
        for step in steps
        if step.get("type") == "assert"
        for statement in step.get("statements", [])
        for name in re.findall(
            r"is_set\(\{\{\s*([A-Za-z0-9_]+)\s*\}\}\)",
            statement if isinstance(statement, str) else statement.get("statement", ""),
        )
    }

    expected_failure_calls = [
        step
        for step in steps
        if step.get("type") == "call" and step.get("expected_failure") is True
    ]
    assert expected_failure_calls, (
        f"{os.path.basename(workflow_path)} has no expected_failure call — the "
        "leave assertion this test guards has gone missing"
    )

    for step in expected_failure_calls:
        outputs = step.get("outputs") or {}
        captures = [
            variable
            for variable, field in outputs.items()
            if field in ("error", "error_message")
        ]
        assert captures, (
            f"'{step.get('name')}' relies on expected_failure alone. `call` only "
            "warns when the call succeeds, so this step cannot fail the run: "
            "capture `error` and assert `is_set` on it"
        )
        assert any(capture in guarded for capture in captures), (
            f"'{step.get('name')}' captures {captures} but no assert step checks "
            "is_set on any of them, so an unexpected success stays green"
        )
