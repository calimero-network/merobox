import os
import signal
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from merobox.commands.binary_manager import BinaryManager
from merobox.commands.constants import CleanupResult


@patch("merobox.commands.binary_manager.apply_near_devnet_config_to_file")
@patch("subprocess.Popen")
@patch("subprocess.run")
def test_run_node_calls_shared_config_utils(mock_run, mock_popen, mock_apply_config):
    # Setup
    manager = BinaryManager(
        binary_path="/bin/merod", require_binary=False, enable_signal_handlers=False
    )

    near_config = {
        "rpc_url": "http://127.0.0.1:3030",
        "contract_id": "test.near",
        "account_id": "node.near",
        "public_key": "pk",
        "secret_key": "sk",
    }

    # Mock mocks
    manager._load_pid = MagicMock(return_value=None)

    # Mock paths to simulate first-run (config exists check)
    with (
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.exists", return_value=False),
        patch("builtins.open", mock_open()),
    ):

        # Run the node
        manager.run_node("node1", near_devnet_config=near_config)

        # Verify the `apply_near_devnet_config_to_file` utility function was called
        mock_apply_config.assert_called_once()

        # Verify arguments passed to it
        args, _ = mock_apply_config.call_args

        # args[0] is config_file path object
        assert isinstance(args[0], Path)

        assert str(args[0]).endswith("config.toml")
        assert args[1] == "node1"
        assert args[2] == near_config["rpc_url"]
        assert args[6] == near_config["secret_key"]


def test_binary_manager_path_fix():
    """Ensure BinaryManager uses the correct nested path for config.toml."""
    bm = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=False
    )

    with patch(
        "merobox.commands.binary_manager.apply_near_devnet_config_to_file"
    ) as mock_apply:
        # Mock filesystem/subprocess interactions
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.exists", return_value=False),
            patch("subprocess.run"),
            patch("subprocess.Popen"),
            patch("builtins.open"),
        ):

            bm.run_node(
                "node1",
                near_devnet_config={
                    "rpc_url": "u",
                    "contract_id": "c",
                    "account_id": "a",
                    "public_key": "p",
                    "secret_key": "s",
                },
            )

    # Verify the path argument to apply_near_devnet_config_to_file
    mock_apply.assert_called_once()
    args, _ = mock_apply.call_args
    config_path = str(args[0])

    # Expect: .../data/node1/node1/config.toml
    expected_suffix = os.path.join("node1", "node1", "node1", "config.toml")
    assert config_path.endswith(expected_suffix)


def test_binary_manager_signal_handlers_registered():
    """Test that signal handlers are registered when enabled."""
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=True
    )

    # Verify handlers were stored
    assert manager._original_sigint_handler is not None
    assert manager._original_sigterm_handler is not None

    # Cleanup - restore original handlers
    manager.remove_signal_handlers()


def test_binary_manager_signal_handlers_disabled():
    """Test that signal handlers are not registered when disabled."""
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=False
    )

    # Verify handlers were not stored (None indicates not set up)
    assert manager._original_sigint_handler is None
    assert manager._original_sigterm_handler is None


def test_binary_manager_cleanup_resources():
    """Test that _cleanup_resources stops all managed processes."""
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=False
    )

    # Add mock processes to track
    mock_process1 = MagicMock()
    mock_process2 = MagicMock()
    manager.processes = {"node1": mock_process1, "node2": mock_process2}
    manager.node_rpc_ports = {"node1": 2528, "node2": 2529}

    # Mock _remove_pid_file to avoid file system operations
    manager._remove_pid_file = MagicMock()

    # Call cleanup
    manager._cleanup_resources()

    # Verify processes were terminated
    mock_process1.terminate.assert_called_once()
    mock_process1.wait.assert_called_once_with(timeout=5)
    mock_process2.terminate.assert_called_once()
    mock_process2.wait.assert_called_once_with(timeout=5)

    # Verify tracking dicts were cleared
    assert len(manager.processes) == 0
    assert len(manager.node_rpc_ports) == 0


def test_binary_manager_cleanup_resources_timeout():
    """Test that _cleanup_resources kills processes that don't terminate gracefully."""
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=False
    )

    # Add mock process that times out on terminate
    mock_process = MagicMock()
    mock_process.wait.side_effect = [subprocess.TimeoutExpired(cmd="merod", timeout=5)]
    manager.processes = {"node1": mock_process}

    # Mock _remove_pid_file to avoid file system operations
    manager._remove_pid_file = MagicMock()

    # Call cleanup
    manager._cleanup_resources()

    # Verify process was terminated and then killed
    mock_process.terminate.assert_called_once()
    mock_process.kill.assert_called_once()


def test_binary_manager_remove_signal_handlers():
    """Test that signal handlers can be removed and restored."""
    # Store original handlers before test
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    # Create manager with signal handlers enabled
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=True
    )

    # Handlers should be different now
    current_sigint = signal.getsignal(signal.SIGINT)
    assert current_sigint == manager._signal_handler

    # Remove handlers
    manager.remove_signal_handlers()

    # Verify handlers were restored
    assert signal.getsignal(signal.SIGINT) == original_sigint
    assert signal.getsignal(signal.SIGTERM) == original_sigterm


def test_binary_manager_cleanup_prevents_double_cleanup():
    """Test that _cleanup_resources prevents double cleanup races."""
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=False
    )

    # Add mock processes to track
    mock_process1 = MagicMock()
    manager.processes = {"node1": mock_process1}
    manager.node_rpc_ports = {"node1": 2528}
    manager._remove_pid_file = MagicMock()

    # First cleanup should work and return PERFORMED
    result = manager._cleanup_resources()
    assert result == CleanupResult.PERFORMED
    assert mock_process1.terminate.call_count == 1
    assert manager._cleanup_done is True

    # Second cleanup should be skipped and return ALREADY_DONE
    mock_process2 = MagicMock()
    manager.processes = {"node2": mock_process2}
    result = manager._cleanup_resources()
    assert result == CleanupResult.ALREADY_DONE

    # Process2 should not have been terminated
    assert mock_process2.terminate.call_count == 0


def test_binary_manager_cleanup_concurrent_access():
    """Test that _cleanup_resources handles concurrent access correctly.

    Spawns multiple threads calling _cleanup_resources simultaneously to verify
    the lock ensures at-most-once execution under concurrent access.
    Also verifies return value distribution: exactly one PERFORMED, rest ALREADY_DONE.
    """
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=False
    )

    # Add a mock process
    mock_process = MagicMock()
    manager.processes = {"node1": mock_process}
    manager.node_rpc_ports = {"node1": 2528}
    manager._remove_pid_file = MagicMock()

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
    assert mock_process.terminate.call_count == 1

    # Verify return value distribution: exactly one PERFORMED, rest ALREADY_DONE
    performed_count = sum(1 for r in results if r == CleanupResult.PERFORMED)
    done_count = sum(1 for r in results if r == CleanupResult.ALREADY_DONE)
    assert performed_count == 1, f"Expected exactly 1 PERFORMED, got {performed_count}"
    assert (
        done_count == num_threads - 1
    ), f"Expected {num_threads - 1} ALREADY_DONE, got {done_count}"


def test_binary_manager_cleanup_in_progress_returns_in_progress():
    """Test that re-entrant cleanup call returns IN_PROGRESS when cleanup is running.

    This simulates a signal handler calling cleanup while atexit cleanup is running.
    With RLock, the same thread can re-enter, and should get IN_PROGRESS indicating
    cleanup is already in progress.
    """
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=False
    )

    # Manually set cleanup in progress to simulate mid-cleanup state
    manager._cleanup_in_progress = True

    # Calling cleanup while in progress should return IN_PROGRESS
    result = manager._cleanup_resources()
    assert result == CleanupResult.IN_PROGRESS

    # Reset and verify normal cleanup works
    manager._cleanup_in_progress = False
    mock_process = MagicMock()
    manager.processes = {"node1": mock_process}
    manager._remove_pid_file = MagicMock()

    result = manager._cleanup_resources()
    assert result == CleanupResult.PERFORMED
    assert mock_process.terminate.call_count == 1
