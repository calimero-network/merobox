"""Host writes and deletes refuse symlinks a checked-out repo could plant."""

import os

import pytest

from merobox.commands import safe_fs


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """cwd is `<tmp>/repo`; `<tmp>/victim` is a file outside it."""
    (tmp_path / "repo").mkdir()
    (tmp_path / "victim").write_text("precious\n")
    monkeypatch.chdir(tmp_path / "repo")
    return tmp_path


def test_open_for_write_refuses_a_symlinked_directory(repo):
    os.symlink(repo, repo / "repo" / "data")
    with pytest.raises(safe_fs.SymlinkedPathError):
        safe_fs.open_for_write("data/victim")
    assert (repo / "victim").read_text() == "precious\n"


def test_open_for_write_refuses_a_symlinked_file(repo):
    os.symlink(repo / "victim", repo / "repo" / "out.log")
    with pytest.raises(OSError):
        safe_fs.open_for_write("out.log")
    assert (repo / "victim").read_text() == "precious\n"


def test_open_for_write_writes_a_plain_path(repo):
    os.makedirs("data/node")
    with safe_fs.open_for_write("data/node/x.log") as f:
        f.write("ok")
    assert (repo / "repo" / "data" / "node" / "x.log").read_text() == "ok"


def test_open_for_write_checks_below_the_root_too(repo):
    root = repo / "checkout"
    (root / "sub").mkdir(parents=True)
    os.symlink(repo, root / "sub" / "link")
    with pytest.raises(safe_fs.SymlinkedPathError):
        safe_fs.open_for_write(root / "sub" / "link" / "victim", root=root)


def test_remove_refuses_a_symlinked_parent(repo):
    os.symlink(repo, repo / "repo" / "pids")
    with pytest.raises(safe_fs.SymlinkedPathError):
        safe_fs.remove("pids/victim")
    assert (repo / "victim").exists()


def test_rmtree_refuses_a_symlink(repo):
    (repo / "keep").mkdir()
    (repo / "keep" / "f").write_text("x")
    os.makedirs("data")
    os.symlink(repo / "keep", repo / "repo" / "data" / "calimero-node-1")
    with pytest.raises(safe_fs.SymlinkedPathError):
        safe_fs.rmtree("data/calimero-node-1")
    assert (repo / "keep" / "f").exists()


def test_copy_file_refuses_a_symlinked_destination(repo):
    (repo / "repo" / "src.toml").write_text("new")
    os.symlink(repo / "victim", repo / "repo" / "config.toml")
    with pytest.raises(OSError):
        safe_fs.copy_file("src.toml", "config.toml")
    assert (repo / "victim").read_text() == "precious\n"


@pytest.fixture
def no_precheck(monkeypatch):
    """Model a component swapped for a symlink after any lstat check: the open,
    remove or rmtree must refuse it on its own."""
    monkeypatch.setattr(safe_fs, "refuse_symlinks", lambda *a, **k: None)


def test_open_refuses_a_symlinked_directory_without_the_precheck(repo, no_precheck):
    os.symlink(repo, repo / "repo" / "data")
    with pytest.raises(OSError):
        safe_fs.open_for_write("data/victim")
    assert (repo / "victim").read_text() == "precious\n"


def test_remove_refuses_a_symlinked_parent_without_the_precheck(repo, no_precheck):
    os.symlink(repo, repo / "repo" / "pids")
    with pytest.raises(OSError):
        safe_fs.remove("pids/victim")
    assert (repo / "victim").exists()


def test_rmtree_refuses_a_symlink_without_the_precheck(repo, no_precheck):
    (repo / "keep").mkdir()
    (repo / "keep" / "f").write_text("x")
    os.symlink(repo / "keep", repo / "repo" / "node")
    with pytest.raises(OSError):
        safe_fs.rmtree("node")
    assert (repo / "keep" / "f").exists()


def test_rmtree_removes_links_inside_without_following_them(repo):
    (repo / "keep").mkdir()
    (repo / "keep" / "f").write_text("x")
    (repo / "repo" / "node" / "sub").mkdir(parents=True)
    os.symlink(repo / "keep", repo / "repo" / "node" / "sub" / "link")
    os.symlink(repo / "keep" / "f", repo / "repo" / "node" / "flink")
    safe_fs.rmtree("node")
    assert not (repo / "repo" / "node").exists()
    assert (repo / "keep" / "f").exists()


def test_makedirs_refuses_a_symlinked_component(repo, no_precheck):
    os.symlink(repo, repo / "repo" / "data")
    with pytest.raises(OSError):
        safe_fs.makedirs("data/a/b")
    assert not (repo / "a").exists()


def test_makedirs_creates_nested_directories(repo):
    safe_fs.makedirs("data/a/b", mode=0o700)
    assert (repo / "repo" / "data" / "a" / "b").is_dir()
