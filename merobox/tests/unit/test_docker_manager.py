import signal
import threading
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
def test_docker_manager_cleanup_prevents_double_cleanup(mock_docker):
    """Test that _cleanup_resources prevents double cleanup races."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    # Add mock containers to track
    mock_container1 = MagicMock()
    manager.nodes = {"node1": mock_container1}
    manager.node_rpc_ports = {"node1": 2528}

    # First cleanup should work and return True
    result = manager._cleanup_resources()
    assert result is True
    assert mock_container1.stop.call_count == 1
    assert manager._cleanup_done is True

    # Second cleanup should be skipped and return False
    mock_container2 = MagicMock()
    manager.nodes = {"node2": mock_container2}
    result = manager._cleanup_resources()
    assert result is False

    # Container2 should not have been stopped
    assert mock_container2.stop.call_count == 0


@patch("docker.from_env")
def test_docker_manager_cleanup_concurrent_access(mock_docker):
    """Test that _cleanup_resources handles concurrent access correctly.

    Spawns multiple threads calling _cleanup_resources simultaneously to verify
    the lock ensures at-most-once execution under concurrent access.
    Also verifies return value distribution: exactly one True, rest False.
    """
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    # Add a mock container
    mock_container = MagicMock()
    manager.nodes = {"node1": mock_container}
    manager.node_rpc_ports = {"node1": 2528}

    # Collect return values from each thread
    results = []
    results_lock = threading.Lock()

    # Spawn multiple threads calling cleanup concurrently
    threads = []
    num_threads = 10
    barrier = threading.Barrier(num_threads)

    def thread_func():
        barrier.wait()  # Synchronize all threads to start at once
        result = manager._cleanup_resources()
        with results_lock:
            results.append(result)

    for _ in range(num_threads):
        t = threading.Thread(target=thread_func)
        threads.append(t)
        t.start()

    # Wait for all threads to complete
    for t in threads:
        t.join()

    # Verify cleanup was only performed once despite concurrent access
    assert mock_container.stop.call_count == 1
    assert mock_container.remove.call_count == 1

    # Verify return value distribution: exactly one True, rest False
    true_count = sum(1 for r in results if r is True)
    false_count = sum(1 for r in results if r is False)
    assert true_count == 1, f"Expected exactly 1 True, got {true_count}"
    assert (
        false_count == num_threads - 1
    ), f"Expected {num_threads - 1} False, got {false_count}"


@patch("docker.from_env")
def test_docker_manager_cleanup_in_progress_returns_none(mock_docker):
    """Test that re-entrant cleanup call returns None when cleanup is in progress.

    This simulates a signal handler calling cleanup while atexit cleanup is running.
    With RLock, the same thread can re-enter, and should get None indicating
    cleanup is already in progress.
    """
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    # Manually set cleanup in progress to simulate mid-cleanup state
    manager._cleanup_in_progress = True

    # Calling cleanup while in progress should return None
    result = manager._cleanup_resources()
    assert result is None

    # Reset and verify normal cleanup works
    manager._cleanup_in_progress = False
    mock_container = MagicMock()
    manager.nodes = {"node1": mock_container}

    result = manager._cleanup_resources()
    assert result is True
    assert mock_container.stop.call_count == 1
