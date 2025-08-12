"""
List command - List all running Calimero nodes.
"""

import click
from .manager import CalimeroManager

@click.command()
def list():
    """List all running Calimero nodes."""
    calimero_manager = CalimeroManager()
    calimero_manager.list_nodes()
