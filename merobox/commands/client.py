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

Request timeouts:
    From calimero-client-py 0.8.1 (built on core 0.11.0-rc.79, core#4455) every
    request is bounded, and nothing on the Python side can change the bounds:

    - 5 min: installing from the registry (`install_application`), creating,
      joining, syncing and resyncing a context, joining a namespace or a
      subgroup by inheritance, syncing and upgrading a group, and JSON-RPC
      (`execute_function`).
    - 1 h: blob upload and download.
    - 30 s: every other call, including `install_dev_application`,
      `create_namespace`, invitations, membership and role changes, leaves,
      deletes, account pairing and `perform_intent`.
    - 10 s to connect.

    A call that runs past its bound raises
    `RuntimeError("Client error: error sending request for url (...)")`, the
    same text a refused connection produces. `get_client_for_rpc_url` hands out
    a client that tells the two apart by how long the call took, so a step that
    timed out says so instead of reading as a node that went away.
"""

import functools
import time
from typing import Any, Optional

from calimero_client_py import create_client, create_connection

from merobox.commands.manager import DockerManager
from merobox.commands.utils import get_node_rpc_url

# The client's shortest per-request bound (core's DEFAULT_REQUEST_TIMEOUT). A
# send failure that took at least this long was the bound expiring, not the node
# refusing the connection; the margin absorbs timer and scheduling jitter.
CLIENT_REQUEST_TIMEOUT_SECONDS = 30.0
_TIMEOUT_MARGIN_SECONDS = 1.0

# What the client says when reqwest gives up on a request, whatever the reason.
_SEND_FAILURE_MARKER = "error sending request for url"


def explain_request_timeout(error: Exception, method: str, elapsed: float) -> str:
    """The message for a client call that failed, naming a timeout if it was one.

    Returns the error's own text unless it is a send failure that took at least
    the client's shortest bound, which is a timeout and not a dead node.
    """
    message = str(error)
    if _SEND_FAILURE_MARKER not in message:
        return message
    if elapsed < CLIENT_REQUEST_TIMEOUT_SECONDS - _TIMEOUT_MARGIN_SECONDS:
        return message
    return (
        f"{message} - `{method}` had no answer after {elapsed:.0f}s, so "
        f"calimero-client-py stopped waiting. It bounds each call at 30s, or "
        f"5 min for joins, syncs, registry installs, context creation and "
        f"JSON-RPC, or 1 h for blobs. The node may still complete the call, so "
        f"check its state before repeating it."
    )


class _TimeoutNamingClient:
    """A calimero-client-py client whose timeouts say they are timeouts.

    Every attribute is the wrapped client's. A method that fails with a send
    error after running for the client's request bound is re-raised as a
    `RuntimeError` whose message says it timed out; anything else passes through
    untouched. It stays a `RuntimeError` on purpose: the retry helpers retry
    `ConnectionError`/`TimeoutError`, and repeating a write the node may still
    be completing would not be safe.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: Any):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        @functools.wraps(attr)
        def call(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return attr(*args, **kwargs)
            except RuntimeError as error:
                message = explain_request_timeout(
                    error, name, time.monotonic() - started
                )
                if message == str(error):
                    raise
                raise RuntimeError(message) from error

        return call

    def __repr__(self) -> str:
        return repr(self._inner)


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
        A Calimero client instance whose request timeouts name themselves (see
        the module docstring).
    """
    connection = create_connection(rpc_url, node_name=node_name)
    return _TimeoutNamingClient(create_client(connection))


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
