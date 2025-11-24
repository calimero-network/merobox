"""
Stop command - Stop Calimero node(s).

Supports Docker containers by default, and native binary mode with --no-docker.
"""

import sys

import click
import docker
from rich.console import Console

from merobox.commands.binary_manager import BinaryManager
from merobox.commands.manager import DockerManager


@click.command()
@click.argument("node_name", required=False)
@click.option(
    "--all",
    "stop_all",
    is_flag=True,
    help="Stop all running nodes and auth service stack",
)
@click.option(
    "--auth-service", is_flag=True, help="Stop auth service stack (Traefik + Auth)"
)
@click.option(
    "--no-docker",
    is_flag=True,
    help="Stop nodes in binary mode (managed as native processes)",
)
def stop(node_name, stop_all, auth_service, no_docker):
    """Stop Calimero node(s)."""
    console = Console()

    if auth_service and no_docker:
        console.print(
            "[cyan]• Auth service stack is only available in Docker mode[/cyan]"
        )
        sys.exit(0)

    calimero_manager = BinaryManager() if no_docker else DockerManager()

    if auth_service:
        # Stop auth service stack
        success = calimero_manager.stop_auth_service_stack()
        sys.exit(0 if success else 1)
    elif stop_all:
        # Stop all nodes
        nodes_success = calimero_manager.stop_all_nodes()

        auth_success = True  # Default to success if no auth services to stop
        if not no_docker:
            try:
                # Check if auth service containers exist before trying to stop them
                calimero_manager.client.containers.get("auth")
                calimero_manager.client.containers.get("proxy")
                # If we get here, at least one auth service container exists
                auth_success = calimero_manager.stop_auth_service_stack()
            except docker.errors.NotFound:
                # No auth service containers found, which is fine
                console.print("[cyan]• No auth service stack to stop[/cyan]")
            except docker.errors.DockerException as exc:
                console.print(
                    "[yellow]⚠ Unable to stop auth service stack: "
                    f"{getattr(exc, 'explanation', str(exc))}[/yellow]"
                )
                auth_success = False
        else:
            console.print("[cyan]• No auth service stack in binary mode[/cyan]")
        # Also stop auth service stack when stopping all nodes (if it's running)

        # Exit with success only if both operations succeeded
        sys.exit(0 if (nodes_success and auth_success) else 1)
    elif node_name:
        # Stop specific node
        success = calimero_manager.stop_node(node_name)
        sys.exit(0 if success else 1)
    else:
        console.print(
            "[red]Error: Please specify a node name, --all, or --auth-service[/red]"
        )
        console.print("Examples:")
        console.print("  merobox stop calimero-node-1")
        console.print("  merobox stop --all")
        console.print("  merobox stop --auth-service")
        sys.exit(1)
