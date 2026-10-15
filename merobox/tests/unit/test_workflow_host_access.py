"""Workflow YAML keys that reach the host are refused without --trust-workflow."""

import asyncio
import importlib
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from merobox import testing
from merobox.commands.binary_manager import BinaryManager
from merobox.commands.bootstrap.run import run as run_module
from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.bootstrap.steps.blob import UploadBlobStep
from merobox.commands.bootstrap.steps.download_blob import DownloadBlobStep
from merobox.commands.bootstrap.steps.install import InstallApplicationStep
from merobox.commands.bootstrap.steps.node_exec import NodeExecStep
from merobox.commands.bootstrap.steps.script import ScriptStep
from merobox.commands.errors import NodeResolutionError
from merobox.commands.manager import DockerManager
from merobox.commands.remote_nodes import RemoteNodeManager

bootstrap_module = importlib.import_module("merobox.commands.bootstrap.bootstrap")

NODES = """
nodes:
  count: 1
  image: ghcr.io/calimero-network/merod:edge
"""


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Run a workflow, by default `<tmp>/wf/wf.yml` with cwd `<tmp>` as CI runs the
    examples; `workflow` and `cwd` are relative to `<tmp>`."""

    def _run(yaml_text, workflow="wf/wf.yml", cwd=".", **kwargs):
        workflow = tmp_path / workflow
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: t\n" + yaml_text)
        (tmp_path / cwd).mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(tmp_path / cwd)
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


INIT_ARGS = NODES + "  init_args:\n    calimero-node-1: [--server-host, 0.0.0.0]\n"


def test_yaml_init_args_in_binary_mode_are_refused(run):
    assert_refused(run(INIT_ARGS, no_docker=True), "init_args")


def test_yaml_init_args_in_binary_mode_run_when_trusted(run):
    ok, *_ = run(INIT_ARGS, no_docker=True, trust_workflow=True)
    assert ok


def test_yaml_init_args_in_docker_mode_need_no_trust(run):
    ok, *_ = run(INIT_ARGS)
    assert ok


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


@pytest.fixture
def git_repo(tmp_path):
    """`<tmp>/repo` is a git checkout; its `.git` is a file, as in a worktree."""
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".git").write_text("gitdir: elsewhere\n")


CORE_WORKFLOW = "repo/workflows/app-migration/wf.yml"


@pytest.mark.parametrize(
    "path,cwd",
    [
        ("dist/app.mpk", "repo"),
        ("../../dist/app.mpk", "repo/apps/app"),
        ("workflows/../dist/app.mpk", "repo"),
    ],
)
def test_paths_inside_the_git_checkout_need_no_trust(run, git_repo, path, cwd):
    steps = step("install_application", path=path)
    ok, *_ = run(NODES + "steps:\n" + steps, workflow=CORE_WORKFLOW, cwd=cwd)
    assert ok


@pytest.mark.parametrize(
    "path,cwd", [("../outside.mpk", "repo"), ("../../../x.mpk", "repo/apps/app")]
)
def test_paths_leaving_the_git_checkout_are_refused(run, git_repo, path, cwd):
    steps = step("install_application", path=path)
    assert_refused(
        run(NODES + "steps:\n" + steps, workflow=CORE_WORKFLOW, cwd=cwd), "path"
    )


@pytest.mark.parametrize(
    "steps,key",
    [
        (step("upload_blob", file_path="\"'/etc/passwd'\""), "file_path"),
        (
            step("download_blob", blob_id="b", output_path="\"'/tmp/merobox-x'\""),
            "output_path",
        ),
    ],
)
def test_quoted_path_literals_are_refused_up_front(run, steps, key):
    # From inside the root, the quoted text read as a relative name would pass.
    assert_refused(run(NODES + "steps:\n" + steps, cwd="wf"), key)


def test_placeholder_step_paths_are_left_to_the_step(run):
    steps = step("upload_blob", file_path="'{{blob_path}}'")
    ok, *_ = run(NODES + "steps:\n" + steps)
    assert ok


def run_step(step_class, config, dynamic_values, host_root, **patches):
    """Execute a step as a workflow would, with every node call stubbed."""
    step_ = step_class(config, manager=MagicMock(binary_path=None), host_root=host_root)
    with (
        patch.object(step_, "_resolve_node_for_client") as resolve,
        patch.object(step_, "_validate_export_config", return_value=True),
    ):
        resolve.return_value = ("http://localhost:2528", None)
        for name, value in patches.items():
            setattr(step_, name, value)
        ok = asyncio.run(step_.execute({}, dynamic_values))
    return ok, resolve


@pytest.mark.parametrize(
    "path", ["{{blob_path}}", "'{{blob_path}}'", "\"'{{blob_path}}'\""]
)
def test_upload_blob_refuses_a_resolved_path_outside_the_root(tmp_path, path):
    ok, resolve = run_step(
        UploadBlobStep,
        {"type": "upload_blob", "node": "n", "file_path": path},
        {"blob_path": "/etc/hosts"},
        str(tmp_path),
    )
    assert not ok
    resolve.assert_not_called()


def test_upload_blob_reads_a_resolved_path_inside_the_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "blob.bin").write_bytes(b"x")
    upload = AsyncMock(return_value={"success": False, "error": "stub"})
    ok, resolve = run_step(
        UploadBlobStep,
        {"type": "upload_blob", "node": "n", "file_path": "{{blob_path}}"},
        {"blob_path": "blob.bin"},
        str(tmp_path),
        _upload_blob_to_node=upload,
    )
    upload.assert_called_once()


def test_download_blob_refuses_a_resolved_output_outside_the_root(tmp_path):
    target = tmp_path.parent / f"{tmp_path.name}-escape.bin"
    download = AsyncMock(return_value={"success": True, "data": b"payload"})
    ok, _ = run_step(
        DownloadBlobStep,
        {
            "type": "download_blob",
            "node": "n",
            "blob_id": "b",
            "output_path": "{{out}}",
        },
        {"out": str(target)},
        str(tmp_path),
        _download_blob_from_node=download,
    )
    assert not ok
    assert not target.exists()
    download.assert_not_called()


def test_install_refuses_a_path_outside_the_root(tmp_path):
    ok, resolve = run_step(
        InstallApplicationStep,
        {"type": "install_application", "node": "n", "path": "/etc/hosts"},
        {},
        str(tmp_path),
    )
    assert not ok
    resolve.assert_not_called()


def test_node_exec_refuses_a_data_dir_outside_the_root(tmp_path):
    manager = MagicMock(binary_path=None)
    step_ = NodeExecStep(
        {"type": "node_exec", "node": "n", "args": ["x"], "data_dir": "/"},
        manager=manager,
        host_root=str(tmp_path),
    )
    assert not asyncio.run(step_.execute({}, {}))
    manager.is_node_running.assert_not_called()


def test_executor_hands_the_root_to_every_step(tmp_path):
    manager = MagicMock(binary_path=None)
    upload = {"type": "upload_blob", "node": "n", "file_path": "x"}
    untrusted = WorkflowExecutor({"name": "t"}, manager, workflow_dir=str(tmp_path))
    trusted = WorkflowExecutor(
        {"name": "t"}, manager, workflow_dir=str(tmp_path), trust_workflow=True
    )
    assert untrusted._create_step_executor("upload_blob", upload).host_root == str(
        tmp_path
    )
    assert trusted._create_step_executor("upload_blob", upload).host_root is None
    repeat = untrusted._create_step_executor(
        "repeat", {"type": "repeat", "count": 1, "steps": [upload]}
    )
    install = {"type": "install_application", "node": "n", "path": "x"}
    nested = repeat._create_nested_step_executor("install_application", install)
    assert nested.host_root == str(tmp_path)


@pytest.mark.parametrize("node", ["/home/u/.ssh", "../../etc", "{{target}}"])
def test_node_exec_refuses_a_node_name_that_is_a_path(tmp_path, node):
    manager = MagicMock(binary_path=None)
    step_ = NodeExecStep(
        {"type": "node_exec", "node": node, "args": ["x"]}, manager=manager
    )
    assert not asyncio.run(step_.execute({}, {"target": "/home/u/.ssh"}))
    manager.is_node_running.assert_not_called()


@pytest.mark.parametrize(
    "yaml_text",
    [
        "nodes:\n  count: 1\n  prefix: ../../evil\n",
        "nodes:\n  /home/u/.ssh:\n    port: 2428\n",
        "nodes:\n  ../escape:\n    port: 2428\n",
    ],
)
@pytest.mark.parametrize("trusted", [False, True])
def test_node_names_that_are_paths_are_refused_even_when_trusted(
    run, yaml_text, trusted
):
    ok, executor, _, printed = run(yaml_text, trust_workflow=trusted)
    assert not ok
    assert "node name" in printed
    executor.assert_not_called()


@pytest.mark.parametrize(
    "yaml_text,key",
    [
        ("auth_image: evil.example/mero-auth\n", "auth_image"),
        (
            "topology:\n  type: nat\n  boot_node:\n    image: evil.example/boot\n",
            "topology.boot_node.image",
        ),
        (
            "topology:\n  type: bootstrap\n  boot_node:\n    image: alpine\n",
            "topology.boot_node.image",
        ),
    ],
)
def test_yaml_service_images_outside_calimero_are_refused(run, yaml_text, key):
    assert_refused(run(NODES + yaml_text), key)


def test_yaml_calimero_service_images_need_no_trust(run):
    ok, *_ = run(
        NODES + "auth_image: ghcr.io/calimero-network/mero-auth:edge\n"
        "topology:\n  type: nat\n  boot_node:\n"
        "    image: ghcr.io/calimero-network/boot-node:latest\n"
    )
    assert ok


def test_cli_remote_node_name_alone_means_the_saved_registry_entry():
    assert bootstrap_module.parse_remote_nodes(("prod",)) == {"prod": {}}


def test_cli_remote_node_name_alone_allows_the_entry_without_rewriting_it(
    user_registry, tmp_path
):
    registry = tmp_path / "remote_nodes.json"
    RemoteNodeManager().register(
        "prod", "https://prod.example", auth_method="user_password", username="me"
    )
    saved = registry.read_text()
    resolver = resolver_for({"name": "t", "remote_nodes": {"prod": {}}})
    resolved = resolver.resolve_sync("prod", skip_auth=True)
    assert resolved.url == "https://prod.example"
    assert resolved.auth_method == "user_password"
    assert registry.read_text() == saved


def test_cli_remote_node_name_alone_that_is_not_registered_stays_refused(
    empty_registry,
):
    resolver = resolver_for({"name": "t", "remote_nodes": {"prod": {}}})
    with pytest.raises(NodeResolutionError):
        resolver.resolve_sync("prod", skip_auth=True)


def test_cli_binary_path_counts_as_consent_to_yaml_no_docker(run):
    ok, _, binary, _ = run(NODES + "no_docker: true\n", binary_path="/usr/bin/merod")
    assert ok
    binary.assert_called_once_with(binary_path="/usr/bin/merod")


def test_validate_reports_what_a_run_would_refuse(tmp_path):
    workflow = tmp_path / "wf.yml"
    workflow.write_text("name: t\n" + NODES + "steps:\n" + UPLOAD_OUTSIDE)
    result = CliRunner().invoke(bootstrap_module.bootstrap, ["validate", str(workflow)])
    assert "file_path" in result.output
    assert "--trust-workflow" in result.output


def test_a_step_built_without_a_root_refuses_host_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "blob.bin").write_bytes(b"x")
    step_ = UploadBlobStep(
        {"type": "upload_blob", "node": "n", "file_path": "blob.bin"}
    )
    upload = AsyncMock(return_value={"success": False, "error": "stub"})
    with (
        patch.object(step_, "_upload_blob_to_node", upload),
        patch.object(
            step_, "_resolve_node_for_client", return_value=("http://n", None)
        ),
    ):
        assert not asyncio.run(step_.execute({}, {}))
    upload.assert_not_called()


def test_parallel_hands_the_root_to_its_steps(tmp_path):
    executor = WorkflowExecutor(
        {"name": "t"}, MagicMock(binary_path=None), workflow_dir=str(tmp_path)
    )
    install = {"type": "install_application", "node": "n", "path": "x"}
    parallel = executor._create_step_executor(
        "parallel",
        {"type": "parallel", "groups": [{"name": "g", "steps": [install]}]},
    )
    nested = parallel._create_nested_step_executor("install_application", install)
    assert nested.host_root == str(tmp_path)


EVIL_SELF_SCRIPT = """
nodes:
  count: 1
  image: ghcr.io/calimero-network/merod:edge
steps:
  - name: harmless ; touch /tmp/PWNED
    type: script
    target: local
    script: wf/wf.yml
"""


def test_local_script_step_is_refused_up_front(run):
    ok, executor, _, printed = run(EVIL_SELF_SCRIPT)
    assert not ok and "target local" in printed and "--trust-workflow" in printed
    executor.assert_not_called()


def test_container_script_steps_need_no_trust(run):
    ok, *_ = run(EVIL_SELF_SCRIPT.replace("target: local", "target: nodes"))
    assert ok


def test_script_path_outside_the_root_is_refused(run):
    yaml_text = EVIL_SELF_SCRIPT.replace("target: local", "target: nodes")
    assert_refused(run(yaml_text.replace("wf/wf.yml", "outside.sh")), "script")


LOCAL_SCRIPT = {"type": "script", "name": "s", "target": "local", "script": "evil.sh"}


@pytest.mark.parametrize("given_root", [True, False])
def test_script_step_refuses_to_run_locally_unless_trusted(
    tmp_path, monkeypatch, given_root
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "evil.sh").write_text("touch PWNED\n")
    kwargs = {"host_root": str(tmp_path)} if given_root else {}
    step_ = ScriptStep(LOCAL_SCRIPT, **kwargs)
    with patch("merobox.commands.bootstrap.steps.script.subprocess.run") as sh:
        assert not asyncio.run(step_.execute({}, {}))
    sh.assert_not_called()


def test_script_step_runs_locally_when_trusted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "evil.sh").write_text("touch PWNED\n")
    step_ = ScriptStep(LOCAL_SCRIPT, host_root=None)
    with patch("merobox.commands.bootstrap.steps.script.subprocess.run") as sh:
        sh.return_value.returncode = 0
        sh.return_value.stdout = ""
        assert asyncio.run(step_.execute({}, {}))
    sh.assert_called_once()


ESCAPING_NAME = "../../../../tmp/escaped"


@pytest.fixture
def deep_cwd(tmp_path, monkeypatch):
    """A cwd four levels down, so an escaping ./data/<name> stays inside <tmp>."""
    cwd = tmp_path / "a" / "b" / "c" / "d"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    return tmp_path


def test_binary_run_node_refuses_a_name_that_is_a_path(deep_cwd):
    manager = BinaryManager(
        binary_path="/usr/bin/true", require_binary=False, enable_signal_handlers=False
    )
    with patch("merobox.commands.binary_manager.subprocess") as proc:
        assert not manager.run_node(ESCAPING_NAME)
    proc.Popen.assert_not_called()
    proc.run.assert_not_called()
    assert not list(deep_cwd.rglob("escaped"))


def test_binary_stop_node_never_reads_a_pid_file_outside_its_dir(deep_cwd):
    manager = BinaryManager(
        binary_path="/usr/bin/true", require_binary=False, enable_signal_handlers=False
    )
    # ./data/.pids/../../victim.pid is the cwd's victim.pid.
    (deep_cwd / "a" / "b" / "c" / "d" / "victim.pid").write_text("1")
    with (
        patch("merobox.commands.binary_manager._request_stop_pid") as stop,
        patch.object(manager, "_is_process_running", return_value=True),
    ):
        assert not manager.stop_node("../../victim")
    stop.assert_not_called()


def test_docker_run_node_refuses_a_name_that_is_a_path(deep_cwd):
    manager = DockerManager.__new__(DockerManager)
    manager.client = MagicMock()
    with patch.object(manager, "_ensure_image_pulled", return_value=True):
        assert not manager.run_node(ESCAPING_NAME)
    manager.client.containers.run.assert_not_called()
    assert not list(deep_cwd.rglob("escaped"))


def test_docker_stop_node_refuses_a_name_that_is_a_path():
    manager = DockerManager.__new__(DockerManager)
    manager.client = MagicMock()
    manager.nodes = {}
    assert not manager.stop_node(ESCAPING_NAME)
    manager.client.containers.get.assert_not_called()


def test_binary_node_logs_are_never_read_from_outside_the_data_dir(deep_cwd):
    manager = BinaryManager(
        binary_path="/usr/bin/true", require_binary=False, enable_signal_handlers=False
    )
    cwd = deep_cwd / "a" / "b" / "c" / "d"
    (cwd / "evil" / "logs").mkdir(parents=True)
    # ./data/../evil/logs/../evil.log is ./evil/evil.log, outside ./data.
    (cwd / "evil" / "evil.log").write_text("secret\n")
    assert manager.get_node_logs("../evil") is None


@pytest.mark.parametrize(
    "steps,key",
    [
        (step("download_blob", blob_id="b", output_path=".git/config"), "output_path"),
        (step("upload_blob", file_path=".git/config"), "file_path"),
        (step("upload_blob", file_path="vendor/lib/.git/config"), "file_path"),
    ],
)
def test_paths_under_dot_git_are_refused(run, git_repo, steps, key):
    assert_refused(
        run(NODES + "steps:\n" + steps, workflow=CORE_WORKFLOW, cwd="repo"), key
    )


def test_a_step_refuses_a_resolved_path_under_dot_git(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    download = AsyncMock(return_value={"success": True, "data": b"x"})
    ok, _ = run_step(
        DownloadBlobStep,
        {"type": "download_blob", "node": "n", "blob_id": "b", "output_path": "{{o}}"},
        {"o": ".git/config"},
        str(tmp_path),
        _download_blob_from_node=download,
    )
    assert not ok
    download.assert_not_called()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """`<tmp>/home` is $HOME, and a git checkout, as a dotfiles repo makes it."""
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".git").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_a_dotfiles_home_is_never_the_root(run, home):
    steps = step("upload_blob", file_path="../.bashrc")
    assert_refused(
        run(NODES + "steps:\n" + steps, workflow="home/proj/wf.yml", cwd="home/proj"),
        "file_path",
    )


def test_a_project_under_home_still_confines_to_itself(run, home):
    steps = step("upload_blob", file_path="blob.bin")
    ok, *_ = run(
        NODES + "steps:\n" + steps, workflow="home/proj/wf.yml", cwd="home/proj"
    )
    assert ok


@pytest.mark.parametrize("workflow", ["home/wf.yml", "wf.yml"])
def test_a_workflow_at_or_above_home_gets_no_host_paths(run, home, workflow):
    steps = step("upload_blob", file_path="home/blob.bin")
    assert_refused(
        run(NODES + "steps:\n" + steps, workflow=workflow, cwd="."), "file_path"
    )


@pytest.mark.parametrize(
    "path",
    [
        ".GIT/config",
        ".Git/hooks/pre-commit",
        ".git",
        "vendor/.gitmodules",
        ".gitattributes",
        "sub/.gitconfig",
    ],
)
def test_git_control_files_are_refused_at_any_depth(run, git_repo, path):
    steps = step("upload_blob", file_path=path)
    assert_refused(
        run(NODES + "steps:\n" + steps, workflow=CORE_WORKFLOW, cwd="repo"),
        "file_path",
    )


@pytest.mark.parametrize(
    "path",
    ["out/HEAD", "out/config", "out/packed-refs", "out/objects/x", "out/refs/heads/m"],
)
def test_writes_cannot_plant_a_git_directory(run, git_repo, path):
    steps = step("download_blob", blob_id="b", output_path=path)
    assert_refused(
        run(NODES + "steps:\n" + steps, workflow=CORE_WORKFLOW, cwd="repo"),
        "output_path",
    )


def test_a_step_refuses_to_write_a_git_layout_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    download = AsyncMock(return_value={"success": True, "data": b"x"})
    ok, _ = run_step(
        DownloadBlobStep,
        {"type": "download_blob", "node": "n", "blob_id": "b", "output_path": "{{o}}"},
        {"o": "planted/HEAD"},
        str(tmp_path),
        _download_blob_from_node=download,
    )
    assert not ok
    download.assert_not_called()


def test_reads_may_name_git_layout_files(run, git_repo):
    steps = step("upload_blob", file_path="fixtures/config")
    ok, *_ = run(NODES + "steps:\n" + steps, workflow=CORE_WORKFLOW, cwd="repo")
    assert ok
