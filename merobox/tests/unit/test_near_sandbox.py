import asyncio
import io
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

# Import the modules under test explicitly.
# This ensures that 'merobox.commands.near' and its submodules are loaded
# before @patch tries to resolve these paths.
from merobox.commands.near.client import NearDevnetClient
from merobox.commands.near.sandbox import SandboxManager
from merobox.commands.near.utils import safe_tar_extract

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


class TestSafeTarExtract:
    """Tests for safe_tar_extract to prevent zip-slip vulnerabilities."""

    def test_extracts_normal_file(self):
        """Normal files should be extracted correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                content = b"test content"
                tarinfo = tarfile.TarInfo(name="testfile.txt")
                tarinfo.size = len(content)
                tar.addfile(tarinfo, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                safe_tar_extract(tar, extract_path)

            assert (extract_path / "testfile.txt").exists()
            assert (extract_path / "testfile.txt").read_bytes() == b"test content"

    def test_extracts_nested_directory(self):
        """Nested directories within the archive should be extracted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                dirinfo = tarfile.TarInfo(name="subdir")
                dirinfo.type = tarfile.DIRTYPE
                dirinfo.mode = 0o755
                tar.addfile(dirinfo)

                content = b"nested content"
                tarinfo = tarfile.TarInfo(name="subdir/nested.txt")
                tarinfo.size = len(content)
                tar.addfile(tarinfo, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                safe_tar_extract(tar, extract_path)

            assert (extract_path / "subdir" / "nested.txt").exists()
            assert (
                extract_path / "subdir" / "nested.txt"
            ).read_bytes() == b"nested content"

    def test_rejects_path_traversal_with_dotdot(self):
        """Archives with '../' path traversal should raise RuntimeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                content = b"malicious"
                tarinfo = tarfile.TarInfo(name="../../../etc/passwd")
                tarinfo.size = len(content)
                tar.addfile(tarinfo, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                with pytest.raises(RuntimeError, match="Rejected path traversal"):
                    safe_tar_extract(tar, extract_path)

    def test_rejects_absolute_path(self):
        """Archives with absolute paths should raise RuntimeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                content = b"malicious"
                tarinfo = tarfile.TarInfo(name="/etc/passwd")
                tarinfo.size = len(content)
                tar.addfile(tarinfo, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                with pytest.raises(RuntimeError, match="Rejected path traversal"):
                    safe_tar_extract(tar, extract_path)

    def test_skips_symlinks(self):
        """Symlinks in the archive should be skipped, not extracted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name="evil_link")
                tarinfo.type = tarfile.SYMTYPE
                tarinfo.linkname = "/etc/passwd"
                tar.addfile(tarinfo)

                content = b"safe content"
                safe_info = tarfile.TarInfo(name="safe_file.txt")
                safe_info.size = len(content)
                tar.addfile(safe_info, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                safe_tar_extract(tar, extract_path)

            assert not (extract_path / "evil_link").exists()
            assert (extract_path / "safe_file.txt").exists()

    def test_skips_hardlinks(self):
        """Hardlinks in the archive should be skipped, not extracted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name="evil_hardlink")
                tarinfo.type = tarfile.LNKTYPE
                tarinfo.linkname = "/etc/passwd"
                tar.addfile(tarinfo)

                content = b"safe content"
                safe_info = tarfile.TarInfo(name="safe_file.txt")
                safe_info.size = len(content)
                tar.addfile(safe_info, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                safe_tar_extract(tar, extract_path)

            assert not (extract_path / "evil_hardlink").exists()
            assert (extract_path / "safe_file.txt").exists()

    def test_skips_block_device(self):
        """Block device files in the archive should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name="evil_block_device")
                tarinfo.type = tarfile.BLKTYPE
                tar.addfile(tarinfo)

                content = b"safe content"
                safe_info = tarfile.TarInfo(name="safe_file.txt")
                safe_info.size = len(content)
                tar.addfile(safe_info, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                safe_tar_extract(tar, extract_path)

            assert not (extract_path / "evil_block_device").exists()
            assert (extract_path / "safe_file.txt").exists()

    def test_skips_char_device(self):
        """Character device files in the archive should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name="evil_char_device")
                tarinfo.type = tarfile.CHRTYPE
                tar.addfile(tarinfo)

                content = b"safe content"
                safe_info = tarfile.TarInfo(name="safe_file.txt")
                safe_info.size = len(content)
                tar.addfile(safe_info, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                safe_tar_extract(tar, extract_path)

            assert not (extract_path / "evil_char_device").exists()
            assert (extract_path / "safe_file.txt").exists()

    def test_skips_fifo(self):
        """FIFO files in the archive should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_path = Path(tmpdir)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name="evil_fifo")
                tarinfo.type = tarfile.FIFOTYPE
                tar.addfile(tarinfo)

                content = b"safe content"
                safe_info = tarfile.TarInfo(name="safe_file.txt")
                safe_info.size = len(content)
                tar.addfile(safe_info, io.BytesIO(content))

            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                safe_tar_extract(tar, extract_path)

            assert not (extract_path / "evil_fifo").exists()
            assert (extract_path / "safe_file.txt").exists()
