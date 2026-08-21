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


# ---------------------------------------------------------------------------
# `expect`: gate on a read, not on a hash
#
# A converged state hash is a claim about state, not proof of it — a node can
# publish a root it cannot back, and even truthful hashes can agree a few
# milliseconds before a pending delta finishes applying. These tests pin that
# agreeing hashes alone are no longer enough to declare sync when the scenario
# has declared what it expects to read.
# ---------------------------------------------------------------------------

EXPECT = {"method": "get", "args": {"key": "k"}, "equals": {"output": "v"}}


def _run_with_expect(step, expect, reads_per_attempt, converge_on_attempt=1):
    """Run ``_wait_for_sync`` with stubbed hash convergence and expect reads.

    ``reads_per_attempt`` is a list of per-node read maps, consumed one entry
    per probe; the last entry repeats once exhausted. Returns
    ``(result, details, probe_count)``.
    """
    attempts = {"n": 0}
    probes = {"n": 0}

    async def fake_check(target, nodes, trigger_sync):
        attempts["n"] += 1
        converged = attempts["n"] >= converge_on_attempt
        return converged, dict.fromkeys(nodes, "h" if converged else None)

    async def fake_expect(expect_arg, context_id, nodes):
        index = min(probes["n"], len(reads_per_attempt) - 1)
        probes["n"] += 1
        observed = reads_per_attempt[index]
        return all(v == expect_arg["equals"] for v in observed.values()), observed

    async def run():
        with (
            patch.object(step, "_check_target_convergence", side_effect=fake_check),
            patch.object(step, "_check_expect_convergence", side_effect=fake_expect),
            patch(
                "merobox.commands.bootstrap.steps.wait_for_sync.asyncio.sleep",
                side_effect=lambda _d: asyncio.sleep(0),
            ),
        ):
            return await step._wait_for_sync(
                TARGETS,
                NODES,
                timeout=30,
                check_interval=0.01,
                initial_check_interval=0.01,
                retry_attempts=4,
                expect=expect,
                expect_context_id="ctx-1",
            )

    result, details = asyncio.run(run())
    return result, details, probes["n"]


def test_expect_satisfied_on_every_node_reports_sync():
    step = _make_step({"expect": EXPECT})
    both_match = {node: {"output": "v"} for node in NODES}
    result, details, probes = _run_with_expect(step, EXPECT, [both_match])

    assert result is True
    assert details["expect_satisfied"] is True
    assert probes == 1


def test_agreeing_hashes_do_not_declare_sync_while_a_node_cannot_read_it():
    """The exact shape of the CI flake this gate exists for.

    Both nodes report the same state hash, so the pre-existing hash-equality
    check would have declared sync immediately — and the scenario's next read
    would have raced an apply that had not landed. One node still returning a
    stale value must keep the step waiting.
    """
    step = _make_step({"expect": EXPECT})
    one_behind = {NODES[0]: {"output": "v"}, NODES[1]: {"output": None}}
    result, details, probes = _run_with_expect(step, EXPECT, [one_behind])

    assert result is False
    assert probes > 1, "a miss must be retried, not accepted"
    assert details["per_node_expect"] == one_behind


def test_expect_that_catches_up_mid_wait_succeeds():
    """The lagging node materialises the write on a later probe."""
    step = _make_step({"expect": EXPECT})
    behind = {NODES[0]: {"output": "v"}, NODES[1]: {"output": None}}
    caught_up = {node: {"output": "v"} for node in NODES}
    result, details, probes = _run_with_expect(step, EXPECT, [behind, caught_up])

    assert result is True
    assert details["expect_satisfied"] is True
    assert probes == 2


def test_expect_is_not_probed_until_the_hashes_agree():
    """Each probe is a real execution on every node, so don't spend them
    while the answer is already known to be "not yet"."""
    step = _make_step({"expect": EXPECT})
    both_match = {node: {"output": "v"} for node in NODES}
    result, _details, probes = _run_with_expect(
        step, EXPECT, [both_match], converge_on_attempt=3
    )

    assert result is True
    assert probes == 1, "probed only after the hashes converged on attempt 3"


@pytest.mark.parametrize(
    "bad_expect,fragment",
    [
        ({"args": {}, "equals": 1}, "expect.method"),
        ({"method": "", "equals": 1}, "expect.method"),
        ({"method": "get"}, "expect.equals"),
        ({"method": "get", "args": [], "equals": 1}, "expect.args"),
        ("not-a-dict", "'expect' must be a dictionary"),
    ],
)
def test_invalid_expect_rejected(bad_expect, fragment):
    with pytest.raises(ValueError, match=fragment):
        _make_step({"expect": bad_expect})


def test_expect_equals_null_is_a_valid_expectation():
    """`equals: null` asserts a read returns nothing — presence of the key is
    what's required, not a truthy value."""
    step = _make_step({"expect": {"method": "get", "equals": None}})
    assert step.config["expect"]["equals"] is None


def test_expect_requires_a_context_to_read_in():
    with pytest.raises(ValueError, match="needs 'context_id'"):
        WaitForSyncStep(
            {
                "type": "wait_for_sync",
                "group_id": "grp-1",
                "nodes": NODES,
                "expect": EXPECT,
            }
        )


# ---------------------------------------------------------------------------
# What `expect` actually observes.
#
# The tests above stub `_check_expect_convergence`, so none of them touch the
# shape `call_function` really returns. That gap shipped a gate that compared
# the JSON-RPC envelope against an `equals` written for the inner result: it
# could never match, so the step waited out its full 60s timeout and reported
# "the value never arrived" while both nodes had returned it. These pin the
# level being compared.
# ---------------------------------------------------------------------------


def test_rpc_result_unwraps_the_envelope():
    from merobox.commands.bootstrap.steps.wait_for_sync import _rpc_result

    envelope = {"id": "1", "jsonrpc": "2.0", "result": {"output": "from_node1"}}
    assert _rpc_result(envelope) == {"output": "from_node1"}


def test_rpc_result_passes_through_a_non_enveloped_payload():
    """A response with no `result` key compares as-is rather than becoming None."""
    from merobox.commands.bootstrap.steps.wait_for_sync import _rpc_result

    assert _rpc_result({"output": "v"}) == {"output": "v"}
    assert _rpc_result(None) is None
    assert _rpc_result("scalar") == "scalar"


def test_expect_read_observes_the_inner_result_not_the_envelope():
    """The regression test for the 60s-timeout-on-a-present-value bug.

    `call_function` hands back the whole envelope; `equals` is written to match
    what `outputs: {x: result}` captures, which is the inner `result`.
    """
    step = _make_step({"expect": EXPECT})
    envelope = {"id": "1", "jsonrpc": "2.0", "result": {"output": "v"}}

    async def fake_call(rpc_url, context_id, method, args, node_name=None):
        return {"success": True, "data": envelope}

    async def run():
        with (
            patch.object(
                step, "_resolve_node_for_client", return_value=("http://x", "n")
            ),
            patch(
                "merobox.commands.bootstrap.steps.wait_for_sync.call_function",
                side_effect=fake_call,
            ),
        ):
            return await step._check_expect_convergence(EXPECT, "ctx-1", NODES)

    satisfied, observed = asyncio.run(run())
    assert satisfied is True, f"envelope was not unwrapped: {observed}"
    assert observed == {node: {"output": "v"} for node in NODES}


def test_expect_read_marks_a_failed_call_unread_not_null():
    """`equals: null` must not be satisfiable by a node that cannot be read."""
    from merobox.commands.bootstrap.steps.wait_for_sync import _EXPECT_UNREAD

    expect = {"method": "get", "args": {}, "equals": None}
    step = _make_step({"expect": expect})

    async def fake_call(rpc_url, context_id, method, args, node_name=None):
        return {"success": False, "error": "boom"}

    async def run():
        with (
            patch.object(
                step, "_resolve_node_for_client", return_value=("http://x", "n")
            ),
            patch(
                "merobox.commands.bootstrap.steps.wait_for_sync.call_function",
                side_effect=fake_call,
            ),
        ):
            return await step._check_expect_convergence(expect, "ctx-1", NODES)

    satisfied, observed = asyncio.run(run())
    assert satisfied is False
    assert all(v is _EXPECT_UNREAD for v in observed.values())
