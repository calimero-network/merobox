"""v1's workflow commands keep their names and route to `merobox cloud`."""

import importlib

import pytest
from click.testing import CliRunner

from merobox.cli import cli

# The module, not the `cloud` group the package re-exports under that name.
cloud_cmd = importlib.import_module("merobox.commands.cloud")

V2 = "backend: cloud-v2\naccounts: [alice]\nsteps:\n  - type: whoami\n    account: alice\n"
V1 = "name: v1\nnodes: { count: 2 }\nsteps:\n  - type: install_application\n"


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(path, binary=None, keep_data=None, logs=False):
        seen.append((path, binary, keep_data, logs))
        return "passed"

    monkeypatch.setattr(cloud_cmd, "run_workflow_outcome", fake)
    return seen


@pytest.mark.parametrize(
    "args",
    [
        ["cloud", "run"],
        ["bootstrap", "run"],
        ["run"],
    ],
)
def test_every_run_name_runs_the_cloud_workflow(tmp_path, calls, args):
    wf = tmp_path / "wf.yml"
    wf.write_text(V2)
    result = CliRunner().invoke(cli, [*args, str(wf), "--binary-path", "/m"])
    assert result.exit_code == 0, result.output
    assert calls == [(str(wf), "/m", None, False)]


@pytest.mark.parametrize("args", [["bootstrap", "run"], ["run"]])
def test_v1_scripts_may_still_pass_no_docker(tmp_path, calls, args):
    wf = tmp_path / "wf.yml"
    wf.write_text(V2)
    result = CliRunner().invoke(cli, [*args, str(wf), "--no-docker"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("args", [["cloud", "validate"], ["bootstrap", "validate"]])
def test_validate_names(tmp_path, args):
    wf = tmp_path / "wf.yml"
    wf.write_text(V2)
    result = CliRunner().invoke(cli, [*args, str(wf)])
    assert result.exit_code == 0, result.output
    assert "valid: 1 steps, 1 accounts" in result.output


def test_a_v1_workflow_is_refused_with_the_way_to_port_it(tmp_path):
    wf = tmp_path / "wf.yml"
    wf.write_text(V1)
    result = CliRunner().invoke(cli, ["bootstrap", "run", str(wf)])
    assert result.exit_code == 2
    assert "merobox cloud port" in result.output


def test_v1_only_commands_are_gone():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for name in ("nuke", "stop", "logs", "health", "remote", "group", "namespace"):
        assert f"  {name} " not in result.output
