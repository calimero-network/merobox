"""Lifecycle behaviour of WorkflowExecutor.execute_workflow().

Focused on merobox#227: a workflow that leaves nodes running (``stop_all_nodes:
false``, also the default) must suppress the manager's atexit teardown so the
nodes survive ``merobox bootstrap run`` exiting.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from merobox.commands.bootstrap.run.executor import WorkflowExecutor


def _make_executor(config, manager=...):
    if manager is ...:
        manager = MagicMock()
        manager.binary_path = None  # exercise the Docker-mode path
    return WorkflowExecutor(config, manager), manager


def _run_with_stubs(executor):
    """Run execute_workflow() with all node-management I/O stubbed out."""
    with (
        patch.object(executor, "_setup_resolver"),
        patch.object(executor, "_has_local_nodes", return_value=True),
        patch.object(executor, "_has_remote_nodes", return_value=False),
        patch.object(executor, "_start_nodes", new=AsyncMock(return_value=True)),
        patch.object(
            executor, "_wait_for_nodes_ready", new=AsyncMock(return_value=True)
        ),
        patch.object(
            executor, "_execute_workflow_steps", new=AsyncMock(return_value=True)
        ),
    ):
        return asyncio.run(executor.execute_workflow())


def test_keep_resources_on_exit_called_when_nodes_left_running():
    executor, manager = _make_executor(
        {"name": "wf", "nodes": {"count": 1}, "stop_all_nodes": False}
    )

    assert _run_with_stubs(executor) is True
    manager.keep_resources_on_exit.assert_called_once()


def test_keep_resources_on_exit_called_by_default():
    # `stop_all_nodes` omitted -> defaults to leaving nodes running.
    executor, manager = _make_executor({"name": "wf", "nodes": {"count": 1}})

    assert _run_with_stubs(executor) is True
    manager.keep_resources_on_exit.assert_called_once()


def test_keep_resources_on_exit_not_called_when_stopping_nodes():
    executor, manager = _make_executor(
        {"name": "wf", "nodes": {"count": 1}, "stop_all_nodes": True}
    )

    assert _run_with_stubs(executor) is True
    manager.keep_resources_on_exit.assert_not_called()


def test_remote_only_workflow_has_no_manager_to_keep():
    # manager is None for remote-only runs; execute_workflow must not blow up.
    executor, _ = _make_executor({"name": "wf", "remote_nodes": {}}, manager=None)
    with (
        patch.object(executor, "_setup_resolver"),
        patch.object(executor, "_has_local_nodes", return_value=False),
        patch.object(executor, "_has_remote_nodes", return_value=True),
        patch.object(
            executor, "_execute_workflow_steps", new=AsyncMock(return_value=True)
        ),
    ):
        assert asyncio.run(executor.execute_workflow()) is True
