"""
Unit tests for the `delete_blob` workflow step (admin-API variant).

Covers `DeleteBlobStep` validation + execute: success (deleted=true), the
`missing_ok` not-found path (success without deleting), not-found WITHOUT
missing_ok (fail), generic client error, expected_failure, JSON-RPC error body,
and outputs export. The client is a MagicMock patched in via
`get_client_for_rpc_url`.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.delete_blob import DeleteBlobStep

_MODULE = "merobox.commands.bootstrap.steps.delete_blob"

_BLOB = "Bk8aZ2x9Qm"
# client-py `delete_blob` returns the flat snake_case BlobDeleteResponse.
_RESPONSE = {"blob_id": _BLOB, "deleted": True}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestDeleteBlobValidation:
    def setup_method(self):
        self.base = {"type": "delete_blob", "node": "node-1", "blob_id": _BLOB}

    def test_valid_config_passes(self):
        DeleteBlobStep(self.base)

    def test_missing_blob_id_raises(self):
        with pytest.raises(ValueError, match="blob_id"):
            DeleteBlobStep({"type": "delete_blob", "node": "node-1"})

    def test_blob_id_not_string_raises(self):
        with pytest.raises(ValueError, match="'blob_id' must be a string"):
            DeleteBlobStep({**self.base, "blob_id": 5})

    def test_missing_ok_not_bool_raises(self):
        with pytest.raises(ValueError, match="'missing_ok' must be a boolean"):
            DeleteBlobStep({**self.base, "missing_ok": "yes"})


class TestDeleteBlobExecute:
    def setup_method(self):
        self.config = {"type": "delete_blob", "node": "node-1", "blob_id": _BLOB}

    def _patched(self, step, client):
        return (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "node-1"),
            ),
            patch(f"{_MODULE}.get_client_for_rpc_url", return_value=client),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
        )

    def test_success_stores_response(self):
        step = DeleteBlobStep(self.config)
        client = MagicMock()
        client.delete_blob.return_value = _RESPONSE
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        client.delete_blob.assert_called_once_with(_BLOB)
        assert workflow_results["delete_blob_node-1"] == _RESPONSE

    def test_outputs_export_deleted(self):
        cfg = {**self.config, "outputs": {"gone": "deleted"}}
        step = DeleteBlobStep(cfg)
        client = MagicMock()
        client.delete_blob.return_value = _RESPONSE
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("gone") is True

    def test_missing_ok_treats_not_found_as_success(self):
        # Default missing_ok=True: the admin API erroring "Blob not found" is the
        # desired end state (absent here), so the step succeeds without deleting.
        step = DeleteBlobStep(self.config)
        client = MagicMock()
        client.delete_blob.side_effect = RuntimeError("Client error: Blob not found")
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        assert workflow_results["delete_blob_node-1"] == {
            "blob_id": _BLOB,
            "deleted": False,
        }

    def test_not_found_without_missing_ok_fails(self):
        step = DeleteBlobStep({**self.config, "missing_ok": False})
        client = MagicMock()
        client.delete_blob.side_effect = RuntimeError("Client error: Blob not found")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_generic_client_error_fails(self):
        # A non-not-found error fails even under missing_ok.
        step = DeleteBlobStep(self.config)
        client = MagicMock()
        client.delete_blob.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_unrelated_not_found_error_not_swallowed(self):
        # The match is anchored to "blob not found": a network error whose text
        # merely contains "not found" must still fail under missing_ok.
        step = DeleteBlobStep(self.config)
        client = MagicMock()
        client.delete_blob.side_effect = RuntimeError("host not found")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_non_dict_response_fails(self):
        # A non-dict API response is rejected before storing, not silently kept.
        step = DeleteBlobStep(self.config)
        client = MagicMock()
        client.delete_blob.return_value = "not-a-dict"
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is False
        assert "delete_blob_node-1" not in workflow_results

    def test_client_error_with_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = DeleteBlobStep(cfg)
        client = MagicMock()
        client.delete_blob.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is True

    def test_jsonrpc_error_body_fails_step(self):
        step = DeleteBlobStep(self.config)
        client = MagicMock()
        client.delete_blob.return_value = {"error": {"code": -1}}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3, patch.object(step, "_check_jsonrpc_error", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False
