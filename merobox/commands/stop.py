"""
Stop command - Stop Calimero node(s).
"""

import sys

import click

from merobox.commands.manager import CalimeroManager


@click.command()
@click.argument("node_name", required=False)
@click.option(
    "--all", is_flag=True, help="Stop all running nodes and auth service stack"
)
@click.option(
    "--auth-service", is_flag=True, help="Stop auth service stack (Traefik + Auth)"
)
def stop(node_name, all, auth_service):
    """Stop Calimero node(s)."""
    calimero_manager = CalimeroManager()

    if auth_service:
        # Stop auth service stack
        success = calimero_manager.stop_auth_service_stack()
        sys.exit(0 if success else 1)
    elif all:
        # Stop all nodes
        nodes_success = calimero_manager.stop_all_nodes()

        # Also stop auth service stack when stopping all nodes (if it's running)
        auth_success = True  # Default to success if no auth services to stop
        try:
            # Check if auth service containers exist before trying to stop them
            calimero_manager.client.containers.get("auth")
            calimero_manager.client.containers.get("proxy")
            # If we get here, at least one auth service container exists
            auth_success = calimero_manager.stop_auth_service_stack()
        except Exception:
            # No auth service containers found, which is fine
            from rich.console import Console

            console = Console()
            console.print("[cyan]• No auth service stack to stop[/cyan]")

        # Exit with success only if both operations succeeded
        sys.exit(0 if (nodes_success and auth_success) else 1)
    elif node_name:
        # Stop specific node
        success = calimero_manager.stop_node(node_name)
        sys.exit(0 if success else 1)
    else:
        from rich.console import Console

        console = Console()
        console.print(
            "[red]Error: Please specify a node name, --all, or --auth-service[/red]"
        )
        console.print("Examples:")
        console.print("  merobox stop calimero-node-1")
        console.print("  merobox stop --all")
        console.print("  merobox stop --auth-service")
        sys.exit(1)
