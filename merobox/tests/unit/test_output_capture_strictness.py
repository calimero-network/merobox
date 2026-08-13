"""A capture that cannot bind, and a placeholder that never bound, must fail.

Two independent holes, either of which manufactures a green assertion against a
build that never produced the field:

1. `outputs:` naming a field the response does not have only warned. The
   placeholder stayed unbound, and an unbound `{{name}}` reaches an assertion as
   the literal text "{{name}}".
2. That literal then satisfies most comparisons - `not_equal({{x}}, null)` is
   true of the string "{{x}}" - so the assertion could not fail.

The incident that prompted this: a step captured `fleet_completed_at` from a
response that did not carry it, and the `json_assert` written to catch exactly
that absence passed. Both halves are pinned below.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.json_assertion import JsonAssertStep
from merobox.commands.errors import OutputCaptureError, UnresolvedPlaceholderError


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _export(outputs: dict, response: dict) -> dict:
    """Run a capture config against a response; return what it bound."""
    step = BaseStep({"name": "s", "type": "call", "outputs": outputs})
    dynamic: dict = {}
    step._export_custom_outputs(response, "node-1", dynamic)
    return dynamic


class TestMissingCaptureFails:
    def test_absent_field_raises(self):
        with pytest.raises(OutputCaptureError, match="fleet_completed_at"):
            _export(
                {"fleet_completed_at": "fleet_completed_at"},
                {"data": {"fleetId": "f1"}},
            )

    def test_error_names_the_keys_that_were_there(self):
        with pytest.raises(OutputCaptureError, match="fleetId, status"):
            _export({"x": "nope"}, {"data": {"status": "ok", "fleetId": "f1"}})

    def test_absent_dotted_path_raises(self):
        # Previously invisible: the missing-field check gave up on any path with
        # a dot, so a broken path exported None and bound the placeholder.
        with pytest.raises(OutputCaptureError, match="result.output.missing"):
            _export(
                {"v": "result.output.missing"},
                {"data": {"result": {"output": {"present": 1}}}},
            )

    def test_absent_field_on_a_non_object_response_raises(self):
        with pytest.raises(OutputCaptureError, match="not an object"):
            _export({"v": "field"}, {"data": ["a", "list"]})

    def test_dict_form_absent_field_raises(self):
        with pytest.raises(OutputCaptureError, match="absent"):
            _export({"v": {"field": "absent"}}, {"data": {"present": 1}})

    def test_dict_form_absent_path_raises(self):
        with pytest.raises(OutputCaptureError, match="result.deep.missing"):
            _export(
                {"v": {"field": "result", "path": "deep.missing"}},
                {"data": {"result": {"deep": {"present": 1}}}},
            )

    def test_config_that_is_neither_a_field_nor_a_mapping_raises(self):
        with pytest.raises(OutputCaptureError, match="neither"):
            _export({"v": ["not", "a", "field"]}, {"data": {"present": 1}})
        with pytest.raises(OutputCaptureError, match="neither"):
            _export({"v": {"target": "no_field_key"}}, {"data": {"present": 1}})

    def test_a_null_value_is_a_value_not_a_miss(self):
        # The distinction the fix turns on: present-and-null is a legitimate
        # capture, absent is not. Collapsing them would fail half the suite.
        assert _export({"v": "maybe"}, {"data": {"maybe": None}}) == {"v": None}
        assert _export({"v": "a.b"}, {"data": {"a": {"b": None}}}) == {"v": None}

    def test_capture_still_binds_when_the_field_is_there(self):
        assert _export({"v": "value"}, {"data": {"value": "hello"}}) == {"v": "hello"}
        assert _export(
            {"v": {"field": "result", "path": "output", "json": True}},
            {"data": {"result": '{"output": 7}'}},
        ) == {"v": 7}

    def test_error_payload_capture_stays_lenient(self):
        """`expected_failure` steps capture from whichever payload arrives.

        An error report carries the error fields and never the call's own, so
        exactly one half of such a step's captures can bind per outcome. Failing
        on the other half would make the step type unwritable; the unbound
        placeholder is caught at the assertion instead.
        """
        error_info = {
            "success": False,
            "expected": True,
            "error_code": -32000,
            "error_type": "FunctionCallError",
            "error_message": "boom",
        }
        bound = _export({"r": "result", "t": "error_type"}, error_info)
        assert bound == {"t": "FunctionCallError"}


class TestErrorPayloadCaptureContract:
    def test_every_documented_error_field_is_capturable(self):
        """Which error fields a failure fills in varies by how it failed.

        A capture that binds only for a JSON-RPC error and not for a connection
        refusal is one no workflow author can write, so the four documented
        fields are always present, null where unknown.
        """
        step = ExecuteStep(
            {
                "type": "call",
                "name": "read",
                "node": "node-1",
                "context_id": "ctx",
                "method": "get",
            }
        )
        info = step._extract_error_info(
            {"success": False, "error": "connection refused"}, expected=True
        )
        bound = _export(
            {
                "c": "error_code",
                "t": "error_type",
                "m": "error_message",
                "e": "error",
            },
            info,
        )
        assert bound == {
            "c": None,
            "t": None,
            "m": "connection refused",
            "e": "connection refused",
        }


class TestMissingCaptureFailsTheStep:
    """The raise has to reach the step verdict, not die in a helper."""

    def _call_step(self, outputs: dict) -> ExecuteStep:
        return ExecuteStep(
            {
                "type": "call",
                "name": "read",
                "node": "node-1",
                "context_id": "ctx",
                "method": "get",
                "outputs": outputs,
            }
        )

    def _execute(self, step: ExecuteStep, response: dict) -> bool:
        with (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "node-1"),
            ),
            patch(
                "merobox.commands.bootstrap.steps.execute.call_function",
                new=AsyncMock(return_value={"success": True, "data": response}),
            ),
        ):
            return _run(step.execute({}, {}))

    def test_call_step_fails_when_the_capture_cannot_bind(self):
        step = self._call_step({"fleet_completed_at": "fleet_completed_at"})
        assert self._execute(step, {"result": {"output": "{}"}}) is False

    def test_call_step_succeeds_when_the_capture_binds(self):
        step = self._call_step({"got": "result"})
        assert self._execute(step, {"result": {"output": "v"}}) is True


class TestExpectedFailureThatSucceeds:
    """A protected key is already bound, so nothing may demand it bind twice.

    `call` keeps its lenient unexpected-success path: it exports the four error
    fields as None, marks them protected, then exports the success payload over
    the top. The error captures cannot bind from that second payload - it is
    the call's own result and never carries them - and requiring them to is
    what failed `negative-testing-example` and `propagation-monitoring`, both
    of which capture `error_type` beside `result` on a read that usually
    succeeds.
    """

    def _execute(self, outputs: dict, response: dict) -> tuple[bool, dict]:
        step = ExecuteStep(
            {
                "type": "call",
                "name": "read",
                "node": "node-1",
                "context_id": "ctx",
                "method": "get",
                "expected_failure": True,
                "outputs": outputs,
            }
        )
        dynamic: dict = {}
        with (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "node-1"),
            ),
            patch(
                "merobox.commands.bootstrap.steps.execute.call_function",
                new=AsyncMock(return_value={"success": True, "data": response}),
            ),
        ):
            return _run(step.execute({}, dynamic)), dynamic

    def test_error_captures_stay_bound_when_the_call_succeeds(self):
        ok, dynamic = self._execute(
            {"check_error": "error_type", "check_result": "result"},
            {"result": {"output": "v"}},
        )
        assert ok is True
        # Bound, not absent: the placeholder resolves, so no assertion holding
        # it compares against its own text.
        assert "check_error" in dynamic
        assert dynamic["check_error"] is None
        assert dynamic["check_result"] == {"output": "v"}

    def test_the_dict_form_with_a_custom_target_is_protected_too(self):
        ok, dynamic = self._execute(
            {
                "code": {"field": "error_code", "target": "err_{node_name}"},
                "check_result": "result",
            },
            {"result": {"output": "v"}},
        )
        assert ok is True
        assert dynamic["err_node-1"] is None

    def test_a_capture_of_a_field_nobody_sent_still_fails(self):
        # The strictness is unchanged for captures no pass ever bound.
        ok, _ = self._execute(
            {"fleet_completed_at": "fleet_completed_at"}, {"result": {"output": "v"}}
        )
        assert ok is False


class TestUnresolvedPlaceholderInAssertions:
    """An assertion may not compare a placeholder against its own text."""

    def _assert_step(self, statement: str, dynamic: dict | None = None) -> bool:
        step = AssertStep({"type": "assert", "name": "a", "statements": [statement]})
        return _run(step.execute({}, dynamic or {}))

    def _json_assert_step(self, statement: str, dynamic: dict | None = None) -> bool:
        step = JsonAssertStep(
            {"type": "json_assert", "name": "a", "statements": [statement]}
        )
        return _run(step.execute({}, dynamic or {}))

    def test_the_reported_incident(self):
        """`not_equal(x, null)` on a capture that never bound.

        The assertion was written to catch a build that does not produce
        `fleet_completed_at`. Against exactly that build it used to pass, because
        the unbound placeholder reached it as the string "{{fleet_completed_at}}",
        which is indeed not null.
        """
        assert (
            self._json_assert_step("not_equal({{fleet_completed_at}}, null)") is False
        )

    def test_the_same_assertion_still_passes_on_a_real_value(self):
        bound = {"fleet_completed_at": "2026-08-10T00:00:00Z"}
        assert (
            self._json_assert_step("not_equal({{fleet_completed_at}}, null)", bound)
            is True
        )

    def test_the_same_assertion_still_fails_on_a_captured_null(self):
        assert (
            self._json_assert_step(
                "not_equal({{fleet_completed_at}}, null)", {"fleet_completed_at": None}
            )
            is False
        )

    def test_is_set_on_an_unbound_placeholder_fails(self):
        # The other shape of the same false pass: the literal is a non-empty
        # string, so `is_set` used to be satisfied by the typo itself.
        assert self._assert_step("is_set({{never_captured}})") is False

    def test_inequality_against_an_unbound_placeholder_fails(self):
        # `assert` reaches the same false pass by its own route: the literal is
        # not equal to anything the workflow names, so `not_equal` is satisfied.
        assert self._assert_step("not_equal({{never_captured}}, 'x')") is False

    def test_embedded_unbound_placeholder_fails(self):
        # The incident's shape with the placeholder embedded. Not detectable
        # after the fact: an embedded placeholder that resolves to nothing is
        # replaced by its own NAME, braces and all gone, so the resolved string
        # carries no trace of the failure - and "prefix_never_captured" is just
        # as not-null as the value the assertion was written to demand.
        assert (
            self._json_assert_step("not_equal(prefix_{{never_captured}}, null)")
            is False
        )

    def test_bound_placeholders_are_untouched(self):
        assert self._assert_step("is_set({{ctx}})", {"ctx": "abc"}) is True
        assert self._assert_step("equal(id_{{ctx}}, 'id_abc')", {"ctx": "abc"}) is True

    def test_a_nested_json_literal_operand_still_compares(self):
        # Braces are the signal strictness keys off, so a brace-heavy operand is
        # where over-eager detection would show up first.
        assert (
            self._json_assert_step(
                'json_equal({{v}}, {"a": {"b": 1}})', {"v": {"a": {"b": 1}}}
            )
            is True
        )

    def test_non_blocking_mode_records_it_without_aborting(self):
        # Fuzzy runs collect failures rather than stopping, so this must arrive
        # as a failed assertion and not as an exception through the step.
        step = AssertStep(
            {
                "type": "assert",
                "name": "a",
                "non_blocking": True,
                "statements": ["is_set({{never_captured}})"],
            }
        )
        tracker = MagicMock()
        assert _run(step.execute({}, {"_fuzzy_test_results": tracker})) is True
        assert tracker.record_assertion.call_args.kwargs["passed"] is False


class TestNonAssertionStepsStayPermissive:
    """Strictness is scoped to assertions on purpose.

    Every other step type resolves optional and cosmetic config through the same
    helper, so raising there would fail workflows for placeholders nothing
    compares against. An unbound placeholder that does reach a comparison is
    caught by the assertion step, which is where it can do harm.
    """

    def test_a_call_step_still_passes_an_unknown_placeholder_through(self):
        step = ExecuteStep(
            {
                "type": "call",
                "name": "c",
                "node": "node-1",
                "context_id": "{{never_captured}}",
                "method": "get",
            }
        )
        assert (
            step._resolve_dynamic_value("{{never_captured}}", {}, {})
            == "{{never_captured}}"
        )

    def test_an_assertion_step_raises_on_the_same_input(self):
        step = AssertStep(
            {"type": "assert", "name": "a", "statements": ["is_set({{x}})"]}
        )
        with pytest.raises(UnresolvedPlaceholderError, match="never resolved"):
            step._resolve_dynamic_value("{{never_captured}}", {}, {})
