"""
Identity command - List and generate identities for Calimero contexts using JSON-RPC client.
"""

import sys

import click
from rich import box
from rich.table import Table

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import (
    ADMIN_API_CONTEXTS_INVITE,
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
async def list_identities_via_admin_api(rpc_url: str, context_id: str) -> dict:
    """List identities using calimero-client-py."""
    try:
        client = get_client_for_rpc_url(rpc_url)
        result = client.list_identities(context_id)
        return ok(result)
    except Exception as e:
        return fail("list_identities failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def generate_identity_via_admin_api(rpc_url: str) -> dict:
    """Generate identity using calimero-client-py."""
    try:
        client = get_client_for_rpc_url(rpc_url)
        result = client.generate_context_identity()
        return ok(result, endpoint=f"{rpc_url}{ADMIN_API_IDENTITY_CONTEXT}")
    except Exception as e:
        return fail("generate_context_identity failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def invite_identity_via_admin_api(
    rpc_url: str,
    context_id: str,
    inviter_id: str,
    invitee_id: str,
    capability: str = None,
) -> dict:
    """Invite identity using calimero-client-py."""
    try:
        client = get_client_for_rpc_url(rpc_url)
        # Some clients may not need inviter id; keeping parameter for compatibility
        result = client.invite_to_context(
            context_id=context_id, inviter_id=inviter_id, invitee_id=invitee_id
        )
        return ok(
            result, endpoint=f"{rpc_url}{ADMIN_API_CONTEXTS_INVITE}", payload_format=0
        )
    except Exception as e:
        return fail("invite_to_context failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def create_open_invitation_via_admin_api(
    rpc_url: str, context_id: str, inviter_id: str, valid_for_blocks: int = 1000
) -> dict:
    """Create an open invitation using calimero-client-py."""
    try:
        client = get_client_for_rpc_url(rpc_url)
        result = client.invite_to_context_by_open_invitation(
            context_id=context_id,
            inviter_id=inviter_id,
            valid_for_blocks=valid_for_blocks,
        )
        return ok(
            result,
            endpoint=f"{rpc_url}/admin-api/dev/contexts/invite-open",
            payload_format=0,
        )
    except Exception as e:
        return fail("create_open_invitation failed", error=e)


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


@identity.command()
@click.option("--node", "-n", required=True, help="Node name to invite identity on")
@click.option("--context-id", required=True, help="Context ID to invite identity to")
@click.option(
    "--inviter-id", required=True, help="Public key of the inviter (context member)"
)
@click.option(
    "--invitee-id", required=True, help="Public key of the identity to invite"
)
@click.option(
    "--capability",
    default=None,
    help="Capability (not used in invitation, kept for compatibility)",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def invite(node, context_id, inviter_id, invitee_id, capability, verbose):
    """Invite an identity to join a context."""
    manager = DockerManager()

    # Check if node is running
    # check_node_running(node, manager) # This function is removed from utils, so commenting out or removing

    # Get admin API URL and run invitation
    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Inviting identity {invitee_id} to context {context_id} on node {node} via {admin_url}[/blue]"
    )

    result = run_async_function(
        invite_identity_via_admin_api,
        admin_url,
        context_id,
        inviter_id,
        invitee_id,
        capability,
    )

    # Show which endpoint was used if successful
    if result["success"] and "endpoint" in result:
        console.print(f"[dim]Used endpoint: {result['endpoint']}[/dim]")

    if result["success"]:
        console.print("\n[green]✓ Identity invited successfully![/green]")

        # Create table
        table = Table(title="Identity Invitation Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Context ID", context_id)
        table.add_row("Inviter ID", inviter_id)
        table.add_row("Invitee ID", invitee_id)

        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to invite identity[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")

        # Show detailed error information if available
        if "errors" in result:
            console.print("\n[yellow]Detailed errors:[/yellow]")
            for error in result["errors"]:
                console.print(f"[red]  {error}[/red]")

        if "tried_payloads" in result:
            console.print("\n[yellow]Tried payload formats:[/yellow]")
            for i, payload in enumerate(result["tried_payloads"]):
                console.print(f"[dim]  Format {i}: {payload}[/dim]")

        # Provide helpful information for common errors
        if "unable to grant privileges to non-member" in result.get("error", ""):
            console.print(
                "\n[yellow]Note: This error suggests the invite endpoint might not be working as expected.[/yellow]"
            )
            console.print(
                "[yellow]The identity should be automatically added as a member when invited.[/yellow]"
            )

        sys.exit(1)


@identity.command("invite-open")
@click.option("--node", "-n", required=True, help="Node name to create invitation on")
@click.option("--context-id", required=True, help="Context ID to create invitation for")
@click.option(
    "--inviter-id", required=True, help="Public key of the inviter (context member)"
)
@click.option(
    "--valid-for-blocks",
    default=1000,
    help="Number of blocks the invitation is valid for (default: 1000)",
    type=int,
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def invite_open(node, context_id, inviter_id, valid_for_blocks, verbose):
    """Create an open invitation that can be used by multiple identities."""
    manager = DockerManager()

    # Get admin API URL and run invitation creation
    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Creating open invitation for context {context_id} on node {node}[/blue]"
    )
    console.print(f"[blue]Valid for {valid_for_blocks} blocks[/blue]")

    result = run_async_function(
        create_open_invitation_via_admin_api,
        admin_url,
        context_id,
        inviter_id,
        valid_for_blocks,
    )

    # Show which endpoint was used if successful
    if result["success"] and "endpoint" in result:
        console.print(f"[dim]Used endpoint: {result['endpoint']}[/dim]")

    if result["success"]:
        console.print("\n[green]✓ Open invitation created successfully![/green]")

        # Create table
        table = Table(title="Open Invitation Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Context ID", context_id)
        table.add_row("Inviter ID", inviter_id)
        table.add_row("Valid for Blocks", str(valid_for_blocks))

        console.print(table)

        # Display the invitation data
        import json

        response_data = result.get("data", {})
        if response_data:
            console.print("\n[bold cyan]Invitation Data:[/bold cyan]")
            invitation_json = json.dumps(response_data, indent=2)
            console.print(f"[yellow]{invitation_json}[/yellow]")
            console.print(
                "\n[dim]Save this invitation data to share with others who want to join.[/dim]"
            )

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to create open invitation[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")

        # Show detailed error information if available
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
