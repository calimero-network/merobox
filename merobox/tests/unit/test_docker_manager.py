from unittest.mock import MagicMock, patch

from merobox.commands.manager import DockerManager


@patch("merobox.commands.managers.node.apply_near_devnet_config_to_file")
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

    # Mock internal checks - now need to mock on the node manager
    manager._node_manager._ensure_image_pulled = MagicMock(return_value=True)

    # Run the node
    manager.run_node("node1", near_devnet_config=near_config)

    # Verify the `apply_near_devnet_config_to_file` utility function was called
    mock_apply_config.assert_called_once()
    args, _ = mock_apply_config.call_args

    # Check args
    assert str(args[0]).endswith("config.toml")
    assert args[1] == "node1"


@patch("docker.from_env")
def test_docker_manager_has_node_manager(mock_docker):
    """Test that DockerManager properly initializes NodeManager."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager()

    # Verify sub-managers are accessible
    assert manager._node_manager is not None
    assert manager.network_manager is not None
    assert manager.mock_relayer_manager is not None
    assert manager.auth_service_manager is not None


@patch("docker.from_env")
def test_docker_manager_backward_compatibility(mock_docker):
    """Test that DockerManager maintains backward compatible properties."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager()

    # Verify backward compatible properties
    assert manager.client is not None
    assert isinstance(manager.nodes, dict)
    assert isinstance(manager.node_rpc_ports, dict)
    assert manager.mock_relayer_url is None  # Initially None
