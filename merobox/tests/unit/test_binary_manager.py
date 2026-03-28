import signal
import subprocess
import threading
from unittest.mock import MagicMock

from merobox.commands.binary_manager import BinaryManager
from merobox.commands.constants import CleanupResult


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
    in_progress_count = sum(1 for r in results if r == CleanupResult.IN_PROGRESS)
    assert performed_count == 1, f"Expected exactly 1 PERFORMED, got {performed_count}"
    assert (
        done_count == num_threads - 1
    ), f"Expected {num_threads - 1} ALREADY_DONE, got {done_count}"
    assert in_progress_count == 0, f"Expected 0 IN_PROGRESS, got {in_progress_count}"


def test_binary_manager_cleanup_returns_in_progress_when_flag_set():
    """Test that cleanup returns IN_PROGRESS when _cleanup_in_progress flag is set.

    This tests the flag check logic specifically by manually setting the flag.
    In production, this would occur when a signal handler calls cleanup while
    atexit cleanup is already running in the same thread (RLock allows re-entry).
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
