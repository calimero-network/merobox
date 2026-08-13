"""A capture that cannot bind, and a placeholder that never bound, must fail.

Two independent holes, either of which manufactures a green assertion against a
build that never produced the field: an `outputs:` entry naming a field the
response lacks only warned, and the resulting unbound `{{name}}` reached the
assertion as its own literal text, which satisfies most comparisons.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.group_upgrade import (
    _summarize_cascade_status,
    _summarize_migration_status,
)
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


def _call_step(outputs: dict | None = None, **extra) -> ExecuteStep:
    return ExecuteStep(
        {
            "type": "call",
            "name": "read",
            "node": "node-1",
            "context_id": "ctx",
            "method": "get",
            "outputs": outputs or {},
            **extra,
        }
    )


def _run_call(outputs: dict, response: dict, **extra) -> tuple[bool, dict]:
    """Run a `call` step whose request succeeds; return (verdict, bound values)."""
    step = _call_step(outputs, **extra)
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
        # An error report carries the error fields and never the call's own, so
        # exactly one half of an `expected_failure` step's captures binds.
        error_info = {
            "success": False,
            "expected": True,
            "error_code": -32000,
            "error_type": "FunctionCallError",
            "error_message": "boom",
        }
        bound = _export({"r": "result", "t": "error_type"}, error_info)
        assert bound == {"t": "FunctionCallError"}


class TestSummarizedResponseCapture:
    """A step that exports its own dict, not the raw response, gets the same check.

    The migration-status steps flatten the response first, so `.get()`-ing a
    field core omitted put the key in the exported dict anyway: the capture bound
    `None`, indistinguishable from a null core sent, and the check above had
    nothing to raise about. The summarizers now omit what core did not send.
    """

    def test_capturing_fleet_completed_at_from_a_response_without_it_fails(self):
        # The incident in the module docstring, end to end through the
        # summarizer that made the fix above inert for it.
        summary = _summarize_migration_status({"rollup": {"total": 2, "migrated": 0}})
        with pytest.raises(OutputCaptureError, match="fleet_completed_at"):
            _export({"fleet_completed_at": "fleet_completed_at"}, summary)

    def test_the_failure_names_the_fields_the_response_did_carry(self):
        summary = _summarize_migration_status({"rollup": {"total": 1}})
        with pytest.raises(OutputCaptureError, match="all_migrated, failed"):
            _export({"t": "cohort_pinned_at_hlc"}, summary)

    def test_a_timestamp_core_sent_as_null_still_binds(self):
        summary = _summarize_migration_status(
            {"fleetCompletedAt": None, "rollup": {"total": 1}}
        )
        assert _export({"t": "fleet_completed_at"}, summary) == {"t": None}

    def test_a_zero_timestamp_still_binds(self):
        summary = _summarize_migration_status(
            {"fleetCompletedAt": 0, "rollup": {"total": 1}}
        )
        assert _export({"t": "fleet_completed_at"}, summary) == {"t": 0}

    def test_capturing_an_unreported_member_field_fails(self):
        summary = _summarize_migration_status(
            {"targetVersion": 2, "members": [{"peer": "a", "state": "unknown"}]}
        )
        with pytest.raises(OutputCaptureError, match="members.0.schema_version"):
            _export({"v": "members.0.schema_version"}, summary)

    def test_a_reported_member_field_still_binds(self):
        summary = _summarize_migration_status(
            {
                "targetVersion": 2,
                "members": [
                    {"peer": "a", "state": "migrated", "report": {"schemaVersion": 2}}
                ],
            }
        )
        assert _export({"v": "members.0.schema_version"}, summary) == {"v": 2}

    def test_capturing_cascade_groups_fails_when_core_sent_no_list(self):
        with pytest.raises(OutputCaptureError, match="groups"):
            _export({"g": "groups"}, _summarize_cascade_status({"error": "boom"}))

    def test_an_empty_cascade_subtree_still_binds_an_empty_list(self):
        assert _export({"g": "groups"}, _summarize_cascade_status({"data": []})) == {
            "g": []
        }


class TestErrorPayloadCaptureContract:
    def test_every_documented_error_field_is_capturable(self):
        # A capture that binds for a JSON-RPC error but not a connection refusal
        # is unwritable, so all four fields are present, null where unknown.
        info = _call_step()._extract_error_info(
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

    def test_call_step_fails_when_the_capture_cannot_bind(self):
        ok, _ = _run_call(
            {"fleet_completed_at": "fleet_completed_at"}, {"result": {"output": "{}"}}
        )
        assert ok is False

    def test_call_step_succeeds_when_the_capture_binds(self):
        ok, _ = _run_call({"got": "result"}, {"result": {"output": "v"}})
        assert ok is True


class TestExpectedFailureThatSucceeds:
    """A protected key is already bound, so nothing may demand it bind twice.

    `call` exports the error fields as None, marks them protected, then exports
    the success payload over the top, which never carries them.
    """

    def test_error_captures_stay_bound_when_the_call_succeeds(self):
        ok, dynamic = _run_call(
            {"check_error": "error_type", "check_result": "result"},
            {"result": {"output": "v"}},
            expected_failure=True,
        )
        assert ok is True
        # Bound, not absent: the placeholder resolves, so no assertion holding
        # it compares against its own text.
        assert "check_error" in dynamic
        assert dynamic["check_error"] is None
        assert dynamic["check_result"] == {"output": "v"}

    def test_the_dict_form_with_a_custom_target_is_protected_too(self):
        ok, dynamic = _run_call(
            {
                "code": {"field": "error_code", "target": "err_{node_name}"},
                "check_result": "result",
            },
            {"result": {"output": "v"}},
            expected_failure=True,
        )
        assert ok is True
        assert dynamic["err_node-1"] is None

    def test_a_capture_of_a_field_nobody_sent_still_fails(self):
        # The strictness is unchanged for captures no pass ever bound.
        ok, _ = _run_call(
            {"fleet_completed_at": "fleet_completed_at"},
            {"result": {"output": "v"}},
            expected_failure=True,
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
        # Written to catch a build that never produces `fleet_completed_at`, it
        # passed on exactly that build: "{{fleet_completed_at}}" is not null.
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
        # Undetectable after the fact: an embedded miss is substituted as its
        # own NAME, braces gone, and "prefix_never_captured" is not null either.
        assert (
            self._json_assert_step("not_equal(prefix_{{never_captured}}, null)")
            is False
        )

    @pytest.mark.parametrize(
        "placeholder",
        [
            "install.node-1",
            "context.node-1",
            "context.node-1.memberPublicKey",
            "identity.node-1",
            "invite.malformed",
            "iteration_index",
            "never_captured",
        ],
    )
    def test_every_unresolved_branch_still_hands_the_name_back(self, placeholder):
        # A miss is detected as `resolved is placeholder`, so a branch that built
        # a new string instead would silently stop enforcing strictness.
        step = AssertStep({"type": "assert", "name": "a", "statements": ["1 == 1"]})
        with pytest.raises(UnresolvedPlaceholderError, match="never resolved"):
            step._resolve_dynamic_value(f"prefix_{{{{{placeholder}}}}}_suffix", {}, {})

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

    Other step types resolve optional and cosmetic config through the same
    helper, where nothing compares the result and nothing can be faked green.
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
