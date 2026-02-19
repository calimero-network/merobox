import asyncio
from unittest.mock import MagicMock

import docker

from merobox.commands.bootstrap.steps.stop_node import StopNodeStep


def test_stop_node_step_treats_confirmed_stopped_node_as_success():
    manager = MagicMock()
    manager.stop_node.return_value = False
    manager.is_node_running.return_value = False

    step = StopNodeStep({"type": "stop_node", "nodes": ["node-1"]}, manager=manager)
    result = asyncio.run(step.execute({}, {}))

    assert result is True
    manager.stop_node.assert_called_once_with("node-1")
    manager.is_node_running.assert_called_once_with("node-1")


def test_stop_node_step_fails_when_status_check_is_unknown():
    manager = MagicMock()
    manager.stop_node.return_value = False
    manager.is_node_running.side_effect = docker.errors.APIError("permission denied")

    step = StopNodeStep({"type": "stop_node", "nodes": ["node-1"]}, manager=manager)
    result = asyncio.run(step.execute({}, {}))

    assert result is False
    manager.stop_node.assert_called_once_with("node-1")
    manager.is_node_running.assert_called_once_with("node-1")


def test_stop_node_step_does_not_check_status_after_successful_stop():
    manager = MagicMock()
    manager.stop_node.return_value = True

    step = StopNodeStep({"type": "stop_node", "nodes": ["node-1"]}, manager=manager)
    result = asyncio.run(step.execute({}, {}))

    assert result is True
    manager.stop_node.assert_called_once_with("node-1")
    manager.is_node_running.assert_not_called()
