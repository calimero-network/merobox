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
