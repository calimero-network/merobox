import asyncio
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

# Import the modules under test explicitly.
# This ensures that 'merobox.commands.near' and its submodules are loaded
# before @patch tries to resolve these paths.
from merobox.commands.near.client import NearDevnetClient
from merobox.commands.near.sandbox import SandboxManager

# We need ed25519.create_keypair() to return objects that have .to_bytes()
mock_sk = MagicMock()
mock_sk.to_bytes.return_value = b"secret_32_bytes_1234567890123456"
mock_pk = MagicMock()
mock_pk.to_bytes.return_value = b"public_32_bytes_1234567890123456"


# Mocking py-near dependencies
# This test ensures that wrapper around the `py-near` library correctly translates Merobox commands into `py-near` actions.
# 1. Verifes Key Generation:
# * When the client creates an account, the code correctly generates a local Ed25519 keypair using the ed25519 library.
# * Assert that the keys are formatted correctly as strings (e.g., starting with ed25519:...).
#
# 2. Verifies account creation logic - assert that client.create_account() calls the underlying
# `py-near Account.create_account()` method exactly once with the correct
# arguments (new account ID, and public key).
#
# 3. Verifies Contract Deployment:
# * simulate reading a WASM file from disk.
# * assert that `client.deploy_contract()` reads those bytes and passes them to the underlying `py-near Account.deploy_contract()` method.
@patch("merobox.commands.near.client.Account")
@patch("merobox.commands.near.client.ed25519")
def test_near_client_methods(mock_ed25519, mock_account):
    # Setup ed25519 mock to return bytes
    mock_ed25519.create_keypair.return_value = (mock_sk, mock_pk)

    # Mock Account instance
    mock_acc_instance = MagicMock()
    # Use AsyncMock for async methods so they can be awaited
    mock_acc_instance.create_account = AsyncMock()
    mock_acc_instance.deploy_contract = AsyncMock()
    mock_acc_instance.function_call = AsyncMock()
    mock_account.return_value = mock_acc_instance

    client = NearDevnetClient("http://url", "test.near", "ed25519:key")

    # Verify init called correctly
    mock_account.assert_called_with("test.near", "ed25519:key", "http://url")

    # Test Create Account
    loop = asyncio.new_event_loop()
    res = loop.run_until_complete(client.create_account("new.near"))
    loop.close()

    assert res["account_id"] == "new.near"
    assert res["public_key"].startswith("ed25519:")
    mock_acc_instance.create_account.assert_called_once()

    # Test Deploy
    with patch("builtins.open", mock_open(read_data=b"wasm")):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(client.deploy_contract("path"))
        loop.close()
        mock_acc_instance.deploy_contract.assert_called_with(b"wasm")


# This test ensures that Merobox can automatically set up the environment if the near-sandbox binary is missing.
# 1. Verifies Missing Binary Detection:
# * mock the filesystem (`pathlib.Path.exists`) to return `False` initially.
# * verify that this triggers the download logic.
#
# 2. Verifies Download Execution:
# * assert that `requests.get()` is called (which would perform the download in a real run).
# * assert that the code handles the HTTP response headers (checking content length).
#
# 3. Verifies Extraction:
# * assert that `tarfile.open()` is called, confirming that the code attempts to unpack the downloaded archive.
@patch("merobox.commands.near.sandbox.requests.get")
@patch("merobox.commands.near.sandbox.tarfile.open")
def test_sandbox_download_logic(mock_tar, mock_get):
    # Setup Request Mock
    mock_resp = MagicMock()
    mock_resp.headers = {"content-length": "1024"}
    mock_resp.iter_content.return_value = [b"bytes" * 10]  # Chunks of data
    mock_get.return_value = mock_resp

    mgr = SandboxManager(home_dir="/tmp/test")

    # Setup File Mock
    # Mock file.write to return an int (bytes written) for tqdm
    mock_file = MagicMock()
    mock_file.write.return_value = 5  # Return bytes written

    # Setup mock_open context manager to return our mock_file
    m_open = mock_open()
    m_open.return_value.__enter__.return_value = mock_file

    # Mock pathlib.Path.exists (Fail first to trigger download, then succeed)
    with patch("pathlib.Path.exists", side_effect=[False, True]):
        # Mock builtins.open
        with patch("builtins.open", m_open):
            # Mock pathlib.Path.unlink to avoid FileNotFoundError
            with patch("pathlib.Path.unlink") as mock_unlink:
                # Mock chmod
                with patch("pathlib.Path.chmod"):
                    mgr.ensure_binary()

    assert mock_get.called
    assert mock_tar.called
    # Ensure write was called
    mock_file.write.assert_called()
    # Ensure unlink was called (cleanup)
    mock_unlink.assert_called()
