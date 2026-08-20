"""`create_mesh` has to hand back the ids it already holds.

The step does the whole bootstrap - namespace, context, invitations, joins -
but used to export only the context id and member public key. Without the
namespace id no workflow can create a subgroup under the namespace it just
made, and without the per-node account no step can address a member by the id
`list_group_members` returns. Both values pass through the step's own calls, so
dropping them forced scenarios back onto a hand-rolled nine-step preamble.
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


def _config(**overrides):
    cfg = {
        "type": "create_mesh",
        "name": "Create mesh",
        "context_node": "n1",
        "application_id": "app-id",
        "nodes": ["n1", "n2", "n3"],
    }
    cfg.update(overrides)
    return cfg


def _client():
    client = MagicMock()
    client.create_namespace.return_value = {"data": {"namespaceId": NAMESPACE_ID}}
    client.create_context.return_value = {
        "contextId": CONTEXT_ID,
        "memberPublicKey": "ctx-key",
    }
    return client


def _join_result(account):
    return {
        "success": True,
        "data": {
            "data": {"memberIdentity": f"id-{account[:4]}", "memberAccount": account}
        },
    }


@pytest.fixture
def dynamic_values():
    accounts = iter([ACCOUNT_2, ACCOUNT_3])
    step = CreateMeshStep(_config())
    step._resolve_node_for_client = MagicMock(side_effect=lambda n: (f"http://{n}", n))

    with (
        patch(f"{MESH}.get_client_for_rpc_url", return_value=_client()),
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


def test_namespace_id_is_exported(dynamic_values):
    assert dynamic_values["namespace_id"] == NAMESPACE_ID
    assert dynamic_values["namespace_id_n1"] == NAMESPACE_ID


def test_member_account_is_exported_per_node(dynamic_values):
    assert dynamic_values["member_account_n2"] == ACCOUNT_2
    assert dynamic_values["member_account_n3"] == ACCOUNT_3


def test_existing_exports_are_unchanged(dynamic_values):
    assert dynamic_values["context_id"] == CONTEXT_ID
    assert dynamic_values["member_public_key"] == "ctx-key"


def test_outputs_can_capture_the_namespace_id():
    step = CreateMeshStep(_config(outputs={"ns": "namespaceId", "ctx": "contextId"}))
    step._resolve_node_for_client = MagicMock(side_effect=lambda n: (f"http://{n}", n))

    with (
        patch(f"{MESH}.get_client_for_rpc_url", return_value=_client()),
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
            new=AsyncMock(return_value=_join_result(ACCOUNT_2)),
        ),
    ):
        values: dict = {}
        assert asyncio.run(step.execute({}, values)) is True

    assert values["ns"] == NAMESPACE_ID
    assert values["ctx"] == CONTEXT_ID
