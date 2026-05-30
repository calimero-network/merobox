"""Verbosity gating tests for the wait_for_sync step.

Asserts the CI-friendly default: the happy path emits a banner and a single
success summary but no per-attempt blocks, verbose mode restores the
per-attempt detail, and failures always dump the full per-node state
regardless of level.
"""

import asyncio
from unittest.mock import patch

import pytest

from merobox.commands import utils
from merobox.commands.bootstrap.steps.wait_for_sync import WaitForSyncStep
from merobox.commands.utils import (
    LOG_LEVEL_NORMAL,
    LOG_LEVEL_QUIET,
    LOG_LEVEL_VERBOSE,
)

TARGETS = [{"kind": "context", "id": "ctx-1", "field": "contextStateHash"}]
NODES = ["calimero-node-1", "calimero-node-2"]


@pytest.fixture(autouse=True)
def _reset_log_level():
    original = utils.get_log_level()
    yield
    utils.set_log_level(original)


def _make_step() -> WaitForSyncStep:
    return WaitForSyncStep(
        {"type": "wait_for_sync", "context_id": "ctx-1", "nodes": NODES}
    )


def _run_capturing_output(step, level, converge_on_attempt, **kwargs):
    """Run _wait_for_sync at the given verbosity, capturing all printed text.

    Patches ``print`` on the single shared Console instance rather than
    rebinding the ``console`` name in each module: ``vprint`` and the step's
    direct ``console.print`` calls both target that same object, so one patch
    captures banner, per-attempt, and summary output regardless of how the
    step routes a given line. Returns ``(result, joined_output, attempts)``;
    ``attempts`` lets callers assert the loop ran the expected number of
    rounds, making the tests sensitive to early-exit bugs.

    Note: ``TARGETS`` has a single entry, so the per-call ``fake_check``
    counter equals the attempt number. With multiple targets it would
    increment once per target per attempt — fine here, but a multi-target
    fixture would need a per-round counter.
    """
    utils.set_log_level(level)
    printed: list[str] = []

    def _record(*a, **k):
        printed.append(str(a[0]) if a else "")

    attempts = {"n": 0}

    async def fake_check(target, nodes, trigger_sync):
        attempts["n"] += 1
        converged = attempts["n"] >= converge_on_attempt
        node_hashes = dict.fromkeys(nodes, "h" if converged else None)
        return converged, node_hashes

    async def fake_sleep(_duration):
        return None

    async def run():
        with (
            patch.object(utils.console, "print", side_effect=_record),
            patch.object(step, "_check_target_convergence", side_effect=fake_check),
            patch(
                "merobox.commands.bootstrap.steps.wait_for_sync.asyncio.sleep",
                side_effect=fake_sleep,
            ),
        ):
            return await step._wait_for_sync(TARGETS, NODES, timeout=30, **kwargs)

    result, _details = asyncio.run(run())
    return result, "\n".join(printed), attempts["n"]


def test_happy_path_at_normal_hides_per_attempt_shows_summary():
    step = _make_step()
    # Miss the first check, converge on the second.
    result, output, attempts = _run_capturing_output(
        step, LOG_LEVEL_NORMAL, converge_on_attempt=2, check_interval=0.01
    )

    assert result is True
    assert attempts == 2  # actually polled twice; not an early exit
    # Banner present, per-attempt block absent, success summary present.
    assert "Waiting for" in output
    assert "not all converged yet" not in output
    assert "All targets synced" in output


def test_verbose_restores_per_attempt_detail():
    step = _make_step()
    result, output, attempts = _run_capturing_output(
        step, LOG_LEVEL_VERBOSE, converge_on_attempt=2, check_interval=0.01
    )

    assert result is True
    assert attempts == 2
    assert "not all converged yet" in output
    assert "All targets synced" in output


def test_quiet_hides_banner_but_keeps_summary():
    step = _make_step()
    result, output, attempts = _run_capturing_output(
        step, LOG_LEVEL_QUIET, converge_on_attempt=2, check_interval=0.01
    )

    assert result is True
    assert attempts == 2
    assert "Waiting for" not in output
    assert "not all converged yet" not in output
    # Final summary must always survive, even when quiet.
    assert "All targets synced" in output


def test_failure_dumps_full_state_even_at_normal():
    step = _make_step()
    # Never converge; bound the loop with retry_attempts.
    result, output, attempts = _run_capturing_output(
        step,
        LOG_LEVEL_NORMAL,
        converge_on_attempt=999,
        check_interval=0.01,
        retry_attempts=2,
    )

    assert result is False
    # Ran the full bounded budget (2 loop rounds) before giving up, not an
    # early exit; the post-loop consistency check polls once more.
    assert attempts >= 2
    # Diagnostics are never swallowed on failure.
    assert "Sync verification failed" in output
    assert "Final state" in output
    assert "calimero-node-1" in output
