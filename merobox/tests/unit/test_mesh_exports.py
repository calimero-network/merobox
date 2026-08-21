"""`create_mesh` has to hand back the ids it already holds.

The step does the whole bootstrap - namespace, context, invitations, joins -
but exported only the context id and member public key. Without the namespace
id no workflow can create a subgroup under the namespace it just made, and
without the per-node account no step can address a member by the id
`list_group_members` returns. Both values pass through the step's own calls, so
dropping them forced scenarios back onto a hand-rolled nine-step preamble.

A real node nests a success payload under `data`, and the export pass reads
that level rather than the envelope. Both shapes are exercised here: a mock
that only ever returned the flat one hid a capture failure CI then caught.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.mesh import CreateMeshStep

NAMESPACE_ID = "ns-hex"
CONTEXT_ID = "ctx-hex"
ACCOUNT_2 = "a" * 64
ACCOUNT_3 = "b" * 64
MESH = "merobox.commands.bootstrap.steps.mesh"

# The keys a real merod returns from create_context, per the CI run that caught
# the envelope bug: nested under `data`, and naming the namespace `groupId`.
NESTED_CONTEXT_RESPONSE = {
    "data": {
        "contextId": CONTEXT_ID,
        "memberPublicKey": "ctx-key",
        "groupId": NAMESPACE_ID,
        "groupCreated": False,
    }
}
FLAT_CONTEXT_RESPONSE = {"contextId": CONTEXT_ID, "memberPublicKey": "ctx-key"}
CONTEXT_SHAPES = [
    pytest.param(NESTED_CONTEXT_RESPONSE, id="nested-under-data"),
    pytest.param(FLAT_CONTEXT_RESPONSE, id="flat"),
]


def _client(context_response):
    client = MagicMock()
    client.create_namespace.return_value = {"data": {"namespaceId": NAMESPACE_ID}}
    client.create_context.return_value = context_response
    return client


def _join_result(account):
    return {
        "success": True,
        "data": {
            "data": {"memberIdentity": f"id-{account[:4]}", "memberAccount": account}
        },
    }


def _run_mesh(context_response, outputs=None, nodes=("n1", "n2", "n3")):
    """Drive execute() with every API call mocked; returns dynamic_values."""
    accounts = iter([ACCOUNT_2, ACCOUNT_3])
    config = {
        "type": "create_mesh",
        "name": "Create mesh",
        "context_node": "n1",
        "application_id": "app-id",
        "nodes": list(nodes),
    }
    if outputs is not None:
        config["outputs"] = outputs

    step = CreateMeshStep(config)
    step._resolve_node_for_client = MagicMock(side_effect=lambda n: (f"http://{n}", n))

    with (
        patch(f"{MESH}.get_client_for_rpc_url", return_value=_client(context_response)),
        patch(
            f"{MESH}.generate_identity_via_admin_api",
            new=AsyncMock(return_value={"success": True, "data": {"publicKey": "pk"}}),
        ),
        patch(
            f"{MESH}.create_namespace_invitation_via_admin_api",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "data": {"data": {"inviter_signature": "sig"}},
                }
            ),
        ),
        patch(
            f"{MESH}.join_namespace_via_admin_api",
            new=AsyncMock(side_effect=lambda *a, **k: _join_result(next(accounts))),
        ),
    ):
        values: dict = {}
        assert asyncio.run(step.execute({}, values)) is True
        return values


@pytest.mark.parametrize("context_response", CONTEXT_SHAPES)
def test_namespace_id_is_exported(context_response):
    values = _run_mesh(context_response)
    assert values["namespace_id"] == NAMESPACE_ID
    assert values["namespace_id_n1"] == NAMESPACE_ID


@pytest.mark.parametrize("context_response", CONTEXT_SHAPES)
def test_member_account_is_exported_per_node(context_response):
    values = _run_mesh(context_response)
    assert values["member_account_n2"] == ACCOUNT_2
    assert values["member_account_n3"] == ACCOUNT_3


@pytest.mark.parametrize("context_response", CONTEXT_SHAPES)
def test_existing_exports_are_unchanged(context_response):
    values = _run_mesh(context_response)
    assert values["context_id"] == CONTEXT_ID
    assert values["member_public_key"] == "ctx-key"


@pytest.mark.parametrize("context_response", CONTEXT_SHAPES)
def test_outputs_can_capture_the_namespace_id(context_response):
    """The capture CI caught: folding namespaceId into the envelope instead of
    the body left it invisible to the export pass on a real response."""
    values = _run_mesh(
        context_response,
        outputs={"ns": "namespaceId", "ctx": "contextId"},
        nodes=("n1", "n2"),
    )
    assert values["ns"] == NAMESPACE_ID
    assert values["ctx"] == CONTEXT_ID
