import signal
import threading
from unittest.mock import MagicMock, patch

import docker
import pytest

from merobox.commands.constants import CleanupResult
from merobox.commands.manager import (
    CORS_ALLOWED_HEADERS,
    DEFAULT_CORS_ORIGINS,
    DockerManager,
    _get_node_hostname,
    _validate_cors_origins,
)

# Realistic-length base58btc libp2p peer IDs for the cluster-bootstrap tests
# (the production code validates the peer-ID format before using it).
_PID = {n: "12D3KooW" + chr(ord("A") + n) * 44 for n in range(1, 5)}
_IP = {n: f"172.20.0.{n + 1}" for n in range(1, 5)}


def _mock_cluster_container(ip, network="merobox-cluster", status="running"):
    """A MagicMock container that reports `ip` on `network` (for IP discovery)."""
    c = MagicMock()
    c.status = status
    c.attrs = {"NetworkSettings": {"Networks": {network: {"IPAddress": ip}}}}
    return c


def _capture_run_config_factory(container_configs):
    """Build a `client.containers.run` side effect that records kwargs."""

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

    return capture_run_config


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

    # File-permission caps (bind mount handling) + PERFMON (required for
    # `perf record` / sys_perf_event_open inside the profiling image).
    expected_caps = [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
        "PERFMON",
    ]
    for cap in expected_caps:
        assert (
            cap in main_config["cap_add"]
        ), f"Expected capability {cap} in cap_add list"


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

    # First cleanup should work and return PERFORMED
    result = manager._cleanup_resources()
    assert result == CleanupResult.PERFORMED
    assert mock_container1.stop.call_count == 1
    assert manager._cleanup_done is True

    # Second cleanup should be skipped and return ALREADY_DONE
    mock_container2 = MagicMock()
    manager.nodes = {"node2": mock_container2}
    result = manager._cleanup_resources()
    assert result == CleanupResult.ALREADY_DONE

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

    # Verify return value distribution: exactly one PERFORMED, rest ALREADY_DONE
    performed_count = sum(1 for r in results if r == CleanupResult.PERFORMED)
    done_count = sum(1 for r in results if r == CleanupResult.ALREADY_DONE)
    in_progress_count = sum(1 for r in results if r == CleanupResult.IN_PROGRESS)
    assert performed_count == 1, f"Expected exactly 1 PERFORMED, got {performed_count}"
    assert (
        done_count == num_threads - 1
    ), f"Expected {num_threads - 1} ALREADY_DONE, got {done_count}"
    assert in_progress_count == 0, f"Expected 0 IN_PROGRESS, got {in_progress_count}"


@patch("docker.from_env")
def test_docker_manager_cleanup_returns_in_progress_when_flag_set(mock_docker):
    """Test that cleanup returns IN_PROGRESS when _cleanup_in_progress flag is set.

    This tests the flag check logic specifically by manually setting the flag.
    In production, this would occur when a signal handler calls cleanup while
    atexit cleanup is already running in the same thread (RLock allows re-entry).
    """
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    # Manually set cleanup in progress to simulate mid-cleanup state
    manager._cleanup_in_progress = True

    # Calling cleanup while in progress should return IN_PROGRESS
    result = manager._cleanup_resources()
    assert result == CleanupResult.IN_PROGRESS

    # Reset and verify normal cleanup works
    manager._cleanup_in_progress = False
    mock_container = MagicMock()
    manager.nodes = {"node1": mock_container}

    result = manager._cleanup_resources()
    assert result == CleanupResult.PERFORMED
    assert mock_container.stop.call_count == 1


# ============================================================================
# keep_resources_on_exit (merobox#227)
# ============================================================================


@patch("docker.from_env")
def test_cleanup_on_exit_tears_down_by_default(mock_docker):
    """By default the atexit handler stops every managed container."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)
    mock_container = MagicMock()
    manager.nodes = {"node1": mock_container}

    manager._cleanup_on_exit()

    assert mock_container.stop.call_count == 1
    assert manager.nodes == {}


@patch("docker.from_env")
def test_keep_resources_on_exit_skips_atexit_teardown(mock_docker):
    """keep_resources_on_exit() makes the atexit handler a no-op.

    Regression test for merobox#227: `stop_all_nodes: false` must actually
    leave the nodes running once `merobox bootstrap run` exits.
    """
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)
    mock_container = MagicMock()
    manager.nodes = {"node1": mock_container}

    manager.keep_resources_on_exit()
    manager._cleanup_on_exit()

    assert mock_container.stop.call_count == 0
    assert manager.nodes == {"node1": mock_container}

    # ...and flipping it back restores the default teardown.
    manager.keep_resources_on_exit(False)
    manager._cleanup_on_exit()
    assert mock_container.stop.call_count == 1
    assert manager.nodes == {}


@patch("docker.from_env")
def test_keep_resources_on_exit_does_not_block_signal_cleanup(mock_docker):
    """keep_resources_on_exit() only suppresses atexit, not SIGINT/SIGTERM."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)
    mock_container = MagicMock()
    manager.nodes = {"node1": mock_container}

    manager.keep_resources_on_exit()
    # An explicit cleanup (what the signal handler invokes) still runs.
    result = manager._cleanup_resources()

    assert result == CleanupResult.PERFORMED
    assert mock_container.stop.call_count == 1


# ============================================================================
# Graceful shutdown tests
# ============================================================================


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


# ============================================================================
# CORS configuration tests
# ============================================================================


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

    # Check the per-node CORS middleware does NOT use wildcard
    cors_origin_key = (
        "traefik.http.middlewares.cors-test-node.headers.accesscontrolalloworiginlist"
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

    # Verify custom origins are used (per-node middleware name)
    labels = main_config["labels"]
    cors_origin_key = (
        "traefik.http.middlewares.cors-test-node.headers.accesscontrolalloworiginlist"
    )
    assert cors_origin_key in labels
    cors_origins = labels[cors_origin_key]

    # Custom origins should be present
    assert "https://example.com" in cors_origins
    assert "https://myapp.example.com" in cors_origins

    # Default localhost origins should NOT be present when custom ones are specified
    assert "http://localhost:3000" not in cors_origins


@patch("docker.from_env")
def test_cors_uses_explicit_headers_not_wildcard(mock_docker):
    """Test that CORS uses explicit headers instead of wildcard for credentials."""
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
    labels = main_config["labels"]

    # Check the per-node CORS middleware uses explicit headers
    cors_headers_key = (
        "traefik.http.middlewares.cors-test-node.headers.accesscontrolallowheaders"
    )
    assert cors_headers_key in labels
    assert labels[cors_headers_key] != "*", "CORS headers should not be wildcard"
    assert labels[cors_headers_key] == CORS_ALLOWED_HEADERS


@patch("docker.from_env")
def test_cors_origins_propagated_to_auth_service(mock_docker):
    """Test that CORS origins are correctly propagated to auth service stack."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    # Mock internal checks
    manager._ensure_image_pulled = MagicMock(return_value=True)
    manager._ensure_auth_networks = MagicMock()

    # Don't mock _start_auth_service_stack - let it run to verify propagation
    # But mock the container operations it uses
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
    client.volumes.get.side_effect = docker.errors.NotFound("Not found")
    client.volumes.create.return_value = MagicMock()

    # Custom CORS origins
    custom_origins = ["https://example.com", "https://myapp.example.com"]

    # Run the node with auth_service enabled and custom origins
    manager.run_node(
        "test-node", auth_service=True, cors_allowed_origins=custom_origins
    )

    # Find the auth container config (name="auth")
    auth_configs = [c for c in container_configs if c.get("name") == "auth"]
    assert len(auth_configs) >= 1, "Expected auth container config"

    auth_config = auth_configs[0]
    labels = auth_config["labels"]

    # Verify auth container uses custom origins
    cors_origin_key = (
        "traefik.http.middlewares.cors-auth.headers.accesscontrolalloworiginlist"
    )
    assert cors_origin_key in labels
    cors_origins = labels[cors_origin_key]
    assert "https://example.com" in cors_origins
    assert "https://myapp.example.com" in cors_origins


def test_validate_cors_origins_rejects_wildcard():
    """Test that _validate_cors_origins rejects wildcard origin."""
    with pytest.raises(ValueError, match="Wildcard"):
        _validate_cors_origins(["http://localhost", "*"])


def test_validate_cors_origins_rejects_comma():
    """Test that _validate_cors_origins rejects origins with commas."""
    with pytest.raises(ValueError, match="comma"):
        _validate_cors_origins(["http://localhost,http://evil.com"])


def test_validate_cors_origins_valid():
    """Test that _validate_cors_origins accepts valid origins."""
    origins = ["http://localhost", "https://example.com"]
    result = _validate_cors_origins(origins)
    assert result == origins


def test_get_node_hostname():
    """Test hostname transformation from node name."""
    assert _get_node_hostname("calimero-node-1") == "node1"
    assert _get_node_hostname("calimero-foo-bar") == "foobar"
    assert _get_node_hostname("test-node") == "testnode"


def test_default_cors_origins_constant():
    """Test that DEFAULT_CORS_ORIGINS has expected values."""
    assert "http://localhost" in DEFAULT_CORS_ORIGINS
    assert "http://127.0.0.1" in DEFAULT_CORS_ORIGINS
    assert "http://localhost:3000" in DEFAULT_CORS_ORIGINS


# ============================================================================
# Cluster networking (#231): user-defined bridge + auto bootstrap peers + gate
# ============================================================================


@patch("docker.from_env")
def test_ensure_cluster_network_reuses_existing(mock_docker):
    """If the cluster bridge already exists, it is reused (not recreated)."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    client.networks.get.return_value = MagicMock()

    name = manager._ensure_cluster_network()

    assert name == DockerManager.CLUSTER_NETWORK_NAME
    client.networks.get.assert_called_once_with(DockerManager.CLUSTER_NETWORK_NAME)
    client.networks.create.assert_not_called()


@patch("docker.from_env")
def test_ensure_cluster_network_creates_when_missing(mock_docker):
    """If the cluster bridge is missing, it is created as a user-defined bridge."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    client.networks.get.side_effect = docker.errors.NotFound("nope")

    name = manager._ensure_cluster_network()

    assert name == DockerManager.CLUSTER_NETWORK_NAME
    client.networks.create.assert_called_once_with(
        name=DockerManager.CLUSTER_NETWORK_NAME, driver="bridge"
    )


@patch("docker.from_env")
def test_ensure_cluster_network_returns_none_on_failure(mock_docker):
    """If the bridge cannot be created, return None so callers can fall back."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    client.networks.get.side_effect = docker.errors.NotFound("nope")
    client.networks.create.side_effect = docker.errors.APIError("boom")

    assert manager._ensure_cluster_network() is None


@patch("docker.from_env")
def test_run_node_attaches_to_given_network(mock_docker):
    """run_node(network=...) attaches the run container to that network."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)
    manager._ensure_image_pulled = MagicMock(return_value=True)

    container_configs = []
    client.containers.run.side_effect = _capture_run_config_factory(container_configs)
    client.containers.get.side_effect = docker.errors.NotFound("Not found")

    manager.run_node("test-node", network="merobox-cluster")

    main_configs = [c for c in container_configs if c.get("detach") is True]
    assert main_configs and main_configs[0].get("network") == "merobox-cluster"


@patch("docker.from_env")
def test_run_node_auth_network_wins_over_cluster_network(mock_docker):
    """When auth is enabled, the auth web network takes precedence over `network`."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)
    manager._ensure_image_pulled = MagicMock(return_value=True)
    manager._start_auth_service_stack = MagicMock(return_value=True)
    manager._ensure_auth_networks = MagicMock()

    container_configs = []
    client.containers.run.side_effect = _capture_run_config_factory(container_configs)
    client.containers.get.side_effect = docker.errors.NotFound("Not found")
    client.networks.get.return_value = MagicMock()

    manager.run_node("test-node", auth_service=True, network="merobox-cluster")

    main_configs = [c for c in container_configs if c.get("detach") is True]
    assert main_configs and main_configs[0].get("network") == "calimero_web"


def _setup_mock_cluster(manager, mock_read_peer_id, nodes, network="merobox-cluster"):
    """Wire a manager + read_peer_id mock for `nodes`, deterministically.

    Returns (peer_ids, ips, config_files) dicts keyed by node name. The
    config-file paths are explicit (recorded in `manager.node_config_files`) so
    `read_peer_id` is mocked as an exact lookup, not a path-substring heuristic.
    """
    peer_ids = {n: _PID[i + 1] for i, n in enumerate(nodes)}
    ips = {n: _IP[i + 1] for i, n in enumerate(nodes)}
    config_files = {n: f"/abs/data/{n}/{n}/config.toml" for n in nodes}
    manager.node_config_files = dict(config_files)
    by_path = {p: peer_ids[n] for n, p in config_files.items()}
    mock_read_peer_id.side_effect = lambda cfg: by_path.get(str(cfg))
    manager.nodes = {n: _mock_cluster_container(ips[n], network=network) for n in nodes}
    manager._fix_permissions = MagicMock()
    return peer_ids, ips, config_files


@patch("merobox.commands.manager.apply_bootstrap_nodes")
@patch("merobox.commands.manager.read_peer_id")
@patch("docker.from_env")
def test_wire_cluster_bootstrap_peers_populates_siblings(
    mock_docker, mock_read_peer_id, mock_apply_bootstrap
):
    """Each cluster node gets the *other* nodes wired as /ip4 static bootstrap peers."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    nodes = ["calimero-node-1", "calimero-node-2", "calimero-node-3"]
    peer_ids, ips, _cfgs = _setup_mock_cluster(manager, mock_read_peer_id, nodes)

    assert (
        manager._wire_cluster_bootstrap_peers(nodes, "merobox-cluster", e2e_mode=True)
        is True
    )

    assert mock_apply_bootstrap.call_count == 3
    for call in mock_apply_bootstrap.call_args_list:
        _config_file, node_name, addrs = call.args
        assert addrs, f"{node_name} got an empty bootstrap list"
        assert all("/dns4/" not in a for a in addrs)
        assert all(ips[node_name] not in a for a in addrs)  # never itself
        for sib in (n for n in nodes if n != node_name):
            assert f"/ip4/{ips[sib]}/tcp/2428/p2p/{peer_ids[sib]}" in addrs
            assert f"/ip4/{ips[sib]}/udp/2428/quic-v1/p2p/{peer_ids[sib]}" in addrs
    for c in manager.nodes.values():
        c.restart.assert_called_once()


@patch("merobox.commands.manager.apply_bootstrap_nodes")
@patch("merobox.commands.manager.read_peer_id")
@patch("docker.from_env")
def test_wire_cluster_bootstrap_peers_bails_when_too_few_endpoints(
    mock_docker, mock_read_peer_id, mock_apply_bootstrap
):
    """If fewer than two peer endpoints resolve, fall back to mDNS (no rewrites)."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)
    mock_read_peer_id.return_value = None

    assert (
        manager._wire_cluster_bootstrap_peers(
            ["calimero-node-1", "calimero-node-2"], "merobox-cluster"
        )
        is False
    )
    mock_apply_bootstrap.assert_not_called()


@patch("merobox.commands.manager.apply_bootstrap_nodes")
@patch("merobox.commands.manager.read_peer_id")
@patch("docker.from_env")
def test_wire_cluster_bootstrap_peers_appends_to_explicit_list(
    mock_docker, mock_read_peer_id, mock_apply_bootstrap
):
    """An explicit bootstrap_nodes list is preserved; sibling /ip4 addrs are appended."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    nodes = ["calimero-node-1", "calimero-node-2"]
    _peer_ids, _ips, _cfgs = _setup_mock_cluster(manager, mock_read_peer_id, nodes)

    explicit = ["/ip4/63.181.86.34/tcp/4001/p2p/" + _PID[4]]
    manager._wire_cluster_bootstrap_peers(
        nodes, "merobox-cluster", e2e_mode=True, base_bootstrap_nodes=explicit
    )

    assert mock_apply_bootstrap.call_count == 2
    for call in mock_apply_bootstrap.call_args_list:
        _config_file, _node_name, addrs = call.args
        assert addrs[0] == explicit[0]
        assert any(a.startswith("/ip4/172.20.0.") and "/p2p/" in a for a in addrs[1:])


def test_peers_count_from_response_handles_various_shapes():
    """`GET /admin-api/peers` returns {"count": N}; older shapes use a list."""
    f = DockerManager._peers_count_from_response
    assert f({"count": 3}) == 3  # current merod shape
    assert f({"data": {"count": 2}}) == 2
    assert f({"data": {"peers": ["a", "b"]}}) == 2
    assert f({"peers": ["a"]}) == 1
    assert f(["a", "b", "c"]) == 3
    assert f({"data": {"total": 0}}) == 0  # unrecognized -> 0 (not len(dict))
    assert f({"count": True}) == 0  # a bool is not a peer count
    assert f(None) == 0


@patch("merobox.commands.manager.requests")
@patch("docker.from_env")
def test_wait_for_cluster_peers_true_when_all_connected(mock_docker, mock_requests):
    """Returns True once every node reports at least `expected_peers` peers."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)
    manager.node_rpc_ports = {"calimero-node-1": 2528, "calimero-node-2": 2529}

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"count": 1}  # GET /admin-api/peers shape
    mock_requests.get.return_value = resp

    assert (
        manager.wait_for_cluster_peers(
            ["calimero-node-1", "calimero-node-2"],
            expected_peers=1,
            timeout=2.0,
            interval=0.01,
        )
        is True
    )


@patch("merobox.commands.manager.requests")
@patch("docker.from_env")
def test_wait_for_cluster_peers_false_on_timeout(mock_docker, mock_requests):
    """Returns False if some node never reaches `expected_peers` within the timeout."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)
    manager.node_rpc_ports = {"calimero-node-1": 2528, "calimero-node-2": 2529}

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"count": 0}
    mock_requests.get.return_value = resp

    assert (
        manager.wait_for_cluster_peers(
            ["calimero-node-1", "calimero-node-2"],
            expected_peers=1,
            timeout=0.05,
            interval=0.01,
        )
        is False
    )


@patch.dict("os.environ", {"MEROBOX_LEGACY_CLUSTER_NETWORKING": "1"})
@patch("docker.from_env")
def test_run_multiple_nodes_legacy_env_skips_cluster_wiring(mock_docker):
    """The legacy kill-switch disables the dedicated network, wiring and gate."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    manager._find_available_ports = MagicMock(side_effect=[[2428, 2429], [2528, 2529]])
    manager.run_node = MagicMock(return_value=True)
    manager._ensure_cluster_network = MagicMock()
    manager._wire_cluster_bootstrap_peers = MagicMock()
    manager.wait_for_cluster_peers = MagicMock()

    assert manager.run_multiple_nodes(2) is True
    manager._ensure_cluster_network.assert_not_called()
    manager._wire_cluster_bootstrap_peers.assert_not_called()
    manager.wait_for_cluster_peers.assert_not_called()
    # run_node still called per node, with no cluster network
    assert manager.run_node.call_count == 2
    for call in manager.run_node.call_args_list:
        assert call.kwargs.get("network") is None


@patch("docker.from_env")
def test_run_multiple_nodes_wires_cluster_and_fails_on_gate(mock_docker):
    """Multi-node cluster: network + wiring happen, and a failed gate fails the run."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    manager._find_available_ports = MagicMock(side_effect=[[2428, 2429], [2528, 2529]])
    manager.run_node = MagicMock(return_value=True)
    manager._ensure_cluster_network = MagicMock(return_value="merobox-cluster")
    manager._wire_cluster_bootstrap_peers = MagicMock()
    manager.wait_for_cluster_peers = MagicMock(return_value=False)

    result = manager.run_multiple_nodes(2)

    manager._ensure_cluster_network.assert_called_once()
    manager._wire_cluster_bootstrap_peers.assert_called_once()
    manager.wait_for_cluster_peers.assert_called_once()
    assert result is False  # gate failed -> run fails


@patch("docker.from_env")
def test_run_multiple_nodes_single_node_unchanged(mock_docker):
    """A single-node run does not touch the cluster network / wiring / gate."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    manager._find_available_ports = MagicMock(side_effect=[[2428], [2528]])
    manager.run_node = MagicMock(return_value=True)
    manager._ensure_cluster_network = MagicMock()
    manager._wire_cluster_bootstrap_peers = MagicMock()
    manager.wait_for_cluster_peers = MagicMock()

    assert manager.run_multiple_nodes(1) is True
    manager._ensure_cluster_network.assert_not_called()
    manager._wire_cluster_bootstrap_peers.assert_not_called()
    manager.wait_for_cluster_peers.assert_not_called()


# ============================================================================
# is_node_running (PR #143 — stop_node/start_node steps)
# ============================================================================


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


# ============================================================================
# Graceful stop timeout overrides (issue #237)
# ============================================================================


@patch.dict("os.environ", {"MEROBOX_STOP_TIMEOUT": "120"}, clear=False)
@patch("docker.from_env")
def test_stop_all_nodes_honors_env_stop_timeout(mock_docker):
    """MEROBOX_STOP_TIMEOUT overrides the default 10s container stop grace."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    seed = MagicMock()
    seed.name = "calimero-node-1"
    worker = MagicMock()
    worker.name = "calimero-node-2"
    client.containers.list.return_value = [seed, worker]

    manager.stop_all_nodes(drain_timeout=0)

    seed.stop.assert_called_once_with(timeout=120)
    worker.stop.assert_called_once_with(timeout=120)


@patch.dict("os.environ", {"MEROBOX_STOP_TIMEOUT": "not-a-number"}, clear=False)
@patch("docker.from_env")
def test_stop_all_nodes_falls_back_when_env_is_garbage(mock_docker):
    """A non-numeric env value must not abort cleanup — fall back silently."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    seed = MagicMock()
    seed.name = "calimero-node-1"
    client.containers.list.return_value = [seed]

    manager.stop_all_nodes(drain_timeout=0)

    # 10s is the CONTAINER_STOP_TIMEOUT default.
    seed.stop.assert_called_once_with(timeout=10)


@patch.dict("os.environ", {"MEROBOX_STOP_TIMEOUT": "120"}, clear=False)
@patch("docker.from_env")
def test_explicit_stop_timeout_overrides_env(mock_docker):
    """An explicit caller-provided stop_timeout wins over the env override."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    seed = MagicMock()
    client.containers.list.return_value = [seed]

    manager.stop_all_nodes(drain_timeout=0, stop_timeout=42)

    seed.stop.assert_called_once_with(timeout=42)


@patch("docker.from_env")
def test_stop_all_nodes_gives_seed_and_workers_same_grace(mock_docker):
    """Regression for #237: workers must get the same stop_timeout as the seed."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    containers = [MagicMock(name=f"calimero-node-{i}") for i in range(1, 5)]
    for i, c in enumerate(containers, start=1):
        c.name = f"calimero-node-{i}"
    client.containers.list.return_value = containers

    manager.stop_all_nodes(drain_timeout=0, stop_timeout=90)

    for c in containers:
        c.kill.assert_called_once_with(signal="SIGTERM")
        c.stop.assert_called_once_with(timeout=90)


@patch.dict("os.environ", {"MEROBOX_DRAIN_TIMEOUT": "2"}, clear=False)
@patch("docker.from_env")
def test_drain_timeout_env_override(mock_docker):
    """MEROBOX_DRAIN_TIMEOUT controls the post-SIGTERM pre-stop wait."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    seed = MagicMock()
    client.containers.list.return_value = [seed]

    with patch("merobox.commands.manager.time.sleep") as mock_sleep:
        manager.stop_all_nodes(stop_timeout=10)

    # assert_any_call rather than assert_called_with: _graceful_stop_containers_batch
    # may call time.sleep for other reasons (none today, but defensive).
    mock_sleep.assert_any_call(2)


@patch.dict("os.environ", {"MEROBOX_STOP_TIMEOUT": "120.5"}, clear=False)
@patch("docker.from_env")
def test_float_env_value_is_truncated_not_rejected(mock_docker):
    """Float strings like '120.5' truncate to int, not silently fall back to default."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    seed = MagicMock()
    client.containers.list.return_value = [seed]

    manager.stop_all_nodes(drain_timeout=0)

    seed.stop.assert_called_once_with(timeout=120)


@patch.dict("os.environ", {"MEROBOX_DRAIN_TIMEOUT": "0"}, clear=False)
@patch("docker.from_env")
def test_drain_timeout_zero_skips_drain_phase(mock_docker):
    """MEROBOX_DRAIN_TIMEOUT=0 propagates and skips the drain sleep entirely."""
    client = MagicMock()
    mock_docker.return_value = client

    manager = DockerManager(enable_signal_handlers=False)

    seed = MagicMock()
    client.containers.list.return_value = [seed]

    with patch("merobox.commands.manager.time.sleep") as mock_sleep:
        manager.stop_all_nodes(stop_timeout=10)

    mock_sleep.assert_not_called()


# --- export_node_logs (merobox#207): persist logs without stopping nodes -----


def _logging_container(name, log_text):
    """A MagicMock container whose `.logs()` returns `log_text` as bytes."""
    c = MagicMock()
    c.name = name
    c.logs.return_value = log_text.encode("utf-8")
    return c


@patch("docker.from_env")
def test_export_node_logs_writes_running_nodes(mock_docker, tmp_path, monkeypatch):
    """With no explicit names, all running nodes are dumped to data/container-logs/."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    node = _logging_container("calimero-node-1", "hello from node-1")
    # get_running_nodes() lists running calimero nodes; export then re-fetches
    # each by name via containers.get().
    client.containers.list.return_value = [node]
    client.containers.get.return_value = node

    monkeypatch.chdir(tmp_path)
    written = manager.export_node_logs()

    assert written == 1
    log_file = tmp_path / "data" / "container-logs" / "calimero-node-1.log"
    assert log_file.read_text() == "hello from node-1"
    # Logs are captured with timestamps, matching the stop-path format.
    node.logs.assert_called_once_with(timestamps=True)
    # Node was never stopped — it is left running.
    node.stop.assert_not_called()


@patch("docker.from_env")
def test_export_node_logs_explicit_names(mock_docker, tmp_path, monkeypatch):
    """Explicit names bypass get_running_nodes and are fetched directly."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    node = _logging_container("calimero-node-2", "node-2 logs")
    client.containers.get.return_value = node

    monkeypatch.chdir(tmp_path)
    written = manager.export_node_logs(["calimero-node-2"])

    assert written == 1
    assert (
        tmp_path / "data" / "container-logs" / "calimero-node-2.log"
    ).read_text() == "node-2 logs"
    # No running-node discovery when names are supplied.
    client.containers.list.assert_not_called()


@patch("docker.from_env")
def test_export_node_logs_no_running_nodes_returns_zero(
    mock_docker, tmp_path, monkeypatch
):
    """No running nodes -> nothing written, returns 0, no directory churn."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    client.containers.list.return_value = []

    monkeypatch.chdir(tmp_path)
    assert manager.export_node_logs() == 0
    assert not (tmp_path / "data" / "container-logs").exists()


@patch("docker.from_env")
def test_export_node_logs_skips_missing_container(mock_docker, tmp_path, monkeypatch):
    """A name that can't be fetched is skipped without aborting the rest."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    good = _logging_container("calimero-node-1", "ok")

    def get(name):
        if name == "calimero-node-1":
            return good
        raise docker.errors.NotFound("gone")

    client.containers.get.side_effect = get

    monkeypatch.chdir(tmp_path)
    written = manager.export_node_logs(["calimero-node-1", "calimero-node-missing"])

    assert written == 1
    assert (tmp_path / "data" / "container-logs" / "calimero-node-1.log").exists()
    assert not (
        tmp_path / "data" / "container-logs" / "calimero-node-missing.log"
    ).exists()
