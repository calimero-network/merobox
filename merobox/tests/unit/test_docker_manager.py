from unittest.mock import MagicMock, patch

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
def test_container_not_privileged(mock_docker):
    """Test that containers are not started with privileged mode (security).

    Privileged mode grants full host access and should not be used.
    Docker's default capabilities are sufficient for merod operations.
    """
    import docker

    # Setup
    client = MagicMock()
    mock_docker.return_value = client

    # Mock the container that gets returned
    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.short_id = "abc123"
    mock_container.attrs = {
        "NetworkSettings": {"Ports": {"2528/tcp": [{"HostPort": "2528"}]}}
    }
    client.containers.run.return_value = mock_container
    # Use docker.errors.NotFound to simulate container not existing
    client.containers.get.side_effect = docker.errors.NotFound("not found")

    manager = DockerManager()
    manager._ensure_image_pulled = MagicMock(return_value=True)

    # Run the node
    manager.run_node("test-node")

    # Verify containers.run was called (at least for init and run containers)
    assert client.containers.run.called

    # Check all calls to containers.run
    for call in client.containers.run.call_args_list:
        _, kwargs = call
        # Ensure privileged is not set to True
        assert (
            kwargs.get("privileged") is not True
        ), "Container should not be started with privileged=True"
