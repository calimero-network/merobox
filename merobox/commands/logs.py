"""
Logs command - Show logs from a specific node.

Supports Docker containers by default, and native binary mode with --no-docker.
"""

import click

from merobox.commands.binary_manager import BinaryManager
from merobox.commands.manager import DockerManager


@click.command()
@click.argument("node_name")
@click.option("--tail", default=100, help="Number of log lines to show (default: 100)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output (live)")
@click.option(
    "--no-docker",
    is_flag=True,
    help="Fetch logs for nodes run as native processes (binary mode)",
)
def logs(node_name, tail, follow, no_docker):
    """Show logs from a specific node."""
    if no_docker:
        bm = BinaryManager()
        if follow:
            bm.follow_node_logs(node_name, tail=tail)
        else:
            content = bm.get_node_logs(node_name, lines=tail)
            from rich.console import Console

            console = Console()
            if content is None:
                console.print(
                    f"[yellow]No logs found for {node_name}. Ensure the node is running and check ./data/{node_name}/logs/{node_name}.log[/yellow]"
                )
                return
            console.print(f"\n[bold]Logs for {node_name}:[/bold]")
            console.print(content)
    else:
        calimero_manager = DockerManager()
        calimero_manager.get_node_logs(node_name, tail)
