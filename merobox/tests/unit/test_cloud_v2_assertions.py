"""`assert` and `json_assert` in cloud-v2 workflows.

These keep v1's statement syntax so a ported workflow asserts what it did
before; the cases are carried over from v1's assertion-step tests.
"""

from unittest.mock import MagicMock

import pytest

from merobox.cloud_v2.assertions import (
    AssertionConfigError,
    eval_json_statement,
    eval_statement,
    resolve_operand,
    run_assert_step,
)
from merobox.cloud_v2.workflow import Runner, WorkflowError

VALUES = {"a": "x", "b": "y", "n": 5}


def _assert(statement: str, variables: dict | None = None, **extra) -> bool:
    step = {"type": "assert", "name": "a", "statements": [statement], **extra}
    return run_assert_step(step, variables or {})


def _json(statement: str, variables: dict | None = None) -> bool:
    step = {"type": "json_assert", "name": "a", "statements": [statement]}
    return run_assert_step(step, variables or {})


class TestPlaceholders:
    @pytest.mark.parametrize(
        "value, resolved",
        [
            ("{{a}},{{b}}", "x,y"),
            ("{{a}}{{b}}", "xy"),
            ("{{ a }}-{{b}}", "x-y"),
            ("pre-{{a}}-{{b}}", "pre-x-y"),
        ],
    )
    def test_every_placeholder_in_the_string_resolves(self, value, resolved):
        assert resolve_operand(value, VALUES) == resolved

    def test_a_lone_placeholder_keeps_the_value_s_type(self):
        assert resolve_operand("{{n}}", VALUES) == 5

    def test_a_quoted_literal_loses_its_quotes(self):
        assert resolve_operand("'abc'", {}) == "abc"
        assert resolve_operand('"abc"', {}) == "abc"

    def test_bound_placeholders_compare(self):
        assert _assert("is_set({{ctx}})", {"ctx": "abc"}) is True
        assert _assert("equal(id_{{ctx}}, 'id_abc')", {"ctx": "abc"}) is True


class TestUnresolvedPlaceholdersFail:
    """An assertion may not compare a placeholder against its own text."""

    def test_is_set_on_an_unbound_placeholder_fails(self):
        assert _assert("is_set({{never_captured}})") is False

    def test_inequality_against_an_unbound_placeholder_fails(self):
        assert _assert("not_equal({{never_captured}}, 'x')") is False

    def test_one_unbound_among_several_fails(self):
        assert _assert("equal({{a}},{{missing}}, 'x')", VALUES) is False

    def test_json_inequality_against_an_unbound_placeholder_fails(self):
        assert _json("not_equal({{fleet_completed_at}}, null)") is False

    def test_embedded_unbound_placeholder_fails(self):
        assert _json("not_equal(prefix_{{never_captured}}, null)") is False

    def test_the_same_assertion_passes_on_a_real_value(self):
        bound = {"fleet_completed_at": "2026-08-10T00:00:00Z"}
        assert _json("not_equal({{fleet_completed_at}}, null)", bound) is True

    def test_and_fails_on_a_captured_null(self):
        bound = {"fleet_completed_at": None}
        assert _json("not_equal({{fleet_completed_at}}, null)", bound) is False


class TestAssertStatements:
    @pytest.mark.parametrize(
        "statement, passed",
        [
            ("contains({{s}}, 'ell')", True),
            ("contains({{s}}, 'xyz')", False),
            ("not_contains({{s}}, 'xyz')", True),
            ("regex({{s}}, '^he')", True),
            ("regex({{s}}, '^lo')", False),
            ("is_set({{s}})", True),
            ("is_empty({{empty}})", True),
            ("is_empty({{s}})", False),
            ("equal({{s}}, 'hello')", True),
            ("equals({{s}}, 'hello')", True),
            ("not_equal({{s}}, 'hello')", False),
            ("not_equals({{s}}, 'bye')", True),
            ("{{n}} >= 3", True),
            ("{{n}} > 5", False),
            ("{{n}} == 5", True),
            ("{{n}} != 5", False),
            ("{{n}} < 10", True),
            ("{{n}} <= 4", False),
            ("{{s}} == hello", True),
        ],
    )
    def test_statement(self, statement, passed):
        variables = {"s": "hello", "n": 5, "empty": ""}
        assert eval_statement(statement, variables)[0] is passed

    def test_numbers_compare_as_numbers(self):
        assert eval_statement("{{n}} > 9", {"n": "10"})[0] is True

    def test_an_unrecognized_statement_fails(self):
        passed, detail = eval_statement("bogus", {})
        assert passed is False
        assert "unrecognized" in detail


class TestJsonStatements:
    def test_not_equal(self):
        assert eval_json_statement('json_not_equal("aa", "bb")', {})[0] is True
        assert eval_json_statement('json_not_equal("aa", "aa")', {})[0] is False
        assert eval_json_statement('not_equal("aa", "bb")', {})[0] is True

    def test_resolves_placeholders_on_both_sides(self):
        variables = {"old": "ee" * 32, "new": "ff" * 32}
        assert eval_json_statement("json_not_equal({{new}}, {{old}})", variables)[0]
        variables["new"] = variables["old"]
        assert not eval_json_statement("json_not_equal({{new}}, {{old}})", variables)[0]

    def test_normalises_before_comparing(self):
        assert (
            eval_json_statement('json_not_equal({"a": 1}, { "a": 1 })', {})[0] is False
        )

    def test_equal_and_subset(self):
        assert eval_json_statement('json_equal("aa", "aa")', {})[0] is True
        assert eval_json_statement('json_subset({"a": 1, "b": 2}, {"a": 1})', {})[0]
        assert not eval_json_statement('json_subset({"a": 1}, {"a": 1, "b": 2})', {})[0]

    def test_a_multi_key_literal_on_the_left_is_one_operand(self):
        assert eval_json_statement(
            'json_equal({"a": 1, "b": 2}, {"a": 1, "b": 2})', {}
        )[0]
        assert not eval_json_statement(
            'json_equal({"a": 1, "b": 2}, {"a": 1, "b": 3})', {}
        )[0]

    def test_a_comma_inside_a_json_string_is_not_a_separator(self):
        assert eval_json_statement('json_equal({"k": "a,b"}, {"k": "a,b"}) ', {})[0]
        assert eval_json_statement('json_not_equal({"k": "a,b"}, {"k": "a;b"})', {})[0]

    def test_arrays(self):
        assert eval_json_statement("json_equal([1, 2, 3], [1, 2, 3])", {})[0]
        assert eval_json_statement("json_not_equal([1, 2, 3], [1, 2])", {})[0]

    def test_a_nested_json_literal_operand_compares(self):
        assert _json('json_equal({{v}}, {"a": {"b": 1}})', {"v": {"a": {"b": 1}}})

    def test_an_unknown_function_fails_rather_than_raising(self):
        passed, desc, _l, _r = eval_json_statement('json_greater_than("2", "1")', {})
        assert passed is False
        assert "Unrecognized" in desc


class TestStep:
    def test_all_statements_must_pass(self):
        step = {
            "type": "json_assert",
            "statements": ['json_not_equal("aa", "bb")', 'json_equal("cc", "cc")'],
        }
        assert run_assert_step(step, {}) is True
        step["statements"][1] = 'json_not_equal("cc", "cc")'
        assert run_assert_step(step, {}) is False

    def test_a_statement_may_carry_a_message(self):
        step = {
            "type": "assert",
            "statements": [{"statement": "1 == 1", "message": "m"}],
        }
        assert run_assert_step(step, {}) is True

    def test_non_blocking_reports_without_failing(self):
        assert _assert("is_set({{never_captured}})", non_blocking=True) is True

    @pytest.mark.parametrize(
        "step",
        [
            {"type": "assert", "statements": []},
            {"type": "assert", "statements": [1]},
            {"type": "assert", "statements": [{"message": "no statement"}]},
            {"type": "assert", "statements": ["1 == 1"], "non_blocking": "yes"},
        ],
    )
    def test_malformed_statements_are_a_config_error(self, step):
        with pytest.raises(AssertionConfigError):
            run_assert_step(step, {})


class TestRunner:
    """The runner turns a failed or malformed assertion into a WorkflowError."""

    def _runner(self, variables: dict) -> Runner:
        runner = Runner.__new__(Runner)
        runner.variables = variables
        runner.cluster = MagicMock()
        return runner

    def test_a_passing_assertion_is_quiet(self):
        self._runner({"n": 1})._assert({"type": "assert", "statements": ["{{n}} == 1"]})

    def test_a_failing_assertion_raises(self):
        with pytest.raises(WorkflowError, match="assertion failed"):
            self._runner({"n": 1})._assert(
                {"type": "assert", "name": "n", "statements": ["{{n}} == 2"]}
            )

    def test_a_malformed_assertion_raises(self):
        with pytest.raises(WorkflowError, match="non-empty list"):
            self._runner({})._assert({"type": "json_assert", "statements": []})
