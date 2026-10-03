"""A client call that times out must say so.

calimero-client-py 0.8.1 (core 0.11.0-rc.79, core#4455) bounds every request: 30 s
by default, 5 min for joins, syncs, registry installs, context creation and
JSON-RPC, 1 h for blobs. When a bound expires the binding raises
`RuntimeError("Client error: error sending request for url (...)")` - the exact
text a refused connection gets. Without help, a step that waited out the bound
reads as a node that died, and the node logs show it still working.

`get_client_for_rpc_url` wraps the client so the two read differently. These
tests pin that the wrapper changes only that one case.
"""

from unittest.mock import MagicMock, patch

import pytest

from merobox.commands import client as client_module
from merobox.commands.client import (
    CLIENT_REQUEST_TIMEOUT_SECONDS,
    _TimeoutNamingClient,
    explain_request_timeout,
    get_client_for_rpc_url,
)

_SEND_FAILURE = (
    "Client error: error sending request for url "
    "(http://localhost:2528/admin-api/namespaces)"
)


class _Clock:
    """Stands in for time.monotonic: each call to the wrapped method advances it."""

    def __init__(self, elapsed: float):
        self.now = 1000.0
        self.elapsed = elapsed

    def __call__(self) -> float:
        return self.now

    def advance(self) -> None:
        self.now += self.elapsed


def _client_raising(message: str, clock: _Clock):
    inner = MagicMock()

    def create_namespace(*_args, **_kwargs):
        clock.advance()
        raise RuntimeError(message)

    inner.create_namespace = create_namespace
    return _TimeoutNamingClient(inner)


def test_a_send_failure_after_the_bound_is_named_a_timeout(monkeypatch):
    clock = _Clock(elapsed=30.0)
    monkeypatch.setattr(client_module.time, "monotonic", clock)
    client = _client_raising(_SEND_FAILURE, clock)

    with pytest.raises(RuntimeError) as info:
        client.create_namespace(application_id="app")

    message = str(info.value)
    # The original text survives, so anything matching on it still matches.
    assert message.startswith(_SEND_FAILURE)
    assert "`create_namespace` had no answer after 30s" in message
    assert "may still complete" in message
    assert isinstance(info.value.__cause__, RuntimeError)


def test_a_fast_send_failure_is_a_refused_connection_and_is_left_alone(monkeypatch):
    clock = _Clock(elapsed=0.01)
    monkeypatch.setattr(client_module.time, "monotonic", clock)
    client = _client_raising(_SEND_FAILURE, clock)

    with pytest.raises(RuntimeError) as info:
        client.create_namespace(application_id="app")

    assert str(info.value) == _SEND_FAILURE
    assert info.value.__cause__ is None


def test_a_slow_failure_that_is_not_a_send_failure_is_left_alone(monkeypatch):
    clock = _Clock(elapsed=45.0)
    monkeypatch.setattr(client_module.time, "monotonic", clock)
    refusal = "Client error: HTTP 403 Forbidden: not an admin"
    client = _client_raising(refusal, clock)

    with pytest.raises(RuntimeError) as info:
        client.create_namespace(application_id="app")

    assert str(info.value) == refusal


def test_it_stays_a_runtime_error_so_retry_helpers_do_not_repeat_the_call():
    # NETWORK_RETRY_CONFIG retries ConnectionError/TimeoutError. A timed-out
    # write may still land on the node, so repeating it is not safe.
    from merobox.commands.retry import NETWORK_RETRY_CONFIG

    message = explain_request_timeout(
        RuntimeError(_SEND_FAILURE), "create_namespace", 31.0
    )
    assert message != _SEND_FAILURE
    assert not issubclass(RuntimeError, NETWORK_RETRY_CONFIG.exceptions)


def test_the_timeout_threshold_is_the_clients_shortest_bound():
    assert CLIENT_REQUEST_TIMEOUT_SECONDS == 30.0
    just_under = CLIENT_REQUEST_TIMEOUT_SECONDS - 2
    assert (
        explain_request_timeout(RuntimeError(_SEND_FAILURE), "m", just_under)
        == _SEND_FAILURE
    )


def test_results_and_attributes_pass_through():
    inner = MagicMock()
    inner.list_namespaces.return_value = {"data": []}
    inner.some_value = 7
    client = _TimeoutNamingClient(inner)

    assert client.list_namespaces() == {"data": []}
    inner.list_namespaces.assert_called_once_with()
    assert client.some_value == 7


def test_a_missing_binding_still_reads_as_missing():
    # Steps probe optional bindings with getattr(client, name, None).
    class Bare:
        def list_namespaces(self):
            return {}

    client = _TimeoutNamingClient(Bare())
    assert getattr(client, "create_group_in_namespace", None) is None
    with pytest.raises(AttributeError):
        client.create_group_in_namespace  # noqa: B018


def test_get_client_for_rpc_url_hands_out_the_wrapped_client():
    with patch.object(client_module, "create_connection") as connect:
        with patch.object(client_module, "create_client") as create:
            client = get_client_for_rpc_url("http://localhost:2528", node_name="n1")

    connect.assert_called_once_with("http://localhost:2528", node_name="n1")
    assert isinstance(client, _TimeoutNamingClient)
    assert client._inner is create.return_value
