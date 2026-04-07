"""
Call command - Execute function calls using JSON-RPC client.
"""

import json
from typing import Any, Optional

import click
from rich.console import Console
from rich.panel import Panel

from merobox.commands.client import create_client, create_connection
from merobox.commands.manager import DockerManager
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import (
    ensure_json_string,
    get_node_rpc_url,
    run_async_function,
)

console = Console()


@with_retry(config=NETWORK_RETRY_CONFIG)
async def _call_function_with_retry(
    rpc_url: str,
    context_id: str,
    function_name: str,
    args: Optional[dict[str, Any]] = None,
    node_name: Optional[str] = None,
) -> dict:
    """Internal function that performs the actual API call with retry support."""
    connection = create_connection(rpc_url, node_name=node_name)
    client = create_client(connection)

    encoded_args = ensure_json_string(args or {})
    result = client.execute_function(
        context_id=context_id,
        method=function_name,
        args=encoded_args,
    )
    return ok(result)


async def call_function(
    rpc_url: str,
    context_id: str,
    function_name: str,
    args: Optional[dict[str, Any]] = None,
    node_name: Optional[str] = None,
) -> dict:
    """Execute a function call using the admin API.

    Args:
        rpc_url: The admin API URL of the Calimero node.
        context_id: The ID of the context to execute in.
        function_name: The function to call.
        args: Optional arguments for the function call.
        node_name: Optional node name for token caching.

    Returns:
        dict: A result dict with 'success': True and 'data' on success,
              or 'success': False and 'error'/'exception' on failure.
    """
    try:
        return await _call_function_with_retry(
            rpc_url,
            context_id,
            function_name,
            args,
            node_name,
        )
    except Exception as e:
        return fail("execute_function failed", error=e)


@click.command()
@click.option("--node", required=True, help="Node name to execute the function on")
@click.option("--context-id", required=True, help="Context ID to execute in")
@click.option("--function", required=True, help="Function name to call")
@click.option("--args", help="JSON string of arguments for the function call")
def call(
    node: str,
    context_id: str,
    function: str,
    args: str = None,
):
    """Execute function calls."""

    manager = DockerManager()
    rpc_url = get_node_rpc_url(node, manager)

    console.print(f"[blue]Using RPC endpoint: {rpc_url}[/blue]")

    parsed_args = None
    if args:
        try:
            parsed_args = json.loads(args)
        except json.JSONDecodeError as e:
            console.print(f"[red]Error parsing JSON arguments: {str(e)}[/red]")
            return

    result = run_async_function(
        call_function, rpc_url, context_id, function, parsed_args
    )

    if result:
        if result.get("success"):
            console.print(
                Panel(
                    f"[green]Function call successful![/green]\n\n"
                    f"Function: {function}\n"
                    f"Context: {context_id}\n"
                    f"Node: {node}\n"
                    f"Result: {result.get('data', 'No data returned')}",
                    title="Function Call Result",
                    border_style="green",
                )
            )
        else:
            error_msg = result.get("error", "Unknown error")
            error_details = ""

            if "exception" in result:
                exc = result["exception"]
                error_type = exc.get("type", "Unknown")
                error_message = exc.get("message", "No message")
                error_details = f"\nException Type: {error_type}\nException Message: {error_message}"
                if "traceback" in exc:
                    error_details += f"\n\nTraceback:\n{exc['traceback']}"

            console.print(
                Panel(
                    f"[red]Function call failed![/red]\n\n"
                    f"Function: {function}\n"
                    f"Context: {context_id}\n"
                    f"Node: {node}\n"
                    f"Error: {error_msg}{error_details}",
                    title="Function Call Error",
                    border_style="red",
                )
            )
    else:
        console.print("[red]Failed to execute function call[/red]")


if __name__ == "__main__":
    call()
