"""`join_namespace` must bind `namespaceId` whichever spelling the node sends.

The step has always declared a `namespaceId` export, but core's join endpoint
returned `groupId` - a namespace is a root group internally, and only
`create_namespace` translated that noun on the way out. The export captured
nothing and the fallback never fired; nothing failed only because no workflow
read the value. calimero-network/core#3598 renames it, so both spellings are in
the wild and both have to work.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.group_join import JoinNamespaceStep

NAMESPACE_ID = "ns-hex"
JOIN = "merobox.commands.bootstrap.steps.group_join"


def _response(id_field):
    """A join response naming the joined id `id_field`, nested as a node sends it."""
    return {
        "data": {
            id_field: NAMESPACE_ID,
            "memberIdentity": "member-key",
            "memberAccount": "a" * 64,
        }
    }


SPELLINGS = [
    pytest.param("namespaceId", id="new-namespaceId"),
    pytest.param("groupId", id="old-groupId"),
]


def _run(coro):
    # A fresh loop rather than `asyncio.run`, which closes the default one and
    # leaves the sibling tests still on `asyncio.get_event_loop()` with none.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _run_join(response, outputs=None):
    config = {
        "type": "join_namespace",
        "name": "Join",
        "node": "n1",
        "namespace_id": NAMESPACE_ID,
        "invitation": '{"inviter_signature": "sig"}',
    }
    if outputs is not None:
        config["outputs"] = outputs

    step = JoinNamespaceStep(config)
    step._resolve_node_for_client = MagicMock(return_value=("http://n1", "n1"))
    client = MagicMock()
    client.join_namespace.return_value = response

    with patch(f"{JOIN}.get_client_for_rpc_url", return_value=client):
        values: dict = {}
        assert _run(step.execute({}, values)) is True
        return values


@pytest.mark.parametrize("id_field", SPELLINGS)
def test_namespace_id_is_bound_from_either_spelling(id_field):
    values = _run_join(_response(id_field))
    assert values["namespace_id_n1"] == NAMESPACE_ID


@pytest.mark.parametrize("id_field", SPELLINGS)
def test_outputs_can_capture_namespace_id_from_either_spelling(id_field):
    values = _run_join(_response(id_field), outputs={"ns": "namespaceId"})
    assert values["ns"] == NAMESPACE_ID


@pytest.mark.parametrize("id_field", SPELLINGS)
def test_member_exports_are_untouched(id_field):
    values = _run_join(
        _response(id_field),
        outputs={"identity": "memberIdentity", "account": "memberAccount"},
    )
    assert values["identity"] == "member-key"
    assert values["account"] == "a" * 64
