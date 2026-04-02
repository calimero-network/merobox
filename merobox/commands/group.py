"""
Group command - Create, list, and manage context groups for Calimero nodes.
"""

import json as json_lib
import sys
from typing import Any

import click
from rich import box
from rich.table import Table

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url, run_async_function


def unwrap_api_response(result: dict) -> Any:
    """Unwrap the nested ``{"data": {"data": ...}}`` envelope returned by admin API calls."""
    data = result.get("data", {})
    if isinstance(data, dict):
        data = data.get("data", data)
    return data


@with_retry(config=NETWORK_RETRY_CONFIG)
async def _call_admin_api_with_retry(
    rpc_url: str, method_name: str, *args: Any, node_name: str = None
) -> dict:
    """Internal function that performs the admin API call with retry support.

    This function may raise exceptions to trigger the retry decorator.
    Use call_admin_api() for the public API that always returns result objects.
    """
    client = get_client_for_rpc_url(rpc_url, node_name=node_name)
    result = getattr(client, method_name)(*args)
    return ok(result)


async def call_admin_api(
    rpc_url: str, method_name: str, *args: Any, node_name: str = None
) -> dict:
    """Generic wrapper that calls *method_name* on the admin client with retry.

    Always returns a result dict with a 'success' key. Never raises exceptions -
    all errors (including network failures after retry exhaustion) are converted
    to fail() results.
    """
    try:
        return await _call_admin_api_with_retry(
            rpc_url, method_name, *args, node_name=node_name
        )
    except Exception as e:
        return fail(f"{method_name} failed", error=e)


# ---- CLI command group ----


@click.group()
def group():
    """Manage context groups."""
    pass


@group.command(name="list")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_groups(node, verbose):
    """List all groups on a node."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Listing groups on node {node}[/blue]")

    result = run_async_function(call_admin_api, rpc_url, "list_groups")

    if result["success"]:
        data = unwrap_api_response(result)

        groups = []
        if isinstance(data, dict) and "groups" in data:
            groups = data["groups"] if isinstance(data["groups"], list) else []
        elif isinstance(data, list):
            groups = data

        if not groups:
            console.print(f"[yellow]No groups found on node {node}[/yellow]")
            return

        table = Table(title="Groups", box=box.ROUNDED)
        table.add_column("Group ID", style="cyan")
        table.add_column("Application ID", style="yellow")
        table.add_column("Members", style="green")
        table.add_column("Contexts", style="magenta")

        for g in groups:
            if isinstance(g, dict):
                table.add_row(
                    g.get("groupId", g.get("id", "Unknown")),
                    g.get("targetApplicationId", "Unknown"),
                    str(g.get("memberCount", "?")),
                    str(g.get("contextCount", "?")),
                )

        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(json_lib.dumps(result, indent=2))
    else:
        console.print(f"[red]✗ Failed to list groups: {result.get('error')}[/red]")
        sys.exit(1)


@group.command()
@click.option("--node", "-n", required=True, help="Node name")
@click.option(
    "--application-id", "-a", required=True, help="Application ID for the group"
)
@click.option(
    "--parent-group-id",
    "-p",
    default=None,
    help="Parent group ID to create this as a subgroup",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def create(node, application_id, parent_group_id, verbose):
    """Create a new group."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Creating group for application {application_id} on node {node}[/blue]"
    )

    result = run_async_function(
        call_admin_api, rpc_url, "create_group", application_id, parent_group_id
    )

    if result["success"]:
        data = unwrap_api_response(result)
        group_id = data.get("groupId") if isinstance(data, dict) else None
        console.print("[green]✓ Group created successfully![/green]")
        if group_id:
            console.print(f"[cyan]Group ID: {group_id}[/cyan]")
        if verbose:
            console.print(json_lib.dumps(result, indent=2))
    else:
        console.print(f"[red]✗ Failed to create group: {result.get('error')}[/red]")
        sys.exit(1)


@group.command()
@click.argument("group_id")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def get(group_id, node, verbose):
    """Get group information."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Getting group {group_id} from node {node}[/blue]")

    result = run_async_function(call_admin_api, rpc_url, "get_group_info", group_id)

    if result["success"]:
        data = unwrap_api_response(result)

        console.print("[green]✓ Group info:[/green]")
        if isinstance(data, dict):
            for key, value in data.items():
                console.print(f"[cyan]{key}:[/cyan] {value}")

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(json_lib.dumps(result, indent=2))
    else:
        console.print(f"[red]✗ Failed to get group: {result.get('error')}[/red]")
        sys.exit(1)


@group.command()
@click.argument("group_id")
@click.option("--node", "-n", required=True, help="Node name")
def delete(group_id, node):
    """Delete a group (must have no registered contexts)."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Deleting group {group_id} on node {node}[/blue]")

    result = run_async_function(call_admin_api, rpc_url, "delete_group", group_id)

    if result["success"]:
        console.print("[green]✓ Group deleted successfully![/green]")
    else:
        console.print(f"[red]✗ Failed to delete group: {result.get('error')}[/red]")
        sys.exit(1)


@group.command()
@click.argument("group_id")
@click.option("--node", "-n", required=True, help="Node name")
def invite(group_id, node):
    """Create a group invitation (prints invitation JSON for sharing)."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Creating invitation for group {group_id} on node {node}[/blue]"
    )

    result = run_async_function(
        call_admin_api, rpc_url, "create_group_invitation", group_id
    )

    if result["success"]:
        data = unwrap_api_response(result)

        # Print the full SignedGroupOpenInvitation (invitation + inviter_signature),
        # not just the inner GroupInvitationFromAdmin, so the joiner has complete data.
        console.print(
            "[green]✓ Invitation created. Share this JSON with the joiner:[/green]"
        )
        console.print(json_lib.dumps(data, indent=2))
    else:
        console.print(
            f"[red]✗ Failed to create invitation: {result.get('error')}[/red]"
        )
        sys.exit(1)


@group.command(name="join")
@click.argument("invitation_json")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def join_group_cmd(invitation_json, node, verbose):
    """Join a group using an invitation JSON string."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)

    # Validate JSON before sending
    try:
        json_lib.loads(invitation_json)
    except json_lib.JSONDecodeError as e:
        console.print(f"[red]✗ Invalid invitation JSON: {e}[/red]")
        sys.exit(1)

    console.print(f"[blue]Joining group on node {node}[/blue]")

    result = run_async_function(call_admin_api, rpc_url, "join_group", invitation_json)

    if result["success"]:
        data = unwrap_api_response(result)
        console.print("[green]✓ Joined group successfully![/green]")
        if isinstance(data, dict):
            if "groupId" in data:
                console.print(f"[cyan]Group ID: {data['groupId']}[/cyan]")
            if "memberIdentity" in data:
                console.print(f"[cyan]Member Identity: {data['memberIdentity']}[/cyan]")
        if verbose:
            console.print(json_lib.dumps(result, indent=2))
    else:
        console.print(f"[red]✗ Failed to join group: {result.get('error')}[/red]")
        sys.exit(1)


@group.group(name="members")
def group_members():
    """Manage group members."""
    pass


@group_members.command(name="list")
@click.argument("group_id")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_members(group_id, node, verbose):
    """List members of a group."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Listing members of group {group_id} on node {node}[/blue]")

    result = run_async_function(call_admin_api, rpc_url, "list_group_members", group_id)

    if result["success"]:
        data = unwrap_api_response(result)

        members = []
        if isinstance(data, dict) and "members" in data:
            members = data["members"] if isinstance(data["members"], list) else []
        elif isinstance(data, list):
            members = data

        if not members:
            console.print(f"[yellow]No members found in group {group_id}[/yellow]")
            return

        table = Table(title="Group Members", box=box.ROUNDED)
        table.add_column("Identity", style="cyan")
        table.add_column("Role", style="yellow")

        for m in members:
            if isinstance(m, dict):
                table.add_row(
                    m.get("identity", m.get("id", "Unknown")),
                    m.get("role", "Member"),
                )

        console.print(table)

        if verbose:
            console.print(json_lib.dumps(result, indent=2))
    else:
        console.print(f"[red]✗ Failed to list members: {result.get('error')}[/red]")
        sys.exit(1)


@group.group(name="contexts")
def group_contexts():
    """Manage group contexts."""
    pass


@group_contexts.command(name="list")
@click.argument("group_id")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_contexts_in_group(group_id, node, verbose):
    """List contexts in a group."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Listing contexts in group {group_id} on node {node}[/blue]")

    result = run_async_function(
        call_admin_api, rpc_url, "list_group_contexts", group_id
    )

    if result["success"]:
        data = unwrap_api_response(result)

        contexts = []
        if isinstance(data, dict) and "contexts" in data:
            contexts = data["contexts"] if isinstance(data["contexts"], list) else []
        elif isinstance(data, list):
            contexts = data

        if not contexts:
            console.print(f"[yellow]No contexts found in group {group_id}[/yellow]")
            return

        table = Table(title="Group Contexts", box=box.ROUNDED)
        table.add_column("Context ID", style="cyan")
        table.add_column("Application ID", style="yellow")

        for ctx in contexts:
            if isinstance(ctx, dict):
                table.add_row(
                    ctx.get("contextId", ctx.get("id", "Unknown")),
                    ctx.get("applicationId", "Unknown"),
                )
            else:
                table.add_row(str(ctx), "Unknown")

        console.print(table)

        if verbose:
            console.print(json_lib.dumps(result, indent=2))
    else:
        console.print(
            f"[red]✗ Failed to list group contexts: {result.get('error')}[/red]"
        )
        sys.exit(1)


@group.command(name="join-context")
@click.argument("group_id")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--context-id", "-c", required=True, help="Context ID to join")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def join_context(group_id, node, context_id, verbose):
    """Join an existing context via group membership."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Joining context {context_id} in group {group_id} on node {node}[/blue]"
    )

    result = run_async_function(
        call_admin_api, rpc_url, "join_context", context_id
    )

    if result["success"]:
        data = unwrap_api_response(result)
        console.print("[green]✓ Joined group context successfully![/green]")
        if isinstance(data, dict):
            if "contextId" in data:
                console.print(f"[cyan]Context ID: {data['contextId']}[/cyan]")
            if "memberPublicKey" in data:
                console.print(
                    f"[cyan]Member Public Key: {data['memberPublicKey']}[/cyan]"
                )
        if verbose:
            console.print(json_lib.dumps(result, indent=2))
    else:
        console.print(
            f"[red]✗ Failed to join group context: {result.get('error')}[/red]"
        )
        sys.exit(1)
