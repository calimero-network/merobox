"""
Execute function calls on Calimero nodes.
"""

from typing import Any, Optional

from merobox.commands.client import create_client, create_connection
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, ensure_json_string

console = console


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
