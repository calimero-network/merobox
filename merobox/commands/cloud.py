"""`merobox cloud`: release-v2 (Calimero Cloud) clusters and workflows.

`merobox bootstrap run|validate` and `merobox run` are v1's names for running
and checking a workflow; they route here.
"""

import sys

import click

from merobox.cloud_v2 import WorkflowError, load_workflow, run_workflow_outcome


def _run(workflow, binary, data_dir, logs):
    try:
        outcome = run_workflow_outcome(
            workflow, binary=binary, keep_data=data_dir, logs=logs
        )
    except (WorkflowError, FileNotFoundError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)
    sys.exit({"passed": 0, "failed": 1, "not_applicable": 3}[outcome])


def _validate(workflow):
    try:
        wf = load_workflow(workflow)
    except WorkflowError as e:
        click.echo(f"invalid: {e}", err=True)
        sys.exit(1)
    click.echo(f"valid: {len(wf['steps'])} steps, {len(wf['accounts'])} accounts")


def _run_options(f):
    """`run`'s options, shared by `cloud run` and its v1-named aliases."""
    f = click.option("--logs", is_flag=True, help="Show the nodes' logs")(f)
    f = click.option(
        "--data-dir",
        type=click.Path(),
        help="Keep the cluster's state here instead of a temporary directory",
    )(f)
    f = click.option(
        "--binary",
        "--binary-path",
        "binary",
        help="merod binary (default: MEROD_BINARY, then MEROD_V2_BINARY, then PATH)",
    )(f)
    f = click.argument("workflow", type=click.Path(exists=True))(f)
    return f


@click.group()
def cloud():
    """Run workflows against a release-v2 cluster (merod, one process per node)."""


@cloud.command("run")
@_run_options
def run_cmd(workflow, binary, data_dir, logs):
    """Start the workflow's cluster, run its steps, stop the cluster.

    Exits 0 when it passes, 1 when it fails, 2 when the file is invalid, 3 for
    a port marked not applicable."""
    _run(workflow, binary, data_dir, logs)


@cloud.command("validate")
@click.argument("workflow", type=click.Path(exists=True))
def validate_cmd(workflow):
    """Check a workflow without starting anything."""
    _validate(workflow)


@cloud.command("port")
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--wasm-map",
    type=click.Path(exists=True),
    help="JSON: {bundle path: {service or '': wasm path}} for install/create steps",
)
@click.option(
    "--out-dir", type=click.Path(), required=True, help="Where ported files go"
)
@click.option(
    "--base-port", type=int, default=2800, help="First file's cluster port base"
)
def port_cmd(sources, wasm_map, out_dir, base_port):
    """Port v1 workflows (nodes, namespaces, contexts) to cloud-v2 workflows."""
    from pathlib import Path

    from merobox.cloud_v2.port import load_wasm_map, port_file

    mapping = load_wasm_map(wasm_map)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for i, src in enumerate(sources):
        text, porter = port_file(src, mapping, base_port + 10 * i)
        target = out / Path(src).name
        target.write_text(text)
        dropped = sum(porter.dropped.values())
        click.echo(f"{src} -> {target} ({dropped} steps dropped)")


# --------------------------------------------------------- v1's command names

_NO_DOCKER_HELP = "Accepted for v1 scripts: nodes always run as merod processes"


@click.group()
def bootstrap():
    """v1's name for `merobox cloud run` and `merobox cloud validate`."""


@bootstrap.command("run")
@_run_options
@click.option("--no-docker", is_flag=True, hidden=True, help=_NO_DOCKER_HELP)
def bootstrap_run(workflow, binary, data_dir, logs, no_docker):
    """Same as `merobox cloud run`."""
    _run(workflow, binary, data_dir, logs)


@bootstrap.command("validate")
@click.argument("workflow", type=click.Path(exists=True))
def bootstrap_validate(workflow):
    """Same as `merobox cloud validate`."""
    _validate(workflow)


@click.command("run")
@_run_options
@click.option("--no-docker", is_flag=True, hidden=True, help=_NO_DOCKER_HELP)
def run(workflow, binary, data_dir, logs, no_docker):
    """Same as `merobox cloud run`: run a workflow on its own cluster."""
    _run(workflow, binary, data_dir, logs)
