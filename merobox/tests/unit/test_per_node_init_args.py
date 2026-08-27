"""Per-node `merod init` flags (`nodes.init_args`).

The rest of the `nodes:` block configures every node identically, so a posture
that only ONE node should have had no way to be expressed. `merod init` is built
literally in the managers, and the only flag a workflow could influence was
`--auth-mode`.

The motivating case is `merod init --no-account-root`: a node whose account root
lives on another machine. Testing it needs one node in that posture and one
holding the root, in the same workflow.
"""

from unittest.mock import MagicMock, patch

import docker

from merobox.commands.bootstrap.config import NodesConfig
from merobox.commands.manager import DockerManager

# The flags the managers always pass. `init_args` must never displace these.
_MANDATORY = ["--server-port", "--swarm-port"]


def _capture(container_configs):
    def capture_run_config(**kwargs):
        container_configs.append(kwargs)
        c = MagicMock()
        c.status = "running"
        c.short_id = "abc123"
        c.attrs = {"NetworkSettings": {"Ports": {}}, "Config": {"Env": []}}
        return c

    return capture_run_config


def _init_command(container_configs):
    """The init container's command — it is the one run with detach=False."""
    inits = [c for c in container_configs if c.get("detach") is False]
    assert len(inits) == 1, f"expected exactly one init container, got {len(inits)}"
    return inits[0]["command"]


def test_the_schema_accepts_per_node_init_args():
    cfg = NodesConfig(count=2, init_args={"calimero-node-2": ["--no-account-root"]})
    assert cfg.init_args == {"calimero-node-2": ["--no-account-root"]}


def test_the_schema_accepts_a_workflow_with_none():
    """Absence is the ordinary case and must stay valid."""
    assert NodesConfig(count=2).init_args is None


@patch("docker.from_env")
def test_init_args_reach_the_init_command(mock_docker):
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager()
    manager._ensure_image_pulled = MagicMock(return_value=True)

    configs = []
    client.containers.run.side_effect = _capture(configs)
    client.containers.get.side_effect = docker.errors.NotFound("Not found")

    manager.run_node(
        "test-node", init_args=["--no-account-root", "--account-root", "ab" * 32]
    )

    cmd = _init_command(configs)
    assert "--no-account-root" in cmd
    assert "--account-root" in cmd
    assert cmd[cmd.index("--account-root") + 1] == "ab" * 32


@patch("docker.from_env")
def test_init_args_come_last_so_they_cannot_displace_the_ports(mock_docker):
    """Appended after the mandatory flags, never before.

    merobox assigns the ports and depends on them to reach the node afterwards.
    A workflow that could insert flags ahead of them — or worse, replace the
    command — would produce a node merobox cannot talk to, and the failure would
    read as the node being broken rather than as the workflow overriding it.
    """
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager()
    manager._ensure_image_pulled = MagicMock(return_value=True)

    configs = []
    client.containers.run.side_effect = _capture(configs)
    client.containers.get.side_effect = docker.errors.NotFound("Not found")

    manager.run_node("test-node", init_args=["--no-account-root"])

    cmd = _init_command(configs)
    for flag in _MANDATORY:
        assert flag in cmd, f"{flag} must survive init_args"
        assert cmd.index(flag) < cmd.index(
            "--no-account-root"
        ), f"{flag} must come before the workflow's own flags"


@patch("docker.from_env")
def test_a_node_with_no_init_args_is_unchanged(mock_docker):
    """The default path must not gain a stray argument.

    Every existing workflow goes through here, so a `None` that leaked into the
    command as an empty string or a literal "None" would break all of them.
    """
    client = MagicMock()
    mock_docker.return_value = client
    manager = DockerManager()
    manager._ensure_image_pulled = MagicMock(return_value=True)

    configs = []
    client.containers.run.side_effect = _capture(configs)
    client.containers.get.side_effect = docker.errors.NotFound("Not found")

    manager.run_node("test-node")

    cmd = _init_command(configs)
    assert "None" not in cmd
    assert "" not in cmd
    assert cmd[-1] in (str(p) for p in cmd), "sanity: command is all strings"
    for flag in _MANDATORY:
        assert flag in cmd
