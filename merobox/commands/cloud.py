"""`merobox cloud`: release-v2 (Calimero Cloud) clusters and workflows."""

import sys

import click

from merobox.cloud_v2 import WorkflowError, load_workflow, run_workflow


@click.group()
def cloud():
    """Run workflows against a release-v2 cluster (merod-v2, one process per node)."""


@cloud.command("run")
@click.argument("workflow", type=click.Path(exists=True))
@click.option("--binary", help="merod-v2 binary (default: MEROD_V2_BINARY, then PATH)")
@click.option(
    "--data-dir",
    type=click.Path(),
    help="Keep the cluster's state here instead of a temporary directory",
)
@click.option("--logs", is_flag=True, help="Show the nodes' logs")
def run_cmd(workflow, binary, data_dir, logs):
    """Start the workflow's cluster, run its steps, stop the cluster."""
    try:
        ok = run_workflow(workflow, binary=binary, keep_data=data_dir, logs=logs)
    except (WorkflowError, FileNotFoundError) as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)
    sys.exit(0 if ok else 1)


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
