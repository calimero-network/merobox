"""
Unit tests for the `get_application` workflow step.

Covers `GetApplicationStep` validation + execute (success / outputs path to the
nested blob bytecode / client error / expected_failure / JSON-RPC error). The
client is a MagicMock patched in via `get_client_for_rpc_url`.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.get_application import GetApplicationStep

_MODULE = "merobox.commands.bootstrap.steps.get_application"

# get_application returns the {data: {application: {...}}} envelope.
_RESPONSE = {
    "data": {
        "application": {
            "id": "app123",
            "version": "2.0.0",
            "blob": {"bytecode": "Bk8aZ2x9Qm", "compiled": "Cp9bY3w0Rn"},
        }
    }
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestGetApplicationValidation:
    def setup_method(self):
        self.base = {
            "type": "get_application",
            "node": "node-1",
            "application_id": "app123",
        }

    def test_valid_config_passes(self):
        GetApplicationStep(self.base)

    def test_missing_application_id_raises(self):
        cfg = {**self.base}
        del cfg["application_id"]
        with pytest.raises(ValueError, match="application_id"):
            GetApplicationStep(cfg)

    def test_application_id_not_string_raises(self):
        with pytest.raises(ValueError, match="'application_id' must be a string"):
            GetApplicationStep({**self.base, "application_id": 5})


class TestGetApplicationExecute:
    def setup_method(self):
        self.config = {
            "type": "get_application",
            "node": "node-1",
            "application_id": "app123",
        }

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
        step = GetApplicationStep(self.config)
        client = MagicMock()
        client.get_application.return_value = _RESPONSE
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        client.get_application.assert_called_once_with("app123")
        # The `{data: ...}` envelope is unwrapped before storing, so a direct
        # read and an `outputs:` path both root at `application`.
        assert workflow_results["get_application_node-1"] == _RESPONSE["data"]
        assert (
            workflow_results["get_application_node-1"]["application"]["blob"][
                "bytecode"
            ]
            == "Bk8aZ2x9Qm"
        )

    def test_outputs_export_nested_bytecode(self):
        # The export unwrap strips the top-level `data`, so the blob id is
        # reachable at `application.blob.bytecode` — this is how the strand
        # workflow captures a version's blob id while it is the row's latest.
        cfg = {**self.config, "outputs": {"v2_blob": "application.blob.bytecode"}}
        step = GetApplicationStep(cfg)
        client = MagicMock()
        client.get_application.return_value = _RESPONSE
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("v2_blob") == "Bk8aZ2x9Qm"

    def test_outputs_export_version(self):
        cfg = {**self.config, "outputs": {"ver": "application.version"}}
        step = GetApplicationStep(cfg)
        client = MagicMock()
        client.get_application.return_value = _RESPONSE
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("ver") == "2.0.0"

    def test_client_error_fails_step(self):
        step = GetApplicationStep(self.config)
        client = MagicMock()
        client.get_application.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_client_error_with_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = GetApplicationStep(cfg)
        client = MagicMock()
        client.get_application.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is True

    def test_jsonrpc_error_body_fails_step(self):
        step = GetApplicationStep(self.config)
        client = MagicMock()
        client.get_application.return_value = {"error": {"code": -1}}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3, patch.object(step, "_check_jsonrpc_error", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False
