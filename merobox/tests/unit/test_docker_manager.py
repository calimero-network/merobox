import signal
from unittest.mock import MagicMock, patch

import docker

from merobox.commands.manager import DockerManager


@patch("docker.from_env")
def test_docker_container_uses_cap_add_not_privileged(mock_docker):
    """Test that containers use specific capabilities instead of privileged mode."""
    # Setup
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager()

    # Mock internal checks
    manager._ensure_image_pulled = MagicMock(return_value=True)

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


@patch("merobox.commands.manager.apply_near_devnet_config_to_file")
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
def test_docker_manager_signal_handlers_registered(mock_docker):
    """Test that signal handlers are registered when enabled."""
    client = MagicMock()
    mock_docker.return_value = client

    # Create manager with signal handlers enabled
    manager = DockerManager(enable_signal_handlers=True)

    # Verify handlers were stored
    assert manager._original_sigint_handler is not None
    assert manager._original_sigterm_handler is not None

    # Cleanup - restore original handlers
    manager.remove_signal_handlers()


@patch("docker.from_env")
def test_docker_manager_signal_handlers_disabled(mock_docker):
    """Test that signal handlers are not registered when disabled."""
    client = MagicMock()
    mock_docker.return_value = client

    # Create manager with signal handlers disabled
    manager = DockerManager(enable_signal_handlers=False)

    # Verify handlers were not stored (None indicates not set up)
    assert manager._original_sigint_handler is None
    assert manager._original_sigterm_handler is None


@patch("docker.from_env")
def test_docker_manager_cleanup_resources(mock_docker):
    """Test that _cleanup_resources stops all managed containers."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    # Add mock containers to track
    mock_container1 = MagicMock()
    mock_container2 = MagicMock()
    manager.nodes = {"node1": mock_container1, "node2": mock_container2}
    manager.node_rpc_ports = {"node1": 2528, "node2": 2529}

    # Call cleanup
    manager._cleanup_resources()

    # Verify containers were stopped and removed
    mock_container1.stop.assert_called_once_with(timeout=10)
    mock_container1.remove.assert_called_once()
    mock_container2.stop.assert_called_once_with(timeout=10)
    mock_container2.remove.assert_called_once()

    # Verify tracking dicts were cleared
    assert len(manager.nodes) == 0
    assert len(manager.node_rpc_ports) == 0


@patch("docker.from_env")
def test_docker_manager_remove_signal_handlers(mock_docker):
    """Test that signal handlers can be removed and restored."""
    client = MagicMock()
    mock_docker.return_value = client

    # Store original handlers before test
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    # Create manager with signal handlers enabled
    manager = DockerManager(enable_signal_handlers=True)

    # Handlers should be different now
    current_sigint = signal.getsignal(signal.SIGINT)
    assert current_sigint == manager._signal_handler

    # Remove handlers
    manager.remove_signal_handlers()

    # Verify handlers were restored
    assert signal.getsignal(signal.SIGINT) == original_sigint
    assert signal.getsignal(signal.SIGTERM) == original_sigterm


@patch("docker.from_env")
def test_graceful_stop_container_sends_sigterm(mock_docker):
    """Test that _graceful_stop_container sends SIGTERM before stopping."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    mock_container = MagicMock()
    mock_container.name = "test-node"

    # Call graceful stop with minimal drain timeout for faster test
    result = manager._graceful_stop_container(
        mock_container, "test-node", drain_timeout=0, stop_timeout=10
    )

    # Verify SIGTERM was sent
    mock_container.kill.assert_called_once_with(signal="SIGTERM")
    # Verify container was stopped and removed
    mock_container.stop.assert_called_once_with(timeout=10)
    mock_container.remove.assert_called_once()
    assert result is True


@patch("docker.from_env")
def test_graceful_stop_container_handles_sigterm_failure(mock_docker):
    """Test that graceful stop continues even if SIGTERM fails."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    mock_container = MagicMock()
    mock_container.name = "test-node"
    # Simulate SIGTERM failure (container already stopped)
    mock_container.kill.side_effect = docker.errors.APIError("Container not running")

    result = manager._graceful_stop_container(
        mock_container, "test-node", drain_timeout=0, stop_timeout=10
    )

    # Should still try to stop and remove
    mock_container.stop.assert_called_once_with(timeout=10)
    mock_container.remove.assert_called_once()
    assert result is True


@patch("docker.from_env")
def test_stop_node_uses_graceful_shutdown(mock_docker):
    """Test that stop_node uses graceful shutdown with connection draining."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    mock_container = MagicMock()
    manager.nodes = {"test-node": mock_container}
    manager.node_rpc_ports = {"test-node": 2528}

    # Call stop_node with minimal drain timeout
    result = manager.stop_node("test-node", drain_timeout=0, stop_timeout=10)

    # Verify graceful shutdown sequence
    mock_container.kill.assert_called_once_with(signal="SIGTERM")
    mock_container.stop.assert_called_once_with(timeout=10)
    mock_container.remove.assert_called_once()

    # Verify cleanup
    assert "test-node" not in manager.nodes
    assert "test-node" not in manager.node_rpc_ports
    assert result is True


@patch("docker.from_env")
def test_stop_all_nodes_uses_batch_graceful_shutdown(mock_docker):
    """Test that stop_all_nodes uses batch graceful shutdown (O(timeout) not O(n*timeout))."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    mock_container1 = MagicMock()
    mock_container1.name = "node1"
    mock_container2 = MagicMock()
    mock_container2.name = "node2"

    client.containers.list.return_value = [mock_container1, mock_container2]
    manager.nodes = {"node1": mock_container1, "node2": mock_container2}
    manager.node_rpc_ports = {"node1": 2528, "node2": 2529}

    # Call stop_all_nodes with minimal drain timeout
    result = manager.stop_all_nodes(drain_timeout=0, stop_timeout=10)

    # Verify both containers received SIGTERM (batch signal phase)
    mock_container1.kill.assert_called_once_with(signal="SIGTERM")
    mock_container2.kill.assert_called_once_with(signal="SIGTERM")

    # Verify containers were stopped and removed
    mock_container1.stop.assert_called_once_with(timeout=10)
    mock_container1.remove.assert_called_once()
    mock_container2.stop.assert_called_once_with(timeout=10)
    mock_container2.remove.assert_called_once()

    assert result is True


@patch("docker.from_env")
def test_cleanup_resources_uses_batch_graceful_shutdown(mock_docker):
    """Test that _cleanup_resources uses batch graceful shutdown."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    mock_container1 = MagicMock()
    mock_container2 = MagicMock()
    manager.nodes = {"node1": mock_container1, "node2": mock_container2}
    manager.node_rpc_ports = {"node1": 2528, "node2": 2529}

    # Call cleanup with minimal drain timeout
    manager._cleanup_resources(drain_timeout=0, stop_timeout=10)

    # Verify SIGTERM was sent to both containers (batch signal phase)
    mock_container1.kill.assert_called_once_with(signal="SIGTERM")
    mock_container2.kill.assert_called_once_with(signal="SIGTERM")

    # Verify containers were stopped and removed
    mock_container1.stop.assert_called_once_with(timeout=10)
    mock_container1.remove.assert_called_once()
    mock_container2.stop.assert_called_once_with(timeout=10)
    mock_container2.remove.assert_called_once()

    # Verify tracking dicts were cleared
    assert len(manager.nodes) == 0
    assert len(manager.node_rpc_ports) == 0


@patch("docker.from_env")
def test_graceful_stop_containers_batch_sends_sigterm_to_all_first(mock_docker):
    """Test that batch shutdown sends SIGTERM to all containers before sleeping."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    mock_container1 = MagicMock()
    mock_container2 = MagicMock()

    containers = [("node1", mock_container1), ("node2", mock_container2)]

    # Call batch graceful stop with minimal drain timeout
    success_count, failed = manager._graceful_stop_containers_batch(
        containers, drain_timeout=0, stop_timeout=10
    )

    # Verify SIGTERM was sent to both containers
    mock_container1.kill.assert_called_once_with(signal="SIGTERM")
    mock_container2.kill.assert_called_once_with(signal="SIGTERM")

    # Verify containers were stopped and removed
    mock_container1.stop.assert_called_once_with(timeout=10)
    mock_container1.remove.assert_called_once()
    mock_container2.stop.assert_called_once_with(timeout=10)
    mock_container2.remove.assert_called_once()

    assert success_count == 2
    assert failed == []


@patch("docker.from_env")
def test_cors_uses_explicit_origins_not_wildcard(mock_docker):
    """Test that CORS configuration uses explicit origins instead of wildcard."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    # Mock internal checks
    manager._ensure_image_pulled = MagicMock(return_value=True)
    manager._start_auth_service_stack = MagicMock(return_value=True)
    manager._ensure_auth_networks = MagicMock()

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
    client.networks.get.return_value = MagicMock()

    # Run the node with auth_service enabled
    manager.run_node("test-node", auth_service=True)

    # Find the main container config (has detach=True)
    main_configs = [c for c in container_configs if c.get("detach") is True]
    assert len(main_configs) >= 1, "Expected at least one main container config"

    main_config = main_configs[0]

    # Verify labels exist and CORS origins are not wildcard
    assert "labels" in main_config
    labels = main_config["labels"]

    # Check the main CORS middleware does NOT use wildcard
    cors_origin_key = (
        "traefik.http.middlewares.cors.headers.accesscontrolalloworiginlist"
    )
    assert cors_origin_key in labels
    assert labels[cors_origin_key] != "*", "CORS should not allow wildcard origin"

    # Verify default localhost origins are included
    cors_origins = labels[cors_origin_key]
    assert "http://localhost" in cors_origins
    assert "http://127.0.0.1" in cors_origins


@patch("docker.from_env")
def test_cors_custom_origins_are_used(mock_docker):
    """Test that custom CORS origins can be specified."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    # Mock internal checks
    manager._ensure_image_pulled = MagicMock(return_value=True)
    manager._start_auth_service_stack = MagicMock(return_value=True)
    manager._ensure_auth_networks = MagicMock()

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
    client.networks.get.return_value = MagicMock()

    # Custom CORS origins
    custom_origins = ["https://example.com", "https://myapp.example.com"]

    # Run the node with auth_service enabled and custom origins
    manager.run_node(
        "test-node", auth_service=True, cors_allowed_origins=custom_origins
    )

    # Find the main container config (has detach=True)
    main_configs = [c for c in container_configs if c.get("detach") is True]
    assert len(main_configs) >= 1, "Expected at least one main container config"

    main_config = main_configs[0]

    # Verify custom origins are used
    labels = main_config["labels"]
    cors_origin_key = (
        "traefik.http.middlewares.cors.headers.accesscontrolalloworiginlist"
    )
    assert cors_origin_key in labels
    cors_origins = labels[cors_origin_key]

    # Custom origins should be present
    assert "https://example.com" in cors_origins
    assert "https://myapp.example.com" in cors_origins

    # Default localhost origins should NOT be present when custom ones are specified
    assert "http://localhost:3000" not in cors_origins
