"""
Identity command - List and generate identities for Calimero contexts using JSON-RPC client.
"""

import sys

import click
from rich import box
from rich.table import Table

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import (
    ADMIN_API_IDENTITY_CONTEXT,
)
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url, run_async_function


def extract_identities_from_response(response_data: dict) -> list:
    """Extract identities from different possible response structures."""
    identities_data = response_data.get("identities")
    return identities_data if identities_data else []


def create_identity_table(identities_data: list, context_id: str) -> Table:
    """Create a table to display identities."""
    table = Table(title=f"Identities for Context {context_id}", box=box.ROUNDED)
    table.add_column("Identity ID", style="cyan")
    table.add_column("Context ID", style="cyan")
    table.add_column("Public Key", style="yellow")
    table.add_column("Status", style="blue")

    for identity_info in identities_data:
        if isinstance(identity_info, dict):
            # Handle case where identity_info is a dictionary
            table.add_row(
                identity_info.get("id", "Unknown"),
                identity_info.get("contextId", "Unknown"),
                identity_info.get("publicKey", "Unknown"),
                identity_info.get("status", "Unknown"),
            )
        else:
            # Handle case where identity_info is a string (just the ID)
            table.add_row(str(identity_info), context_id, "N/A", "Active")

    return table


@with_retry(config=NETWORK_RETRY_CONFIG)
async def list_identities_via_admin_api(
    rpc_url: str, context_id: str, node_name: str = None
) -> dict:
    """List identities using calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        context_id: Context ID to list identities for.
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        client = get_client_for_rpc_url(rpc_url, node_name=node_name)
        result = client.list_identities(context_id)
        return ok(result)
    except Exception as e:
        return fail("list_identities failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def generate_identity_via_admin_api(rpc_url: str, node_name: str = None) -> dict:
    """Generate identity using calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        client = get_client_for_rpc_url(rpc_url, node_name=node_name)
        result = client.generate_context_identity()
        return ok(result, endpoint=f"{rpc_url}{ADMIN_API_IDENTITY_CONTEXT}")
    except Exception as e:
        import traceback

        console.print(
            f"[red]Exception in generate_identity: {type(e).__name__}: {e}[/red]"
        )
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        return fail(f"generate_context_identity failed: {e}", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def create_namespace_invitation_via_admin_api(
    rpc_url: str,
    namespace_id: str,
    recursive: bool = False,
    node_name: str = None,
) -> dict:
    """Create a namespace invitation using calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        namespace_id: The namespace ID to create an invitation for.
        recursive: Whether to create a recursive invitation.
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        client = get_client_for_rpc_url(rpc_url, node_name=node_name)
        create_namespace_invitation = getattr(client, "create_namespace_invitation", None)
        if callable(create_namespace_invitation):
            result = create_namespace_invitation(
                namespace_id=namespace_id, recursive=recursive
            )
        else:
            # Backward compatibility for older client versions.
            result = client.create_group_invitation(namespace_id)
        return ok(result)
    except Exception as e:
        return fail("create_namespace_invitation failed", error=e)


# Deprecated alias kept for backward compatibility.
create_group_invitation_via_admin_api = create_namespace_invitation_via_admin_api


@click.group()
def identity():
    """Manage Calimero identities for contexts."""
    pass


@identity.command()
@click.option("--node", "-n", required=True, help="Node name to list identities from")
@click.option("--context-id", required=True, help="Context ID to list identities for")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_identities(node, context_id, verbose):
    """List identities for a specific context on a node."""
    manager = DockerManager()

    # Check if node is running
    # check_node_running(node, manager) # This function is removed from utils, so commenting out or removing

    # Get admin API URL and run listing
    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Listing identities for context {context_id} on node {node} via {admin_url}[/blue]"
    )

    result = run_async_function(list_identities_via_admin_api, admin_url, context_id)

    if result["success"]:
        response_data = result.get("data", {})
        identities_data = extract_identities_from_response(response_data)

        if not identities_data:
            console.print(
                f"\n[yellow]No identities found for context {context_id} on node {node}[/yellow]"
            )
            if verbose:
                console.print("\n[bold]Response structure:[/bold]")
                console.print(f"{result}")
            return

        console.print(f"\n[green]Found {len(identities_data)} identity(ies):[/green]")

        # Create and display table
        table = create_identity_table(identities_data, context_id)
        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to list identities[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)


@identity.command()
@click.option("--node", "-n", required=True, help="Node name to generate identity on")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def generate(node, verbose=False):
    """Generate a new identity using the admin API."""
    manager = DockerManager()

    # Check if node is running
    # check_node_running(node, manager) # This function is removed from utils, so commenting out or removing

    # Get admin API URL and run generation
    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Generating new identity on node {node} via {admin_url}[/blue]"
    )

    result = run_async_function(generate_identity_via_admin_api, admin_url)

    # Show which endpoint was used if successful
    if result["success"] and "endpoint" in result:
        console.print(f"[dim]Used endpoint: {result['endpoint']}[/dim]")

    if result["success"]:
        response_data = result.get("data", {})

        # Extract identity information from response
        identity_data = (
            response_data.get("identity") or response_data.get("data") or response_data
        )

        if identity_data:
            console.print("\n[green]✓ Identity generated successfully![/green]")

            # Create table
            table = Table(title="New Identity Details", box=box.ROUNDED)
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")

            if "id" in identity_data:
                table.add_row("Identity ID", identity_data["id"])
            if "publicKey" in identity_data:
                table.add_row("Public Key", identity_data["publicKey"])

            console.print(table)
        else:
            console.print("\n[green]✓ Identity generated successfully![/green]")
            console.print(f"[yellow]Response: {response_data}[/yellow]")

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to generate identity[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)


@identity.command("invite-group")
@click.option("--node", "-n", required=True, help="Node name to create invitation on")
@click.option("--group-id", required=True, help="Group ID to create invitation for")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def invite_group(node, group_id, verbose):
    """Create a group invitation (replaces the old context invite flow)."""
    manager = DockerManager()

    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Creating group invitation for group {group_id} on node {node} via {admin_url}[/blue]"
    )

    result = run_async_function(
        create_group_invitation_via_admin_api,
        admin_url,
        group_id,
    )

    if result["success"]:
        console.print("\n[green]✓ Group invitation created successfully![/green]")

        table = Table(title="Group Invitation Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Group ID", group_id)
        table.add_row("Node", node)

        console.print(table)

        import json

        response_data = result.get("data", {})
        if response_data:
            console.print("\n[bold cyan]Invitation Data:[/bold cyan]")
            invitation_json = json.dumps(response_data, indent=2)
            console.print(f"[yellow]{invitation_json}[/yellow]")
            console.print(
                "\n[dim]Save this invitation data to share with others who want to join the group.[/dim]"
            )

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to create group invitation[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")

        if "errors" in result:
            console.print("\n[yellow]Detailed errors:[/yellow]")
            for error in result["errors"]:
                console.print(f"[red]  {error}[/red]")

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

        sys.exit(1)


if __name__ == "__main__":
    identity()
