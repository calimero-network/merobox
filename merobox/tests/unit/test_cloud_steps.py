"""
Unit tests for the cloud (mdma) workflow steps and the ``${ENV}`` resolver.

Covers IssueOwnershipProofStep, CloudRequestStep and AssertEqualsStep. Both
HTTP steps reach the network through ``requests.request``, so that is mocked at
module level (``merobox.commands.bootstrap.steps.cloud.requests``) the same way
the TEE step tests do it.
"""

import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps._env import EnvRefError, resolve_env_refs
from merobox.commands.bootstrap.steps.assertion import AssertEqualsStep
from merobox.commands.bootstrap.steps.cloud import (
    DEFAULT_MDMA_URL,
    NAMESPACE_PROOF_AUDIENCE,
    CloudRequestStep,
    IssueOwnershipProofStep,
)

_MODULE = "merobox.commands.bootstrap.steps.cloud"
_ADMIN_URL = "http://localhost:9180"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _response(status_code=200, payload=None, text=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text if text is not None else json.dumps(payload or {})
    return resp


def _make_step(cls, **overrides):
    cfg = {"type": overrides.pop("type", "cloud")}
    cfg.update(overrides)
    step = cls(cfg, manager=MagicMock())
    step._get_node_rpc_url = lambda _n: _ADMIN_URL
    return step


# =============================================================================
# ${ENV} resolution
# =============================================================================


class TestResolveEnvRefs:
    def test_substitutes_from_environment(self):
        with patch.dict(os.environ, {"MEROBOX_TEST_TOKEN": "s3cret"}):
            assert resolve_env_refs("${MEROBOX_TEST_TOKEN}") == "s3cret"
            assert resolve_env_refs("Bearer ${MEROBOX_TEST_TOKEN}") == "Bearer s3cret"

    def test_unset_variable_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(EnvRefError, match="MEROBOX_MISSING"):
                resolve_env_refs("${MEROBOX_MISSING}")

    def test_empty_variable_raises(self):
        """An empty secret must fail, not be sent as an empty string."""
        with patch.dict(os.environ, {"MEROBOX_EMPTY": ""}):
            with pytest.raises(EnvRefError):
                resolve_env_refs("${MEROBOX_EMPTY}")

    def test_walks_containers_and_leaves_other_types(self):
        with patch.dict(os.environ, {"MEROBOX_X": "1"}):
            assert resolve_env_refs({"a": ["${MEROBOX_X}", 2], "b": True}) == {
                "a": ["1", 2],
                "b": True,
            }

    def test_bare_dollar_is_not_a_reference(self):
        assert resolve_env_refs("$NOT_A_REF") == "$NOT_A_REF"


# =============================================================================
# IssueOwnershipProofStep
# =============================================================================


class TestIssueOwnershipProofValidation:
    def test_missing_subject_raises(self):
        with pytest.raises(ValueError, match="subject"):
            IssueOwnershipProofStep(
                {
                    "type": "issue_ownership_proof",
                    "node": "node-1",
                    "group_id": "ns",
                },
                manager=MagicMock(),
            )

    def test_expires_in_ms_must_be_int(self):
        with pytest.raises(ValueError, match="expires_in_ms"):
            IssueOwnershipProofStep(
                {
                    "type": "issue_ownership_proof",
                    "node": "node-1",
                    "group_id": "ns",
                    "subject": "a@b.c",
                    "expires_in_ms": "soon",
                },
                manager=MagicMock(),
            )


class TestIssueOwnershipProofExecute:
    def _step(self, **overrides):
        step = _make_step(
            IssueOwnershipProofStep,
            type="issue_ownership_proof",
            node="node-1",
            group_id="ns-hex",
            subject="a@b.c",
            **overrides,
        )
        step._resolve_dynamic_value = lambda v, *_a, **_k: v
        return step

    def test_camelcase_body_without_context_id(self):
        step = self._step()
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200, {"data": {"signerPublicKey": "ab", "signature": "sig"}}
            )
            assert _run(step.execute({}, {})) is True

        method, url = req.request.call_args.args
        assert method == "POST"
        assert (
            url
            == f"{_ADMIN_URL}/admin-api/groups/ns-hex/issue-namespace-ownership-proof"
        )
        body = req.request.call_args.kwargs["json"]
        assert body["audience"] == NAMESPACE_PROOF_AUDIENCE
        assert body["subject"] == "a@b.c"
        assert isinstance(body["expiresAtMs"], int)
        assert body["nonce"]
        # The namespace variant rejects a contextId with a 400.
        assert "contextId" not in body
        assert "context_id" not in body

    def test_exports_proof_verbatim(self):
        step = self._step(outputs={"ownership_proof": "proof"})
        proof = {"signerPublicKey": "ab", "signedPayload": "cd", "signature": "ef"}
        dynamic = {}
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {"data": proof})
            assert _run(step.execute({}, dynamic)) is True
        # camelCase keys preserved — mdma accepts both spellings, so no
        # re-keying step is needed and none is done.
        assert dynamic["ownership_proof"] == proof

    def test_non_200_fails_the_step(self):
        step = self._step()
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(400, text="missing field")
            assert _run(step.execute({}, {})) is False

    def test_unset_env_subject_fails_without_calling_the_node(self):
        step = _make_step(
            IssueOwnershipProofStep,
            type="issue_ownership_proof",
            node="node-1",
            group_id="ns",
            subject="${MEROBOX_MISSING_EMAIL}",
        )
        step._resolve_dynamic_value = lambda v, *_a, **_k: v
        with patch.dict(os.environ, {}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                assert _run(step.execute({}, {})) is False
                req.request.assert_not_called()


# =============================================================================
# CloudRequestStep
# =============================================================================


class TestCloudRequestValidation:
    def test_bad_method_raises(self):
        with pytest.raises(ValueError, match="method"):
            CloudRequestStep(
                {"type": "cloud_request", "method": "FETCH", "path": "/x"},
                manager=MagicMock(),
            )

    def test_body_must_be_mapping(self):
        with pytest.raises(ValueError, match="body"):
            CloudRequestStep(
                {
                    "type": "cloud_request",
                    "method": "POST",
                    "path": "/x",
                    "body": ["nope"],
                },
                manager=MagicMock(),
            )

    def test_expect_status_must_be_ints(self):
        with pytest.raises(ValueError, match="expect_status"):
            CloudRequestStep(
                {
                    "type": "cloud_request",
                    "method": "GET",
                    "path": "/x",
                    "expect_status": ["200"],
                },
                manager=MagicMock(),
            )


class TestCloudRequestExecute:
    def _step(self, **overrides):
        cfg = {
            "type": "cloud_request",
            "name": "Claim namespace",
            "method": "POST",
            "path": "/api/cloud/namespaces/claim",
        }
        cfg.update(overrides)
        return CloudRequestStep(cfg, manager=MagicMock())

    def test_defaults_to_prod_url_and_env_token(self):
        step = self._step(method="GET", path="/api/cloud/me/namespaces")
        with patch.dict(os.environ, {"MDMA_SESSION": "jwt"}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                req.request.return_value = _response(200, {"ok": True})
                assert _run(step.execute({}, {})) is True

        method, url = req.request.call_args.args
        assert method == "GET"
        assert url == f"{DEFAULT_MDMA_URL}/api/cloud/me/namespaces"
        headers = req.request.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer jwt"

    def test_mdma_url_env_overrides_default(self):
        step = self._step(method="GET", path="/x")
        with patch.dict(
            os.environ,
            {"MDMA_SESSION": "jwt", "MDMA_URL": "http://localhost:8080/"},
            clear=True,
        ):
            with patch(f"{_MODULE}.requests") as req:
                req.request.return_value = _response(200, {})
                assert _run(step.execute({}, {})) is True
        assert req.request.call_args.args[1] == "http://localhost:8080/x"

    def test_forwards_a_dict_placeholder_verbatim(self):
        """``ownership_proof: '{{proof}}'`` must send the object, not its repr."""
        proof = {"signerPublicKey": "ab", "signature": "ef"}
        step = self._step(
            body={"namespace_id": "{{ns}}", "ownership_proof": "{{proof}}"}
        )
        dynamic = {"ns": "ns-hex", "proof": proof}
        with patch.dict(os.environ, {"MDMA_SESSION": "jwt"}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                req.request.return_value = _response(200, {})
                assert _run(step.execute({}, dynamic)) is True
        body = req.request.call_args.kwargs["json"]
        assert body == {"namespace_id": "ns-hex", "ownership_proof": proof}

    def test_path_placeholder_is_resolved(self):
        step = self._step(method="POST", path="/api/cloud/namespaces/{{ns}}/enable-ha")
        with patch.dict(os.environ, {"MDMA_SESSION": "jwt"}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                req.request.return_value = _response(200, {})
                assert _run(step.execute({}, {"ns": "abc"})) is True
        assert req.request.call_args.args[1].endswith(
            "/api/cloud/namespaces/abc/enable-ha"
        )

    def test_unexpected_status_fails_the_step(self):
        """The whole point: a 401 must not read as success (the shell bug)."""
        step = self._step()
        with patch.dict(os.environ, {"MDMA_SESSION": "jwt"}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                req.request.return_value = _response(401, text="expired")
                assert _run(step.execute({}, {})) is False

    def test_non_blocking_survives_an_error_status(self):
        step = self._step(non_blocking=True)
        with patch.dict(os.environ, {"MDMA_SESSION": "jwt"}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                req.request.return_value = _response(500, text="boom")
                assert _run(step.execute({}, {})) is True

    def test_missing_session_fails_without_calling_the_cloud(self):
        step = self._step()
        with patch.dict(os.environ, {}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                assert _run(step.execute({}, {})) is False
                req.request.assert_not_called()

    def test_explicit_null_token_sends_no_authorization(self):
        step = self._step(method="GET", path="/api/cloud/plans", token=None)
        with patch.dict(os.environ, {}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                req.request.return_value = _response(200, {})
                assert _run(step.execute({}, {})) is True
        assert "Authorization" not in req.request.call_args.kwargs["headers"]

    def test_exports_nested_response_fields(self):
        step = self._step(
            method="GET",
            path="/api/cloud/me/namespaces/{{ns}}/relays",
            outputs={
                "authorship_ready": "relays.0.authorship_ready",
                "executor_account": "relays.0.executor_account",
            },
        )
        payload = {
            "namespace_id": "abc",
            "relays": [
                {
                    "peer_id": "12D3Koo",
                    "executor_account": "ff" * 32,
                    "authorship_ready": True,
                    "status": "active",
                }
            ],
        }
        dynamic = {"ns": "abc"}
        with patch.dict(os.environ, {"MDMA_SESSION": "jwt"}, clear=True):
            with patch(f"{_MODULE}.requests") as req:
                req.request.return_value = _response(200, payload)
                assert _run(step.execute({}, dynamic)) is True
        assert dynamic["authorship_ready"] is True
        assert dynamic["executor_account"] == "ff" * 32


# =============================================================================
# AssertEqualsStep
# =============================================================================


class TestAssertEquals:
    def _step(self, **cfg):
        base = {"type": "assert_equals", "name": "check"}
        base.update(cfg)
        return AssertEqualsStep(base, manager=MagicMock())

    def test_json_bool_matches_yaml_bool(self):
        step = self._step(actual="{{authorship_ready}}", equals=True)
        assert _run(step.execute({}, {"authorship_ready": True})) is True

    def test_json_bool_mismatch_fails(self):
        step = self._step(actual="{{authorship_ready}}", equals=True)
        assert _run(step.execute({}, {"authorship_ready": False})) is False

    def test_string_spelling_of_bool_matches(self):
        step = self._step(actual="{{ready}}", equals=True)
        assert _run(step.execute({}, {"ready": "True"})) is True

    def test_identity_equality_with_ignore_case(self):
        step = self._step(
            actual="{{fleet_identity}}",
            equals="{{executor_account}}",
            ignore_case=True,
        )
        dynamic = {"fleet_identity": "AB" * 32, "executor_account": "ab" * 32}
        assert _run(step.execute({}, dynamic)) is True

    def test_identity_mismatch_fails(self):
        step = self._step(actual="{{a}}", equals="{{b}}")
        assert _run(step.execute({}, {"a": "aa", "b": "bb"})) is False

    def test_non_blocking_reports_but_passes(self):
        step = self._step(actual="{{a}}", equals="{{b}}", non_blocking=True)
        assert _run(step.execute({}, {"a": "aa", "b": "bb"})) is True

    def test_int_does_not_equal_its_string(self):
        step = self._step(actual=1, equals="1")
        assert _run(step.execute({}, {})) is False
