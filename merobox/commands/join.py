"""
Join command - Join Calimero namespaces and contexts via admin API.

The new flow uses namespaces instead of direct context invitations:
- join_namespace_via_admin_api: Join a namespace using a namespace invitation
- join_context_via_admin_api: Join a specific context via namespace/group membership
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
async def join_namespace_via_admin_api(
    rpc_url: str,
    namespace_id: str,
    invitation_data,
    node_name: str = None,
) -> dict:
    """Join a namespace using an invitation via calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        namespace_id: Namespace ID to join.
        invitation_data: Invitation JSON (dict or string) from create_namespace_invitation.
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        import json as json_lib

        client = get_client_for_rpc_url(rpc_url, node_name=node_name)

        # The calimero-client-py join_namespace() expects the raw
        # SignedGroupOpenInvitation JSON. Unwrap nested {"invitation": ...}
        # layers until we reach the actual invitation (has inviter_signature).
        if isinstance(invitation_data, dict):
            while (
                "invitation" in invitation_data
                and isinstance(invitation_data["invitation"], dict)
                and "inviter_signature" not in invitation_data
            ):
                invitation_data = invitation_data["invitation"]
            invitation_json = json_lib.dumps(invitation_data)
        elif isinstance(invitation_data, str):
            try:
                parsed = json_lib.loads(invitation_data)
                if isinstance(parsed, dict):
                    while (
                        "invitation" in parsed
                        and isinstance(parsed["invitation"], dict)
                        and "inviter_signature" not in parsed
                    ):
                        parsed = parsed["invitation"]
                    invitation_json = json_lib.dumps(parsed)
                else:
                    invitation_json = invitation_data
            except (json_lib.JSONDecodeError, ValueError):
                invitation_json = invitation_data
        else:
            invitation_json = str(invitation_data)

        join_namespace = getattr(client, "join_namespace", None)
        if callable(join_namespace):
            result = join_namespace(
                namespace_id=namespace_id, invitation_json=invitation_json
            )
        else:
            # Backward compatibility for older client versions.
            result = client.join_group(invitation_json=invitation_json)
        return ok(result)
    except Exception as e:
        return fail("join_namespace failed", error=e)


# Deprecated alias kept for backward compatibility.
join_group_via_admin_api = join_namespace_via_admin_api


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
    """Join Calimero namespaces and contexts."""
    pass


@join.command("namespace")
@click.option("--node", "-n", required=True, help="Node name to join namespace on")
@click.option("--namespace-id", required=True, help="Namespace ID to join")
@click.option(
    "--invitation",
    required=True,
    help="Namespace invitation JSON data (from namespace invite command)",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def join_namespace_cmd(node, namespace_id, invitation, verbose):
    """Join a namespace using an invitation."""
    manager = DockerManager()

    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Joining namespace {namespace_id} on node {node} via invitation[/blue]"
    )

    result = run_async_function(
        join_namespace_via_admin_api, admin_url, namespace_id, invitation, node
    )

    if result["success"]:
        console.print("\n[green]✓ Successfully joined namespace![/green]")

        response_data = result.get("data", {})

        table = Table(title="Namespace Join Details", box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        if isinstance(response_data, dict):
            nested = response_data.get("data", response_data)
            if isinstance(nested, dict):
                joined_namespace_id = nested.get(
                    "namespaceId", nested.get("groupId", "N/A")
                )
                table.add_row("Namespace ID", joined_namespace_id)

        table.add_row("Node", node)
        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to join namespace[/red]")
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
    """Join a context via namespace/group membership."""
    manager = DockerManager()

    admin_url = get_node_rpc_url(node, manager)
    console.print(
        f"[blue]Joining context {context_id} on node {node} via namespace/group membership[/blue]"
    )

    result = run_async_function(join_context_via_admin_api, admin_url, context_id)

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
