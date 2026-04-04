"""
Namespace command - Manage namespaces and namespace-scoped groups.
"""

import json as json_lib
import sys

import click
from rich import box
from rich.table import Table

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url, run_async_function


def unwrap_api_response(result: dict):
    """Unwrap nested API response envelopes."""
    data = result.get("data", {})
    if isinstance(data, dict):
        data = data.get("data", data)
    return data


@with_retry(config=NETWORK_RETRY_CONFIG)
async def _call_namespace_api_with_retry(
    rpc_url: str, method_name: str, *args, node_name: str = None, **kwargs
) -> dict:
    """Internal function that performs namespace API call with retry support."""
    client = get_client_for_rpc_url(rpc_url, node_name=node_name)
    method = getattr(client, method_name, None)
    if callable(method):
        result = method(*args, **kwargs)
        return ok(result)
    raise AttributeError(f"Client has no method '{method_name}'")


async def call_namespace_api(
    rpc_url: str, method_name: str, *args, node_name: str = None, **kwargs
) -> dict:
    """Public wrapper for namespace API calls."""
    try:
        return await _call_namespace_api_with_retry(
            rpc_url, method_name, *args, node_name=node_name, **kwargs
        )
    except Exception as e:
        return fail(f"{method_name} failed", error=e)


@click.group()
def namespace():
    """Manage namespaces."""
    pass


@namespace.command(name="list")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_namespaces(node, verbose):
    """List namespaces on a node."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Listing namespaces on node {node}[/blue]")

    result = run_async_function(
        call_namespace_api, rpc_url, "list_namespaces", node_name=node
    )
    if not result["success"]:
        console.print(f"[red]✗ Failed to list namespaces: {result.get('error')}[/red]")
        sys.exit(1)

    data = unwrap_api_response(result)
    namespaces = data if isinstance(data, list) else data.get("namespaces", [])
    if not namespaces:
        console.print(f"[yellow]No namespaces found on node {node}[/yellow]")
        return

    table = Table(title="Namespaces", box=box.ROUNDED)
    table.add_column("Namespace ID", style="cyan")
    table.add_column("Alias", style="yellow")
    table.add_column("Application ID", style="green")
    for ns in namespaces:
        if isinstance(ns, dict):
            table.add_row(
                str(ns.get("namespaceId", ns.get("groupId", ns.get("id", "Unknown")))),
                str(ns.get("alias", "N/A")),
                str(ns.get("applicationId", ns.get("targetApplicationId", "N/A"))),
            )
    console.print(table)
    if verbose:
        console.print(json_lib.dumps(result, indent=2))


@namespace.command(name="identity")
@click.argument("namespace_id")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def namespace_identity(namespace_id, node, verbose):
    """Get namespace identity."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Getting namespace identity for {namespace_id} on node {node}[/blue]"
    )

    result = run_async_function(
        call_namespace_api,
        rpc_url,
        "get_namespace_identity",
        namespace_id,
        node_name=node,
    )
    if not result["success"]:
        console.print(
            f"[red]✗ Failed to get namespace identity: {result.get('error')}[/red]"
        )
        sys.exit(1)

    data = unwrap_api_response(result)
    table = Table(title="Namespace Identity", box=box.ROUNDED)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Namespace ID", namespace_id)
    if isinstance(data, dict):
        for key, value in data.items():
            table.add_row(str(key), str(value))
    else:
        table.add_row("Identity", str(data))
    console.print(table)
    if verbose:
        console.print(json_lib.dumps(result, indent=2))


@namespace.command(name="create")
@click.argument("application_id")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--alias", help="Namespace alias")
@click.option("--upgrade-policy", help="Upgrade policy")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def create_namespace(node, application_id, alias, upgrade_policy, verbose):
    """Create a namespace."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Creating namespace for application {application_id} on node {node}[/blue]"
    )

    kwargs = {}
    if alias is not None:
        kwargs["alias"] = alias
    if upgrade_policy is not None:
        kwargs["upgrade_policy"] = upgrade_policy

    result = run_async_function(
        call_namespace_api,
        rpc_url,
        "create_namespace",
        application_id,
        node_name=node,
        **kwargs,
    )
    if not result["success"]:
        console.print(f"[red]✗ Failed to create namespace: {result.get('error')}[/red]")
        sys.exit(1)

    data = unwrap_api_response(result)
    namespace_id = data.get("namespaceId", data.get("groupId")) if isinstance(data, dict) else None
    console.print("[green]✓ Namespace created successfully![/green]")
    if namespace_id:
        console.print(f"[cyan]Namespace ID: {namespace_id}[/cyan]")
    if verbose:
        console.print(json_lib.dumps(result, indent=2))


@namespace.command(name="invite")
@click.argument("namespace_id")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--recursive", is_flag=True, help="Create recursive invitation")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def invite_namespace(namespace_id, node, recursive, verbose):
    """Create a namespace invitation."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Creating namespace invitation for {namespace_id} on node {node}[/blue]"
    )

    result = run_async_function(
        call_namespace_api,
        rpc_url,
        "create_namespace_invitation",
        namespace_id,
        node_name=node,
        recursive=recursive,
    )
    if not result["success"]:
        console.print(
            f"[red]✗ Failed to create namespace invitation: {result.get('error')}[/red]"
        )
        sys.exit(1)

    data = unwrap_api_response(result)
    console.print("[green]✓ Namespace invitation created[/green]")
    console.print(json_lib.dumps(data, indent=2))
    if verbose:
        console.print(json_lib.dumps(result, indent=2))


@namespace.command(name="join")
@click.argument("namespace_id")
@click.argument("invitation_json")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def join_namespace(namespace_id, invitation_json, node, verbose):
    """Join a namespace using invitation JSON."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Joining namespace {namespace_id} on node {node}[/blue]")

    result = run_async_function(
        call_namespace_api,
        rpc_url,
        "join_namespace",
        namespace_id,
        invitation_json,
        node_name=node,
    )
    if not result["success"]:
        console.print(f"[red]✗ Failed to join namespace: {result.get('error')}[/red]")
        sys.exit(1)

    console.print("[green]✓ Joined namespace successfully![/green]")
    if verbose:
        console.print(json_lib.dumps(result, indent=2))


@namespace.command(name="groups")
@click.argument("namespace_id")
@click.option("--node", "-n", required=True, help="Node name")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_namespace_groups(namespace_id, node, verbose):
    """List groups in a namespace."""
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)
    console.print(f"[blue]Listing groups for namespace {namespace_id} on node {node}[/blue]")

    result = run_async_function(
        call_namespace_api,
        rpc_url,
        "list_namespace_groups",
        namespace_id,
        node_name=node,
    )
    if not result["success"]:
        console.print(
            f"[red]✗ Failed to list namespace groups: {result.get('error')}[/red]"
        )
        sys.exit(1)

    data = unwrap_api_response(result)
    groups = data if isinstance(data, list) else data.get("groups", [])
    if not groups:
        console.print(f"[yellow]No groups found for namespace {namespace_id}[/yellow]")
        return

    table = Table(title="Namespace Groups", box=box.ROUNDED)
    table.add_column("Group ID", style="cyan")
    table.add_column("Alias", style="yellow")
    for g in groups:
        if isinstance(g, dict):
            table.add_row(
                str(g.get("groupId", g.get("id", "Unknown"))),
                str(g.get("alias", "N/A")),
            )
    console.print(table)
    if verbose:
        console.print(json_lib.dumps(result, indent=2))
