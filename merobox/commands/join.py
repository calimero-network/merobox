"""
Join command - Join Calimero contexts using invitations via admin API.
"""

import sys

import click
from rich import box
from rich.table import Table

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import ADMIN_API_CONTEXTS_JOIN
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url, run_async_function


@with_retry(config=NETWORK_RETRY_CONFIG)
async def join_context_via_admin_api(
    rpc_url: str, context_id: str, invitee_id: str, invitation_data: str
) -> dict:
    """Join a context using calimero-client-py."""
    try:
        client = get_client_for_rpc_url(rpc_url)

        result = client.join_context(
            context_id=context_id,
            invitee_id=invitee_id,
            invitation_payload=invitation_data,
        )
        return ok(
            result, endpoint=f"{rpc_url}{ADMIN_API_CONTEXTS_JOIN}", payload_format=0
        )
    except Exception as e:
        return fail("join_context failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def join_context_via_open_invitation(
    rpc_url: str, invitation_json: str, new_member_public_key: str
) -> dict:
    """Join a context using an open invitation via calimero-client-py."""
    try:

        client = get_client_for_rpc_url(rpc_url)

        # The client expects two separate parameters:
        # 1. invitation_json: The SignedOpenInvitation as JSON string
        # 2. new_member_public_key: The public key as string
        # invitation_json is already a JSON string, so we can pass it directly

        result = client.join_context_by_open_invitation(
            invitation_json=invitation_json, new_member_public_key=new_member_public_key
        )
        return ok(
            result,
            endpoint=f"{rpc_url}/admin-api/dev/contexts/join-open",
            payload_format=0,
        )
    except Exception as e:
        return fail("join_context_via_open_invitation failed", error=e)


@click.group()
def join():
    """Join Calimero contexts using invitations."""
    pass


@join.command()
@click.option("--node", "-n", required=True, help="Node name to join context on")
@click.option("--context-id", required=True, help="Context ID to join")
@click.option(
    "--invitee-id", required=True, help="Public key of the identity joining the context"
)
@click.option(
    "--invitation", required=True, help="Invitation data/token to join the context"
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def context(node, context_id, invitee_id, invitation, verbose):
    """Join a context using an invitation."""
    manager = DockerManager()

    # Get admin API URL and run join
    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Joining context {context_id} on node {node} as {invitee_id} via {admin_url}[/blue]"
    )

    result = run_async_function(
        join_context_via_admin_api, admin_url, context_id, invitee_id, invitation
    )

    # Show which endpoint was used if successful
    if result["success"] and "endpoint" in result:
        console.print(f"[dim]Used endpoint: {result['endpoint']}[/dim]")

    if result["success"]:
        console.print("\n[green]✓ Successfully joined context![/green]")

        # Create table
        table = Table(title="Context Join Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Context ID", context_id)
        table.add_row("Invitee ID", invitee_id)
        table.add_row("Node", node)
        table.add_row("Payload Format", str(result.get("payload_format", "N/A")))

        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to join context[/red]")
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


@join.command("open")
@click.option("--node", "-n", required=True, help="Node name to join context on")
@click.option(
    "--invitee-id", required=True, help="Public key of the identity joining the context"
)
@click.option(
    "--invitation",
    required=True,
    help="Open invitation JSON data (from invite-open command)",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def open_invitation(node, invitee_id, invitation, verbose):
    """Join a context using an open invitation."""
    manager = DockerManager()

    # Get admin API URL and run join
    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Joining context on node {node} as {invitee_id} via open invitation[/blue]"
    )

    result = run_async_function(
        join_context_via_open_invitation, admin_url, invitation, invitee_id
    )

    # Show which endpoint was used if successful
    if result["success"] and "endpoint" in result:
        console.print(f"[dim]Used endpoint: {result['endpoint']}[/dim]")

    if result["success"]:
        console.print(
            "\n[green]✓ Successfully joined context via open invitation![/green]"
        )

        response_data = result.get("data", {})

        # Create table
        table = Table(title="Context Join Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        # Extract context ID and member public key from response
        context_id = response_data.get("contextId", "N/A")
        member_public_key = response_data.get("memberPublicKey", "N/A")

        table.add_row("Context ID", context_id)
        table.add_row("Member Public Key", member_public_key)
        table.add_row("Invitee ID", invitee_id)
        table.add_row("Node", node)

        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to join context via open invitation[/red]")
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
    join()
