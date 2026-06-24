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


# ---------------------------------------------------------------------------
# Initial `nodes:` boot path: a per-node `mock_tee: true` in the workflow node
# definition must reach manager.run_node(mock_tee=True), so a TEE replica can
# boot mock from the start without a stop_node + start_node restart (the
# restart perturbed the gossipsub topic mesh and broke fleet-join admission).
# ---------------------------------------------------------------------------


def _run_start_nodes(node_cfg):
    """Drive WorkflowExecutor._start_nodes for one binary-mode node and return
    the kwargs handed to manager.run_node()."""
    from merobox.commands.bootstrap.run.executor import WorkflowExecutor

    manager = MagicMock()
    manager.binary_path = "merod"  # binary mode
    manager.run_node = MagicMock(return_value=True)

    config = {"name": "wf", "nodes": {"tee-replica": node_cfg}}
    executor = WorkflowExecutor(config, manager)
    # Force the "not already running" branch so run_node is actually invoked.
    executor._is_node_running = MagicMock(return_value=False)

    assert asyncio.run(executor._start_nodes(restart=False)) is True
    assert manager.run_node.call_count == 1
    return manager.run_node.call_args.kwargs


def test_nodes_boot_path_forwards_mock_tee_when_true():
    kwargs = _run_start_nodes({"port": 7081, "rpc_port": 7181, "mock_tee": True})
    assert kwargs.get("mock_tee") is True


def test_nodes_boot_path_defaults_mock_tee_false_when_absent():
    kwargs = _run_start_nodes({"port": 7081, "rpc_port": 7181})
    assert kwargs.get("mock_tee") is False


# ---------------------------------------------------------------------------
# start_node restart path (`_start_single_node`): a per-node `mock_tee: true`
# in the workflow node definition must survive a restart even when the
# `start_node` step itself does not set `mock_tee`, otherwise a restarted TEE
# replica silently comes back without `--mock-tee`.
# ---------------------------------------------------------------------------


def _run_start_single_node(
    node_name, *, node_config=None, nodes_config=None, mock_tee=False
):
    """Drive WorkflowExecutor._start_single_node and return the run_node kwargs."""
    from merobox.commands.bootstrap.run.executor import WorkflowExecutor

    manager = MagicMock()
    manager.binary_path = "merod"  # binary mode
    manager.run_node = MagicMock(return_value=True)

    config = {"name": "wf", "nodes": nodes_config or {}}
    executor = WorkflowExecutor(config, manager)
    executor._is_node_running = MagicMock(return_value=False)

    assert (
        asyncio.run(
            executor._start_single_node(
                node_name,
                node_config=node_config,
                nodes_config=nodes_config,
                mock_tee=mock_tee,
            )
        )
        is True
    )
    assert manager.run_node.call_count == 1
    return manager.run_node.call_args.kwargs


def test_start_single_node_honours_per_node_mock_tee_from_nodes_config():
    kwargs = _run_start_single_node(
        "tee-replica",
        nodes_config={
            "tee-replica": {"port": 7081, "rpc_port": 7181, "mock_tee": True}
        },
        mock_tee=False,
    )
    assert kwargs.get("mock_tee") is True


def test_start_single_node_honours_per_node_mock_tee_from_node_config():
    kwargs = _run_start_single_node(
        "tee-replica",
        node_config={"port": 7081, "rpc_port": 7181, "mock_tee": True},
        mock_tee=False,
    )
    assert kwargs.get("mock_tee") is True


def test_start_single_node_mock_tee_false_when_unset_everywhere():
    kwargs = _run_start_single_node(
        "tee-replica",
        nodes_config={"tee-replica": {"port": 7081, "rpc_port": 7181}},
        mock_tee=False,
    )
    assert kwargs.get("mock_tee") is False
