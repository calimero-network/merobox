import asyncio
from unittest.mock import AsyncMock, MagicMock

from merobox.commands.bootstrap.steps.start_node import StartNodeStep


def _make_step(config_extra=None, *, manager=None, executor=None, workflow_config=None):
    config = {"type": "start_node", "nodes": ["node-1"], "wait_for_ready": False}
    if config_extra:
        config.update(config_extra)
    return StartNodeStep(
        config,
        manager=manager if manager is not None else MagicMock(),
        workflow_config=workflow_config or {"nodes": {"count": 1}},
        executor=executor,
    )


def test_start_node_step_starts_nodes_via_executor():
    executor = MagicMock()
    executor._start_single_node = AsyncMock(return_value=True)
    step = _make_step({"nodes": ["node-1", "node-2"]}, executor=executor)

    assert asyncio.run(step.execute({}, {})) is True
    assert executor._start_single_node.await_count == 2


def test_start_node_step_fails_without_executor():
    step = _make_step(executor=None)

    assert asyncio.run(step.execute({}, {})) is False


def test_start_node_step_fails_when_a_node_does_not_start():
    executor = MagicMock()
    executor._start_single_node = AsyncMock(side_effect=[True, False])
    step = _make_step({"nodes": ["node-1", "node-2"]}, executor=executor)

    assert asyncio.run(step.execute({}, {})) is False


def test_start_node_step_skips_readiness_check_when_ports_unknown():
    executor = MagicMock()
    executor._start_single_node = AsyncMock(return_value=True)
    # Manager exposes neither get_node_rpc_port nor node_rpc_ports.
    manager = MagicMock(spec=[])
    step = _make_step(
        {"wait_for_ready": True, "wait_timeout": 1},
        manager=manager,
        executor=executor,
    )

    assert asyncio.run(step.execute({}, {})) is True
