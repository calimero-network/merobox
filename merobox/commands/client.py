"""
Client helpers - Centralized creation of Calimero client instances.
"""

from typing import Tuple, Optional
from calimero_client_py import create_connection, create_client
from merobox.commands.manager import CalimeroManager
from merobox.commands.utils import get_node_rpc_url
from merobox.commands.retry import with_retry, NETWORK_RETRY_CONFIG


def get_client_for_rpc_url(rpc_url: str):
    """Create a Calimero client for a given RPC URL."""
    connection = create_connection(rpc_url)
    client = create_client(connection)
    return client


def get_client_for_node(node_name: str) -> Tuple[object, str]:
    """Create a Calimero client for a node name and return (client, rpc_url)."""
    manager = CalimeroManager()
    rpc_url = get_node_rpc_url(node_name, manager)
    client = get_client_for_rpc_url(rpc_url)
    return client, rpc_url


