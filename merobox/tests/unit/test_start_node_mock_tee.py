"""Tests for the `mock_tee` option on the start_node step.

When a `start_node` step sets `mock_tee: true`, the launched `merod` must be
started with `merod run --mock-tee` (mock TEE attestation for local testing).
The flag must be appended in BOTH the native (binary) argv and the Docker
container run command. When the option is absent the flag must NOT appear.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import docker
import pytest

from merobox.commands.binary_manager import BinaryManager
from merobox.commands.bootstrap.steps.start_node import StartNodeStep
from merobox.commands.manager import DockerManager


def _make_step(config_extra=None, *, start_node_fn=None):
    config = {"type": "start_node", "nodes": ["node-1"], "wait_for_ready": False}
    if config_extra:
        config.update(config_extra)
    return StartNodeStep(
        config,
        manager=MagicMock(),
        workflow_config={"nodes": {"count": 1}},
        start_node_fn=start_node_fn,
    )


# ---------------------------------------------------------------------------
# Step level: the option is validated and forwarded to the start callable.
# ---------------------------------------------------------------------------


def test_mock_tee_forwarded_to_start_fn_when_true():
    start_fn = AsyncMock(return_value=True)
    step = _make_step({"mock_tee": True}, start_node_fn=start_fn)

    assert asyncio.run(step.execute({}, {})) is True
    assert start_fn.await_args.kwargs.get("mock_tee") is True


def test_mock_tee_defaults_to_false_when_absent():
    start_fn = AsyncMock(return_value=True)
    step = _make_step(start_node_fn=start_fn)

    assert asyncio.run(step.execute({}, {})) is True
    assert start_fn.await_args.kwargs.get("mock_tee") is False


def test_mock_tee_rejects_non_boolean():
    with pytest.raises(ValueError, match="mock_tee"):
        _make_step({"mock_tee": "yes"}, start_node_fn=AsyncMock(return_value=True))


# ---------------------------------------------------------------------------
# Docker path: --mock-tee lands in the container run command.
# ---------------------------------------------------------------------------


def _capture_run_config_factory(container_configs):
    def capture_run_config(**kwargs):
        container_configs.append(kwargs)
        c = MagicMock()
        c.status = "running"
        c.short_id = "abc123"
        c.attrs = {"NetworkSettings": {"Ports": {}}, "Config": {"Env": []}}
        return c

    return capture_run_config


def _run_docker_node(mock_tee):
    """Drive DockerManager.run_node and return the main container command."""
    with patch("docker.from_env") as mock_docker:
        client = MagicMock()
        mock_docker.return_value = client
        manager = DockerManager(enable_signal_handlers=False)
        manager._ensure_image_pulled = MagicMock(return_value=True)

        container_configs = []
        client.containers.run.side_effect = _capture_run_config_factory(
            container_configs
        )
        client.containers.get.side_effect = docker.errors.NotFound("Not found")

        manager.run_node("test-node", mock_tee=mock_tee)

    main_configs = [c for c in container_configs if c.get("detach") is True]
    assert main_configs, "Expected a main container config"
    return main_configs[0]["command"]


def test_docker_command_includes_mock_tee_when_true():
    command = _run_docker_node(mock_tee=True)
    assert "--mock-tee" in command
    # Flag is appended after the `run` subcommand, not before it.
    assert command.index("--mock-tee") > command.index("run")


def test_docker_command_omits_mock_tee_when_false():
    command = _run_docker_node(mock_tee=False)
    assert "--mock-tee" not in command


# ---------------------------------------------------------------------------
# Native (binary) path: --mock-tee lands in the merod run argv.
# ---------------------------------------------------------------------------


def _run_binary_node(tmp_path, mock_tee):
    """Drive BinaryManager.run_node and return the argv handed to Popen."""
    manager = BinaryManager(
        binary_path="merod", require_binary=False, enable_signal_handlers=False
    )

    data_dir = tmp_path / "data"
    node_dir = data_dir / "test-node"
    node_dir.mkdir(parents=True, exist_ok=True)
    # Pre-create config.toml so run_node skips the `merod init` subprocess.
    (node_dir / "config.toml").write_text("")

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        proc = MagicMock()
        proc.pid = 4242
        return proc

    manager._load_pid = MagicMock(return_value=None)
    manager._save_pid = MagicMock()
    manager._is_process_running = MagicMock(return_value=True)

    with (
        patch("merobox.commands.binary_manager.subprocess.Popen", fake_popen),
        patch("merobox.commands.binary_manager.time.sleep"),
        patch(
            "merobox.commands.binary_manager.socket.create_connection",
            side_effect=OSError("not listening"),
        ),
    ):
        result = manager.run_node(
            "test-node", data_dir=str(data_dir), mock_tee=mock_tee
        )

    assert result is True
    assert "cmd" in captured, "Popen was never invoked"
    return captured["cmd"]


def test_binary_argv_includes_mock_tee_when_true(tmp_path):
    cmd = _run_binary_node(tmp_path, mock_tee=True)
    assert "--mock-tee" in cmd
    assert cmd.index("--mock-tee") > cmd.index("run")


def test_binary_argv_omits_mock_tee_when_false(tmp_path):
    cmd = _run_binary_node(tmp_path, mock_tee=False)
    assert "--mock-tee" not in cmd
