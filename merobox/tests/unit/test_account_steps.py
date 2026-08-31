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
import sys
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.account import (
    AccountApplicationsStep,
    AccountCreateStep,
    AccountDevicesStep,
    AccountPairStep,
    AccountRelinkStep,
    AccountRevokeStep,
    NodeIdentityStep,
    PerformIntentStep,
    SignWarrantStep,
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


def _client_error(status, body="device is linked to another account"):
    """What calimero-client-py raises for a non-2xx answer, verbatim: a plain
    RuntimeError whose message is the only place the status appears."""
    return RuntimeError(f"Client error: HTTP {status}: {body}")


def _step(cls, config, client=None):
    """A step whose `_client` hands back `client` (a fresh MagicMock by default)."""
    step = cls(config)
    step._client = MagicMock(return_value=client or MagicMock())  # noqa: SLF001
    return step


DEVICE = "ee" * 32
APP_ONE = "1" * 44
APP_TWO = "2" * 44


# =============================================================================
# AccountRelinkStep
# =============================================================================


class TestAccountRelinkStep:
    def setup_method(self):
        self.config = {
            "type": "account_relink",
            "name": "Relink",
            "node": "calimero-node-1",
            "device_id": DEVICE,
        }

    @pytest.mark.parametrize("field", ["node", "device_id"])
    def test_missing_required_field_raises(self, field):
        config = {**self.config}
        del config[field]
        with pytest.raises(ValueError, match=field):
            AccountRelinkStep(config)

    def test_applications_must_be_a_list_of_strings(self):
        with pytest.raises(ValueError, match="applications"):
            AccountRelinkStep({**self.config, "applications": APP_ONE})

    def test_naming_no_application_repairs_without_widening(self):
        """Empty means "do not widen" here, the opposite of `account_pair`.

        Passing `[]` through as a list would read server-side as a widening to
        nothing; `None` is what asks for a repair against the stored scope.
        """
        client = MagicMock()
        client.relink_device.return_value = _envelope(
            {"applications": [APP_ONE], "outcomes": []}
        )
        step = _step(AccountRelinkStep, self.config, client)

        assert _run(step.execute({}, {})) is True
        client.relink_device.assert_called_once_with(DEVICE, None)

    def test_naming_applications_passes_them_through(self):
        client = MagicMock()
        client.relink_device.return_value = _envelope(
            {"applications": [APP_ONE, APP_TWO], "outcomes": []}
        )
        step = _step(
            AccountRelinkStep,
            {**self.config, "applications": [APP_TWO]},
            client,
        )

        assert _run(step.execute({}, {})) is True
        client.relink_device.assert_called_once_with(DEVICE, [APP_TWO])

    def test_a_failing_relink_fails_the_step(self):
        client = MagicMock()
        client.relink_device.side_effect = RuntimeError("revoked")
        step = _step(AccountRelinkStep, self.config, client)
        assert _run(step.execute({}, {})) is False

    def test_exports_the_scope_after_the_repair(self):
        client = MagicMock()
        client.relink_device.return_value = _envelope(
            {"applications": [APP_ONE], "outcomes": []}
        )
        step = _step(
            AccountRelinkStep,
            {**self.config, "outputs": {"scope": "applications"}},
            client,
        )

        dynamic_values = {}
        assert _run(step.execute({}, dynamic_values)) is True
        assert dynamic_values["scope"] == [APP_ONE]


# =============================================================================
# AccountDevicesStep and AccountApplicationsStep
# =============================================================================


class TestListingAssertions:
    """A listing is fetched once and checked in place.

    Before this the scenario called the endpoint twice: the step to read it, then
    `assert_api_response` on the same path to say anything about what came back.
    """

    def _devices(self, **overrides):
        row = {
            "deviceId": DEVICE,
            "isSelf": False,
            "revoked": False,
            "applications": [APP_ONE],
            "namespaces": ["ns-a1"],
        }
        row.update(overrides)
        return _envelope({"devices": [row, {"deviceId": "cc" * 32, "isSelf": True}]})

    def _step_with(self, client, **extra):
        return _step(
            AccountDevicesStep,
            {
                "type": "account_devices",
                "name": "Devices",
                "node": "calimero-node-1",
                **extra,
            },
            client,
        )

    def test_where_and_match_pass_on_the_named_row(self):
        client = MagicMock()
        client.list_account_devices.return_value = self._devices()
        step = self._step_with(
            client,
            where={"deviceId": DEVICE},
            match={"isSelf": False, "applications.0": APP_ONE},
        )
        assert _run(step.execute({}, {})) is True
        client.list_account_devices.assert_called_once_with()

    def test_a_missed_assertion_fails_the_step(self):
        client = MagicMock()
        client.list_account_devices.return_value = self._devices()
        step = self._step_with(
            client, where={"deviceId": DEVICE}, match={"applications.0": APP_TWO}
        )
        assert _run(step.execute({}, {})) is False

    def test_the_wrong_row_is_not_asserted_against(self):
        # The sibling row IS isSelf, so a `where` that did nothing would pass this.
        client = MagicMock()
        client.list_account_devices.return_value = self._devices()
        step = self._step_with(
            client, where={"deviceId": DEVICE}, match={"isSelf": True}
        )
        assert _run(step.execute({}, {})) is False

    def test_no_matching_row_fails(self):
        client = MagicMock()
        client.list_account_devices.return_value = self._devices()
        step = self._step_with(client, where={"deviceId": "zz" * 32}, match={})
        assert _run(step.execute({}, {})) is False

    def test_without_assertions_it_is_still_a_plain_read(self):
        client = MagicMock()
        client.list_account_devices.return_value = self._devices()
        step = self._step_with(client)
        assert _run(step.execute({}, {})) is True

    def test_it_rereads_until_the_assertion_holds(self):
        """The paired-device case: a member of nothing cannot be barriered on."""
        client = MagicMock()
        client.list_account_devices.side_effect = [
            self._devices(applications=[]),
            self._devices(applications=[]),
            self._devices(),
        ]
        step = self._step_with(
            client,
            where={"deviceId": DEVICE},
            match={"applications.0": APP_ONE},
            retries=3,
            interval=0.01,
        )
        assert _run(step.execute({}, {})) is True
        assert client.list_account_devices.call_count == 3

    def test_it_stops_rereading_once_satisfied(self):
        client = MagicMock()
        client.list_account_devices.return_value = self._devices()
        step = self._step_with(
            client,
            where={"deviceId": DEVICE},
            match={"applications.0": APP_ONE},
            retries=5,
            interval=0.01,
        )
        assert _run(step.execute({}, {})) is True
        assert client.list_account_devices.call_count == 1

    def test_it_gives_up_after_the_budget(self):
        client = MagicMock()
        client.list_account_devices.return_value = self._devices(applications=[])
        step = self._step_with(
            client,
            where={"deviceId": DEVICE},
            match={"applications.0": APP_ONE},
            retries=3,
            interval=0.01,
        )
        assert _run(step.execute({}, {})) is False
        assert client.list_account_devices.call_count == 3

    @pytest.mark.parametrize("field", ["retries", "interval"])
    def test_a_non_positive_budget_is_a_scenario_bug(self, field):
        with pytest.raises(ValueError, match=field):
            AccountDevicesStep(
                {
                    "type": "account_devices",
                    "name": "Devices",
                    "node": "calimero-node-1",
                    field: 0,
                }
            )


class TestAccountListingSteps:
    def test_devices_requires_a_node(self):
        with pytest.raises(ValueError, match="node"):
            AccountDevicesStep({"type": "account_devices", "name": "Devices"})

    def test_devices_lists_and_exports(self):
        client = MagicMock()
        client.list_account_devices.return_value = _envelope(
            {"devices": [{"deviceId": DEVICE, "applications": [APP_ONE]}]}
        )
        step = _step(
            AccountDevicesStep,
            {
                "type": "account_devices",
                "name": "Devices",
                "node": "calimero-node-1",
                "outputs": {"devices": "devices"},
            },
            client,
        )

        dynamic_values = {}
        assert _run(step.execute({}, dynamic_values)) is True
        client.list_account_devices.assert_called_once_with()
        assert dynamic_values["devices"][0]["deviceId"] == DEVICE

    def test_applications_requires_a_node(self):
        with pytest.raises(ValueError, match="node"):
            AccountApplicationsStep({"type": "account_applications", "name": "Apps"})

    def test_applications_lists_and_exports(self):
        client = MagicMock()
        client.list_account_applications.return_value = _envelope(
            {"applications": [{"applicationId": APP_ONE, "namespaces": [NAMESPACE]}]}
        )
        step = _step(
            AccountApplicationsStep,
            {
                "type": "account_applications",
                "name": "Apps",
                "node": "calimero-node-1",
                "outputs": {"apps": "applications"},
            },
            client,
        )

        dynamic_values = {}
        assert _run(step.execute({}, dynamic_values)) is True
        client.list_account_applications.assert_called_once_with()
        assert dynamic_values["apps"][0]["applicationId"] == APP_ONE

    def test_a_failing_listing_fails_the_step(self):
        client = MagicMock()
        client.list_account_devices.side_effect = RuntimeError("no account")
        step = _step(
            AccountDevicesStep,
            {"type": "account_devices", "name": "Devices", "node": "calimero-node-1"},
            client,
        )
        assert _run(step.execute({}, {})) is False


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
            "namespaces": [NAMESPACE],
            "root_key": "cc" * 32,
        }

    def test_valid_config_passes_validation(self):
        _step(AccountPairStep, self.config)

    @pytest.mark.parametrize("field", ["node", "holder", "namespaces", "root_key"])
    def test_missing_required_field_raises(self, field):
        config = {**self.config}
        del config[field]
        with pytest.raises(ValueError, match=field):
            AccountPairStep(config)

    def _init_payload(self, code="0011223344556677"):
        return {
            "accountId": "aa" * 32,
            "deviceId": "ee" * 32,
            "kemPublicKey": "11" * 32,
            "signPublicKey": "22" * 32,
            "statement": "33" * 64,
            "confirmationCode": code,
        }

    def _paired(self, init, code=None, **complete):
        """A step wired to two clients: the new device's, and the holder's.

        Both endpoints really answer with `accountId` and `deviceId`, so complete
        echoes init's unless a test overrides one to model a disagreement.
        """
        new_device = MagicMock()
        new_device.pair_device_init.return_value = _envelope(init)
        holder = MagicMock()
        holder.pair_device_complete.return_value = _envelope(
            {
                "accountId": init.get("accountId"),
                "deviceId": init.get("deviceId"),
                "keyDelivered": True,
                "confirmationCode": code or init.get("confirmationCode"),
                **complete,
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

        new_device.pair_device_init.assert_called_once_with("cc" * 32, [NAMESPACE])
        # Everything init minted has to reach complete verbatim; a dropped field
        # would be a pairing that certifies key material nobody committed to.
        holder.pair_device_complete.assert_called_once_with(
            init["deviceId"],
            init["kemPublicKey"],
            init["signPublicKey"],
            init["statement"],
            init["confirmationCode"],
            None,
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

    @pytest.mark.parametrize("field", ["accountId", "deviceId"])
    def test_complete_certifying_something_else_fails_the_step(self, field):
        """pair-complete has to have certified the device pair-init minted.

        Taking init's value over complete's would export a device id the holder
        never linked, and hide a link made to another account entirely.
        """
        init = self._init_payload()
        step, _new_device, _holder = self._paired(init, **{field: "99" * 32})
        assert _run(step.execute({}, {})) is False

    @pytest.mark.parametrize(
        "omit",
        [
            "accountId",
            "deviceId",
            "kemPublicKey",
            "signPublicKey",
            "statement",
            "confirmationCode",
        ],
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
            "accountId": "aa" * 32,
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
                "accountId": init["accountId"],
                "deviceId": init["deviceId"],
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
                "namespaces": [NAMESPACE],
                "root_key": "cc" * 32,
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


# =============================================================================
# SignWarrantStep
# =============================================================================

#: A device certified by a fixed test account, captured from
#: `merod account sign-cert --generate` against the well-known
#: "legal winner thank year…" phrase. It owns nothing.
#:
#: Frozen rather than generated because it has to certify exactly the key
#: WARRANT_SECRET holds; generating a consistent pair here would mean
#: reimplementing the certification, and the test would exercise the fixture.
WARRANT_CREDENTIAL = (
    "02b2a942ff4c98718bed76e255987f6d59b1a72d3b2cd2510003e6170ac63a9ffb00000000"
    "0e2cd2d3dc84e1db5088e32510ca45bc491e4033bbb0f6bbb733bc0c7b7f5e304d0774b93e"
    "8028899a745dbe03d7727fa31fc2f060945b5789cb36c23cba380366245580f7aa816a35d1"
    "ff324a714355995ef44a72bcd2341e21d9587d16efce973135e50bc7280f06bb32a53a5669"
    "83cf0f0c8428be4b461df54264f073195400000000000000"
    "00e0c3743677508f5cfbe245f043f2d7bc3ba6c88c001464cae581e2e9ec8cb63780f1f5c2"
    "a393521a0038b357fffe63092403fa6e0e2ec12da5e96d50692d400f"
)
WARRANT_SECRET = "4987ccd0fb7ef36bf7f61e8f99fd150d33e6adac47649f23bfd7109c2e36a3ba"
WARRANT_ACCOUNT = "0e2cd2d3dc84e1db5088e32510ca45bc491e4033bbb0f6bbb733bc0c7b7f5e30"
#: Base58, because a context id is one. An account id, above, is hex.
WARRANT_CONTEXT = "1thX6LZfHDZZKUs92febYZhYRcXddmzfzF2NvTkPNE"


class TestSignWarrantStep:
    """Plumbing only, deliberately.

    `conftest.py` replaces `calimero_client_py` with a MagicMock for every test in
    this suite, so unit tests never need the native extension built. That is the
    right call and it decides the split: what belongs here is that the step
    resolves its fields, hands the binding the arguments it should, and exports
    what comes back.

    The properties that actually matter cryptographically — that reformatting an
    intent's arguments cannot change what the signature commits to, that a
    credential must certify the signing key, that a context is base58 and an
    account hex — are asserted in calimero-client-py, in Rust and in pytest,
    where the real binding runs. Re-asserting them against a mock here would
    prove only that the mock agrees with itself.
    """

    def setup_method(self):
        self.config = {
            "type": "sign_warrant",
            "name": "Mint",
            "context_id": WARRANT_CONTEXT,
            "executor": WARRANT_ACCOUNT,
            "method": "set",
            "args": {"key": "k", "value": "v"},
            "device_secret": WARRANT_SECRET,
            "credential": WARRANT_CREDENTIAL,
        }
        self.payload = {
            "warrant": "ab" * 8,
            "authorAccount": WARRANT_ACCOUNT,
            "authorDeviceKey": "66245580f7aa816a35d1ff324a714355995ef44a72bcd2341e21d9587d16efce",
            "intentHash": "cd" * 32,
            "nonce": 1,
            "notAfter": 1787588328,
        }

    def _patched(self, **overrides):
        """The step, with the module-level binding it imports stood in for."""
        minter = MagicMock(return_value=self.payload)
        module = MagicMock()
        module.sign_warrant = minter
        patcher = patch.dict(sys.modules, {"calimero_client_py": module})
        return SignWarrantStep({**self.config, **overrides}), minter, patcher

    def test_valid_config_passes_validation(self):
        SignWarrantStep(self.config)

    @pytest.mark.parametrize(
        "field", ["context_id", "executor", "method", "device_secret", "credential"]
    )
    def test_missing_required_field_raises(self, field):
        config = {**self.config}
        del config[field]
        with pytest.raises(ValueError, match=field):
            SignWarrantStep(config)

    def test_args_must_be_a_mapping(self):
        """A list is valid JSON of the wrong shape.

        Left to the binding it would mint a warrant committing to it, and the node
        would refuse the intent as a mismatch — a long way from the line at fault.
        """
        with pytest.raises(ValueError, match="args"):
            SignWarrantStep({**self.config, "args": ["k", "v"]})

    def test_it_mints_with_the_configured_fields_and_exports_the_result(self):
        step, minter, patcher = self._patched()
        results = {}
        with patcher:
            assert _run(step.execute(results, {})) is True

        # `args` reaches the binding as a JSON *string*: that is its signature,
        # and both sides re-serialize so the committed bytes cannot depend on how
        # a scenario spelled the mapping.
        minter.assert_called_once_with(
            context_id=WARRANT_CONTEXT,
            executor=WARRANT_ACCOUNT,
            method="set",
            args='{"key": "k", "value": "v"}',
            nonce=1,
            device_secret=WARRANT_SECRET,
            credential=WARRANT_CREDENTIAL,
            valid_for=300,
        )
        # Keyed on the executor, not a node: this step has no node, and two
        # warrants in one scenario are told apart by who may spend them.
        assert results[f"signed_warrant_{WARRANT_ACCOUNT}"] == self.payload

    def test_nonce_and_validity_are_passed_through_as_numbers(self):
        step, minter, patcher = self._patched(nonce=7, valid_for=60)
        with patcher:
            assert _run(step.execute({}, {})) is True
        kwargs = minter.call_args.kwargs
        assert kwargs["nonce"] == 7
        assert kwargs["valid_for"] == 60

    def test_placeholders_inside_args_are_resolved(self):
        """`args` carries scenario values, so it has to resolve like every field.

        Leaving them literal would commit the warrant to the text `{{key}}` and
        the node would refuse the intent — correctly, and unhelpfully.
        """
        step, minter, patcher = self._patched(args={"key": "{{captured_key}}"})
        with patcher:
            assert _run(step.execute({}, {"captured_key": "resolved"})) is True
        assert minter.call_args.kwargs["args"] == '{"key": "resolved"}'

    def test_a_refused_mint_fails_the_step(self):
        """A bad credential or a mismatched key is refused by the binding.

        It has to fail the step rather than pass with nothing, or the scenario
        would carry an empty warrant to a perform_intent step and fail there.
        """
        module = MagicMock()
        module.sign_warrant = MagicMock(
            side_effect=ValueError("this credential certifies a different key")
        )
        step = SignWarrantStep(self.config)
        with patch.dict(sys.modules, {"calimero_client_py": module}):
            assert _run(step.execute({}, {})) is False


# =============================================================================
# PerformIntentStep
# =============================================================================


class TestPerformIntentStep:
    def setup_method(self):
        self.config = {
            "type": "perform_intent",
            "name": "Delegate",
            "node": "calimero-node-1",
            "context_id": WARRANT_CONTEXT,
            "method": "set",
            "args": {"key": "k", "value": "v"},
            "warrant": "aa" * 8,
            "author_proof": WARRANT_CREDENTIAL,
        }

    def test_valid_config_passes_validation(self):
        _step(PerformIntentStep, self.config)

    @pytest.mark.parametrize(
        "field", ["node", "context_id", "method", "warrant", "author_proof"]
    )
    def test_missing_required_field_raises(self, field):
        config = {**self.config}
        del config[field]
        with pytest.raises(ValueError, match=field):
            PerformIntentStep(config)

    def test_it_sends_the_authors_half_and_records_the_root(self):
        """Only the author's half goes out.

        The node attaches its own credential, so a scenario never has to learn
        which of the node's processes runs the intent — and a re-key on its side
        does not void a warrant already minted.
        """
        payload = {
            "rootHash": "415a214f379558acccf0c2d59ad5e5eb18e61d4a7d1d215f45034cf162666480",
            "returns": None,
        }
        client = MagicMock()
        client.perform_intent.return_value = _envelope(payload)
        step = _step(PerformIntentStep, self.config, client)

        results = {}
        assert _run(step.execute(results, {})) is True

        client.perform_intent.assert_called_once_with(
            WARRANT_CONTEXT,
            "set",
            '{"key": "k", "value": "v"}',
            "aa" * 8,
            WARRANT_CREDENTIAL,
        )
        assert results["performed_intent_calimero-node-1"] == payload

    def test_a_refused_intent_fails_the_step(self):
        """A refusal is the normal answer to a spent warrant or a missing grant.

        It has to fail the step rather than pass quietly, or a scenario asserting
        a delegated write would go green on a write that never happened.
        """
        client = MagicMock()
        client.perform_intent.side_effect = RuntimeError(
            "the executor holds no CAN_AUTHOR_ON_BEHALF grant"
        )
        step = _step(PerformIntentStep, self.config, client)
        assert _run(step.execute({}, {})) is False


class TestPerformIntentExpectedFailure:
    """A refusal is a first-class outcome, and the RIGHT refusal at that.

    The endpoint refuses three things worth asserting positively — a relay with
    no authorship grant, a warrant that does not cover the intent, and one
    already spent. A scenario that can only assert acceptance cannot show the
    grant is load-bearing, which is the whole point of granting it.
    """

    def setup_method(self):
        self.config = {
            "type": "perform_intent",
            "name": "Delegate",
            "node": "calimero-node-1",
            "context_id": WARRANT_CONTEXT,
            "method": "set",
            "args": {"key": "k"},
            "warrant": "aa" * 8,
            "author_proof": WARRANT_CREDENTIAL,
            "expected_failure": True,
        }

    def _refusing(self, message):
        client = MagicMock()
        client.perform_intent.side_effect = RuntimeError(message)
        return client

    def test_a_refusal_passes_when_it_is_expected(self):
        step = _step(
            PerformIntentStep,
            self.config,
            self._refusing("the executor holds no CAN_AUTHOR_ON_BEHALF grant"),
        )
        assert _run(step.execute({}, {})) is True

    def test_the_expected_error_must_actually_match(self):
        """Otherwise an unreachable node satisfies the refusal under test.

        This is the assertion that makes `expected_failure` worth having rather
        than merely permissive: a connection error and a `403` are both failures,
        and only one of them is evidence.
        """
        step = _step(
            PerformIntentStep,
            {**self.config, "expected_error": "CAN_AUTHOR_ON_BEHALF"},
            self._refusing("connection refused"),
        )
        assert _run(step.execute({}, {})) is False

        step = _step(
            PerformIntentStep,
            {**self.config, "expected_error": "CAN_AUTHOR_ON_BEHALF"},
            self._refusing("the executor holds no CAN_AUTHOR_ON_BEHALF grant"),
        )
        assert _run(step.execute({}, {})) is True

    def test_an_unexpected_success_fails_the_step(self):
        """A warrant that should have been refused and was not is the worst
        outcome of the three, so it must not read as a pass."""
        client = MagicMock()
        client.perform_intent.return_value = _envelope(
            {"rootHash": "abc", "returns": None}
        )
        step = _step(PerformIntentStep, self.config, client)
        assert _run(step.execute({}, {})) is False


class TestSignWarrantExpectedFailure:
    def test_a_refused_mint_passes_when_expected(self):
        module = MagicMock()
        module.sign_warrant = MagicMock(
            side_effect=ValueError("this credential certifies a different key")
        )
        step = SignWarrantStep(
            {
                "type": "sign_warrant",
                "name": "Mint",
                "context_id": WARRANT_CONTEXT,
                "executor": WARRANT_ACCOUNT,
                "method": "set",
                "device_secret": WARRANT_SECRET,
                "credential": WARRANT_CREDENTIAL,
                "expected_failure": True,
                "expected_error": "certifies a different key",
            }
        )
        with patch.dict(sys.modules, {"calimero_client_py": module}):
            assert _run(step.execute({}, {})) is True


# =============================================================================
# expect_status
# =============================================================================


class TestExpectStatus:
    """Assert WHICH refusal happened, not merely that one did.

    `expected_failure` passes on a 500 and on an unreachable node, which are the
    answers core's typed statuses exist to tell apart from a real refusal.
    """

    RELINK = {
        "type": "account_relink",
        "name": "Relink",
        "node": "calimero-node-1",
        "device_id": DEVICE,
    }

    # Every account step that takes the field, with the binding it refuses on and
    # what that binding answers when it does not.
    STEPS = [
        (
            AccountCreateStep,
            {
                "type": "account_create",
                "name": "Create",
                "node": "calimero-node-1",
                "namespace_id": NAMESPACE,
            },
            "create_account",
            {"accountId": "aa" * 32, "deviceId": DEVICE},
        ),
        (
            AccountRevokeStep,
            {
                "type": "account_revoke",
                "name": "Revoke",
                "node": "calimero-node-1",
                "namespace_id": NAMESPACE,
                "device_id": DEVICE,
            },
            "revoke_device",
            {"keyRotated": True},
        ),
        (
            AccountRelinkStep,
            RELINK,
            "relink_device",
            {"applications": [APP_ONE], "outcomes": []},
        ),
    ]

    def _relink(self, expect_status, side_effect=None, **extra):
        client = MagicMock()
        if side_effect is not None:
            client.relink_device.side_effect = side_effect
        else:
            client.relink_device.return_value = _envelope(
                {"applications": [APP_ONE], "outcomes": []}
            )
        return _step(
            AccountRelinkStep,
            {**self.RELINK, "expect_status": expect_status, **extra},
            client,
        )

    def test_the_expected_status_passes_the_step(self):
        step = self._relink(403, side_effect=_client_error(403))
        assert _run(step.execute({}, {})) is True

    def test_another_status_fails_the_step(self):
        """The whole point: a 500 is not the refusal under test."""
        step = self._relink(403, side_effect=_client_error(500))
        assert _run(step.execute({}, {})) is False

    def test_succeeding_fails_the_step(self):
        step = self._relink(403)
        assert _run(step.execute({}, {})) is False

    def test_a_failure_carrying_no_status_fails_the_step(self):
        """An unreachable node has no status, and must not stand in for one."""
        step = self._relink(
            403,
            side_effect=RuntimeError(
                "Client error: error sending request for url (http://127.0.0.1:1/x)"
            ),
        )
        assert _run(step.execute({}, {})) is False

    def test_the_status_is_the_transport_s_not_the_body_s(self):
        step = self._relink(403, side_effect=_client_error(409, "not HTTP 403: no"))
        assert _run(step.execute({}, {})) is False

    def test_a_status_merely_quoted_elsewhere_is_not_read_as_one(self):
        """Read off the message's front rather than searched for, so a transport
        failure carrying the digits somewhere is not mistaken for a refusal."""
        step = self._relink(
            403,
            side_effect=RuntimeError(
                "Client error: error sending request for url "
                "(http://node/admin-api/HTTP 403)"
            ),
        )
        assert _run(step.execute({}, {})) is False

    @pytest.mark.parametrize("value", ["403", True, 40.3, None])
    def test_a_non_integer_status_is_rejected_at_validation(self, value):
        config = {**self.RELINK, "expect_status": value}
        if value is None:
            # Absent and null both mean "assert nothing", so neither may raise.
            AccountRelinkStep(config)
            return
        with pytest.raises(ValueError, match="expect_status"):
            AccountRelinkStep(config)

    @pytest.mark.parametrize("status", [400, 403, 404, 409])
    def test_each_status_core_types_is_matched_exactly(self, status):
        assert _run(
            self._relink(status, side_effect=_client_error(status)).execute({}, {})
        )
        other = 409 if status != 409 else 400
        assert not _run(
            self._relink(status, side_effect=_client_error(other)).execute({}, {})
        )

    @pytest.mark.parametrize("cls,config,binding,_payload", STEPS)
    def test_every_step_taking_it_matches_the_status(
        self, cls, config, binding, _payload
    ):
        client = MagicMock()
        getattr(client, binding).side_effect = _client_error(404)
        step = _step(cls, {**config, "expect_status": 404}, client)
        assert _run(step.execute({}, {})) is True

        client = MagicMock()
        getattr(client, binding).side_effect = _client_error(500)
        step = _step(cls, {**config, "expect_status": 404}, client)
        assert _run(step.execute({}, {})) is False

    @pytest.mark.parametrize("cls,config,binding,payload", STEPS)
    def test_every_step_taking_it_fails_when_the_call_succeeds(
        self, cls, config, binding, payload
    ):
        """Each step wires the branch itself, so a step that forgot it would let
        the refusal it was asserting go through unnoticed."""
        client = MagicMock()
        getattr(client, binding).return_value = _envelope(payload)
        step = _step(cls, {**config, "expect_status": 404}, client)
        assert _run(step.execute({}, {})) is False

    def _pair(self, expect_status, complete_side_effect=None, **complete):
        init = {
            "accountId": "aa" * 32,
            "deviceId": "ee" * 32,
            "kemPublicKey": "11" * 32,
            "signPublicKey": "22" * 32,
            "statement": "33" * 64,
            "confirmationCode": "0011223344556677",
        }
        new_device = MagicMock()
        new_device.pair_device_init.return_value = _envelope(init)
        holder = MagicMock()
        if complete_side_effect is not None:
            holder.pair_device_complete.side_effect = complete_side_effect
        else:
            holder.pair_device_complete.return_value = _envelope(
                {**init, "keyDelivered": True, **complete}
            )
        step = AccountPairStep(
            {
                "type": "account_pair",
                "name": "Pair",
                "node": "calimero-node-3",
                "holder": "calimero-node-2",
                "namespaces": [NAMESPACE],
                "root_key": "cc" * 32,
                "expect_status": expect_status,
            }
        )
        step._client = MagicMock(  # noqa: SLF001
            side_effect=lambda name: (
                new_device if name == "calimero-node-3" else holder
            )
        )
        return step

    def test_pair_asserts_the_status_of_the_holder_s_refusal(self):
        """Pairing's refusals come from pair-complete, on the OTHER node, so the
        assertion has to survive the two-call shape of the step."""
        step = self._pair(400, complete_side_effect=_client_error(400))
        assert _run(step.execute({}, {})) is True

    def test_pair_fails_when_the_pairing_goes_through(self):
        step = self._pair(400)
        assert _run(step.execute({}, {})) is False

    def test_a_cross_check_failure_is_not_a_status_refusal(self):
        """A mismatch merobox itself raised carries no HTTP status, so it fails
        closed rather than passing for the refusal the scenario asked about."""
        step = self._pair(403, deviceId="99" * 32)
        assert _run(step.execute({}, {})) is False

    def test_an_expected_status_records_nothing_and_exports_nothing(self):
        """A refused call has no payload, so a scenario must not be able to read
        one out of it and carry a stale value forward."""
        step = self._relink(
            403, side_effect=_client_error(403), outputs={"scope": "applications"}
        )
        results, dynamic_values = {}, {}
        assert _run(step.execute(results, dynamic_values)) is True
        assert results == {}
        assert dynamic_values == {}
