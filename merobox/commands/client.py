"""
Client helpers - Centralized creation of Calimero client instances.

Token Persistence:
    When creating connections with a `node_name`, the calimero-client-py Rust client
    automatically:
    - Loads cached tokens from ~/.merobox/auth_cache/{node_name_derived}.json
    - Refreshes tokens on 401 and saves updated tokens to the cache
    - Includes Authorization header in subsequent requests

    For this to work correctly:
    1. Merobox must write initial tokens to the path returned by
       `calimero_client_py.get_token_cache_path(node_name)`
    2. The same `node_name` must be used consistently across sessions
"""

from typing import Optional

from calimero_client_py import create_client, create_connection

from merobox.commands.manager import DockerManager
from merobox.commands.utils import get_node_rpc_url


def get_client_for_rpc_url(rpc_url: str, node_name: Optional[str] = None):
    """Create a Calimero client for a given RPC URL.

    Args:
        rpc_url: The RPC URL to connect to.
        node_name: Optional stable node name for token caching. When provided,
                   the Rust client will automatically load/save tokens from
                   ~/.merobox/auth_cache/ and handle token refresh on 401.

                   For authenticated remote nodes, this should be:
                   - Stable: Same value across sessions (to find cached tokens)
                   - Unique: Different for each node (to avoid token collisions)

    Returns:
        A Calimero client instance.
    """
    connection = create_connection(rpc_url, node_name=node_name)
    client = create_client(connection)
    return client


def get_client_for_node(node_name: str) -> tuple[object, str]:
    """Create a Calimero client for a local node name and return (client, rpc_url).

    This is used for local Docker/binary nodes where authentication is typically
    not required. For authenticated remote nodes, use get_client_for_rpc_url()
    with an explicit node_name for proper token caching.

    Args:
        node_name: The Docker container or binary node name.

    Returns:
        A tuple of (client, rpc_url).
    """
    manager = DockerManager()
    rpc_url = get_node_rpc_url(node_name, manager)
    # Pass node_name to enable token caching (in case local nodes have auth)
    client = get_client_for_rpc_url(rpc_url, node_name=node_name)
    return client, rpc_url
