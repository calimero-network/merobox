"""
Unit tests for the account-identity workflow steps.

Covers: AccountCreateStep, AccountPairStep, AccountRevokeStep, NodeIdentityStep —
validation, the client calls they make, and the two behaviours that are the reason
these exist as steps rather than shell scripts: values flow out through `outputs`,
and pairing's confirmation code is checked rather than passed along.

The steps drive `calimero-client-py`, so these patch the step's client factory
rather than HTTP. What matters here is which binding is called with which
arguments; the wire shape is the client's contract to keep, and duplicating it in
mocks would only pin our guess about someone else's serializer.

No `asyncio.run()` at module scope — each test drives its coroutine on a fresh
loop, per this repo's convention.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from merobox.commands.bootstrap.steps.account import (
    AccountCreateStep,
    AccountPairStep,
    AccountRevokeStep,
    NodeIdentityStep,
)

NAMESPACE = "ab" * 32


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _envelope(payload):
    """What a binding returns: the api envelope with the payload under `data`."""
    return {"data": payload}


def _step(cls, config, client=None):
    """A step whose `_client` hands back `client` (a fresh MagicMock by default)."""
    step = cls(config)
    step._client = MagicMock(return_value=client or MagicMock())  # noqa: SLF001
    return step


# =============================================================================
# AccountCreateStep
# =============================================================================


class TestAccountCreateStep:
    def setup_method(self):
        self.config = {
            "type": "account_create",
            "name": "Enrol",
            "node": "calimero-node-1",
            "namespace_id": NAMESPACE,
        }

    def test_valid_config_passes_validation(self):
        _step(AccountCreateStep, self.config)

    @pytest.mark.parametrize("field", ["node", "namespace_id"])
    def test_missing_required_field_raises(self, field):
        config = {**self.config}
        del config[field]
        with pytest.raises(ValueError, match=field):
            AccountCreateStep(config)

    def test_calls_create_account_and_records_the_result(self):
        payload = {
            "accountId": "aa" * 32,
            "deviceId": "bb" * 32,
            "accountRootKey": "cc" * 32,
            "accountNonce": "dd" * 16,
        }
        client = MagicMock()
        client.create_account.return_value = _envelope(payload)
        step = _step(AccountCreateStep, self.config, client)

        results = {}
        assert _run(step.execute(results, {})) is True

        client.create_account.assert_called_once_with(NAMESPACE)
        assert results["account_calimero-node-1"] == payload

    def test_a_response_without_an_account_id_fails_the_step(self):
        """A success that carries no account is a failure, not a silent pass.

        Every later step keys off the account, so accepting an empty one turns a
        broken enrolment into a confusing failure three steps downstream.
        """
        client = MagicMock()
        client.create_account.return_value = _envelope({"deviceId": "bb" * 32})
        step = _step(AccountCreateStep, self.config, client)
        assert _run(step.execute({}, {})) is False

    def test_a_client_error_fails_the_step(self):
        client = MagicMock()
        client.create_account.side_effect = RuntimeError("Client error: not a member")
        step = _step(AccountCreateStep, self.config, client)
        assert _run(step.execute({}, {})) is False

    def test_an_unwrapped_response_is_accepted(self):
        """Tolerate a payload that is not wrapped in `data`.

        The api types wrap; insisting on the wrapper here would couple these tests
        to somebody else's serializer for no gain.
        """
        client = MagicMock()
        client.create_account.return_value = {"accountId": "aa" * 32}
        step = _step(AccountCreateStep, self.config, client)
        results = {}
        assert _run(step.execute(results, {})) is True
        assert results["account_calimero-node-1"]["accountId"] == "aa" * 32


# =============================================================================
# AccountPairStep
# =============================================================================


class TestAccountPairStep:
    def setup_method(self):
        self.config = {
            "type": "account_pair",
            "name": "Pair",
            "node": "calimero-node-3",
            "holder": "calimero-node-2",
            "namespace_id": NAMESPACE,
            "root_key": "cc" * 32,
            "nonce": "dd" * 16,
        }

    def test_valid_config_passes_validation(self):
        _step(AccountPairStep, self.config)

    @pytest.mark.parametrize(
        "field", ["node", "holder", "namespace_id", "root_key", "nonce"]
    )
    def test_missing_required_field_raises(self, field):
        config = {**self.config}
        del config[field]
        with pytest.raises(ValueError, match=field):
            AccountPairStep(config)

    def _init_payload(self, code="0011223344556677"):
        return {
            "deviceId": "ee" * 32,
            "kemPublicKey": "11" * 32,
            "signPublicKey": "22" * 32,
            "statement": "33" * 64,
            "confirmationCode": code,
        }

    def _paired(self, init, code=None):
        """A step wired to two clients: the new device's, and the holder's."""
        new_device = MagicMock()
        new_device.pair_device_init.return_value = _envelope(init)
        holder = MagicMock()
        holder.pair_device_complete.return_value = _envelope(
            {
                "accountId": "aa" * 32,
                "keyDelivered": True,
                "confirmationCode": code or init.get("confirmationCode"),
            }
        )
        step = AccountPairStep(self.config)
        # `_client` is called with a node name, so route by which node it is.
        step._client = MagicMock(  # noqa: SLF001
            side_effect=lambda name: (
                new_device if name == "calimero-node-3" else holder
            )
        )
        return step, new_device, holder

    def test_inits_on_the_new_node_then_completes_on_the_holder(self):
        """The ordering is forced, not stylistic — assert it rather than assume it.

        The new device cannot mint its id without the account, and the holder
        cannot certify that device without the id and both keys.
        """
        init = self._init_payload()
        step, new_device, holder = self._paired(init)

        results = {}
        assert _run(step.execute(results, {})) is True

        new_device.pair_device_init.assert_called_once_with(
            NAMESPACE, "cc" * 32, "dd" * 16
        )
        # Everything init minted has to reach complete verbatim; a dropped field
        # would be a pairing that certifies key material nobody committed to.
        holder.pair_device_complete.assert_called_once_with(
            NAMESPACE,
            init["deviceId"],
            init["kemPublicKey"],
            init["signPublicKey"],
            init["statement"],
            init["confirmationCode"],
        )
        assert results["paired_account_calimero-node-3"]["deviceId"] == init["deviceId"]

    def test_a_confirmation_code_mismatch_fails_the_step(self):
        """The check a human is supposed to make, made mechanically.

        Both sides derive the code over exactly what gets certified, so a mismatch
        means the payload was altered between them. Passing it along without
        comparing would make this step the "pasted alongside the keys" channel the
        code exists to defeat.
        """
        init = self._init_payload("0011223344556677")
        step, _new_device, _holder = self._paired(init, code="ffffffffffffffff")
        assert _run(step.execute({}, {})) is False

    @pytest.mark.parametrize(
        "omit",
        ["deviceId", "kemPublicKey", "signPublicKey", "statement", "confirmationCode"],
    )
    def test_an_incomplete_init_fails_before_certifying(self, omit):
        """Fail at init rather than sending a half-formed pair-complete.

        `pair-complete` refuses key material that arrives without the signature of
        the device that minted it, so a partial payload produces a refusal that
        reads like a node fault.
        """
        init = self._init_payload()
        del init[omit]
        step, _new_device, holder = self._paired(init)
        assert _run(step.execute({}, {})) is False
        holder.pair_device_complete.assert_not_called()


# =============================================================================
# AccountRevokeStep
# =============================================================================


class TestAccountRevokeStep:
    def setup_method(self):
        self.config = {
            "type": "account_revoke",
            "name": "Revoke",
            "node": "calimero-node-1",
            "namespace_id": NAMESPACE,
            "device_id": "ee" * 32,
        }

    def test_valid_config_passes_validation(self):
        _step(AccountRevokeStep, self.config)

    @pytest.mark.parametrize("field", ["node", "namespace_id", "device_id"])
    def test_missing_required_field_raises(self, field):
        config = {**self.config}
        del config[field]
        with pytest.raises(ValueError, match=field):
            AccountRevokeStep(config)

    def test_names_the_device_and_records_whether_the_key_rotated(self):
        client = MagicMock()
        client.revoke_device.return_value = _envelope({"keyRotated": True})
        step = _step(AccountRevokeStep, self.config, client)

        results = {}
        assert _run(step.execute(results, {})) is True

        client.revoke_device.assert_called_once_with(NAMESPACE, "ee" * 32, None)
        # Exported so a scenario asserts the rotation happened rather than
        # inferring it from a later read that could pass for other reasons.
        assert results["revoke_calimero-node-1"]["keyRotated"] is True

    def test_resolves_a_placeholder_device_id(self):
        """The device id normally arrives from a previous step's output."""
        client = MagicMock()
        client.revoke_device.return_value = _envelope({"keyRotated": True})
        step = _step(
            AccountRevokeStep, {**self.config, "device_id": "{{paired_device}}"}, client
        )
        assert _run(step.execute({}, {"paired_device": "ee" * 32})) is True
        client.revoke_device.assert_called_once_with(NAMESPACE, "ee" * 32, None)

    def test_passes_a_supplied_proof_through(self):
        """The lost-device path: the node publishes a proof it did not mint.

        Signed wherever the account root lives (`merod account revoke-proof`), so
        the node running this step needs no authority of its own.
        """
        client = MagicMock()
        client.revoke_device.return_value = _envelope({"keyRotated": False})
        step = _step(AccountRevokeStep, {**self.config, "proof": "ab" * 40}, client)

        assert _run(step.execute({}, {})) is True
        client.revoke_device.assert_called_once_with(NAMESPACE, "ee" * 32, "ab" * 40)

    def test_resolves_a_placeholder_proof(self):
        """The proof normally arrives from a `node_exec` capture, not inline."""
        client = MagicMock()
        client.revoke_device.return_value = _envelope({"keyRotated": False})
        step = _step(
            AccountRevokeStep, {**self.config, "proof": "{{proof_hex}}"}, client
        )

        assert _run(step.execute({}, {"proof_hex": "cd" * 40})) is True
        client.revoke_device.assert_called_once_with(NAMESPACE, "ee" * 32, "cd" * 40)

    def test_omitting_the_proof_sends_none_rather_than_an_empty_string(self):
        """`None` means "revoke on this node's own authority"; "" would be a lie.

        An empty string reaches the node as a present-but-unusable proof, so the
        distinction has to survive all the way to the client call.
        """
        client = MagicMock()
        client.revoke_device.return_value = _envelope({"keyRotated": True})
        step = _step(AccountRevokeStep, self.config, client)

        assert _run(step.execute({}, {})) is True
        assert client.revoke_device.call_args.args[2] is None

    @pytest.mark.parametrize("bad", ["", "   "])
    def test_an_empty_proof_is_refused_rather_than_silently_ignored(self, bad):
        """Passing a blank proof is always a scenario bug.

        Forwarded as-is it would read to the node as "no proof" and the step would
        revoke on the node's own authority instead — succeeding for a reason the
        author did not intend, which is worse than failing.
        """
        with pytest.raises(ValueError, match="proof"):
            AccountRevokeStep({**self.config, "proof": bad})

    def test_a_non_string_proof_is_refused(self):
        with pytest.raises(ValueError, match="proof"):
            AccountRevokeStep({**self.config, "proof": 1234})


# =============================================================================
# NodeIdentityStep
# =============================================================================


class TestNodeIdentityStep:
    def setup_method(self):
        self.config = {
            "type": "node_identity",
            "name": "Who am I",
            "node": "calimero-node-2",
        }

    def test_valid_config_passes_validation(self):
        _step(NodeIdentityStep, self.config)

    def test_takes_no_namespace(self):
        """A namespace would be a parameter the answer cannot depend on.

        One root key is one account and one device, whatever namespaces the node
        happens to be in — so accepting a `namespace_id` would invite a caller to
        believe the reply varied by it.
        """
        assert (
            "namespace_id"
            not in NodeIdentityStep(self.config, MagicMock())._get_required_fields()
        )

    def test_reads_the_identity_and_keeps_a_null_device_as_an_answer(self):
        """`deviceId: None` is meaningful — it is how "not enrolled yet" is said.

        A step that treated it as missing data could not be used to assert that a
        revocation stuck.
        """
        client = MagicMock()
        client.get_node_identity.return_value = _envelope(
            {
                "accountId": "aa" * 32,
                "deviceId": None,
                "publicKey": "z" * 44,
                "accountRootPublicKey": "bb" * 32,
            }
        )
        step = _step(NodeIdentityStep, self.config, client)

        results = {}
        assert _run(step.execute(results, {})) is True

        client.get_node_identity.assert_called_once_with()
        assert results["identity_calimero-node-2"]["deviceId"] is None


class TestOutputsAreActuallyExported:
    """`outputs:` is inert unless a step calls `_export_variables`.

    Recording a result in `workflow_results` is NOT the same as exporting it, and
    nothing in the framework enforces the second. These steps shipped without it:
    every `outputs:` on them exported nothing, so `{{root_key}}` stayed literal and
    surfaced as a 12-character string hitting an api that wanted 64. The earlier
    tests asserted `workflow_results` and passed throughout.

    So: assert the exported variables, by name, for every step that declares any.
    """

    def test_account_create_exports_its_outputs(self):
        client = MagicMock()
        client.create_account.return_value = _envelope(
            {
                "accountId": "aa" * 32,
                "deviceId": "bb" * 32,
                "accountRootKey": "cc" * 32,
                "accountNonce": "dd" * 16,
            }
        )
        step = _step(
            AccountCreateStep,
            {
                "type": "account_create",
                "name": "Enrol",
                "node": "calimero-node-2",
                "namespace_id": NAMESPACE,
                "outputs": {
                    "account": "accountId",
                    "root_key": "accountRootKey",
                    "nonce": "accountNonce",
                },
            },
            client,
        )

        dynamic_values = {}
        assert _run(step.execute({}, dynamic_values)) is True
        assert dynamic_values["account"] == "aa" * 32
        assert dynamic_values["root_key"] == "cc" * 32
        assert dynamic_values["nonce"] == "dd" * 16

    def test_account_pair_exports_the_paired_device(self):
        new_device = MagicMock()
        init = {
            "deviceId": "ee" * 32,
            "kemPublicKey": "11" * 32,
            "signPublicKey": "22" * 32,
            "statement": "33" * 64,
            "confirmationCode": "0011223344556677",
        }
        new_device.pair_device_init.return_value = _envelope(init)
        holder = MagicMock()
        holder.pair_device_complete.return_value = _envelope(
            {
                "accountId": "aa" * 32,
                "keyDelivered": True,
                "confirmationCode": init["confirmationCode"],
            }
        )
        step = AccountPairStep(
            {
                "type": "account_pair",
                "name": "Pair",
                "node": "calimero-node-3",
                "holder": "calimero-node-2",
                "namespace_id": NAMESPACE,
                "root_key": "cc" * 32,
                "nonce": "dd" * 16,
                "outputs": {"paired_device": "deviceId", "delivered": "keyDelivered"},
            }
        )
        step._client = MagicMock(  # noqa: SLF001
            side_effect=lambda name: (
                new_device if name == "calimero-node-3" else holder
            )
        )

        dynamic_values = {}
        assert _run(step.execute({}, dynamic_values)) is True
        assert dynamic_values["paired_device"] == "ee" * 32
        assert dynamic_values["delivered"] is True

    def test_account_revoke_exports_key_rotated(self):
        client = MagicMock()
        client.revoke_device.return_value = _envelope({"keyRotated": True})
        step = _step(
            AccountRevokeStep,
            {
                "type": "account_revoke",
                "name": "Revoke",
                "node": "calimero-node-1",
                "namespace_id": NAMESPACE,
                "device_id": "ee" * 32,
                "outputs": {"rotated": "keyRotated"},
            },
            client,
        )
        dynamic_values = {}
        assert _run(step.execute({}, dynamic_values)) is True
        assert dynamic_values["rotated"] is True

    def test_node_identity_exports_what_account_create_used_to(self):
        """The three ids scenarios were calling `account_create` to obtain.

        That step was a mutation used as a getter — the join had already enrolled
        the node — and `deviceId` and the account root had no other source. This
        is the source.
        """
        client = MagicMock()
        client.get_node_identity.return_value = _envelope(
            {
                "accountId": "aa" * 32,
                "deviceId": "cc" * 32,
                "publicKey": "z" * 44,
                "accountRootPublicKey": "bb" * 32,
            }
        )
        step = _step(
            NodeIdentityStep,
            {
                "type": "node_identity",
                "name": "Who am I",
                "node": "calimero-node-2",
                "outputs": {
                    "acct": "accountId",
                    "dev": "deviceId",
                    "root": "accountRootPublicKey",
                },
            },
            client,
        )
        dynamic_values = {}
        assert _run(step.execute({}, dynamic_values)) is True
        assert dynamic_values["acct"] == "aa" * 32
        assert dynamic_values["dev"] == "cc" * 32
        assert dynamic_values["root"] == "bb" * 32
