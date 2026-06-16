"""
Unit tests for the `delete_blob_on_disk` workflow step.

Covers `DeleteBlobOnDiskStep` validation + execute (present/absent/missing_ok,
rm failure, survive-after-rm guard, container-not-found, binary-mode rejection,
unsafe-name / non-base58 guards, expected_failure, outputs export). The Docker
container is a MagicMock whose `exec_run` is scripted per call; the module's
`is_binary_mode` / `resolve_container` helpers are patched.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.delete_blob import DeleteBlobOnDiskStep

_MODULE = "merobox.commands.bootstrap.steps.delete_blob"

# Valid base58 (no 0 O I l) and a deterministic expected on-disk path.
_BLOB = "Bk8aZ2x9Qm"
_PATH = f"/app/data/node-1/blobs/{_BLOB}"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Exec:
    """Stand-in for docker's ExecResult (exit_code, output)."""

    def __init__(self, exit_code: int, output: bytes = b""):
        self.exit_code = exit_code
        self.output = output


def _container(*exec_results):
    c = MagicMock()
    c.exec_run.side_effect = list(exec_results)
    return c


def _step(**overrides):
    cfg = {"type": "delete_blob_on_disk", "node": "node-1", "blob_id": _BLOB}
    cfg.update(overrides)
    step = DeleteBlobOnDiskStep(cfg, manager=MagicMock())
    # Identity dynamic-value resolution, like the other step tests.
    step._resolve_dynamic_value = lambda v, *_a, **_k: v
    return step


# =============================================================================
# Validation (construction time)
# =============================================================================


class TestDeleteBlobValidation:
    def test_valid_config_passes(self):
        DeleteBlobOnDiskStep(
            {"type": "delete_blob_on_disk", "node": "node-1", "blob_id": _BLOB},
            manager=MagicMock(),
        )

    def test_missing_node_raises(self):
        with pytest.raises(ValueError, match="node"):
            DeleteBlobOnDiskStep(
                {"type": "delete_blob_on_disk", "blob_id": _BLOB}, manager=MagicMock()
            )

    def test_missing_blob_id_raises(self):
        with pytest.raises(ValueError, match="blob_id"):
            DeleteBlobOnDiskStep(
                {"type": "delete_blob_on_disk", "node": "node-1"}, manager=MagicMock()
            )

    def test_blob_id_not_string_raises(self):
        with pytest.raises(ValueError, match="'blob_id' must be a string"):
            DeleteBlobOnDiskStep(
                {"type": "delete_blob_on_disk", "node": "node-1", "blob_id": 5},
                manager=MagicMock(),
            )

    def test_missing_ok_not_bool_raises(self):
        with pytest.raises(ValueError, match="missing_ok"):
            DeleteBlobOnDiskStep(
                {
                    "type": "delete_blob_on_disk",
                    "node": "node-1",
                    "blob_id": _BLOB,
                    "missing_ok": "yes",
                },
                manager=MagicMock(),
            )


# =============================================================================
# Execute
# =============================================================================


class TestDeleteBlobExecute:
    def _patched(self, container):
        return (
            patch(f"{_MODULE}.is_binary_mode", return_value=False),
            patch(f"{_MODULE}.resolve_container", return_value=container),
        )

    def test_present_blob_removed(self):
        step = _step()
        # exists(0) -> rm(0) -> gone(1)
        container = _container(_Exec(0), _Exec(0), _Exec(1))
        workflow_results = {}
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        # rm targeted the exact base58 path
        container.exec_run.assert_any_call(["rm", "-f", _PATH])
        stored = workflow_results["delete_blob_on_disk_node-1"]
        assert stored["removed"] is True
        assert stored["existed"] is True
        assert stored["path"] == _PATH

    def test_absent_blob_missing_ok_default_passes(self):
        step = _step()
        # exists(1 -> absent) -> rm(0) -> gone(1)
        container = _container(_Exec(1), _Exec(0), _Exec(1))
        workflow_results = {}
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(step.execute(workflow_results, {}))
        assert result is True
        assert workflow_results["delete_blob_on_disk_node-1"]["removed"] is False

    def test_absent_blob_missing_ok_false_fails(self):
        step = _step(missing_ok=False)
        container = _container(_Exec(1), _Exec(0), _Exec(1))
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_rm_nonzero_fails(self):
        step = _step()
        container = _container(_Exec(0), _Exec(1, b"permission denied"))
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_survives_after_rm_fails(self):
        # rm reports success but the file is still there → must fail, else the
        # strand would be a silent no-op.
        step = _step()
        container = _container(_Exec(0), _Exec(0), _Exec(0))
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_container_not_found_fails(self):
        step = _step()
        with (
            patch(f"{_MODULE}.is_binary_mode", return_value=False),
            patch(f"{_MODULE}.resolve_container", return_value=None),
        ):
            result = _run(step.execute({}, {}))
        assert result is False

    def test_binary_mode_fails(self):
        step = _step()
        with patch(f"{_MODULE}.is_binary_mode", return_value=True):
            result = _run(step.execute({}, {}))
        assert result is False

    def test_unsafe_node_name_fails(self):
        step = _step(node="../etc")
        # Guard runs before any container work, so no patches needed.
        result = _run(step.execute({}, {}))
        assert result is False

    def test_non_base58_blob_id_fails(self):
        step = _step(blob_id="blob_0/x")  # '_', '0', '/' are not base58
        result = _run(step.execute({}, {}))
        assert result is False

    def test_rm_failure_with_expected_failure_passes(self):
        step = _step(expected_failure=True)
        container = _container(_Exec(0), _Exec(1, b"boom"))
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(step.execute({}, {}))
        assert result is True

    def test_exec_exception_fails(self):
        step = _step()
        container = MagicMock()
        container.exec_run.side_effect = RuntimeError("docker down")
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(step.execute({}, {}))
        assert result is False

    def test_outputs_export_removed_flag(self):
        cfg_step = _step(outputs={"was_removed": "removed"})
        container = _container(_Exec(0), _Exec(0), _Exec(1))
        dynamic_values = {}
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(cfg_step.execute({}, dynamic_values))
        assert result is True
        assert dynamic_values.get("was_removed") is True

    def test_custom_data_dir_and_subdir(self):
        step = _step(data_dir="/data", blobs_subdir="store")
        container = _container(_Exec(0), _Exec(0), _Exec(1))
        p1, p2 = self._patched(container)
        with p1, p2:
            result = _run(step.execute({}, {}))
        assert result is True
        container.exec_run.assert_any_call(["rm", "-f", f"/data/node-1/store/{_BLOB}"])
