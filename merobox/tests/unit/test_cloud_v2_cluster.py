"""ProcessCluster's command line, with no merod: Popen is replaced."""

import pytest

from merobox.cloud_v2 import cluster as cluster_mod
from merobox.cloud_v2.cluster import ProcessCluster


@pytest.fixture
def started(monkeypatch, tmp_path):
    """A cluster whose nodes are never spawned: answers the argv each got."""
    argv: dict[int, list[str]] = {}

    class FakePopen:
        def __init__(self, args, **_kwargs):
            argv[int(args[args.index("--node-id") + 1])] = list(args)

        def kill(self):
            pass

        def wait(self):
            pass

        def send_signal(self, _sig):
            pass

    monkeypatch.setattr(cluster_mod, "find_binary", lambda b=None: "/bin/merod")
    monkeypatch.setattr(cluster_mod, "run_command", lambda b: [b, "run"])
    monkeypatch.setattr(cluster_mod, "supports", lambda b, flag: False)
    monkeypatch.setattr(cluster_mod.subprocess, "Popen", FakePopen)

    def start(**kwargs):
        c = ProcessCluster(nodes=2, data_dir=str(tmp_path / "data"), **kwargs)
        for i in c.ids:
            c.start_node(i, wait=False)
        return c, argv

    return start


def test_settings_are_written_for_each_node_and_passed_as_settings(started):
    toml = "[outbound]\nenabled = true\n"
    c, argv = started(settings=toml)
    for i in c.ids:
        args = argv[i]
        assert args[:2] == ["/bin/merod", "run"]
        path = args[args.index("--settings") + 1]
        with open(path) as f:
            assert f.read() == toml
    assert argv[1][argv[1].index("--settings") + 1] != (
        argv[2][argv[2].index("--settings") + 1]
    )


def test_without_settings_no_flag_is_passed(started):
    _c, argv = started()
    assert all("--settings" not in args for args in argv.values())


def test_a_cluster_that_fails_to_start_is_stopped(monkeypatch, tmp_path):
    # A node that exits at once (merod refusing its --settings, say) must not
    # leave the others running, or the cluster's temporary data dir behind.
    from merobox.cloud_v2 import workflow

    stopped = []

    class FailingCluster:
        size = 3

        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("node 1 exited with 1")

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(workflow, "ProcessCluster", FailingCluster)
    wf = tmp_path / "wf.yml"
    wf.write_text("backend: cloud-v2\nsteps: [{type: wait, seconds: 0}]\n")
    with pytest.raises(RuntimeError, match="exited"):
        workflow.run_workflow_outcome(str(wf))
    assert stopped == [True]
