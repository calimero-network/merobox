import os
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from merobox.commands.binary_manager import BinaryManager


@patch("merobox.commands.binary_manager.apply_near_devnet_config_to_file")
@patch("subprocess.Popen")
@patch("subprocess.run")
def test_run_node_calls_shared_config_utils(mock_run, mock_popen, mock_apply_config):
    # Setup
    manager = BinaryManager(binary_path="/bin/merod", require_binary=False)

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
    bm = BinaryManager(binary_path="merod", require_binary=False)

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
