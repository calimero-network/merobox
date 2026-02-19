from unittest.mock import MagicMock, patch

import docker
import pytest

from merobox.commands.manager import DockerManager


@patch("merobox.commands.manager.apply_near_devnet_config_to_file")
@patch("docker.from_env")
def test_docker_run_node_calls_shared_config(mock_docker, mock_apply_config):
    # Setup
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager()

    near_config = {
        "rpc_url": "http://127.0.0.1:3030",
        "contract_id": "test.near",
        "account_id": "node.near",
        "public_key": "pk",
        "secret_key": "sk",
    }

    # Mock internal checks
    manager._ensure_image_pulled = MagicMock(return_value=True)

    # Run the node
    manager.run_node("node1", near_devnet_config=near_config)

    # Verify the `apply_near_devnet_config_to_file` utility function was called
    mock_apply_config.assert_called_once()
    args, _ = mock_apply_config.call_args

    # Check args
    assert str(args[0]).endswith("config.toml")
    assert args[1] == "node1"


@patch("docker.from_env")
def test_is_node_running_returns_true_for_running_container(mock_docker):
    client = MagicMock()
    running_container = MagicMock()
    running_container.status = "running"
    client.containers.get.return_value = running_container
    mock_docker.return_value = client

    manager = DockerManager()

    assert manager.is_node_running("node1") is True
    client.containers.get.assert_called_once_with("node1")


@patch("docker.from_env")
def test_is_node_running_returns_false_when_container_missing(mock_docker):
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("missing")
    mock_docker.return_value = client

    manager = DockerManager()

    assert manager.is_node_running("node1") is False
    client.containers.get.assert_called_once_with("node1")


@patch("docker.from_env")
def test_is_node_running_raises_api_error(mock_docker):
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.APIError("denied")
    mock_docker.return_value = client

    manager = DockerManager()

    with pytest.raises(docker.errors.APIError):
        manager.is_node_running("node1")
