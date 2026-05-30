"""Unit tests for the console verbosity controls in ``commands/utils.py``.

Covers level parsing, flag/env resolution priority, the ``vprint`` gate, and
the per-context (ContextVar) scoping of the level.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands import utils
from merobox.commands.utils import (
    LOG_LEVEL_NORMAL,
    LOG_LEVEL_QUIET,
    LOG_LEVEL_VERBOSE,
    parse_log_level,
    resolve_log_level,
    vprint,
)


@pytest.fixture(autouse=True)
def _reset_log_level():
    """Keep the module-global level from leaking across tests."""
    original = utils.get_log_level()
    utils.set_log_level(LOG_LEVEL_NORMAL)
    yield
    utils.set_log_level(original)


class TestParseLogLevel:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("quiet", LOG_LEVEL_QUIET),
            ("QUIET", LOG_LEVEL_QUIET),
            ("normal", LOG_LEVEL_NORMAL),
            (" verbose ", LOG_LEVEL_VERBOSE),
            ("debug", LOG_LEVEL_VERBOSE),
            ("0", LOG_LEVEL_QUIET),
            ("2", LOG_LEVEL_VERBOSE),
            (0, LOG_LEVEL_QUIET),
            (2, LOG_LEVEL_VERBOSE),
            (True, LOG_LEVEL_VERBOSE),
            (False, LOG_LEVEL_NORMAL),
        ],
    )
    def test_recognized_values(self, value, expected):
        assert parse_log_level(value) == expected

    def test_none_returns_default(self):
        assert parse_log_level(None) == LOG_LEVEL_NORMAL
        assert parse_log_level(None, default=LOG_LEVEL_QUIET) == LOG_LEVEL_QUIET

    def test_unknown_falls_back_to_default(self):
        assert parse_log_level("banana") == LOG_LEVEL_NORMAL
        assert parse_log_level("banana", default=LOG_LEVEL_VERBOSE) == LOG_LEVEL_VERBOSE

    def test_out_of_range_int_is_clamped(self):
        assert parse_log_level(99) == LOG_LEVEL_VERBOSE
        assert parse_log_level(-5) == LOG_LEVEL_QUIET


class TestResolveLogLevel:
    def test_verbose_flag_wins(self):
        assert resolve_log_level(verbose=True) == LOG_LEVEL_VERBOSE

    def test_quiet_flag(self):
        assert resolve_log_level(quiet=True) == LOG_LEVEL_QUIET

    def test_verbose_beats_quiet_when_both_set(self):
        assert resolve_log_level(verbose=True, quiet=True) == LOG_LEVEL_VERBOSE

    def test_env_var_used_when_no_flags(self):
        with patch.dict("os.environ", {"MEROBOX_LOG_LEVEL": "verbose"}):
            assert resolve_log_level() == LOG_LEVEL_VERBOSE
        with patch.dict("os.environ", {"MEROBOX_LOG_LEVEL": "quiet"}):
            assert resolve_log_level() == LOG_LEVEL_QUIET

    def test_flag_beats_env_var(self):
        with patch.dict("os.environ", {"MEROBOX_LOG_LEVEL": "quiet"}):
            assert resolve_log_level(verbose=True) == LOG_LEVEL_VERBOSE

    def test_default_is_normal(self):
        with patch.dict("os.environ", {}, clear=True):
            assert resolve_log_level() == LOG_LEVEL_NORMAL

    def test_unknown_env_var_falls_back_to_normal(self):
        with patch.dict("os.environ", {"MEROBOX_LOG_LEVEL": "loud"}):
            assert resolve_log_level() == LOG_LEVEL_NORMAL


class TestVprint:
    def _capture(self):
        """Patch the module console and return the mock for assertions."""
        return patch.object(utils, "console", MagicMock())

    def test_verbose_message_suppressed_at_normal(self):
        utils.set_log_level(LOG_LEVEL_NORMAL)
        with self._capture() as mock_console:
            vprint("chatter", level=LOG_LEVEL_VERBOSE)
            mock_console.print.assert_not_called()

    def test_verbose_message_shown_at_verbose(self):
        utils.set_log_level(LOG_LEVEL_VERBOSE)
        with self._capture() as mock_console:
            vprint("chatter", level=LOG_LEVEL_VERBOSE)
            mock_console.print.assert_called_once()

    def test_normal_message_shown_at_normal_hidden_at_quiet(self):
        utils.set_log_level(LOG_LEVEL_NORMAL)
        with self._capture() as mock_console:
            vprint("banner", level=LOG_LEVEL_NORMAL)
            mock_console.print.assert_called_once()

        utils.set_log_level(LOG_LEVEL_QUIET)
        with self._capture() as mock_console:
            vprint("banner", level=LOG_LEVEL_NORMAL)
            mock_console.print.assert_not_called()

    def test_quiet_level_message_always_shown(self):
        utils.set_log_level(LOG_LEVEL_QUIET)
        with self._capture() as mock_console:
            vprint("always", level=LOG_LEVEL_QUIET)
            mock_console.print.assert_called_once()

    def test_default_level_is_normal(self):
        # vprint with no explicit level behaves as a NORMAL-level message.
        utils.set_log_level(LOG_LEVEL_QUIET)
        with self._capture() as mock_console:
            vprint("default-level")
            mock_console.print.assert_not_called()

    def test_forwards_args_and_kwargs(self):
        utils.set_log_level(LOG_LEVEL_NORMAL)
        with self._capture() as mock_console:
            vprint("msg", level=LOG_LEVEL_NORMAL, markup=False)
            mock_console.print.assert_called_once_with("msg", markup=False)


class TestContextScoping:
    """The level is a ContextVar: child tasks inherit it, but a level set
    inside a task does not leak back to the parent (so concurrent branches
    don't clobber each other)."""

    def test_child_task_inherits_parent_level(self):
        async def scenario():
            utils.set_log_level(LOG_LEVEL_VERBOSE)
            seen = {}

            async def child():
                seen["inherited"] = utils.get_log_level()
                # Mutate only this task's context.
                utils.set_log_level(LOG_LEVEL_QUIET)
                seen["after_local_set"] = utils.get_log_level()

            await asyncio.create_task(child())
            seen["parent_after_child"] = utils.get_log_level()
            return seen

        seen = asyncio.run(scenario())
        assert seen["inherited"] == LOG_LEVEL_VERBOSE
        assert seen["after_local_set"] == LOG_LEVEL_QUIET
        # Child's set did not leak back to the parent context.
        assert seen["parent_after_child"] == LOG_LEVEL_VERBOSE

    def test_concurrent_tasks_are_isolated(self):
        async def scenario():
            results = {}

            async def branch(name, level):
                utils.set_log_level(level)
                # Yield so the other branch interleaves before we read back.
                await asyncio.sleep(0)
                results[name] = utils.get_log_level()

            await asyncio.gather(
                asyncio.create_task(branch("a", LOG_LEVEL_QUIET)),
                asyncio.create_task(branch("b", LOG_LEVEL_VERBOSE)),
            )
            return results

        results = asyncio.run(scenario())
        # Each branch reads back its own level despite interleaving.
        assert results["a"] == LOG_LEVEL_QUIET
        assert results["b"] == LOG_LEVEL_VERBOSE
