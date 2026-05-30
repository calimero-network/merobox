"""Verbosity gating for high-volume step output.

The per-call / per-iteration chatter from the export machinery (validated
config banners, "Exporting variables", per-field "✓ name = value" lines) is
the dominant source of CI log noise on the happy path. These tests pin that
output to VERBOSE while keeping genuine warnings (missing field, etc.) always
visible.
"""

from unittest.mock import patch

import pytest

from merobox.commands import utils
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import (
    LOG_LEVEL_NORMAL,
    LOG_LEVEL_VERBOSE,
)


@pytest.fixture(autouse=True)
def _reset_log_level():
    original = utils.get_log_level()
    yield
    utils.set_log_level(original)


def _capture(level):
    """Set verbosity and patch the shared Console; return the recorder list."""
    utils.set_log_level(level)
    printed: list[str] = []
    return printed, patch.object(
        utils.console,
        "print",
        side_effect=lambda *a, **k: printed.append(str(a[0]) if a else ""),
    )


def test_validate_export_config_banner_is_verbose_only():
    step = BaseStep({"name": "s", "outputs": {"out": "field"}})

    printed, cap = _capture(LOG_LEVEL_NORMAL)
    with cap:
        assert step._validate_export_config() is True
    assert not any("Custom outputs configuration validated" in p for p in printed)

    printed, cap = _capture(LOG_LEVEL_VERBOSE)
    with cap:
        assert step._validate_export_config() is True
    assert any("Custom outputs configuration validated" in p for p in printed)


def test_export_confirmation_lines_are_verbose_only():
    step = BaseStep({"name": "s", "outputs": {"my_key": "value"}})
    response = {"data": {"value": "hello"}}

    printed, cap = _capture(LOG_LEVEL_NORMAL)
    with cap:
        dyn: dict = {}
        step._export_custom_outputs(response, "node-1", dyn)
    # The value is still exported, just not announced on the console.
    assert dyn["my_key"] == "hello"
    assert not any("Exporting variables from" in p for p in printed)
    assert not any("✓" in p for p in printed)

    printed, cap = _capture(LOG_LEVEL_VERBOSE)
    with cap:
        step._export_custom_outputs(response, "node-1", {})
    assert any("Exporting variables from" in p for p in printed)
    assert any("✓" in p for p in printed)


def test_missing_field_warning_always_shown():
    step = BaseStep({"name": "s", "outputs": {"my_key": "absent_field"}})
    response = {"data": {"present": 1}}

    # Even at NORMAL, a genuine export failure must surface.
    printed, cap = _capture(LOG_LEVEL_NORMAL)
    with cap:
        step._export_custom_outputs(response, "node-1", {})
    assert any("Export failed" in p for p in printed)
