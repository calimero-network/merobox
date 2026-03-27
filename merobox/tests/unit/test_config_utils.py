from pathlib import Path
from unittest.mock import mock_open, patch

from merobox.commands.config_utils import (
    apply_bootstrap_nodes,
    apply_e2e_defaults,
    set_nested_config,
)


def test_set_nested_config():
    """Test set_nested_config sets nested values correctly."""
    config = {}
    set_nested_config(config, "bootstrap.nodes", ["addr1", "addr2"], log=False)
    assert config["bootstrap"]["nodes"] == ["addr1", "addr2"]

    # Test deeply nested
    set_nested_config(config, "context.config.near.network", "local", log=False)
    assert config["context"]["config"]["near"]["network"] == "local"


def test_set_nested_config_existing_keys():
    """Test set_nested_config preserves existing keys."""
    config = {"bootstrap": {"timeout": 30}}
    set_nested_config(config, "bootstrap.nodes", ["addr1"], log=False)

    assert config["bootstrap"]["timeout"] == 30
    assert config["bootstrap"]["nodes"] == ["addr1"]


def test_apply_bootstrap_nodes():
    """Test apply_bootstrap_nodes applies config correctly."""
    initial_config = {"discovery": {"mdns": True}}
    bootstrap_nodes = ["/ip4/127.0.0.1/tcp/2428", "/ip4/127.0.0.1/tcp/2429"]

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = initial_config.copy()

        with patch("builtins.open", mock_open()):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.chmod"),
                patch("pathlib.Path.stat"),
            ):
                result = apply_bootstrap_nodes(
                    Path("/tmp/config.toml"), "node1", bootstrap_nodes
                )

                assert result is True

                # Check what was passed to dump
                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                assert config_dict["bootstrap"]["nodes"] == bootstrap_nodes


def test_apply_bootstrap_nodes_file_not_found():
    """Test apply_bootstrap_nodes handles missing file."""
    with patch("pathlib.Path.exists", return_value=False):
        result = apply_bootstrap_nodes(Path("/missing/config.toml"), "node1", [])
        assert result is False


def test_apply_e2e_defaults():
    """Test apply_e2e_defaults applies correct config."""
    initial_config = {}

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = initial_config.copy()

        with patch("builtins.open", mock_open()):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.chmod"),
                patch("pathlib.Path.stat"),
            ):
                result = apply_e2e_defaults(
                    Path("/tmp/config.toml"),
                    "node1",
                    workflow_id="test-123",
                )

                assert result is True

                # Check what was passed to dump
                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                # Verify e2e defaults are applied (bootstrap, discovery, sync only)
                assert config_dict["bootstrap"]["nodes"] == []
                assert (
                    config_dict["discovery"]["rendezvous"]["namespace"]
                    == "calimero/merobox-tests/test-123"
                )
                assert config_dict["discovery"]["mdns"] is True
                assert config_dict["sync"]["timeout_ms"] == 30000
                assert config_dict["sync"]["interval_ms"] == 500
                assert config_dict["sync"]["frequency_ms"] == 1000


def test_apply_e2e_defaults_generates_workflow_id():
    """Test apply_e2e_defaults generates workflow_id when not provided."""
    initial_config = {}

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = initial_config.copy()

        with patch("builtins.open", mock_open()):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.chmod"),
                patch("pathlib.Path.stat"),
            ):
                result = apply_e2e_defaults(
                    Path("/tmp/config.toml"),
                    "node1",
                    workflow_id=None,  # Should generate one
                )

                assert result is True

                # Check what was passed to dump
                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                # Verify a workflow ID was generated in the namespace
                namespace = config_dict["discovery"]["rendezvous"]["namespace"]
                assert namespace.startswith("calimero/merobox-tests/")
                assert len(namespace.split("/")[-1]) == 8  # UUID[:8]


def test_apply_e2e_defaults_file_not_found():
    """Test apply_e2e_defaults handles missing file."""
    with patch("pathlib.Path.exists", return_value=False):
        result = apply_e2e_defaults(Path("/missing/config.toml"), "node1")
        assert result is False
