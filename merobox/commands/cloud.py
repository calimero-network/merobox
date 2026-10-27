"""`merobox cloud`: release-v2 (Calimero Cloud) clusters and workflows."""

import sys

import click

from merobox.cloud_v2 import WorkflowError, load_workflow, run_workflow_outcome


@click.group()
def cloud():
    """Run workflows against a release-v2 cluster (merod, one process per node)."""


@cloud.command("run")
@click.argument("workflow", type=click.Path(exists=True))
@click.option(
    "--binary",
    help="merod binary (default: MEROD_BINARY, then MEROD_V2_BINARY, then PATH)",
)
@click.option(
    "--data-dir",
    type=click.Path(),
    help="Keep the cluster's state here instead of a temporary directory",
)
@click.option("--logs", is_flag=True, help="Show the nodes' logs")
def run_cmd(workflow, binary, data_dir, logs):
    """Start the workflow's cluster, run its steps, stop the cluster.

    Exits 0 when it passes, 1 when it fails, 3 for a port marked not applicable."""
    try:
        outcome = run_workflow_outcome(
            workflow, binary=binary, keep_data=data_dir, logs=logs
        )
    except (WorkflowError, FileNotFoundError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)
    sys.exit({"passed": 0, "failed": 1, "not_applicable": 3}[outcome])


@cloud.command("validate")
@click.argument("workflow", type=click.Path(exists=True))
def validate_cmd(workflow):
    """Check a workflow without starting anything."""
    try:
        wf = load_workflow(workflow)
    except WorkflowError as e:
        click.echo(f"invalid: {e}", err=True)
        sys.exit(1)
    click.echo(f"valid: {len(wf['steps'])} steps, {len(wf['accounts'])} accounts")


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
