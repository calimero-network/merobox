"""
Unit tests for the account-identity workflow steps.

Covers: AccountCreateStep, AccountPairStep, AccountRevokeStep, AccountShowStep —
validation, the admin-api calls they make, and the two behaviours that are the
reason these exist as steps rather than shell scripts: values flow out through
`outputs`, and pairing's confirmation code is checked rather than passed along.

No `asyncio.run()` at module scope — see the note in the repo's test conventions;
these drive the coroutines through `asyncio.new_event_loop()` per test.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.account import (
    AccountCreateStep,
    AccountPairStep,
    AccountRevokeStep,
    AccountShowStep,
)

NAMESPACE = "ab" * 32


def _run(coro):
    """Drive one coroutine on a fresh loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _response(payload, ok=True, status=200):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status
    resp.json.return_value = {"data": payload}
    resp.text = str(payload)
    return resp


def _step(cls, config):
    step = cls(config)
    # Both are resolved through the node resolver in production; the tests care
    # about the request that goes out, not how the URL was found.
    step._resolve_node_for_client = MagicMock(  # noqa: SLF001 - test seam
        return_value=("http://node.test:2428", config.get("node"))
    )
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

    def test_posts_to_the_account_endpoint_and_records_the_result(self):
        step = _step(AccountCreateStep, self.config)
        payload = {
            "accountId": "aa" * 32,
            "deviceId": "bb" * 32,
            "accountRootKey": "cc" * 32,
            "accountNonce": "dd" * 16,
        }
        results = {}
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                return_value=_response(payload),
            ) as post,
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute(results, {})) is True

        called_url = post.call_args.args[0]
        assert called_url.endswith(f"/admin-api/namespaces/{NAMESPACE}/account")
        assert results["account_calimero-node-1"] == payload

    def test_a_response_without_an_account_id_fails_the_step(self):
        """A 200 that carries no account is a failure, not a silent pass.

        The scripts checked this too, and it matters: every later step keys off
        the account, so accepting an empty one turns a broken enrolment into a
        confusing failure three steps downstream.
        """
        step = _step(AccountCreateStep, self.config)
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                return_value=_response({"deviceId": "bb" * 32}),
            ),
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute({}, {})) is False

    def test_a_non_ok_response_fails_with_the_body_in_the_message(self):
        step = _step(AccountCreateStep, self.config)
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                return_value=_response({"error": "not a member"}, ok=False, status=403),
            ),
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute({}, {})) is False

    def test_attaches_the_cached_bearer_token(self):
        step = _step(AccountCreateStep, self.config)
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                return_value=_response({"accountId": "aa" * 32}),
            ) as post,
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            token = MagicMock()
            token.access_token = "tok"
            auth.return_value.get_cached_token.return_value = token
            assert _run(step.execute({}, {})) is True
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


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

    def test_runs_init_on_the_new_node_then_complete_on_the_holder(self):
        """The ordering is forced, not stylistic — assert it rather than assume it.

        The new device cannot mint its id without the account, and the holder
        cannot certify that device without the id and both keys.
        """
        step = _step(AccountPairStep, self.config)
        init = self._init_payload()
        complete = {
            "accountId": "aa" * 32,
            "keyDelivered": True,
            "confirmationCode": init["confirmationCode"],
        }
        results = {}
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                side_effect=[_response(init), _response(complete)],
            ) as post,
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute(results, {})) is True

        first, second = post.call_args_list
        assert first.args[0].endswith("/account/pair-init")
        assert second.args[0].endswith("/account/pair-complete")
        # What init minted has to reach complete verbatim; a dropped field would
        # be a pairing that certifies key material nobody committed to.
        for field in ("deviceId", "kemPublicKey", "signPublicKey", "statement"):
            assert second.kwargs["json"][field] == init[field]
        assert results["paired_account_calimero-node-3"]["deviceId"] == init["deviceId"]

    def test_a_confirmation_code_mismatch_fails_the_step(self):
        """The check a human is supposed to make, made mechanically.

        Both sides derive the code over exactly what gets certified, so a
        mismatch means the payload was altered between them. Passing the code
        along without comparing it would make this step the very "pasted
        alongside the keys" channel the code exists to defeat.
        """
        step = _step(AccountPairStep, self.config)
        init = self._init_payload("0011223344556677")
        complete = {
            "accountId": "aa" * 32,
            "keyDelivered": True,
            "confirmationCode": "ffffffffffffffff",
        }
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                side_effect=[_response(init), _response(complete)],
            ),
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute({}, {})) is False

    @pytest.mark.parametrize(
        "omit",
        ["deviceId", "kemPublicKey", "signPublicKey", "statement", "confirmationCode"],
    )
    def test_an_incomplete_init_response_fails_before_certifying(self, omit):
        """Fail at init rather than sending a half-formed pair-complete.

        `pair-complete` refuses key material that arrives without the signature
        of the device that minted it, so sending a partial payload would produce
        a refusal that reads like a node fault.
        """
        step = _step(AccountPairStep, self.config)
        init = self._init_payload()
        del init[omit]
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                side_effect=[_response(init)],
            ) as post,
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute({}, {})) is False
        assert len(post.call_args_list) == 1, "must not reach pair-complete"


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
        step = _step(AccountRevokeStep, self.config)
        results = {}
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                return_value=_response({"keyRotated": True}),
            ) as post,
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute(results, {})) is True

        assert post.call_args.args[0].endswith("/account/revoke")
        assert post.call_args.kwargs["json"] == {"deviceId": "ee" * 32}
        # Exported so a scenario asserts the rotation happened rather than
        # inferring it from a later read that could pass for other reasons.
        assert results["revoke_calimero-node-1"]["keyRotated"] is True

    def test_resolves_a_placeholder_device_id(self):
        """The device id normally arrives from a previous step's output."""
        config = {**self.config, "device_id": "{{paired_device}}"}
        step = _step(AccountRevokeStep, config)
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.post",
                return_value=_response({"keyRotated": True}),
            ) as post,
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute({}, {"paired_device": "ee" * 32})) is True
        assert post.call_args.kwargs["json"] == {"deviceId": "ee" * 32}


# =============================================================================
# AccountShowStep
# =============================================================================


class TestAccountShowStep:
    def setup_method(self):
        self.config = {
            "type": "account_show",
            "name": "Show",
            "node": "calimero-node-2",
            "namespace_id": NAMESPACE,
        }

    def test_valid_config_passes_validation(self):
        _step(AccountShowStep, self.config)

    def test_reads_the_account_and_keeps_a_null_device_as_an_answer(self):
        """`deviceId: null` is meaningful — it is how "no device here" is said.

        A step that treated it as missing data could not be used to assert that a
        revocation stuck.
        """
        step = _step(AccountShowStep, self.config)
        results = {}
        with (
            patch(
                "merobox.commands.bootstrap.steps.account.requests.get",
                return_value=_response({"accountId": "aa" * 32, "deviceId": None}),
            ) as get,
            patch("merobox.commands.bootstrap.steps.account.AuthManager") as auth,
        ):
            auth.return_value.get_cached_token.return_value = None
            assert _run(step.execute(results, {})) is True

        assert get.call_args.args[0].endswith(
            f"/admin-api/namespaces/{NAMESPACE}/account"
        )
        assert results["shown_account_calimero-node-2"]["deviceId"] is None
