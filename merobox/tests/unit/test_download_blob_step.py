"""
Unit tests for the `download_blob` workflow step.

Covers `DownloadBlobStep` validation + execute: success, the `context_id`
network-discovery pass-through, size / sha256 assertions (match and mismatch),
saving to `output_path`, a non-bytes response, client error, and outputs export.
The client is a MagicMock patched in via `get_client_for_rpc_url`, mirroring
`test_delete_blob_api_step.py`.
"""

import asyncio
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.download_blob import DownloadBlobStep

_MODULE = "merobox.commands.bootstrap.steps.download_blob"

_BLOB = "Bk8aZ2x9Qm"
_CTX = "CtX1234567"
_PAYLOAD = b"calimero blob payload"
_SHA = hashlib.sha256(_PAYLOAD).hexdigest()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestDownloadBlobValidation:
    def setup_method(self):
        self.base = {"type": "download_blob", "node": "node-1", "blob_id": _BLOB}

    def test_valid_config_passes(self):
        DownloadBlobStep(self.base)

    def test_missing_blob_id_raises(self):
        with pytest.raises(ValueError, match="blob_id"):
            DownloadBlobStep({"type": "download_blob", "node": "node-1"})

    def test_blob_id_not_string_raises(self):
        with pytest.raises(ValueError, match="'blob_id' must be a string"):
            DownloadBlobStep({**self.base, "blob_id": 5})

    def test_context_id_not_string_raises(self):
        with pytest.raises(ValueError, match="'context_id' must be a string"):
            DownloadBlobStep({**self.base, "context_id": 7})

    def test_expected_size_not_int_raises(self):
        with pytest.raises(ValueError, match="'expected_size' must be an integer"):
            DownloadBlobStep({**self.base, "expected_size": 1.5})

    def test_expected_size_accepts_placeholder_string(self):
        # Workflows pass `{{blob_size_node-1}}`, resolved at execute time.
        DownloadBlobStep({**self.base, "expected_size": "{{blob_size_node-1}}"})


class TestDownloadBlobExecute:
    def setup_method(self):
        self.config = {"type": "download_blob", "node": "node-1", "blob_id": _BLOB}

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

    def _client(self, payload=_PAYLOAD):
        client = MagicMock()
        client.download_blob.return_value = payload
        return client

    def test_success_stores_size_and_hash(self):
        step = DownloadBlobStep(self.config)
        client = self._client()
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        # No context_id configured -> local-only read.
        client.download_blob.assert_called_once_with(_BLOB, None)
        assert workflow_results["downloaded_blob_node-1"] == {
            "blob_id": _BLOB,
            "size": len(_PAYLOAD),
            "sha256": _SHA,
        }

    def test_context_id_is_passed_through_for_network_discovery(self):
        # This is the whole point of the step: with a context the node may fetch
        # from a peer, exercising announce + provider lookup + signed request.
        step = DownloadBlobStep({**self.config, "context_id": _CTX})
        client = self._client()
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is True
        client.download_blob.assert_called_once_with(_BLOB, _CTX)

    def test_expected_size_match_passes(self):
        step = DownloadBlobStep({**self.config, "expected_size": len(_PAYLOAD)})
        p1, p2, p3 = self._patched(step, self._client())
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is True

    def test_expected_size_mismatch_fails(self):
        # A truncated transfer must turn the step red rather than pass silently.
        step = DownloadBlobStep({**self.config, "expected_size": len(_PAYLOAD) + 1})
        p1, p2, p3 = self._patched(step, self._client())
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is False

    def test_expected_size_as_resolved_string_passes(self):
        step = DownloadBlobStep({**self.config, "expected_size": str(len(_PAYLOAD))})
        p1, p2, p3 = self._patched(step, self._client())
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is True

    def test_expected_size_unresolvable_string_fails(self):
        step = DownloadBlobStep({**self.config, "expected_size": "not-a-number"})
        p1, p2, p3 = self._patched(step, self._client())
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is False

    def test_expected_sha256_match_passes(self):
        step = DownloadBlobStep({**self.config, "expected_sha256": _SHA.upper()})
        p1, p2, p3 = self._patched(step, self._client())
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is True

    def test_expected_sha256_mismatch_fails(self):
        step = DownloadBlobStep({**self.config, "expected_sha256": "ab" * 32})
        p1, p2, p3 = self._patched(step, self._client())
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is False

    def test_output_path_writes_the_bytes(self, tmp_path):
        target = tmp_path / "nested" / "blob.bin"
        step = DownloadBlobStep({**self.config, "output_path": str(target)})
        p1, p2, p3 = self._patched(step, self._client())
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is True
        assert target.read_bytes() == _PAYLOAD

    def test_non_bytes_response_fails(self):
        step = DownloadBlobStep(self.config)
        p1, p2, p3 = self._patched(step, self._client(payload={"not": "bytes"}))
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is False

    def test_bytearray_response_is_accepted(self):
        step = DownloadBlobStep(self.config)
        p1, p2, p3 = self._patched(step, self._client(payload=bytearray(_PAYLOAD)))
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is True

    def test_client_error_fails(self):
        step = DownloadBlobStep(self.config)
        client = MagicMock()
        client.download_blob.side_effect = RuntimeError("Blob not found")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            assert _run(step.execute({}, {})) is False

    def test_outputs_export_size_and_sha256(self):
        cfg = {**self.config, "outputs": {"got_size": "size", "got_hash": "sha256"}}
        step = DownloadBlobStep(cfg)
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, self._client())
        with p1, p2, p3:
            assert _run(step.execute({}, dynamic_values)) is True
        assert dynamic_values.get("got_size") == len(_PAYLOAD)
        assert dynamic_values.get("got_hash") == _SHA
