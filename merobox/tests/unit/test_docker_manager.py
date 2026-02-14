from unittest.mock import MagicMock, patch

import docker

from merobox.commands.manager import DockerManager


@patch("docker.from_env")
def test_docker_container_uses_cap_add_not_privileged(mock_docker):
    """Test that containers use specific capabilities instead of privileged mode."""
    # Setup
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    # Mock internal checks
    manager._node_manager._ensure_image_pulled = MagicMock(return_value=True)

    # Mock container run to capture the config
    container_configs = []

    def capture_run_config(**kwargs):
        container_configs.append(kwargs)
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.short_id = "abc123"
        mock_container.attrs = {
            "NetworkSettings": {"Ports": {}},
            "Config": {"Env": []},
        }
        return mock_container

    client.containers.run.side_effect = capture_run_config
    client.containers.get.side_effect = docker.errors.NotFound("Not found")

    # Run the node
    manager.run_node("test-node")

    # Find the main container config (not init container)
    # Init container has detach=False, main container has detach=True
    main_configs = [c for c in container_configs if c.get("detach") is True]
    assert len(main_configs) >= 1, "Expected at least one main container config"

    main_config = main_configs[0]

    # Verify privileged mode is NOT used
    assert (
        "privileged" not in main_config
    ), "Container should not use privileged mode for security reasons"

    # Verify cap_add is used with appropriate capabilities
    assert (
        "cap_add" in main_config
    ), "Container should use cap_add for specific capabilities"

    expected_caps = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"]
    for cap in expected_caps:
        assert (
            cap in main_config["cap_add"]
        ), f"Expected capability {cap} in cap_add list"


@patch("merobox.commands.managers.node.apply_near_devnet_config_to_file")
@patch("docker.from_env")
def test_docker_run_node_calls_shared_config(mock_docker, mock_apply_config):
    # Setup
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

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

    manager = DockerManager(enable_signal_handlers=False)

    # Verify sub-managers are accessible
    assert manager._node_manager is not None
    assert manager.network_manager is not None
    assert manager.auth_service_manager is not None


@patch("docker.from_env")
def test_docker_manager_backward_compatibility(mock_docker):
    """Test that DockerManager maintains backward compatible properties."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    # Verify backward compatible properties
    assert manager.client is not None
    assert isinstance(manager.nodes, dict)
    assert isinstance(manager.node_rpc_ports, dict)


@patch("docker.from_env")
def test_docker_manager_signal_handlers_disabled(mock_docker):
    """Test that signal handlers can be disabled."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    # Verify signal handler state
    assert manager._original_sigint_handler is None
    assert manager._original_sigterm_handler is None
