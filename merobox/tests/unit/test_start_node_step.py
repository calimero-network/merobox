import asyncio
from unittest.mock import AsyncMock, MagicMock

from merobox.commands.bootstrap.steps.start_node import StartNodeStep


def _make_step(
    config_extra=None, *, manager=None, start_node_fn=None, workflow_config=None
):
    config = {"type": "start_node", "nodes": ["node-1"], "wait_for_ready": False}
    if config_extra:
        config.update(config_extra)
    return StartNodeStep(
        config,
        manager=manager if manager is not None else MagicMock(),
        workflow_config=workflow_config or {"nodes": {"count": 1}},
        start_node_fn=start_node_fn,
    )


def test_start_node_step_starts_nodes_via_callable():
    start_fn = AsyncMock(return_value=True)
    step = _make_step({"nodes": ["node-1", "node-2"]}, start_node_fn=start_fn)

    assert asyncio.run(step.execute({}, {})) is True
    assert start_fn.await_count == 2


def test_start_node_step_fails_without_start_fn():
    step = _make_step(start_node_fn=None)

    assert asyncio.run(step.execute({}, {})) is False


def test_start_node_step_fails_when_a_node_does_not_start():
    start_fn = AsyncMock(side_effect=[True, False])
    step = _make_step({"nodes": ["node-1", "node-2"]}, start_node_fn=start_fn)

    assert asyncio.run(step.execute({}, {})) is False


def test_start_node_step_skips_readiness_check_when_ports_unknown():
    start_fn = AsyncMock(return_value=True)
    # Manager exposes neither get_node_rpc_port nor node_rpc_ports.
    manager = MagicMock(spec=[])
    step = _make_step(
        {"wait_for_ready": True, "wait_timeout": 1},
        manager=manager,
        start_node_fn=start_fn,
    )

    assert asyncio.run(step.execute({}, {})) is True
