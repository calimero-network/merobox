"""
Context command - Create, list, and show contexts for Calimero nodes.
"""

import json as json_lib
import sys

import click
from rich import box
from rich.table import Table

from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import DEFAULT_PROTOCOL
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url, run_async_function


@with_retry(config=NETWORK_RETRY_CONFIG)
async def create_context_via_admin_api(
    rpc_url: str,
    application_id: str,
    protocol: str = None,
    params: str = None,
    node_name: str = None,
) -> dict:
    """Create a context using calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        application_id: Application ID to create context for.
        protocol: Optional protocol type.
        params: Optional initialization parameters as JSON string.
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        client = get_client_for_rpc_url(rpc_url, node_name=node_name)
        protocol = protocol or DEFAULT_PROTOCOL
        api_result = client.create_context(
            application_id=application_id, protocol=protocol, params=params
        )
        return ok(api_result)
    except Exception as e:
        return fail("create_context failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def list_contexts_via_admin_api(rpc_url: str, node_name: str = None) -> dict:
    """List contexts using calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        client = get_client_for_rpc_url(rpc_url, node_name=node_name)
        result = client.list_contexts()
        return ok(result)
    except Exception as e:
        return fail("list_contexts failed", error=e)


@with_retry(config=NETWORK_RETRY_CONFIG)
async def get_context_via_admin_api(
    rpc_url: str, context_id: str, node_name: str = None
) -> dict:
    """Get context details using calimero-client-py.

    Args:
        rpc_url: The RPC URL to connect to.
        context_id: Context ID to get details for.
        node_name: Optional node name for token caching (required for authenticated nodes).
    """
    try:
        client = get_client_for_rpc_url(rpc_url, node_name=node_name)
        result = client.get_context(context_id)
        return ok(result)
    except Exception as e:
        return fail("get_context failed", error=e)


def create_context_table(contexts_data: list) -> Table:
    """Create a table to display contexts."""
    table = Table(title="Contexts", box=box.ROUNDED)
    table.add_column("Context ID", style="cyan")
    table.add_column("Application ID", style="yellow")
    table.add_column("Member Public Key", style="green")

    for context_info in contexts_data:
        if isinstance(context_info, dict):
            # Handle both "id" and "contextId" fields (API uses "id")
            context_id = context_info.get("id") or context_info.get(
                "contextId", "Unknown"
            )
            application_id = context_info.get("applicationId", "Unknown")
            member_public_key = context_info.get("memberPublicKey", "N/A")
            table.add_row(
                context_id,
                application_id,
                member_public_key,
            )
        else:
            # Handle case where context_info is a string (just the ID)
            table.add_row(str(context_info), "N/A", "N/A")

    return table


@click.group()
def context():
    """Manage blockchain contexts."""
    pass


@context.command()
@click.option("--node", "-n", required=True, help="Node name to create context on")
@click.option(
    "--application-id",
    "-a",
    required=True,
    help="Application ID to create context for",
)
@click.option(
    "--protocol",
    "-p",
    default=DEFAULT_PROTOCOL,
    help=f"Protocol type (default: {DEFAULT_PROTOCOL})",
)
@click.option(
    "--params",
    help="Initialization parameters as JSON string (optional)",
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def create(node, application_id, protocol, params, verbose):
    """Create a new context for an application."""
    manager = DockerManager()

    # Validate params if provided
    params_json = None
    if params:
        try:
            json_lib.loads(params)
            params_json = params
        except json_lib.JSONDecodeError as e:
            console.print(f"[red]✗ Invalid JSON in --params: {str(e)}[/red]")
            sys.exit(1)

    # Get admin API URL
    rpc_url = get_node_rpc_url(node, manager)

    console.print(
        f"[blue]Creating context for application {application_id} on node {node}[/blue]"
    )

    result = run_async_function(
        create_context_via_admin_api, rpc_url, application_id, protocol, params_json
    )

    if result["success"]:
        console.print("\n[green]✓ Context created successfully![/green]")

        # Extract and display context ID and member public key
        response_data = result.get("data", {})
        if isinstance(response_data, dict):
            # Handle nested data structure
            actual_data = response_data.get("data", response_data)
            if isinstance(actual_data, dict):
                context_id = actual_data.get(
                    "contextId", actual_data.get("id", actual_data.get("name"))
                )
                member_public_key = actual_data.get("memberPublicKey")

                if context_id:
                    console.print(f"[cyan]Context ID: {context_id}[/cyan]")
                if member_public_key:
                    console.print(
                        f"[cyan]Member Public Key: {member_public_key}[/cyan]"
                    )

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            try:
                formatted_data = json_lib.dumps(response_data, indent=2)
                console.print(f"{formatted_data}")
            except Exception:
                console.print(f"{response_data}")
    else:
        console.print("\n[red]✗ Failed to create context[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")

        if verbose and "exception" in result:
            exc = result["exception"]
            console.print(f"[red]Exception Type: {exc.get('type', 'Unknown')}[/red]")
            console.print(
                f"[red]Exception Message: {exc.get('message', 'No message')}[/red]"
            )
            if "traceback" in exc:
                console.print("\n[bold]Traceback:[/bold]")
                console.print(f"[red]{exc['traceback']}[/red]")

        sys.exit(1)


@context.command(name="list")
@click.option("--node", "-n", required=True, help="Node name to list contexts from")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def list_contexts(node, verbose):
    """List all contexts on a node."""
    manager = DockerManager()

    # Get admin API URL
    rpc_url = get_node_rpc_url(node, manager)

    console.print(f"[blue]Listing contexts on node {node}[/blue]")

    result = run_async_function(list_contexts_via_admin_api, rpc_url)

    if result["success"]:
        response_data = result.get("data", {})

        # Handle nested data structure: result["data"]["data"]["contexts"]
        # The API returns: {"data": {"data": {"contexts": [...]}}}
        if isinstance(response_data, dict):
            # First level: unwrap if there's a "data" key
            if "data" in response_data:
                response_data = response_data["data"]

            # Second level: look for "contexts" key
            if isinstance(response_data, dict) and "contexts" in response_data:
                contexts_value = response_data["contexts"]
                if isinstance(contexts_value, list):
                    contexts_data = contexts_value
                elif isinstance(contexts_value, dict):
                    # Convert dict to list of context objects
                    # Keys are context IDs, values are context objects
                    contexts_data = []
                    for key, value in contexts_value.items():
                        if isinstance(value, dict):
                            # Ensure id/contextId is set
                            context_obj = {**value}
                            if (
                                "contextId" not in context_obj
                                and "id" not in context_obj
                            ):
                                context_obj["id"] = key
                            contexts_data.append(context_obj)
                        else:
                            contexts_data.append({"id": key, "data": value})
                else:
                    contexts_data = []
            elif isinstance(response_data, list):
                # Direct list of contexts
                contexts_data = response_data
            elif isinstance(response_data, dict):
                # Check if this is a dict of contexts (keys might be context IDs)
                # If all values are dicts, treat as context objects
                if response_data and all(
                    isinstance(v, dict) for v in response_data.values()
                ):
                    contexts_data = []
                    for key, value in response_data.items():
                        context_obj = {**value}
                        if "contextId" not in context_obj and "id" not in context_obj:
                            context_obj["id"] = key
                        contexts_data.append(context_obj)
                # Otherwise, treat the whole dict as a single context
                else:
                    contexts_data = [response_data]
            else:
                contexts_data = []
        elif isinstance(response_data, list):
            # Direct list of contexts
            contexts_data = response_data
        else:
            contexts_data = []

        # Ensure contexts_data is a list
        if not isinstance(contexts_data, list):
            console.print(
                f"[yellow]⚠️  Unexpected response format. Expected list, got {type(contexts_data)}[/yellow]"
            )
            if verbose:
                import json as json_lib

                try:
                    formatted_data = json_lib.dumps(result, indent=2)
                    console.print(f"[dim]Full response:\n{formatted_data}[/dim]")
                except Exception:
                    console.print(f"[dim]Full response: {result}[/dim]")
            contexts_data = []

        if not contexts_data:
            console.print(f"\n[yellow]No contexts found on node {node}[/yellow]")
            if verbose:
                console.print("\n[bold]Response structure:[/bold]")
                import json as json_lib

                try:
                    formatted_data = json_lib.dumps(result, indent=2)
                    console.print(f"{formatted_data}")
                except Exception:
                    console.print(f"{result}")
            return

        # Debug: show what we extracted
        if verbose:
            console.print(
                f"\n[cyan]Extracted {len(contexts_data)} context(s) from response[/cyan]"
            )
            import json as json_lib

            try:
                formatted_data = json_lib.dumps(contexts_data, indent=2)
                console.print(f"[dim]Contexts data:\n{formatted_data}[/dim]")
            except Exception:
                console.print(f"[dim]Contexts data: {contexts_data}[/dim]")

        console.print(f"\n[green]Found {len(contexts_data)} context(s):[/green]")

        # Fetch member public key for each context (list API doesn't return it)
        if contexts_data:
            console.print("[dim]Fetching member public keys...[/dim]")
            for context_info in contexts_data:
                if isinstance(context_info, dict):
                    context_id = context_info.get("id") or context_info.get("contextId")
                    if context_id and "memberPublicKey" not in context_info:
                        # Fetch individual context to get member public key
                        context_result = run_async_function(
                            get_context_via_admin_api, rpc_url, context_id
                        )
                        if context_result.get("success"):
                            context_detail_data = context_result.get("data", {})
                            # Handle nested structure
                            if isinstance(context_detail_data, dict):
                                detail_data = context_detail_data.get(
                                    "data", context_detail_data
                                )
                                if isinstance(detail_data, dict):
                                    member_pk = detail_data.get(
                                        "memberPublicKey"
                                    ) or detail_data.get("member_public_key")
                                    if member_pk:
                                        context_info["memberPublicKey"] = member_pk

        # Create and display table
        table = create_context_table(contexts_data)
        console.print(table)

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            import json as json_lib

            try:
                formatted_data = json_lib.dumps(result, indent=2)
                console.print(f"{formatted_data}")
            except Exception:
                console.print(f"{result}")

    else:
        console.print("\n[red]✗ Failed to list contexts[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)


@context.command()
@click.option("--node", "-n", required=True, help="Node name to show context from")
@click.option(
    "--context-id", "-c", required=True, help="Context ID to show details for"
)
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
def show(node, context_id, verbose):
    """Show details of a specific context."""
    manager = DockerManager()

    # Get admin API URL
    rpc_url = get_node_rpc_url(node, manager)

    console.print(f"[blue]Getting context {context_id} from node {node}[/blue]")

    result = run_async_function(get_context_via_admin_api, rpc_url, context_id)

    if result["success"]:
        response_data = result.get("data", {})
        # Handle nested data structure
        if isinstance(response_data, dict) and "data" in response_data:
            context_data = response_data["data"]
        else:
            context_data = response_data

        console.print("\n[green]✓ Context details:[/green]")

        if isinstance(context_data, dict):
            # Display key fields in a readable format
            context_id_display = context_data.get(
                "contextId", context_data.get("id", context_id)
            )
            console.print(f"[cyan]Context ID:[/cyan] {context_id_display}")

            # Extract member public key (check nested structures too)
            member_public_key = context_data.get("memberPublicKey") or context_data.get(
                "member_public_key"
            )
            if not member_public_key and isinstance(response_data, dict):
                # Try to get from nested data structure
                nested_data = response_data.get("data", {})
                if isinstance(nested_data, dict):
                    member_public_key = nested_data.get(
                        "memberPublicKey"
                    ) or nested_data.get("member_public_key")

            if member_public_key:
                console.print(f"[cyan]Member Public Key:[/cyan] {member_public_key}")
            elif verbose:
                console.print(
                    "[yellow]⚠️  Member Public Key not found in response[/yellow]"
                )
                console.print(
                    f"[dim]Available keys in context_data: {list(context_data.keys()) if isinstance(context_data, dict) else 'N/A'}[/dim]"
                )

            if "applicationId" in context_data:
                console.print(
                    f"[cyan]Application ID:[/cyan] {context_data['applicationId']}"
                )

            if "protocol" in context_data:
                console.print(f"[cyan]Protocol:[/cyan] {context_data['protocol']}")

            if "rootHash" in context_data or "root_hash" in context_data:
                root_hash = context_data.get("rootHash") or context_data.get(
                    "root_hash"
                )
                console.print(f"[cyan]Root Hash:[/cyan] {root_hash}")

        if verbose:
            console.print("\n[bold]Full response:[/bold]")
            try:
                formatted_data = json_lib.dumps(response_data, indent=2)
                console.print(f"{formatted_data}")
            except Exception:
                console.print(f"{response_data}")
    else:
        console.print("\n[red]✗ Failed to get context[/red]")
        console.print(f"[red]Error: {result.get('error', 'Unknown error')}[/red]")

        if verbose and "exception" in result:
            exc = result["exception"]
            console.print(f"[red]Exception Type: {exc.get('type', 'Unknown')}[/red]")
            console.print(
                f"[red]Exception Message: {exc.get('message', 'No message')}[/red]"
            )
            if "traceback" in exc:
                console.print("\n[bold]Traceback:[/bold]")
                console.print(f"[red]{exc['traceback']}[/red]")

        sys.exit(1)
