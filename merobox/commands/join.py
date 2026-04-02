"""
Join command - Join Calimero groups and contexts via admin API.

The new flow uses groups instead of direct context invitations:
- join_group_via_admin_api: Join a group using a group invitation (POST /groups/join)
- join_context_via_admin_api: Join a specific context via group membership (POST /contexts/:id/join)
"""

import sys

import click
from rich import box
from rich.table import Table

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url, run_async_function


@with_retry(config=NETWORK_RETRY_CONFIG)
async def join_group_via_admin_api(
    rpc_url: str,
    invitation_data,
    node_name: str = None,
) -> dict:
    """Join a group using a group invitation via calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        invitation_data: Invitation JSON (dict or string) from create_group_invitation.
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        import json as json_lib

        client = get_client_for_rpc_url(rpc_url, node_name=node_name)

        if isinstance(invitation_data, dict):
            invitation_json = json_lib.dumps(invitation_data)
        else:
            invitation_json = str(invitation_data)

        result = client.join_group(invitation_json=invitation_json)
        return ok(result)
    except Exception as e:
        return fail("join_group failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def join_context_via_admin_api(
    rpc_url: str,
    context_id: str,
    node_name: str = None,
) -> dict:
    """Join a context via group membership using calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        context_id: The context ID to join (requires group membership).
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        client = get_client_for_rpc_url(rpc_url, node_name=node_name)
        result = client.join_context(context_id=context_id)
        return ok(result)
    except Exception as e:
        return fail("join_context failed", error=e)


@click.group()
def join():
    """Join Calimero groups and contexts."""
    pass


@join.command("group")
@click.option("--node", "-n", required=True, help="Node name to join group on")
@click.option(
    "--invitation",
    required=True,
    help="Group invitation JSON data (from create-group-invitation command)",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def join_group_cmd(node, invitation, verbose):
    """Join a group using a group invitation."""
    manager = DockerManager()

    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Joining group on node {node} via group invitation[/blue]"
    )

    result = run_async_function(
        join_group_via_admin_api, admin_url, invitation
    )

    if result["success"]:
        console.print(
            "\n[green]✓ Successfully joined group![/green]"
        )

        response_data = result.get("data", {})

        table = Table(title="Group Join Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        if isinstance(response_data, dict):
            nested = response_data.get("data", response_data)
            if isinstance(nested, dict):
                group_id = nested.get("groupId", "N/A")
                table.add_row("Group ID", group_id)

        table.add_row("Node", node)
        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to join group[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")

        if "errors" in result:
            console.print("\n[yellow]Detailed errors:[/yellow]")
            for error in result["errors"]:
                console.print(f"[red]  {error}[/red]")

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

        sys.exit(1)


@join.command("context")
@click.option("--node", "-n", required=True, help="Node name to join context on")
@click.option("--context-id", required=True, help="Context ID to join")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def join_context_cmd(node, context_id, verbose):
    """Join a context via group membership."""
    manager = DockerManager()

    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Joining context {context_id} on node {node} via group membership[/blue]"
    )

    result = run_async_function(
        join_context_via_admin_api, admin_url, context_id
    )

    if result["success"]:
        console.print("\n[green]✓ Successfully joined context![/green]")

        response_data = result.get("data", {})

        table = Table(title="Context Join Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Context ID", context_id)
        table.add_row("Node", node)

        if isinstance(response_data, dict):
            nested = response_data.get("data", response_data)
            if isinstance(nested, dict):
                member_pk = nested.get("memberPublicKey", "N/A")
                table.add_row("Member Public Key", member_pk)

        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to join context[/red]")
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
    join()
