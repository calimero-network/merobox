"""
Unit tests for the nuke command and stale directory cleanup functionality.
"""

from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.nuke import (
    DetectionResult,
    NodeDetectionError,
    _is_valid_calimero_data_dir,
    execute_nuke,
    find_calimero_data_dirs,
    find_stale_data_dirs,
    get_running_node_names,
    nuke_all_data_dirs,
)


class TestFindCalimeroDataDirs:
    """Tests for find_calimero_data_dirs function."""

    def test_returns_empty_list_when_data_dir_not_exists(self, tmp_path, monkeypatch):
        """Should return empty list when data directory doesn't exist."""
        monkeypatch.chdir(tmp_path)
        result = find_calimero_data_dirs()
        assert result == []

    def test_finds_calimero_node_directories(self, tmp_path, monkeypatch):
        """Should find directories starting with calimero-node-."""
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "calimero-node-1").mkdir()
        (data_dir / "calimero-node-2").mkdir()
        (data_dir / "other-dir").mkdir()

        result = find_calimero_data_dirs()
        assert len(result) == 2

    def test_finds_prop_directories(self, tmp_path, monkeypatch):
        """Should find directories starting with prop-."""
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "prop-test-1").mkdir()
        (data_dir / "prop-test-2").mkdir()

        result = find_calimero_data_dirs()
        assert len(result) == 2

    def test_finds_proposal_directories(self, tmp_path, monkeypatch):
        """Should find directories starting with proposal-."""
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "proposal-1").mkdir()

        result = find_calimero_data_dirs()
        assert len(result) == 1

    def test_filters_by_prefix(self, tmp_path, monkeypatch):
        """Should filter directories by given prefix."""
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "calimero-node-1").mkdir()
        (data_dir / "prop-test-1").mkdir()

        result = find_calimero_data_dirs(prefix="prop-")
        assert len(result) == 1
        assert "prop-test-1" in result[0]


class TestGetRunningNodeNames:
    """Tests for get_running_node_names function."""

    @patch("merobox.commands.nuke.BinaryManager")
    @patch("merobox.commands.nuke.DockerManager")
    def test_returns_running_docker_containers(
        self, mock_docker_manager, mock_binary_manager
    ):
        """Should return names of running Docker containers."""
        mock_container = MagicMock()
        mock_container.name = "calimero-node-1"

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]

        mock_manager_instance = MagicMock()
        mock_manager_instance.client = mock_client
        mock_docker_manager.return_value = mock_manager_instance

        mock_binary_instance = MagicMock()
        mock_binary_instance.is_node_running.return_value = False
        mock_binary_manager.return_value = mock_binary_instance

        result = get_running_node_names()

        assert "calimero-node-1" in result

    @patch("merobox.commands.nuke.BinaryManager")
    @patch("merobox.commands.nuke.DockerManager")
    def test_returns_running_binary_processes(
        self, mock_docker_manager, mock_binary_manager, tmp_path, monkeypatch
    ):
        """Should return names of running binary processes."""
        monkeypatch.chdir(tmp_path)

        # Mock Docker to return no containers
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_manager_instance = MagicMock()
        mock_manager_instance.client = mock_client
        mock_docker_manager.return_value = mock_manager_instance

        # Create PID directory with a PID file
        pid_dir = tmp_path / "data" / ".pids"
        pid_dir.mkdir(parents=True)
        (pid_dir / "calimero-node-1.pid").write_text("12345")

        mock_binary_instance = MagicMock()
        mock_binary_instance.is_node_running.return_value = True
        mock_binary_manager.return_value = mock_binary_instance

        result = get_running_node_names()

        assert "calimero-node-1" in result

    @patch("merobox.commands.nuke.BinaryManager")
    @patch("merobox.commands.nuke.DockerManager")
    def test_handles_docker_error_gracefully(
        self, mock_docker_manager, mock_binary_manager
    ):
        """Should handle Docker errors gracefully."""
        mock_docker_manager.side_effect = Exception("Docker not available")

        mock_binary_instance = MagicMock()
        mock_binary_instance.is_node_running.return_value = False
        mock_binary_manager.return_value = mock_binary_instance

        result = get_running_node_names(silent=True)

        assert isinstance(result, set)

    @patch("merobox.commands.nuke.BinaryManager")
    @patch("merobox.commands.nuke.DockerManager")
    def test_deduplicates_nodes_from_both_sources(
        self, mock_docker_manager, mock_binary_manager, tmp_path, monkeypatch
    ):
        """Should deduplicate nodes detected by both Docker and binary detection."""
        monkeypatch.chdir(tmp_path)

        # Docker returns a container named "calimero-node-1"
        mock_container = MagicMock()
        mock_container.name = "calimero-node-1"
        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_manager_instance = MagicMock()
        mock_manager_instance.client = mock_client
        mock_docker_manager.return_value = mock_manager_instance

        # Create PID directory with a PID file for the same node
        pid_dir = tmp_path / "data" / ".pids"
        pid_dir.mkdir(parents=True)
        (pid_dir / "calimero-node-1.pid").write_text("12345")

        # Binary manager also reports the same node as running
        mock_binary_instance = MagicMock()
        mock_binary_instance.is_node_running.return_value = True
        mock_binary_manager.return_value = mock_binary_instance

        result = get_running_node_names(silent=True)

        # Should only have one entry, not duplicates
        assert len(result) == 1
        assert "calimero-node-1" in result


class TestFindStaleDataDirs:
    """Tests for find_stale_data_dirs function."""

    @patch("merobox.commands.nuke._is_valid_calimero_data_dir")
    @patch("merobox.commands.nuke.get_running_node_names")
    @patch("merobox.commands.nuke.find_calimero_data_dirs")
    def test_returns_dirs_without_running_nodes(
        self, mock_find_dirs, mock_running_nodes, mock_is_valid
    ):
        """Should return directories that don't have running nodes."""
        mock_find_dirs.return_value = [
            "data/calimero-node-1",
            "data/calimero-node-2",
            "data/calimero-node-3",
        ]
        mock_running_nodes.return_value = {"calimero-node-1"}
        mock_is_valid.return_value = True

        result = find_stale_data_dirs(silent=True)

        assert len(result) == 2
        assert "data/calimero-node-2" in result
        assert "data/calimero-node-3" in result
        assert "data/calimero-node-1" not in result

    @patch("merobox.commands.nuke._is_valid_calimero_data_dir")
    @patch("merobox.commands.nuke.get_running_node_names")
    @patch("merobox.commands.nuke.find_calimero_data_dirs")
    def test_returns_empty_when_all_nodes_running(
        self, mock_find_dirs, mock_running_nodes, mock_is_valid
    ):
        """Should return empty list when all nodes are running."""
        mock_find_dirs.return_value = [
            "data/calimero-node-1",
            "data/calimero-node-2",
        ]
        mock_running_nodes.return_value = {"calimero-node-1", "calimero-node-2"}
        mock_is_valid.return_value = True

        result = find_stale_data_dirs(silent=True)

        assert result == []

    @patch("merobox.commands.nuke._is_valid_calimero_data_dir")
    @patch("merobox.commands.nuke.get_running_node_names")
    @patch("merobox.commands.nuke.find_calimero_data_dirs")
    def test_returns_all_when_no_nodes_running(
        self, mock_find_dirs, mock_running_nodes, mock_is_valid
    ):
        """Should return all directories when no nodes are running."""
        mock_find_dirs.return_value = [
            "data/calimero-node-1",
            "data/calimero-node-2",
        ]
        mock_running_nodes.return_value = set()
        mock_is_valid.return_value = True

        result = find_stale_data_dirs(silent=True)

        assert len(result) == 2

    @patch("merobox.commands.nuke._is_valid_calimero_data_dir")
    @patch("merobox.commands.nuke.get_running_node_names")
    @patch("merobox.commands.nuke.find_calimero_data_dirs")
    def test_respects_prefix_filter(
        self, mock_find_dirs, mock_running_nodes, mock_is_valid
    ):
        """Should respect prefix filter when finding stale directories."""
        mock_find_dirs.return_value = ["data/prop-test-1"]
        mock_running_nodes.return_value = set()
        mock_is_valid.return_value = True

        find_stale_data_dirs(prefix="prop-", silent=True)

        mock_find_dirs.assert_called_once_with("prop-")


class TestNukeAllDataDirs:
    """Tests for nuke_all_data_dirs function."""

    def test_dry_run_does_not_delete(self, tmp_path):
        """Should not delete directories in dry run mode."""
        test_dir = tmp_path / "test-node"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("test content")

        results = nuke_all_data_dirs([str(test_dir)], dry_run=True)

        assert test_dir.exists()
        assert len(results) == 1
        assert results[0]["status"] == "would_delete"
        assert results[0]["size_bytes"] > 0

    def test_deletes_directories(self, tmp_path):
        """Should delete directories when not in dry run mode."""
        test_dir = tmp_path / "test-node"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("test content")

        results = nuke_all_data_dirs([str(test_dir)], dry_run=False)

        assert not test_dir.exists()
        assert len(results) == 1
        assert results[0]["status"] == "deleted"

    def test_handles_nonexistent_directory(self, tmp_path):
        """Should handle non-existent directories gracefully."""
        nonexistent = str(tmp_path / "nonexistent")

        results = nuke_all_data_dirs([nonexistent], dry_run=False)

        assert len(results) == 1
        assert results[0]["status"] == "not_found"

    def test_handles_multiple_directories(self, tmp_path):
        """Should handle multiple directories."""
        dir1 = tmp_path / "node-1"
        dir2 = tmp_path / "node-2"
        dir1.mkdir()
        dir2.mkdir()

        results = nuke_all_data_dirs([str(dir1), str(dir2)], dry_run=False)

        assert len(results) == 2
        assert not dir1.exists()
        assert not dir2.exists()


class TestExecuteNuke:
    """Tests for execute_nuke function."""

    @patch("merobox.commands.nuke.find_stale_data_dirs")
    def test_stale_only_mode_uses_stale_dirs(self, mock_find_stale):
        """Should use find_stale_data_dirs when stale_only=True."""
        mock_find_stale.return_value = []

        result = execute_nuke(stale_only=True, silent=True)

        assert result is True
        mock_find_stale.assert_called_once()

    @patch("merobox.commands.nuke.find_calimero_data_dirs")
    def test_normal_mode_uses_all_dirs(self, mock_find_dirs):
        """Should use find_calimero_data_dirs when stale_only=False."""
        mock_find_dirs.return_value = []

        result = execute_nuke(stale_only=False, silent=True)

        assert result is True
        mock_find_dirs.assert_called_once()

    @patch("merobox.commands.nuke._stop_running_services")
    @patch("merobox.commands.nuke._cleanup_auth_services")
    @patch("merobox.commands.nuke.nuke_all_data_dirs")
    @patch("merobox.commands.nuke.find_stale_data_dirs")
    def test_stale_only_skips_stop_operations(
        self, mock_find_stale, mock_nuke_dirs, mock_cleanup_auth, mock_stop_services
    ):
        """Should skip stopping processes when stale_only=True."""
        mock_find_stale.return_value = ["data/calimero-node-1"]
        mock_nuke_dirs.return_value = [
            {"path": "data/calimero-node-1", "status": "deleted", "size_bytes": 100}
        ]

        # Create a mock manager
        mock_manager = MagicMock()

        result = execute_nuke(
            manager=mock_manager, stale_only=True, silent=True, force=True
        )

        assert result is True
        # Verify stop services helper was NOT called
        mock_stop_services.assert_not_called()
        # Verify cleanup auth helper was NOT called
        mock_cleanup_auth.assert_not_called()

    @patch("merobox.commands.nuke.find_stale_data_dirs")
    def test_stale_only_returns_false_on_detection_error(self, mock_find_stale):
        """Should return False when NodeDetectionError is raised."""
        mock_find_stale.side_effect = NodeDetectionError("Detection failed")

        result = execute_nuke(stale_only=True, silent=True)

        assert result is False


class TestIsValidCalimeroDataDir:
    """Tests for _is_valid_calimero_data_dir function."""

    def test_returns_false_for_nonexistent_dir(self, tmp_path):
        """Should return False for non-existent directory."""
        nonexistent = str(tmp_path / "nonexistent")
        assert _is_valid_calimero_data_dir(nonexistent) is False

    def test_returns_true_for_dir_with_node_subdir(self, tmp_path):
        """Should return True for directory with expected node subdirectory."""
        node_dir = tmp_path / "calimero-node-1"
        node_dir.mkdir()
        (node_dir / "calimero-node-1").mkdir()

        assert _is_valid_calimero_data_dir(str(node_dir)) is True

    def test_returns_true_for_dir_with_logs(self, tmp_path):
        """Should return True for directory with logs subdirectory."""
        node_dir = tmp_path / "calimero-node-1"
        node_dir.mkdir()
        (node_dir / "logs").mkdir()

        assert _is_valid_calimero_data_dir(str(node_dir)) is True

    def test_returns_true_for_empty_dir(self, tmp_path):
        """Should return True for empty directory (safe to delete)."""
        node_dir = tmp_path / "calimero-node-1"
        node_dir.mkdir()

        assert _is_valid_calimero_data_dir(str(node_dir)) is True

    def test_returns_false_for_unrelated_content(self, tmp_path):
        """Should return False for directory with unrelated content."""
        node_dir = tmp_path / "calimero-node-1"
        node_dir.mkdir()
        (node_dir / "random_file.txt").write_text("random content")

        assert _is_valid_calimero_data_dir(str(node_dir)) is False


class TestDetectionResult:
    """Tests for DetectionResult class."""

    def test_partial_failure_when_only_docker_fails(self):
        """Should detect partial failure when only Docker fails."""
        result = DetectionResult(
            nodes=set(), docker_failed=True, binary_failed=False, warnings=[]
        )
        assert result.partial_failure is True
        assert result.complete_failure is False

    def test_partial_failure_when_only_binary_fails(self):
        """Should detect partial failure when only binary fails."""
        result = DetectionResult(
            nodes=set(), docker_failed=False, binary_failed=True, warnings=[]
        )
        assert result.partial_failure is True
        assert result.complete_failure is False

    def test_complete_failure_when_both_fail(self):
        """Should detect complete failure when both mechanisms fail."""
        result = DetectionResult(
            nodes=set(), docker_failed=True, binary_failed=True, warnings=[]
        )
        assert result.partial_failure is False
        assert result.complete_failure is True

    def test_no_failure_when_both_succeed(self):
        """Should report no failure when both mechanisms succeed."""
        result = DetectionResult(
            nodes={"node1"}, docker_failed=False, binary_failed=False, warnings=[]
        )
        assert result.partial_failure is False
        assert result.complete_failure is False


class TestNodeDetectionError:
    """Tests for NodeDetectionError and fail-safe behavior."""

    @patch("merobox.commands.nuke.BinaryManager")
    @patch("merobox.commands.nuke.DockerManager")
    def test_raises_error_when_both_detections_fail(
        self, mock_docker_manager, mock_binary_manager, tmp_path, monkeypatch
    ):
        """Should raise NodeDetectionError when both Docker and binary detection fail."""
        # Set up working directory with PID directory to exercise binary detection path
        monkeypatch.chdir(tmp_path)
        pid_dir = tmp_path / "data" / ".pids"
        pid_dir.mkdir(parents=True)
        (pid_dir / "calimero-node-1.pid").write_text("12345")

        # Make both detection mechanisms fail
        mock_docker_manager.side_effect = Exception("Docker unavailable")
        mock_binary_manager.side_effect = Exception("Binary manager failed")

        with pytest.raises(NodeDetectionError):
            get_running_node_names(fail_safe=True, silent=True)

    @patch("merobox.commands.nuke.BinaryManager")
    @patch("merobox.commands.nuke.DockerManager")
    def test_no_error_when_fail_safe_disabled(
        self, mock_docker_manager, mock_binary_manager, tmp_path, monkeypatch
    ):
        """Should not raise error when fail_safe=False even if detection fails."""
        # Set up working directory with PID directory to exercise binary detection path
        monkeypatch.chdir(tmp_path)
        pid_dir = tmp_path / "data" / ".pids"
        pid_dir.mkdir(parents=True)
        (pid_dir / "calimero-node-1.pid").write_text("12345")

        mock_docker_manager.side_effect = Exception("Docker unavailable")
        mock_binary_manager.side_effect = Exception("Binary manager failed")

        result = get_running_node_names(fail_safe=False, silent=True)

        assert isinstance(result, set)
        assert len(result) == 0

    @patch("merobox.commands.nuke.BinaryManager")
    @patch("merobox.commands.nuke.DockerManager")
    def test_no_error_when_docker_works(
        self, mock_docker_manager, mock_binary_manager, tmp_path, monkeypatch
    ):
        """Should not raise error when Docker detection succeeds."""
        # Set up working directory with PID directory to exercise binary detection path
        monkeypatch.chdir(tmp_path)
        pid_dir = tmp_path / "data" / ".pids"
        pid_dir.mkdir(parents=True)
        (pid_dir / "calimero-node-1.pid").write_text("12345")

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_manager_instance = MagicMock()
        mock_manager_instance.client = mock_client
        mock_docker_manager.return_value = mock_manager_instance

        mock_binary_manager.side_effect = Exception("Binary manager failed")

        # Should not raise even though binary check failed
        result = get_running_node_names(fail_safe=True, silent=True)

        assert isinstance(result, set)

    def test_no_pid_dir_treated_as_no_binary_nodes(self, tmp_path, monkeypatch):
        """When PID directory doesn't exist, binary detection is skipped (not a failure).

        This is documented behavior: if no PID directory exists, there are no
        binary processes to detect, so it's treated as 'no binary nodes running'
        rather than a detection failure.
        """
        monkeypatch.chdir(tmp_path)
        # Don't create PID directory - should not cause binary_check_failed

        with patch("merobox.commands.nuke.DockerManager") as mock_docker_manager:
            # Make Docker fail
            mock_docker_manager.side_effect = Exception("Docker unavailable")

            # Without PID directory, only Docker fails, so no NodeDetectionError
            # (binary detection skipped, not failed)
            result = get_running_node_names(fail_safe=True, silent=True)
            assert isinstance(result, set)
