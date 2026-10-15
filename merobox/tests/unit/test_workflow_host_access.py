"""A workflow YAML reaches the host outside its directory only with --trust-workflow."""

import asyncio
import importlib
import textwrap
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from merobox import testing
from merobox.commands.bootstrap.run import run as run_module
from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.errors import NodeResolutionError
from merobox.commands.remote_nodes import RemoteNodeManager

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
    ok, executor, binary, _ = run(
        NODES + "no_docker: true\nbinary_path: /bin/echo\n", trust_workflow=True
    )
    assert ok
    binary.assert_called_once_with(binary_path="/bin/echo")
    assert executor.call_args.kwargs["trust_workflow"] is True


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


@pytest.fixture
def empty_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "merobox.commands.remote_nodes.DEFAULT_REGISTRY_PATH",
        tmp_path / "remote_nodes.json",
    )


def resolver_for(config, **kwargs):
    executor = WorkflowExecutor(config, None, **kwargs)
    executor._setup_resolver()
    return executor.resolver


def test_step_node_url_is_refused(empty_registry):
    with pytest.raises(NodeResolutionError, match="--trust-workflow"):
        resolver_for({"name": "t"}).resolve_sync(
            "https://collector.example", skip_auth=True
        )


def test_step_node_url_resolves_when_trusted(empty_registry):
    resolver = resolver_for({"name": "t"}, trust_workflow=True)
    resolved = resolver.resolve_sync("https://collector.example", skip_auth=True)
    assert resolved.url == "https://collector.example"


def test_step_node_url_registered_on_the_cli_needs_no_trust(empty_registry):
    remote = {"prod": {"url": "https://node.example", "auth": {"method": "none"}}}
    resolver = resolver_for({"name": "t", "remote_nodes": remote})
    resolved = resolver.resolve_sync("https://node.example", skip_auth=True)
    assert resolved.node_name == "prod"


def node(**keys):
    lines = "".join(f"    {k}: {v}\n" for k, v in keys.items())
    return f"nodes:\n  image: ghcr.io/calimero-network/merod:edge\n  n1:\n{lines}"


@pytest.mark.parametrize(
    "yaml_text,key",
    [
        (node(data_dir="/"), "data_dir"),
        (node(data_dir="wf/../.."), "data_dir"),
        (node(config_path="../config.toml"), "config_path"),
        (
            NODES.replace("count: 1", "count: 1\n  config_path: /etc/hosts"),
            "config_path",
        ),
        (NODES.replace("ghcr.io/calimero-network/merod:edge", "alpine"), "image"),
        (node(image="evil.example/merod:edge"), "image"),
        (node(image="ghcr.io/calimero-network-evil/merod"), "image"),
    ],
)
def test_yaml_node_host_access_is_refused(run, yaml_text, key):
    assert_refused(run(yaml_text), key)


def test_yaml_node_paths_inside_the_workflow_dir_need_no_trust(run):
    ok, *_ = run(node(data_dir="wf/data", config_path="config.toml"))
    assert ok


def test_yaml_node_host_access_runs_when_trusted(run):
    ok, *_ = run(node(data_dir="/", image="alpine"), trust_workflow=True)
    assert ok


def step(type_, **keys):
    lines = "".join(f"    {k}: {v}\n" for k, v in keys.items())
    return f"  - name: s\n    type: {type_}\n    node: calimero-node-1\n{lines}"


UPLOAD_OUTSIDE = step("upload_blob", file_path="/etc/hosts")


@pytest.mark.parametrize(
    "steps,key",
    [
        (UPLOAD_OUTSIDE, "file_path"),
        (step("upload_blob", file_path="'{{secret_path}}'"), "file_path"),
        (step("download_blob", blob_id="b", output_path="out.bin"), "output_path"),
        (step("install_application", path="~/.ssh/id_rsa"), "path"),
        (step("node_exec", args="[account, export]", data_dir="/"), "data_dir"),
        (step("node_exec", args="[account, export]", image="alpine"), "image"),
        (
            "  - name: p\n    type: parallel\n    groups:\n      - name: g\n"
            "        steps:\n" + textwrap.indent(UPLOAD_OUTSIDE, " " * 6),
            "file_path",
        ),
        (
            "  - name: r\n    type: repeat\n    count: 1\n    steps:\n"
            + textwrap.indent(UPLOAD_OUTSIDE, " " * 2),
            "file_path",
        ),
    ],
)
def test_yaml_step_host_access_is_refused(run, steps, key):
    assert_refused(run(NODES + "steps:\n" + steps), key)


def test_yaml_step_paths_inside_the_workflow_dir_need_no_trust(run):
    steps = (
        step("upload_blob", file_path="wf/blob.bin")
        + step("download_blob", blob_id="b", output_path="wf/out/blob.bin")
        + step("install_application", path="./wf/app.mpk")
        + step("node_exec", args="[account, export]", data_dir="wf/data")
    )
    ok, *_ = run(NODES + "steps:\n" + steps)
    assert ok


def test_yaml_step_host_access_runs_when_trusted(run):
    ok, *_ = run(NODES + "steps:\n" + UPLOAD_OUTSIDE, trust_workflow=True)
    assert ok


@pytest.fixture
def user_registry(empty_registry):
    """The user's own registry already holds `prod`, as `merobox remote register` leaves it."""
    RemoteNodeManager().register("prod", "https://prod.example")


@pytest.mark.parametrize("ref", ["prod", "https://prod.example"])
def test_step_naming_a_user_registry_node_is_refused(user_registry, ref):
    with pytest.raises(NodeResolutionError, match="--trust-workflow"):
        resolver_for({"name": "t"}).resolve_sync(ref, skip_auth=True)


@pytest.mark.parametrize("ref", ["prod", "https://prod.example"])
def test_step_naming_a_user_registry_node_resolves_when_trusted(user_registry, ref):
    resolver = resolver_for({"name": "t"}, trust_workflow=True)
    assert resolver.resolve_sync(ref, skip_auth=True).node_name == "prod"


def test_step_naming_a_cli_remote_node_needs_no_trust(user_registry):
    remote = {"prod": {"url": "https://prod.example", "auth": {"method": "none"}}}
    resolver = resolver_for({"name": "t", "remote_nodes": remote})
    assert resolver.resolve_sync("prod", skip_auth=True).url == "https://prod.example"


def test_local_node_shadowed_by_a_user_registry_name_still_resolves(user_registry):
    resolver = resolver_for({"name": "t"})
    resolver.docker_manager = object()
    with patch.object(
        resolver, "_resolve_docker_node", return_value="http://localhost:2528"
    ):
        resolved = resolver.resolve_sync("prod", skip_auth=True)
    assert (resolved.source, resolved.url) == ("docker", "http://localhost:2528")


@pytest.fixture
def python_api(tmp_path):
    """Run a workflow through merobox.testing.workflow with Docker stubbed out."""

    def _run(yaml_text, **kwargs):
        workflow = tmp_path / "wf.yml"
        workflow.write_text("name: t\n" + yaml_text)
        with (
            patch.object(testing, "WorkflowExecutor") as executor,
            patch.object(testing, "DockerManager") as manager,
        ):
            executor.return_value.execute_workflow = AsyncMock(return_value=True)
            executor.return_value.dynamic_values = {}
            manager.return_value.get_running_nodes.return_value = []
            with testing.workflow(workflow, wait_for_ready=False, **kwargs):
                pass
        return executor

    return _run


def test_python_api_refuses_untrusted_host_access(python_api):
    with pytest.raises(ValueError, match="--trust-workflow"):
        python_api(REMOTE)


def test_python_api_runs_trusted_host_access(python_api, tmp_path):
    executor = python_api(REMOTE, trust_workflow=True)
    assert executor.call_args.kwargs["trust_workflow"] is True
    assert executor.call_args.kwargs["workflow_dir"] == str(tmp_path)
