"""
List command - List all running Calimero nodes.

Supports Docker containers by default, and native binary mode with --no-docker.
"""

import click

from merobox.commands.binary_manager import BinaryManager
from merobox.commands.manager import DockerManager


@click.command()
@click.option(
    "--no-docker",
    is_flag=True,
    help="List nodes managed as native processes (binary mode)",
)
def list(no_docker):
    """List all running Calimero nodes."""
    if no_docker:
        bm = BinaryManager()
        nodes = bm.list_nodes()
        from rich.console import Console
        from rich.table import Table

        console = Console()
        if not nodes:
            console.print(
                "[yellow]No Calimero nodes are currently running (binary mode)[/yellow]"
            )
            return

        table = Table(title="Running Calimero Nodes (Binary Mode)")
        table.add_column("Name", style="cyan")
        table.add_column("PID", style="green")
        table.add_column("Status", style="yellow")
        console.print(table)
    else:
        calimero_manager = DockerManager()
        calimero_manager.list_nodes()
