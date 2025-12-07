from pathlib import Path
from unittest.mock import mock_open, patch

from merobox.commands.config_utils import apply_near_devnet_config_to_file


def test_apply_near_devnet_config_shared():
    # Mock initial TOML content
    initial_toml = """
    [context.config]
    # empty
    """

    rpc_url = "http://localhost:3030"
    contract_id = "calimero.test.near"
    account_id = "node1.test.near"
    pub_key = "ed25519:PUB"
    sec_key = "ed25519:SEC"

    # Mock toml library
    with patch("merobox.commands.config_utils.toml") as mock_toml:
        # Return a dict structure simulating loaded toml
        mock_toml.load.return_value = {"context": {"config": {}}}

        with patch("builtins.open", mock_open(read_data=initial_toml)):
            with patch("pathlib.Path.exists", return_value=True):

                success = apply_near_devnet_config_to_file(
                    Path("/tmp/config.toml"),
                    "node1",
                    rpc_url,
                    contract_id,
                    account_id,
                    pub_key,
                    sec_key,
                )

                assert success is True

                # Check what was passed to dump
                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                # Assertions
                near_cfg = config_dict["context"]["config"]["near"]
                assert near_cfg["network"] == "local"
                assert near_cfg["contract_id"] == contract_id
                assert near_cfg["signer"] == "self"

                local_signer = config_dict["context"]["config"]["signer"]["self"][
                    "near"
                ]["local"]
                assert local_signer["rpc_url"] == rpc_url
                assert local_signer["account_id"] == account_id
                assert local_signer["secret_key"] == sec_key
