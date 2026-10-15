"""PoCs: a checked-out repo plants symlinks where merobox writes or deletes."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands import config_utils
from merobox.commands.binary_manager import BinaryManager
from merobox.commands.bootstrap.steps.install import InstallApplicationStep
from merobox.commands.bootstrap.steps.node_exec import NodeExecStep
from merobox.commands.bootstrap.steps.restart import RestartContainerStep
from merobox.commands.manager import _dump_container_log, _dump_container_state

nuke = importlib.import_module("merobox.commands.nuke")

PRECIOUS = "precious\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """cwd is the checkout `<tmp>/repo`; `<tmp>/outside` holds the victim file."""
    (tmp_path / "repo").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "victim").write_text(PRECIOUS)
    monkeypatch.chdir(tmp_path / "repo")
    return tmp_path


def victim(repo):
    return (repo / "outside" / "victim").read_text()


def link_dir(repo, name):
    """Plant `<cwd>/<name>` as a symlink to the outside directory."""
    path = repo / "repo" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(repo / "outside", path)


def test_node_exec_input_files_do_not_follow_a_symlink(repo):
    home = repo / "repo" / "data" / "n"
    home.mkdir(parents=True)
    os.symlink(repo / "outside", home / "x")
    with pytest.raises(OSError):
        NodeExecStep._write_files({"/app/data/x/victim": "pwned"}, str(home))
    assert victim(repo) == PRECIOUS


def test_install_copy_does_not_follow_a_symlink(repo):
    (repo / "repo" / "data" / "n").mkdir(parents=True)
    os.symlink(repo / "outside" / "victim", repo / "repo" / "data" / "n" / "app.mpk")
    (repo / "repo" / "bundle").mkdir()
    (repo / "repo" / "bundle" / "app.mpk").write_text("pwned")
    step = InstallApplicationStep(
        {"type": "install_application", "node": "n", "path": "x"}, host_root=None
    )
    assert step._prepare_container_path("n", "bundle/app.mpk") is None
    assert victim(repo) == PRECIOUS


def test_container_log_dump_does_not_follow_a_symlink(repo):
    link_dir(repo, "data/container-logs")
    container = MagicMock()
    container.logs.return_value = b"pwned"
    assert not _dump_container_log(container, "victim", "data/container-logs")
    os.remove(repo / "outside" / "victim")
    container.attrs = {"State": {}}
    assert _dump_container_state(container, "victim", "data/container-logs") is None
    assert not (repo / "outside" / "victim.state.json").exists()


def test_restart_log_snapshot_does_not_follow_a_symlink(repo, monkeypatch):
    link_dir(repo, "docker-logs")
    monkeypatch.delenv("MEROBOX_RESTART_LOG_DIR", raising=False)
    monkeypatch.delenv("MEROBOX_PRE_RESTART_LOG_DIR", raising=False)
    container = MagicMock()
    container.logs.return_value = b"pwned"
    RestartContainerStep._snapshot_logs(container, "n", phase="pre")
    assert os.listdir(repo / "outside") == ["victim"]


def test_binary_pid_files_do_not_follow_a_symlink(repo):
    link_dir(repo, "data/.pids")
    manager = BinaryManager(
        binary_path="/usr/bin/true", require_binary=False, enable_signal_handlers=False
    )
    (repo / "outside" / "n.pid").write_text("1")
    with pytest.raises(OSError):
        manager._save_pid("victim", 4242)
    assert victim(repo) == PRECIOUS
    assert manager._load_pid("n") is None


@pytest.mark.parametrize(
    "apply",
    [
        lambda path: config_utils.apply_bootstrap_nodes(path, "n", ["/ip4/1/tcp/1"]),
        lambda path: config_utils.apply_config_value(path, "n", "a.b", 1),
        lambda path: config_utils.apply_e2e_defaults(path, "n", "wf"),
    ],
)
def test_node_config_writes_do_not_follow_a_symlink(repo, apply):
    (repo / "outside" / "victim").write_text("[a]\nb = 0\n")
    (repo / "repo" / "n").mkdir()
    os.symlink(repo / "outside" / "victim", repo / "repo" / "n" / "config.toml")
    assert not apply(str(repo / "repo" / "n" / "config.toml"))
    assert victim(repo) == "[a]\nb = 0\n"


def test_nuke_never_deletes_through_a_symlinked_node_dir(repo):
    (repo / "repo" / "data").mkdir()
    os.symlink(repo / "outside", repo / "repo" / "data" / "calimero-node-1")
    for data_dir in nuke.find_calimero_data_dirs():
        nuke.nuke_all_data_dirs([data_dir])
    nuke.nuke_all_data_dirs(["data/calimero-node-1"])
    assert victim(repo) == PRECIOUS


def test_docker_config_copy_does_not_follow_a_symlink(repo):
    from merobox.commands.manager import DockerManager

    node_dir = repo / "repo" / "data" / "n" / "n"
    node_dir.mkdir(parents=True)
    os.symlink(repo / "outside" / "victim", node_dir / "config.toml")
    (repo / "repo" / "c.toml").write_text("pwned")
    manager = DockerManager.__new__(DockerManager)
    manager.client = MagicMock()
    with patch.object(manager, "_ensure_image_pulled", return_value=True):
        manager.run_node("n", config_path="c.toml")
    assert victim(repo) == PRECIOUS


def test_binary_config_copy_does_not_follow_a_symlink(repo):
    node_dir = repo / "repo" / "data" / "n" / "n" / "n"
    node_dir.mkdir(parents=True)
    os.symlink(repo / "outside" / "victim", node_dir / "config.toml")
    (repo / "repo" / "c.toml").write_text("pwned")
    manager = BinaryManager(
        binary_path="/usr/bin/true", require_binary=False, enable_signal_handlers=False
    )
    with patch("merobox.commands.binary_manager.subprocess"):
        manager.run_node("n", config_path="c.toml")
    assert victim(repo) == PRECIOUS
