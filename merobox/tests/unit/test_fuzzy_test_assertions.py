"""A fuzzy pattern's assertions must fail when the call they check failed.

`fuzzy_test` resolves each pattern step before running it, non-strictly, and an
unresolved embedded placeholder comes back as its bare name. For an assertion
that turned `is_set({{w1}})` after a failed call into `is_set(w1)`, which then
passed on the literal text "w1": a burst of writes to a node that had crashed
reported every write as fine.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from merobox.commands.bootstrap.steps.assertion import FuzzyTestResultsTracker
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.fuzzy_test import FuzzyTestStep

SUCCEEDED = {"success": True, "data": {"result": {"output": None}}}
UNREACHABLE = {"success": False, "error": "connection refused"}


def _fuzzy():
    step = FuzzyTestStep(
        {
            "type": "fuzzy_test",
            "name": "fuzz",
            "duration_minutes": 1,
            "context_id": "ctx",
            "nodes": [{"name": "node-1"}],
            "operations": [{"name": "op", "weight": 1, "steps": [{"type": "wait"}]}],
        }
    )
    step._nodes = [{"name": "node-1"}]
    return step


def _run_pattern(steps, call_response=SUCCEEDED, dynamic=None):
    tracker = FuzzyTestResultsTracker()
    tracker.set_current_pattern("op")
    dynamic = {"_fuzzy_test_results": tracker, **(dynamic or {})}
    loop = asyncio.new_event_loop()
    try:
        with (
            patch.object(
                ExecuteStep,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "node-1"),
            ),
            patch(
                "merobox.commands.bootstrap.steps.execute.call_function",
                new=AsyncMock(return_value=call_response),
            ),
            patch.object(ExecuteStep, "_print_node_logs_on_failure"),
        ):
            loop.run_until_complete(
                _fuzzy()._execute_pattern(
                    {"name": "op", "steps": steps}, {}, dynamic, iteration=1
                )
            )
    finally:
        loop.close()
    return tracker


WRITE = {
    "type": "call",
    "node": "node-1",
    "context_id": "ctx",
    "method": "set",
    "args": {"key": "k", "value": "v"},
    "outputs": {"w1": "result"},
}


def _assert(*statements):
    return {"type": "assert", "statements": list(statements)}


class TestAssertionsSeeFailedCalls:
    def test_is_set_on_a_failed_calls_output_fails(self):
        tracker = _run_pattern([WRITE, _assert("is_set({{w1}})")], UNREACHABLE)
        assert (tracker.assertions_passed, tracker.assertions_failed) == (0, 1)

    def test_is_set_on_a_successful_calls_output_passes(self):
        tracker = _run_pattern([WRITE, _assert("is_set({{w1}})")], SUCCEEDED)
        assert (tracker.assertions_passed, tracker.assertions_failed) == (1, 0)

    def test_one_failed_write_in_a_burst_fails_only_its_own_assertion(self):
        # The shape core's write_burst uses: each write binds its own output.
        second = {**WRITE, "outputs": {"w2": "result"}}
        tracker = _run_pattern(
            [WRITE, _assert("is_set({{w1}})", "is_set({{w2}})")],
            SUCCEEDED,
        )
        assert (tracker.assertions_passed, tracker.assertions_failed) == (1, 1)
        tracker = _run_pattern(
            [WRITE, second, _assert("is_set({{w1}})", "is_set({{w2}})")],
            SUCCEEDED,
        )
        assert (tracker.assertions_passed, tracker.assertions_failed) == (2, 0)

    def test_a_dict_statement_is_strict_too(self):
        tracker = _run_pattern(
            [WRITE, _assert({"statement": "is_set({{w1}})", "message": "write"})],
            UNREACHABLE,
        )
        assert tracker.assertions_failed == 1


class TestWhatStillResolves:
    def test_args_captured_by_the_pattern_still_resolve(self):
        # fuzzy_key / fuzzy_value are bound from the call's args by the pattern
        # runner itself, so an assertion can still use them.
        tracker = _run_pattern(
            [WRITE, _assert("contains({{fuzzy_value}}, v)")], SUCCEEDED
        )
        assert (tracker.assertions_passed, tracker.assertions_failed) == (1, 0)

    def test_random_generators_in_a_statement_are_expanded(self):
        # AssertStep has no binding for a generator, so the pattern runner must
        # keep expanding those; otherwise it would read as unresolved.
        tracker = _run_pattern(
            [_assert("regex({{random_int(1, 9)}}, ^[1-9]$)")], SUCCEEDED
        )
        assert (tracker.assertions_passed, tracker.assertions_failed) == (1, 0)

    def test_a_messages_placeholders_are_still_filled_in(self):
        tracker = _run_pattern(
            [
                WRITE,
                _assert(
                    {"statement": "is_set({{w1}})", "message": "write to {{node}}"}
                ),
            ],
            UNREACHABLE,
            dynamic={"node": "node-1"},
        )
        descriptions = [f["description"] for f in tracker.failed_assertions]
        assert descriptions == ["write to node-1"]
