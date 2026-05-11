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
    client.networks.create.assert_called_once()
    _, kwargs = client.networks.create.call_args
    assert kwargs.get("name") == DockerManager.CLUSTER_NETWORK_NAME
    assert kwargs.get("driver") == "bridge"


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


@patch("merobox.commands.manager.apply_bootstrap_nodes")
@patch("merobox.commands.manager.read_peer_id")
@patch("docker.from_env")
def test_wire_cluster_bootstrap_peers_populates_siblings(
    mock_docker, mock_read_peer_id, mock_apply_bootstrap
):
    """Each cluster node gets the *other* nodes wired as static bootstrap peers."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    peer_ids = {
        "calimero-node-1": "12D3KooWAAA",
        "calimero-node-2": "12D3KooWBBB",
        "calimero-node-3": "12D3KooWCCC",
    }
    mock_read_peer_id.side_effect = lambda cfg: next(
        (
            pid
            for name, pid in peer_ids.items()
            if f"/{name}/" in str(cfg).replace("\\", "/")
        ),
        None,
    )

    containers = {name: MagicMock() for name in peer_ids}
    for c in containers.values():
        c.status = "running"
    manager.nodes = dict(containers)
    manager._fix_permissions = MagicMock()

    manager._wire_cluster_bootstrap_peers(list(peer_ids), e2e_mode=True)

    assert mock_apply_bootstrap.call_count == 3
    for call in mock_apply_bootstrap.call_args_list:
        _config_file, node_name, addrs = call.args
        assert addrs, f"{node_name} got an empty bootstrap list"
        assert all(f"/dns4/{node_name}/" not in a for a in addrs)
        for sib in (n for n in peer_ids if n != node_name):
            assert f"/dns4/{sib}/tcp/2428/p2p/{peer_ids[sib]}" in addrs
            assert f"/dns4/{sib}/udp/2428/quic-v1/p2p/{peer_ids[sib]}" in addrs
    for c in containers.values():
        c.restart.assert_called_once()


@patch("merobox.commands.manager.apply_bootstrap_nodes")
@patch("merobox.commands.manager.read_peer_id")
@patch("docker.from_env")
def test_wire_cluster_bootstrap_peers_bails_when_too_few_peer_ids(
    mock_docker, mock_read_peer_id, mock_apply_bootstrap
):
    """If fewer than two peer IDs can be read, fall back to mDNS (no rewrites)."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)
    mock_read_peer_id.return_value = None

    manager._wire_cluster_bootstrap_peers(["calimero-node-1", "calimero-node-2"])

    mock_apply_bootstrap.assert_not_called()


@patch("merobox.commands.manager.apply_bootstrap_nodes")
@patch("merobox.commands.manager.read_peer_id")
@patch("docker.from_env")
def test_wire_cluster_bootstrap_peers_appends_to_explicit_list(
    mock_docker, mock_read_peer_id, mock_apply_bootstrap
):
    """An explicit bootstrap_nodes list is preserved; siblings are appended."""
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager(enable_signal_handlers=False)

    peer_ids = {"calimero-node-1": "12D3KooWAAA", "calimero-node-2": "12D3KooWBBB"}
    mock_read_peer_id.side_effect = lambda cfg: next(
        (
            pid
            for name, pid in peer_ids.items()
            if f"/{name}/" in str(cfg).replace("\\", "/")
        ),
        None,
    )
    for name in peer_ids:
        manager.nodes[name] = MagicMock(status="running")
    manager._fix_permissions = MagicMock()

    explicit = ["/ip4/63.181.86.34/tcp/4001/p2p/12D3KooWDevnet"]
    manager._wire_cluster_bootstrap_peers(
        list(peer_ids), e2e_mode=True, base_bootstrap_nodes=explicit
    )

    for call in mock_apply_bootstrap.call_args_list:
        _config_file, _node_name, addrs = call.args
        assert addrs[0] == explicit[0]


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
    resp.json.return_value = {"data": {"peers": ["peerA"]}}
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
    resp.json.return_value = {"data": {"peers": []}}
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

    manager._find_available_ports = MagicMock(return_value=[2428, 2429])
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
