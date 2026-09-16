"""A string holding several placeholders resolves each of them.

`{{a}},{{b}}` starts with `{{` and ends with `}}`, yet it is two placeholders,
not one named `a}},{{b`.
"""

import pytest

from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.errors import UnresolvedPlaceholderError

VALUES = {"a": "x", "b": "y", "n": 5}


def _resolve(value, dynamic=VALUES):
    return BaseStep({"name": "s", "type": "call"})._resolve_dynamic_value(
        value, {}, dynamic
    )


@pytest.mark.parametrize(
    "value, resolved",
    [
        ("{{a}},{{b}}", "x,y"),
        ("{{a}}{{b}}", "xy"),
        ("{{ a }}-{{b}}", "x-y"),
        ("pre-{{a}}-{{b}}", "pre-x-y"),
    ],
)
def test_every_placeholder_in_the_string_resolves(value, resolved):
    assert _resolve(value) == resolved


def test_a_lone_placeholder_keeps_the_value_s_type():
    assert _resolve("{{n}}") == 5


def test_a_strict_step_refuses_one_unbound_placeholder_among_several():
    step = AssertStep({"type": "assert", "name": "a", "statements": ["1 == 1"]})
    with pytest.raises(UnresolvedPlaceholderError, match="missing"):
        step._resolve_dynamic_value("{{a}},{{missing}}", {}, VALUES)
