import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.config_utils import apply_near_devnet_config_to_file
from merobox.commands.manager import DockerManager
from merobox.commands.near.client import NearDevnetClient
from merobox.commands.near.sandbox import SandboxManager

# Import modules under test


@pytest.fixture
def mock_manager():
    return MagicMock(spec=DockerManager)


def test_near_devnet_from_config_when_cli_omitted(mock_manager):
    """When CLI does not set near_devnet (None), YAML near_devnet is used."""
    config = {"near_devnet": True, "nodes": {}}
    executor = WorkflowExecutor(config, mock_manager, near_devnet=None)
    assert executor.near_devnet is True


def test_near_devnet_cli_overrides_config(mock_manager):
    """When CLI sets near_devnet (e.g. --enable-relayer), CLI takes precedence over YAML."""
    config = {"near_devnet": True, "nodes": {}}
    executor = WorkflowExecutor(config, mock_manager, near_devnet=False)
    assert executor.near_devnet is False


def test_resolve_contracts_dir_calls_ensure_when_none(mock_manager):
    """When contracts_dir is None, _resolve_contracts_dir calls ensure_calimero_near_contracts."""
    config = {"nodes": {}}
    executor = WorkflowExecutor(
        config, mock_manager, near_devnet=True, contracts_dir=None
    )
    with patch(
        "merobox.commands.bootstrap.run.executor.ensure_calimero_near_contracts"
    ) as mock_ensure:
        mock_ensure.return_value = "/tmp/contracts"
        contracts_dir, ctx_path, proxy_path = executor._resolve_contracts_dir()
    mock_ensure.assert_called_once()
    assert contracts_dir == "/tmp/contracts"
    assert ctx_path.endswith("calimero_context_config_near.wasm")
    assert proxy_path.endswith("calimero_context_proxy_near.wasm")


@pytest.mark.asyncio
async def test_unique_credentials_generation(mock_manager):
    """Bug 1 Fix: Ensure run_multiple_nodes receives distinct credentials map."""
    config = {
        "name": "test",
        "nodes": {"count": 2, "prefix": "test-node", "image": "img"},
    }

    with patch("merobox.commands.near.sandbox.SandboxManager") as MockSandbox:
        executor = WorkflowExecutor(
            config, mock_manager, near_devnet=True, contracts_dir="/tmp"
        )

        # Mock sandbox interaction
        executor.sandbox = MockSandbox.return_value
        executor.sandbox.start = MagicMock()
        executor.sandbox.setup_calimero = AsyncMock(return_value="contract_id")
        executor.sandbox.get_rpc_url.return_value = "http://rpc"

        # Mock create_node_account to return unique values per node
        executor.sandbox.create_node_account = AsyncMock(
            side_effect=lambda n: {
                "account_id": f"{n}.near",
                "secret_key": "sk",
                "public_key": "pk",
            }
        )
        executor.near_config = {"rpc_url": "u", "contract_id": "c"}

        await executor._start_nodes(restart=True)

        # Verification
        mock_manager.run_multiple_nodes.assert_called_once()
        _, kwargs = mock_manager.run_multiple_nodes.call_args
        configs = kwargs.get("near_devnet_config")

        assert configs is not None
        assert "test-node-1" in configs
        assert "test-node-2" in configs
        assert configs["test-node-1"]["account_id"] == "test-node-1.near"
        assert configs["test-node-2"]["account_id"] == "test-node-2.near"


@pytest.mark.asyncio
async def test_non_restart_node_config_passing(mock_manager):
    """Ensure near config is passed when starting individual nodes."""
    config = {"nodes": {"node-1": {"image": "img"}}}

    with patch("merobox.commands.near.sandbox.SandboxManager") as MockSandbox:
        executor = WorkflowExecutor(
            config, mock_manager, near_devnet=True, contracts_dir="/tmp"
        )
        executor.sandbox = MockSandbox.return_value
        executor.sandbox.create_node_account = AsyncMock(
            return_value={"account_id": "acc"}
        )
        executor.near_config = {
            "rpc_url": "u",
            "contract_id": "c",
            "public_key": "pk",
            "secret_key": "sk",
        }

        # Simulate node NOT running to trigger start path
        executor._is_node_running = MagicMock(return_value=False)

        await executor._start_nodes(restart=False)

        mock_manager.run_node.assert_called_once()
        _, kwargs = mock_manager.run_node.call_args
        assert kwargs.get("near_devnet_config") is not None
        assert kwargs["near_devnet_config"]["account_id"] == "acc"


# ==========================================
# Sandbox Platform & Networking Tests
# ==========================================


def test_sandbox_platform_detection_mac_arm():
    """Ensure macOS ARM64 resolves to the correct S3 path component."""
    with (
        patch("platform.system", return_value="Darwin"),
        patch("platform.machine", return_value="arm64"),
    ):

        mgr = SandboxManager(home_dir="/tmp/sb")
        full_name = mgr._get_platform_full_name()

        assert full_name == "Darwin-arm64"
        assert "Darwin-arm64" in mgr._get_platform_url()


def test_sandbox_platform_detection_linux_x64():
    """Ensure Linux x86_64 resolves correctly."""
    with (
        patch("platform.system", return_value="Linux"),
        patch("platform.machine", return_value="x86_64"),
    ):

        mgr = SandboxManager(home_dir="/tmp/sb")
        full_name = mgr._get_platform_full_name()

        assert full_name == "Linux-x86_64"


def test_sandbox_platform_unsupported():
    """Ensure unsupported platforms raise explicit errors."""
    with patch("platform.system", return_value="Windows"):
        with patch("pathlib.Path.mkdir"):  # Suppress dir creation in init
            try:
                SandboxManager(home_dir="/tmp/sb")
                raise AssertionError("Should have raised exception for Windows")
            except Exception as e:
                assert "Unsupported platform" in str(e)


def test_sandbox_rpc_url_generation():
    """Verify RPC URL generation for host vs docker contexts."""
    # We mock platform to Linux to test the specific logic inside get_rpc_url
    with (
        patch("platform.system", return_value="Linux"),
        patch("platform.machine", return_value="x86_64"),
    ):

        mgr = SandboxManager(home_dir="/tmp/sb")
        mgr.rpc_port = 3030

        # Case 1: Host machine access (localhost)
        assert mgr.get_rpc_url(for_docker=False) == "http://localhost:3030"

        # Case 2: Docker container access (host.docker.internal)
        # Note: The manager code adds extra_hosts, so this must return host.docker.internal
        assert mgr.get_rpc_url(for_docker=True) == "http://host.docker.internal:3030"


# ==========================================
# Configuration Robustness Tests
# ==========================================


def test_apply_config_empty_file():
    """Ensure config injection handles completely empty config files by creating structure."""

    # Mock reading an empty file
    with (
        patch("merobox.commands.config_utils.toml") as mock_toml,
        patch("builtins.open", mock_open(read_data="")),
        patch("pathlib.Path.exists", return_value=True),
    ):

        # load returns empty dict for empty file
        mock_toml.load.return_value = {}

        success = apply_near_devnet_config_to_file(
            Path("config.toml"), "node1", "http://rpc", "contract", "acc", "pk", "sk"
        )

        assert success is True

        # Verify write structure
        args, _ = mock_toml.dump.call_args
        config_out = args[0]

        # Ensure deep keys were created
        assert "context" in config_out
        assert "config" in config_out["context"]
        assert "near" in config_out["context"]["config"]
        assert config_out["context"]["config"]["near"]["contract_id"] == "contract"

        signer_path = config_out["context"]["config"]["signer"]["self"]["near"]["local"]
        assert signer_path["secret_key"] == "sk"


def test_apply_config_partial_existing():
    """Ensure injection merges with existing config without destroying other keys."""

    existing_config = {
        "http": {"port": 8080},  # Should be preserved
        "context": {
            "config": {"near": {"network": "testnet"}}  # Should be overwritten
        },
    }

    with (
        patch("merobox.commands.config_utils.toml") as mock_toml,
        patch("builtins.open", mock_open()),
        patch("pathlib.Path.exists", return_value=True),
    ):

        mock_toml.load.return_value = existing_config

        apply_near_devnet_config_to_file(
            Path("config.toml"), "node1", "u", "c", "a", "p", "s"
        )

        args, _ = mock_toml.dump.call_args
        config_out = args[0]

        # Verify preservation
        assert config_out["http"]["port"] == 8080
        # Verify overwrite
        assert config_out["context"]["config"]["near"]["network"] == "local"


# ==========================================
# Client Logic Tests
# ==========================================


@patch("merobox.commands.near.client.Account")
@patch("merobox.commands.near.client.create_function_call_action")
def test_client_call_arg_encoding(mock_action, mock_account):
    """Verify arguments are correctly encoded to bytes before transaction creation."""
    client = NearDevnetClient("url", "acc", "pk")
    mock_account_instance = mock_account.return_value
    mock_account_instance.sign_and_submit_tx = AsyncMock()

    # Test 1: Dict input (should become JSON bytes)
    dict_args = {"foo": "bar"}
    # We mock sign_and_submit_tx to simply return
    import asyncio

    loop = asyncio.new_event_loop()

    # We need to patch the async calls or run them
    async def run_call(args):
        await client.call("contract", "method", args)

    loop.run_until_complete(run_call(dict_args))

    # Check that create_function_call_action received bytes
    call_args = mock_action.call_args
    # second positional arg is args
    passed_args = call_args[0][1]
    assert isinstance(passed_args, bytes)
    assert json.loads(passed_args) == dict_args

    # Test 2: String input (should become utf-8 bytes)
    str_args = "some_string_val"
    loop.run_until_complete(run_call(str_args))

    call_args = mock_action.call_args
    passed_args = call_args[0][1]
    assert isinstance(passed_args, bytes)
    assert passed_args == b"some_string_val"

    loop.close()
