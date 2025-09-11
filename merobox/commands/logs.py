"""
Logs command - Show logs from a specific node.
"""

import click

from merobox.commands.manager import CalimeroManager


@click.command()
@click.argument("node_name")
@click.option("--tail", default=100, help="Number of log lines to show (default: 100)")
def logs(node_name, tail):
    """Show logs from a specific node."""
    calimero_manager = CalimeroManager()
    calimero_manager.get_node_logs(node_name, tail)
