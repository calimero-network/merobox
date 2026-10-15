"""A workflow YAML reaches the host outside its directory only with --trust-workflow."""

import asyncio
import importlib
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from merobox.commands.bootstrap.run import run as run_module

bootstrap_module = importlib.import_module("merobox.commands.bootstrap.bootstrap")

NODES = """
nodes:
  count: 1
  image: ghcr.io/calimero-network/merod:edge
"""


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Run a workflow from `<tmp>/wf/wf.yml` with cwd `<tmp>`, as CI runs the examples."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wf").mkdir()

    def _run(yaml_text, **kwargs):
        workflow = tmp_path / "wf" / "wf.yml"
        workflow.write_text("name: t\n" + yaml_text)
        with (
            patch.object(run_module, "WorkflowExecutor") as executor,
            patch("merobox.commands.binary_manager.BinaryManager") as binary,
            patch("merobox.commands.manager.DockerManager"),
            patch.object(run_module, "console") as console,
        ):
            executor.return_value.execute_workflow = AsyncMock(return_value=True)
            ok = asyncio.run(run_module.run_workflow(str(workflow), **kwargs))
        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
        return ok, executor, binary, printed

    return _run


def assert_refused(result, key):
    ok, executor, binary, printed = result
    assert not ok
    assert key in printed and "--trust-workflow" in printed
    executor.assert_not_called()
    binary.assert_not_called()


@pytest.mark.parametrize(
    "yaml_text,key",
    [
        ("no_docker: true\n", "no_docker"),
        ("no_docker: true\nbinary_path: /bin/echo\n", "binary_path"),
    ],
)
def test_yaml_host_binary_is_refused(run, yaml_text, key):
    assert_refused(run(NODES + yaml_text), key)


def test_yaml_binary_path_is_refused_under_cli_no_docker(run):
    assert_refused(
        run(NODES + "binary_path: /bin/echo\n", no_docker=True), "binary_path"
    )


def test_yaml_host_binary_runs_when_trusted(run):
    ok, _, binary, _ = run(
        NODES + "no_docker: true\nbinary_path: /bin/echo\n", trust_workflow=True
    )
    assert ok
    binary.assert_called_once_with(binary_path="/bin/echo")


def test_cli_no_docker_and_binary_path_need_no_trust(run):
    ok, _, binary, _ = run(
        NODES + "no_docker: true\n", no_docker=True, binary_path="/usr/bin/merod"
    )
    assert ok
    binary.assert_called_once_with(binary_path="/usr/bin/merod")


@pytest.mark.parametrize("args,trusted", [([], False), (["--trust-workflow"], True)])
def test_cli_trust_workflow_flag_reaches_the_runner(tmp_path, args, trusted):
    workflow = tmp_path / "wf.yml"
    workflow.write_text("name: t\n")
    with patch.object(
        bootstrap_module, "run_workflow_sync", return_value=True
    ) as runner:
        result = CliRunner().invoke(
            bootstrap_module.bootstrap, ["run", str(workflow), *args]
        )
    assert result.exit_code == 0, result.output
    assert runner.call_args.kwargs["trust_workflow"] is trusted


REMOTE = """
remote_nodes:
  prod:
    url: https://collector.example
    auth:
      method: api_key
      api_key: ${HOME}
"""


def test_yaml_remote_nodes_are_refused(run):
    assert_refused(run(REMOTE), "remote_nodes")


def test_yaml_remote_nodes_run_when_trusted(run):
    ok, executor, _, _ = run(REMOTE, trust_workflow=True)
    assert ok
    assert executor.call_args.args[0]["remote_nodes"]["prod"]["url"] == (
        "https://collector.example"
    )


def test_cli_remote_nodes_need_no_trust(run):
    cli = {"prod": {"url": "https://node.example", "auth": {"method": "none"}}}
    ok, executor, _, _ = run(NODES, cli_remote_nodes=cli)
    assert ok
    assert executor.call_args.args[0]["remote_nodes"] == cli
