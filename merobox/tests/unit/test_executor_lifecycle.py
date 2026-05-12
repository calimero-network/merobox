"""Lifecycle behaviour of WorkflowExecutor.execute_workflow().

Focused on merobox#227: a workflow that leaves nodes running (``stop_all_nodes:
false``, also the default) must suppress the manager's atexit teardown so the
nodes survive ``merobox bootstrap run`` exiting.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from merobox.commands.bootstrap.run.executor import WorkflowExecutor

# Sentinel: "build a default MagicMock manager" — distinct from manager=None,
# which is a meaningful value (remote-only runs have no manager).
_AUTO = object()


def _make_executor(config, manager=_AUTO):
    if manager is _AUTO:
        manager = MagicMock()
        manager.binary_path = None  # exercise the Docker-mode path
    return WorkflowExecutor(config, manager), manager


def _stub_node_io(executor, *, has_local_nodes=True, steps_ok=True):
    """Stub out all node-management I/O so execute_workflow() runs offline."""
    stack = ExitStack()
    stack.enter_context(patch.object(executor, "_setup_resolver"))
    stack.enter_context(
        patch.object(executor, "_has_local_nodes", return_value=has_local_nodes)
    )
    stack.enter_context(
        patch.object(executor, "_has_remote_nodes", return_value=not has_local_nodes)
    )
    stack.enter_context(
        patch.object(
            executor, "_execute_workflow_steps", new=AsyncMock(return_value=steps_ok)
        )
    )
    if has_local_nodes:
        stack.enter_context(
            patch.object(executor, "_start_nodes", new=AsyncMock(return_value=True))
        )
        stack.enter_context(
            patch.object(
                executor, "_wait_for_nodes_ready", new=AsyncMock(return_value=True)
            )
        )
    return stack


@pytest.mark.asyncio
async def test_keep_resources_on_exit_called_when_nodes_left_running():
    executor, manager = _make_executor(
        {"name": "wf", "nodes": {"count": 1}, "stop_all_nodes": False}
    )

    with _stub_node_io(executor):
        assert await executor.execute_workflow() is True

    manager.keep_resources_on_exit.assert_called_once()
    manager.stop_all_nodes.assert_not_called()


@pytest.mark.asyncio
async def test_keep_resources_on_exit_called_by_default():
    # `stop_all_nodes` omitted -> defaults to leaving nodes running.
    executor, manager = _make_executor({"name": "wf", "nodes": {"count": 1}})

    with _stub_node_io(executor):
        assert await executor.execute_workflow() is True

    manager.keep_resources_on_exit.assert_called_once()


@pytest.mark.asyncio
async def test_stop_all_nodes_true_keeps_atexit_and_stops_nodes_at_step5():
    executor, manager = _make_executor(
        {"name": "wf", "nodes": {"count": 1}, "stop_all_nodes": True}
    )

    with _stub_node_io(executor):
        assert await executor.execute_workflow() is True

    manager.keep_resources_on_exit.assert_not_called()
    # Step 5 explicitly tears the nodes down when stop_all_nodes is true.
    manager.stop_all_nodes.assert_called_once()


@pytest.mark.asyncio
async def test_keep_resources_on_exit_persists_when_workflow_fails():
    # A failed workflow with `stop_all_nodes: false` still leaves the nodes
    # running (useful for inspecting what went wrong) — consistent with
    # _stop_nodes_on_failure() already being gated on `stop_all_nodes`. The
    # atexit suppression is intentionally *not* reverted on failure.
    executor, manager = _make_executor(
        {"name": "wf", "nodes": {"count": 1}, "stop_all_nodes": False}
    )

    with _stub_node_io(executor, steps_ok=False):
        with patch.object(executor, "_stop_nodes_on_failure") as stop_on_failure:
            assert await executor.execute_workflow() is False

    manager.keep_resources_on_exit.assert_called_once()
    stop_on_failure.assert_not_called()
    manager.stop_all_nodes.assert_not_called()


@pytest.mark.asyncio
async def test_remote_only_workflow_has_no_manager_to_keep():
    # manager is None for remote-only runs; execute_workflow must not blow up.
    executor, _ = _make_executor({"name": "wf", "remote_nodes": {}}, manager=None)

    with _stub_node_io(executor, has_local_nodes=False):
        assert await executor.execute_workflow() is True
