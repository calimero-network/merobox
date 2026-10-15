"""Published container ports bind to loopback unless the operator opts in."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import docker
import pytest
from click.testing import CliRunner

from merobox.commands.bootstrap.bootstrap import bootstrap
from merobox.commands.bootstrap.run.run import run_workflow
from merobox.commands.constants import P2P_PORT_BINDING, RPC_PORT_BINDING
from merobox.commands.manager import DockerManager
from merobox.commands.run import run as run_command


def _manager(client, **kwargs):
    with patch("docker.from_env", return_value=client):
        return DockerManager(enable_signal_handlers=False, **kwargs)


def _recording_client():
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("Not found")
    configs = []

    def record(**kwargs):
        configs.append(kwargs)
        container = MagicMock()
        container.status = "running"
        container.attrs = {"NetworkSettings": {"Ports": {}}, "Config": {"Env": []}}
        return container

    client.containers.run.side_effect = record
    return client, configs


@pytest.fixture(autouse=True)
def _no_config_writes():
    with patch("merobox.commands.manager.apply_config_value", return_value=True):
        yield


@pytest.mark.parametrize(
    "kwargs, host", [({}, "127.0.0.1"), ({"bind_host": "0.0.0.0"}, "0.0.0.0")]
)
def test_node_ports_bind_to_chosen_host(kwargs, host):
    client, configs = _recording_client()
    manager = _manager(client, **kwargs)
    manager._ensure_image_pulled = MagicMock(return_value=True)

    assert manager.run_node("node-1", port=2430, rpc_port=2530)

    assert configs, "expected the init and node containers to be started"
    for config in configs:
        assert config["ports"] == {
            P2P_PORT_BINDING: (host, 2430),
            RPC_PORT_BINDING: (host, 2530),
        }


@pytest.mark.parametrize(
    "kwargs, host", [({}, "127.0.0.1"), ({"bind_host": "0.0.0.0"}, "0.0.0.0")]
)
def test_traefik_ports_bind_to_chosen_host(kwargs, host):
    client, configs = _recording_client()
    manager = _manager(client, **kwargs)
    manager._ensure_image_pulled = MagicMock(return_value=True)

    assert manager._start_traefik_container()

    assert configs[0]["ports"] == {"80/tcp": (host, 80), "8080/tcp": (host, 8080)}


def _running_stack_client(proxy_host_ip):
    proxy = MagicMock(status="running")
    proxy.attrs = {
        "HostConfig": {
            "PortBindings": {"80/tcp": [{"HostIp": proxy_host_ip, "HostPort": "80"}]}
        }
    }
    client = MagicMock()
    client.containers.get.side_effect = lambda name: (
        proxy if name == "proxy" else MagicMock(status="running")
    )
    return client


@pytest.mark.parametrize(
    "proxy_host_ip, bind_host, recreated",
    [
        ("", None, True),
        ("0.0.0.0", None, True),
        ("127.0.0.1", "0.0.0.0", True),
        ("127.0.0.1", None, False),
        ("0.0.0.0", "0.0.0.0", False),
    ],
)
def test_running_proxy_is_recreated_only_when_bound_elsewhere(
    proxy_host_ip, bind_host, recreated
):
    manager = _manager(_running_stack_client(proxy_host_ip), bind_host=bind_host)
    manager._start_traefik_container = MagicMock(return_value=True)
    manager._ensure_auth_networks = MagicMock()

    with patch("merobox.commands.manager.time.sleep"):
        assert manager._start_auth_service_stack()

    assert manager._start_traefik_container.called is recreated


@pytest.mark.parametrize(
    "args, expected", [([], None), (["--bind-host", "0.0.0.0"], "0.0.0.0")]
)
def test_run_cli_passes_bind_host_to_docker_manager(args, expected):
    with patch("merobox.commands.run.DockerManager") as manager_cls:
        manager_cls.return_value.run_node.return_value = True
        result = CliRunner().invoke(run_command, args)

    assert result.exit_code == 0, result.output
    manager_cls.assert_called_once_with(bind_host=expected)


def test_bootstrap_cli_passes_bind_host_to_workflow(tmp_path):
    workflow = tmp_path / "wf.yml"
    workflow.touch()
    with patch(
        "merobox.commands.bootstrap.bootstrap.run_workflow_sync", return_value=True
    ) as run_workflow_sync:
        result = CliRunner().invoke(
            bootstrap, ["run", str(workflow), "--bind-host", "0.0.0.0"]
        )

    assert result.exit_code == 0, result.output
    assert run_workflow_sync.call_args.kwargs["bind_host"] == "0.0.0.0"


def test_run_workflow_passes_bind_host_to_docker_manager():
    with (
        patch(
            "merobox.commands.bootstrap.run.run.load_workflow_config",
            return_value={"nodes": {"count": 1}},
        ),
        patch("merobox.commands.manager.DockerManager") as manager_cls,
        patch("merobox.commands.bootstrap.run.run.WorkflowExecutor") as executor,
    ):
        executor.return_value.execute_workflow = AsyncMock(return_value=True)
        assert asyncio.run(run_workflow("wf.yml", bind_host="0.0.0.0"))

    manager_cls.assert_called_once_with(bind_host="0.0.0.0")


def test_run_cli_warns_that_bind_host_is_ignored_in_binary_mode():
    with patch("merobox.commands.run.BinaryManager") as manager_cls:
        manager_cls.return_value.run_node.return_value = True
        result = CliRunner().invoke(
            run_command, ["--no-docker", "--bind-host", "0.0.0.0"]
        )

    assert result.exit_code == 0, result.output
    assert "--bind-host is only supported in Docker mode" in result.output
    manager_cls.assert_called_once_with(binary_path=None)


def test_run_workflow_warns_that_bind_host_is_ignored_for_yaml_binary_mode():
    with (
        patch(
            "merobox.commands.bootstrap.run.run.load_workflow_config",
            return_value={"nodes": {"count": 1}, "no_docker": True},
        ),
        patch("merobox.commands.binary_manager.BinaryManager"),
        patch("merobox.commands.bootstrap.run.run.WorkflowExecutor") as executor,
        patch("merobox.commands.bootstrap.run.run.console") as console,
    ):
        executor.return_value.execute_workflow = AsyncMock(return_value=True)
        assert asyncio.run(run_workflow("wf.yml", bind_host="0.0.0.0"))

    printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
    assert "--bind-host is only supported in Docker mode" in printed


@pytest.mark.parametrize("value", ["127.0.0.1", "::1", "0.0.0.0", "::"])
def test_bind_host_accepts_loopback_and_all_interfaces(value):
    with patch("merobox.commands.run.DockerManager") as manager_cls:
        manager_cls.return_value.run_node.return_value = True
        result = CliRunner().invoke(run_command, ["--bind-host", value])

    assert result.exit_code == 0, result.output
    manager_cls.assert_called_once_with(bind_host=value)


@pytest.mark.parametrize("value", ["192.168.1.10", "127.0.0.2", "localhost", "0.0.0"])
@pytest.mark.parametrize("command", ["run", "bootstrap"])
def test_bind_host_rejects_addresses_merobox_cannot_reach(value, command, tmp_path):
    workflow = tmp_path / "wf.yml"
    workflow.touch()
    cli, args = (
        (run_command, ["--bind-host", value])
        if command == "run"
        else (bootstrap, ["run", str(workflow), "--bind-host", value])
    )
    with (
        patch("merobox.commands.run.DockerManager") as manager_cls,
        patch("merobox.commands.bootstrap.bootstrap.run_workflow_sync") as workflow_run,
    ):
        result = CliRunner().invoke(cli, args)

    assert result.exit_code == 2, result.output
    assert "Invalid value for '--bind-host'" in result.output
    manager_cls.assert_not_called()
    workflow_run.assert_not_called()
