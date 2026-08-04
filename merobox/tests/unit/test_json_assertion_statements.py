"""The `statements:` forms of `json_assert`, and inequality in particular.

`json_not_equal` exists because some properties are only expressible as a
difference. The case that prompted it: a revocation is terminal, so a device that
re-enrols after being revoked must receive a NEW id — and the new id is
unpredictable, so there is no literal to compare it against. Before this, a
scenario had to shell out to a script or assert equality against a stand-in, which
tests the stand-in rather than the property.

The other reason to pin these here: an unrecognised function name does **not**
raise. `_eval_statement` falls through to `return False, "Unrecognized JSON
assertion", None, None`, so a typo or a function that does not exist reads as a
FAILING assertion rather than a broken scenario. That is the safe direction, but it
means a scenario can be quietly asserting nothing meaningful, so the recognised
surface is worth locking down.
"""

import asyncio

from merobox.commands.bootstrap.steps.json_assertion import JsonAssertStep


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _step(statements: list[str]) -> JsonAssertStep:
    return JsonAssertStep(
        {
            "type": "json_assert",
            "name": "Assert",
            "statements": statements,
        }
    )


def _eval(statement: str, dynamic: dict | None = None) -> bool:
    step = _step([statement])
    passed, _desc, _left, _right = step._eval_statement(statement, {}, dynamic or {})
    return passed


class TestJsonNotEqual:
    def test_differing_values_pass(self):
        assert _eval('json_not_equal("aa", "bb")') is True

    def test_equal_values_fail(self):
        assert _eval('json_not_equal("aa", "aa")') is False

    def test_bare_alias_works(self):
        assert _eval('not_equal("aa", "bb")') is True
        assert _eval('not_equal("aa", "aa")') is False

    def test_resolves_placeholders_on_both_sides(self):
        """The real shape: two captured outputs, neither known in advance."""
        dynamic = {"old_device": "ee" * 32, "new_device": "ff" * 32}
        assert _eval("json_not_equal({{new_device}}, {{old_device}})", dynamic) is True

    def test_a_device_that_was_not_re_minted_is_caught(self):
        """The regression this primitive exists to catch.

        If a revoked device id were handed back on re-enrolment, both placeholders
        resolve to the same value and the assertion must fail.
        """
        dynamic = {"old_device": "ee" * 32, "new_device": "ee" * 32}
        assert _eval("json_not_equal({{new_device}}, {{old_device}})", dynamic) is False

    def test_normalises_before_comparing_so_json_shape_wins_over_spelling(self):
        """`{"a": 1}` and `{ "a": 1 }` are the same JSON and must not differ."""
        assert _eval('json_not_equal({"a": 1}, { "a": 1 })') is False


class TestRecognisedSurface:
    def test_equality_still_works(self):
        assert _eval('json_equal("aa", "aa")') is True
        assert _eval('json_equal("aa", "bb")') is False

    def test_subset_still_works(self):
        assert _eval('json_subset({"a": 1, "b": 2}, {"a": 1})') is True
        assert _eval('json_subset({"a": 1}, {"a": 1, "b": 2})') is False

    def test_negative_form_is_not_swallowed_by_the_positive_one(self):
        """`_call_like` anchors with `startswith`, so `equal(` cannot match here.

        Pinned because it is the plausible way to break this: if matching ever
        became a substring scan, `json_not_equal(a, b)` would be handled by the
        `equal` arm and quietly invert every assertion using it.
        """
        assert _eval('json_not_equal("aa", "bb")') is True

    def test_a_multi_key_literal_on_the_left_is_split_correctly(self):
        """Regression for the argument splitter.

        It used to split on the FIRST comma, which is the operand separator only
        when the left side has no comma of its own — true for the common
        placeholder-vs-literal shape, and false the moment the left side is a
        multi-key object. `{"a": 1, "b": 2}, {"a": 1}` became `{"a": 1` and
        `"b": 2}, {"a": 1}`, so the assertion failed on unparseable halves rather
        than on the values.
        """
        assert _eval('json_equal({"a": 1, "b": 2}, {"a": 1, "b": 2})') is True
        assert _eval('json_equal({"a": 1, "b": 2}, {"a": 1, "b": 3})') is False
        assert _eval('json_not_equal({"a": 1, "b": 2}, {"a": 1, "b": 3})') is True

    def test_a_comma_inside_a_json_string_is_not_a_separator(self):
        """Depth tracking is not enough on its own — strings need it too."""
        assert _eval('json_equal({"k": "a,b"}, {"k": "a,b"}) ') is True
        assert _eval('json_not_equal({"k": "a,b"}, {"k": "a;b"})') is True

    def test_arrays_on_the_left_survive_too(self):
        assert _eval("json_equal([1, 2, 3], [1, 2, 3])") is True
        assert _eval("json_not_equal([1, 2, 3], [1, 2])") is True

    def test_an_unknown_function_fails_rather_than_raising(self):
        """Documents the fallback, which is easy to mistake for a real failure."""
        step = _step(['json_greater_than("2", "1")'])
        passed, desc, _l, _r = step._eval_statement(
            'json_greater_than("2", "1")', {}, {}
        )
        assert passed is False
        assert "Unrecognized" in desc


class TestExecution:
    def test_all_statements_must_pass(self):
        step = _step(['json_not_equal("aa", "bb")', 'json_equal("cc", "cc")'])
        assert _run(step.execute({}, {})) is True

    def test_one_failing_statement_fails_the_step(self):
        step = _step(['json_not_equal("aa", "bb")', 'json_not_equal("cc", "cc")'])
        assert _run(step.execute({}, {})) is False
