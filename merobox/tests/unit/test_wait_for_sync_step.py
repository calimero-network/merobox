"""Unit tests for the wait_for_sync step's adaptive backoff polling.

These tests exercise ``_wait_for_sync`` directly with a stubbed convergence
check and a patched ``asyncio.sleep`` so the inter-attempt sleep schedule is
observable without spinning real nodes or wall-clock waits.
"""

import asyncio
from unittest.mock import patch

import pytest

from merobox.commands.bootstrap.steps.wait_for_sync import WaitForSyncStep

TARGETS = [{"kind": "context", "id": "ctx-1", "field": "contextStateHash"}]
NODES = ["calimero-node-1", "calimero-node-2"]


def _make_step(extra_config: dict | None = None) -> WaitForSyncStep:
    config = {
        "type": "wait_for_sync",
        "context_id": "ctx-1",
        "nodes": NODES,
    }
    if extra_config:
        config.update(extra_config)
    return WaitForSyncStep(config)


def _run_with_recorded_sleeps(step, converge_on_attempt, **kwargs):
    """Run _wait_for_sync, returning (result, details, backoff_only).

    The stubbed convergence check reports "not converged" until
    ``converge_on_attempt`` (1-indexed), so the loop sleeps once between each
    pair of attempts. ``asyncio.sleep`` is replaced with a zero-delay recorder
    so the schedule is captured without real waiting.

    ``backoff_only`` strips the per-attempt jitter (0.1 * (attempt % 3)) back
    off each recorded sleep, recovering the pure geometric-backoff component.

    Assumption: the loop sleeps exactly once at the end of every missed
    attempt and never before the convergence check, so the i-th recorded
    sleep (0-indexed) always follows attempt ``i + 1``. If convergence is
    reached on the first check (``converge_on_attempt=1``) the loop sleeps
    zero times and ``sleeps`` is empty — see
    ``test_immediate_convergence_does_not_sleep``.
    """
    attempts = {"n": 0}
    sleeps: list[float] = []

    async def fake_check(target, nodes, trigger_sync):
        attempts["n"] += 1
        converged = attempts["n"] >= converge_on_attempt
        node_hashes = dict.fromkeys(nodes, "h" if converged else None)
        return converged, node_hashes

    async def fake_sleep(duration):
        sleeps.append(duration)

    async def run():
        # Patch sleep on the module under test, not the global asyncio.sleep,
        # so the target is robust to import aliasing / event-loop differences.
        with (
            patch.object(step, "_check_target_convergence", side_effect=fake_check),
            patch(
                "merobox.commands.bootstrap.steps.wait_for_sync.asyncio.sleep",
                side_effect=fake_sleep,
            ),
        ):
            return await step._wait_for_sync(TARGETS, NODES, timeout=30, **kwargs)

    result, details = asyncio.run(run())
    # The i-th sleep (0-indexed) follows attempt (i + 1), whose jitter is
    # 0.1 * ((i + 1) % 3).
    backoff_only = [
        round(sleep - 0.1 * ((i + 1) % 3), 4) for i, sleep in enumerate(sleeps)
    ]
    return result, details, backoff_only


def test_backoff_schedule_grows_geometrically_and_caps():
    """Misses sleep initial, then initial*factor, ... capped at check_interval."""
    step = _make_step()
    # Converge on the 6th check → attempts 1-5 miss → 5 inter-attempt sleeps.
    result, _details, backoff_only = _run_with_recorded_sleeps(
        step,
        converge_on_attempt=6,
        check_interval=0.5,
        initial_check_interval=0.05,
        backoff_factor=2.0,
    )

    assert result is True
    assert backoff_only == [0.05, 0.1, 0.2, 0.4, 0.5]


def test_immediate_convergence_does_not_sleep():
    """Converging on the very first check returns without any backoff sleep."""
    step = _make_step()
    result, _details, backoff_only = _run_with_recorded_sleeps(
        step,
        converge_on_attempt=1,
        check_interval=0.5,
    )

    assert result is True
    # First check hit → loop never reached the end-of-miss sleep.
    assert backoff_only == []


def test_fast_sync_caught_in_a_few_short_steps():
    """A sync that lands just after the first miss is caught well under the cap."""
    step = _make_step()
    result, _details, backoff_only = _run_with_recorded_sleeps(
        step,
        converge_on_attempt=3,
        check_interval=0.5,
        initial_check_interval=0.05,
        backoff_factor=2.0,
    )

    assert result is True
    # Converged on the 3rd check → only 2 short inter-attempt sleeps happened.
    assert backoff_only == [0.05, 0.1]


def test_initial_interval_never_exceeds_check_interval_cap():
    """A configured initial larger than the cap is clamped to the cap."""
    step = _make_step()
    result, _details, backoff_only = _run_with_recorded_sleeps(
        step,
        converge_on_attempt=3,
        check_interval=0.1,
        initial_check_interval=5.0,
        backoff_factor=2.0,
    )

    assert result is True
    # Both sleeps pinned to the cap; the oversized initial was clamped down.
    assert backoff_only == [0.1, 0.1]


def test_backoff_factor_one_holds_interval_flat():
    """factor == 1 reproduces the legacy fixed-interval behavior."""
    step = _make_step()
    result, _details, backoff_only = _run_with_recorded_sleeps(
        step,
        converge_on_attempt=4,
        check_interval=0.5,
        initial_check_interval=0.3,
        backoff_factor=1.0,
    )

    assert result is True
    assert backoff_only == [0.3, 0.3, 0.3]


def test_defaults_use_constant_backoff_schedule():
    """With no overrides, the step uses the module-default 0.05s / 2.0x schedule."""
    step = _make_step()
    result, _details, backoff_only = _run_with_recorded_sleeps(
        step,
        converge_on_attempt=4,
        check_interval=0.5,
    )

    assert result is True
    assert backoff_only == [0.05, 0.1, 0.2]


@pytest.mark.parametrize(
    "bad_config, field",
    [
        ({"initial_check_interval": 0}, "initial_check_interval"),
        ({"initial_check_interval": -1}, "initial_check_interval"),
        ({"initial_check_interval": "x"}, "initial_check_interval"),
        ({"backoff_factor": 0.5}, "backoff_factor"),
        ({"backoff_factor": "x"}, "backoff_factor"),
    ],
)
def test_invalid_backoff_config_rejected(bad_config, field):
    with pytest.raises(ValueError, match=field):
        _make_step(bad_config)


def test_backoff_factor_one_is_accepted():
    # factor == 1 is the lower bound (legacy fixed interval), must validate.
    _make_step({"backoff_factor": 1})
