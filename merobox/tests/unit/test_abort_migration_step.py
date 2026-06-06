"""
Unit tests for the `abort_migration` workflow step.

Covers `AbortMigrationStep` validation + execute (success / export / failure /
expected_failure). Mirrors `test_cascade_status_steps.py`: conftest stubs the
`calimero_client_py` import, and each execute test patches
`get_client_for_rpc_url` to return a MagicMock. On a Python without
calimero-client-py installed, `_CLIENT_PY_VERSION` resolves to None so the
version pre-flight is skipped and never interferes here.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.group_upgrade import AbortMigrationStep

_MODULE = "merobox.commands.bootstrap.steps.group_upgrade"


def _run(coro):
    # Isolated loop so this file can't pollute sibling test modules' global
    # loop state (see test_cascade_status_steps.py for the rationale).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# AbortMigrationStep — validation
# =============================================================================


class TestAbortMigrationValidation:
    def setup_method(self):
        self.base = {
            "type": "abort_migration",
            "name": "Abort",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
        }

    def test_valid_config_passes(self):
        AbortMigrationStep(self.base)

    def test_missing_node_raises(self):
        cfg = {**self.base}
        del cfg["node"]
        with pytest.raises(ValueError, match="node"):
            AbortMigrationStep(cfg)

    def test_missing_namespace_id_raises(self):
        cfg = {**self.base}
        del cfg["namespace_id"]
        with pytest.raises(ValueError, match="namespace_id"):
            AbortMigrationStep(cfg)

    def test_namespace_id_not_string_raises(self):
        cfg = {**self.base, "namespace_id": 5}
        with pytest.raises(ValueError, match="'namespace_id' must be a string"):
            AbortMigrationStep(cfg)


# =============================================================================
# AbortMigrationStep — execute
# =============================================================================


class TestAbortMigrationExecute:
    def setup_method(self):
        self.config = {
            "type": "abort_migration",
            "name": "Abort",
            "node": "calimero-node-1",
            "namespace_id": "ns123",
        }

    def _patched(self, step, client):
        return (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "calimero-node-1"),
            ),
            patch(f"{_MODULE}.get_client_for_rpc_url", return_value=client),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
        )

    def test_success_stores_response(self):
        step = AbortMigrationStep(self.config)
        client = MagicMock()
        client.abort_migration.return_value = {"namespace_id": "ns123", "aborted": True}
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        client.abort_migration.assert_called_once_with(namespace_id="ns123")
        stored = workflow_results["abort_migration_calimero-node-1"]
        assert stored["aborted"] is True
        assert stored["namespace_id"] == "ns123"

    def test_idempotent_nothing_pending(self):
        # Aborting with nothing pending is a no-op success (aborted=False).
        step = AbortMigrationStep(self.config)
        client = MagicMock()
        client.abort_migration.return_value = {"namespace_id": "ns123", "aborted": False}
        workflow_results = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        assert workflow_results["abort_migration_calimero-node-1"]["aborted"] is False

    def test_outputs_export_fields(self):
        cfg = {**self.config, "outputs": {"did_abort": "aborted"}}
        step = AbortMigrationStep(cfg)
        client = MagicMock()
        client.abort_migration.return_value = {"namespace_id": "ns123", "aborted": True}
        dynamic_values = {}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("did_abort") is True

    def test_client_error_fails_step(self):
        step = AbortMigrationStep(self.config)
        client = MagicMock()
        client.abort_migration.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_client_error_with_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = AbortMigrationStep(cfg)
        client = MagicMock()
        client.abort_migration.side_effect = RuntimeError("boom")
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3:
            result = _run(step.execute({}, {}))
        assert result is True

    def test_version_guard_old_client_fails(self):
        # client-py known to predate the binding ⇒ pre-flight blocks (fail-closed).
        step = AbortMigrationStep(self.config)
        with patch(f"{_MODULE}._client_py_below", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False

    def test_version_guard_old_client_expected_failure_passes(self):
        cfg = {**self.config, "expected_failure": True}
        step = AbortMigrationStep(cfg)
        with patch(f"{_MODULE}._client_py_below", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is True

    def test_jsonrpc_error_body_fails_step(self):
        # A transport-level success can still carry a JSON-RPC error body.
        step = AbortMigrationStep(self.config)
        client = MagicMock()
        client.abort_migration.return_value = {"namespace_id": "ns123", "aborted": True}
        p1, p2, p3 = self._patched(step, client)
        with p1, p2, p3, patch.object(step, "_check_jsonrpc_error", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False
