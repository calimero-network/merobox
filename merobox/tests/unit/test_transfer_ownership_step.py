"""Unit tests for the transfer_ownership step (raw admin-api POST)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.group_management import TransferOwnershipStep

MODULE = "merobox.commands.bootstrap.steps.group_management"


def _config(**overrides):
    cfg = {
        "type": "transfer_ownership",
        "name": "hand over",
        "node": "n1",
        "group_id": "ns-hex",
        "new_owner": "acct-hex",
    }
    cfg.update(overrides)
    return cfg


def _exec(step, status=200, text="{}", token=None):
    resp = MagicMock(status_code=status, text=text)
    dynamic = {}
    with (
        patch.object(
            step, "_resolve_node_target", return_value=("http://localhost:7180/", "n1")
        ),
        patch.object(step, "_resolve_token", return_value=token),
        patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
        patch.object(step, "_print_node_logs_on_failure"),
        patch(f"{MODULE}.requests.post", return_value=resp) as post,
    ):
        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(step.execute({}, dynamic))
        finally:
            loop.close()
    return ok, post


class TestValidation:
    @pytest.mark.parametrize("field", ["node", "group_id", "new_owner"])
    def test_missing_field_raises(self, field):
        cfg = _config()
        del cfg[field]
        with pytest.raises(ValueError, match=field):
            TransferOwnershipStep(cfg)

    def test_non_string_new_owner_raises(self):
        with pytest.raises(ValueError, match="new_owner"):
            TransferOwnershipStep(_config(new_owner=7))


class TestExecute:
    def test_posts_camel_case_body_with_bearer_token(self):
        ok, post = _exec(TransferOwnershipStep(_config()), token="jwt")
        assert ok is True
        assert (
            post.call_args[0][0]
            == "http://localhost:7180/admin-api/groups/ns-hex/transfer-ownership"
        )
        assert post.call_args[1]["json"] == {"newOwner": "acct-hex"}
        assert post.call_args[1]["headers"] == {"Authorization": "Bearer jwt"}

    def test_no_token_sends_no_auth_header(self):
        _, post = _exec(TransferOwnershipStep(_config()))
        assert post.call_args[1]["headers"] == {}

    def test_refusal_fails_the_step(self):
        ok, _ = _exec(TransferOwnershipStep(_config()), status=403, text="not owner")
        assert ok is False

    def test_refusal_passes_when_expected(self):
        step = TransferOwnershipStep(
            _config(expected_failure=True, expected_error="HTTP 403")
        )
        ok, _ = _exec(step, status=403, text="not owner")
        assert ok is True

    def test_wrong_reason_fails_when_expected(self):
        step = TransferOwnershipStep(
            _config(expected_failure=True, expected_error="HTTP 403")
        )
        ok, _ = _exec(step, status=500, text="boom")
        assert ok is False

    def test_success_fails_when_failure_expected(self):
        step = TransferOwnershipStep(_config(expected_failure=True))
        ok, _ = _exec(step)
        assert ok is False
