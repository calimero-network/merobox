"""`call` honours `expected_failure` like every other step, and `allow_failure`
is the explicit spelling for a probe that may go either way."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from merobox.commands.bootstrap.config import validate_workflow_step
from merobox.commands.bootstrap.steps.execute import ExecuteStep

SUCCEEDED = {"success": True, "data": {"result": {"output": "v"}}}
REFUSED = {
    "success": True,
    "data": {"error": {"type": "FunctionCallError", "code": -32000, "data": "no"}},
}
UNREACHABLE = {"success": False, "error": "connection refused"}
NO_STATE = {"success": True, "data": {"error": {"type": "Uninitialized"}}}


def _step(**extra):
    return ExecuteStep(
        {
            "type": "call",
            "name": "read",
            "node": "node-1",
            "context_id": "ctx",
            "method": "get",
            **extra,
        }
    )


def _run(step, response):
    dynamic = {}
    loop = asyncio.new_event_loop()
    try:
        with (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "node-1"),
            ),
            patch(
                "merobox.commands.bootstrap.steps.execute.call_function",
                new=AsyncMock(return_value=response),
            ),
            patch.object(step, "_print_node_logs_on_failure"),
        ):
            return loop.run_until_complete(step.execute({}, dynamic)), dynamic
    finally:
        loop.close()


class TestExpectedFailure:
    def test_a_call_that_succeeds_fails_the_step(self):
        ok, _dynamic = _run(_step(expected_failure=True), SUCCEEDED)
        assert ok is False

    @pytest.mark.parametrize("response", [REFUSED, UNREACHABLE])
    def test_a_call_that_fails_passes_the_step(self, response):
        ok, _dynamic = _run(_step(expected_failure=True), response)
        assert ok is True


class TestAllowFailure:
    def test_a_call_that_succeeds_passes_and_binds_no_error(self):
        ok, dynamic = _run(
            _step(allow_failure=True, outputs={"r": "result", "e": "error_type"}),
            SUCCEEDED,
        )
        assert ok is True
        assert dynamic == {"r": {"output": "v"}, "e": None}

    def test_a_call_that_fails_passes_and_binds_the_error(self):
        ok, dynamic = _run(
            _step(allow_failure=True, outputs={"e": "error_type"}), REFUSED
        )
        assert ok is True
        assert dynamic == {"e": "FunctionCallError"}

    def test_state_that_never_arrives_is_one_of_the_outcomes(self):
        ok, dynamic = _run(
            _step(
                allow_failure=True,
                state_retry_attempts=1,
                outputs={"e": "error_type"},
            ),
            NO_STATE,
        )
        assert ok is True
        assert dynamic == {"e": "Uninitialized"}

    def test_expected_failure_does_not_count_missing_state_as_the_refusal(self):
        ok, _dynamic = _run(
            _step(expected_failure=True, state_retry_attempts=1), NO_STATE
        )
        assert ok is False

    def test_it_contradicts_expected_failure(self):
        with pytest.raises(ValueError, match="allow_failure"):
            _step(allow_failure=True, expected_failure=True)

    def test_it_must_be_a_boolean(self):
        with pytest.raises(ValueError, match="allow_failure"):
            _step(allow_failure="yes")

    def test_expected_error_still_needs_expected_failure(self):
        with pytest.raises(ValueError, match="expected_error"):
            _step(allow_failure=True, expected_error="no")


def test_the_schema_takes_allow_failure():
    step = {
        "type": "call",
        "node": "n",
        "context_id": "c",
        "method": "m",
        "allow_failure": True,
    }
    assert validate_workflow_step(step, 0) == []
