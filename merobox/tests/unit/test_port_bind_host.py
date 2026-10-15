"""Published container ports bind to loopback unless the operator opts in."""

from unittest.mock import MagicMock, patch

import docker
import pytest

from merobox.commands.constants import P2P_PORT_BINDING, RPC_PORT_BINDING
from merobox.commands.manager import DockerManager


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
