"""
Stop command - Stop Calimero node(s).
"""

import click
import sys
from .manager import CalimeroManager

@click.command()
@click.argument('node_name', required=False)
@click.option('--all', is_flag=True, help='Stop all running nodes')
def stop(node_name, all):
    """Stop Calimero node(s)."""
    calimero_manager = CalimeroManager()
    
    if all:
        # Stop all nodes
        success = calimero_manager.stop_all_nodes()
        sys.exit(0 if success else 1)
    elif node_name:
        # Stop specific node
        success = calimero_manager.stop_node(node_name)
        sys.exit(0 if success else 1)
    else:
        from rich.console import Console
        console = Console()
        console.print("[red]Error: Please specify a node name or use --all to stop all nodes[/red]")
        console.print("Examples:")
        console.print("  python3 merobox_cli.py stop calimero-node-1")
        console.print("  python3 merobox_cli.py stop --all")
        sys.exit(1)
