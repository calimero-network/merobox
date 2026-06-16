"""
delete_blob_on_disk workflow step — remove a content-addressed bytecode blob
from a node's on-disk blob store via `docker exec`.

This is a fault-injection primitive for the migrations-v2 stranded-context e2e:
`uninstall_application` removes the extracted dir + app row but NOT the
content-addressed blob, and a behind node pre-stages an upgrade-ladder rung's
blob over BlobShare during the sync gate — so `stop_node` alone can't make a
rung's bytecode unobtainable. Deleting the intermediate blob file on every node
that holds it strands a behind context: its lazy ladder replay can no longer
fetch that rung and hits `NoMigrationPath`. `resync_context` then recovers it.

The merod blob store is a flat directory of one file per blob, named by the
blob id's base58 string (calimero core `crates/store/blobs`,
`FileSystem::path` = `<root>/<blob_id>`). That base58 id is exactly what the
`list_application_versions` step surfaces as `blob_id`, so it can be threaded
straight in. In a merobox container `CALIMERO_HOME` is `/app/data` and the
per-node store is `/app/data/<node>/blobs/`.
"""

from __future__ import annotations

import re
from typing import Any

from merobox.commands.bootstrap.steps._docker_utils import (
    is_binary_mode,
    resolve_container,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console

# merod's per-node data root inside a merobox container (CALIMERO_HOME), and the
# blob-store subdir under it (core's BlobStoreConfig default is `blobs`).
_DEFAULT_DATA_DIR = "/app/data"
_DEFAULT_BLOBS_SUBDIR = "blobs"

# Node/container names: same restriction merobox's manager applies, so an
# interpolated value can't path-traverse out of the data dir in the exec path.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# A blob id is a base58-encoded 32-byte hash (bitcoin alphabet — no 0 O I l).
# Validated so the id can't smuggle a path separator / shell metachar into the
# exec target even though we already pass argv as a list (defence in depth).
_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")


class DeleteBlobOnDiskStep(BaseStep):
    """Delete a single blob file from a node's on-disk blob store (docker exec).

    Fields:
      node:       target node (== container name).
      blob_id:    base58 blob id to delete (e.g. from list_application_versions).
      data_dir:   optional CALIMERO_HOME inside the container (default /app/data).
      blobs_subdir: optional blob-store subdir under <data_dir>/<node>
                  (default `blobs`).
      missing_ok: optional (default true) — a node that never held the blob is
                  still a success (the goal is "absent here", reached either way).

    Stores `{blob_id, path, existed, removed}` under
    `delete_blob_on_disk_{node}` and exposes it to `outputs:`.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "blob_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "blob_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        for field in ("data_dir", "blobs_subdir"):
            value = self.config.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"Step '{step_name}': '{field}' must be a string if provided"
                )
        missing_ok = self.config.get("missing_ok")
        if missing_ok is not None and not isinstance(missing_ok, bool):
            raise ValueError(
                f"Step '{step_name}': 'missing_ok' must be a boolean if provided"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolve_dynamic_value(
            self.config["node"], workflow_results, dynamic_values
        )
        blob_id = self._resolve_dynamic_value(
            self.config["blob_id"], workflow_results, dynamic_values
        )
        data_dir = self.config.get("data_dir") or _DEFAULT_DATA_DIR
        blobs_subdir = self.config.get("blobs_subdir") or _DEFAULT_BLOBS_SUBDIR
        missing_ok = bool(self.config.get("missing_ok", True))
        expected_failure = self._is_expected_failure()

        # Defence in depth: every component lands in an exec path, so reject a
        # crafted node name / blob id before building it (argv is a list, so
        # this is belt-and-suspenders against traversal, not shell injection).
        if not _SAFE_NAME_RE.match(str(node_name)):
            return self._fail(f"unsafe node name {node_name!r}", expected_failure)
        if not _BASE58_RE.match(str(blob_id)):
            return self._fail(
                f"blob_id {blob_id!r} is not a base58 blob id", expected_failure
            )

        # docker-exec only: in binary mode the blob is on the host filesystem and
        # this primitive does not apply (the stranded-resync workflow is
        # docker-only, like the partition_peers fault steps it pairs with).
        if is_binary_mode(self.manager):
            return self._fail(
                "delete_blob_on_disk is only supported in Docker mode "
                "(no container to exec into in binary mode)",
                expected_failure,
            )
        if not self.manager:
            return self._fail(
                "no manager available (remote-only mode)", expected_failure
            )

        container = resolve_container(self.manager, node_name)
        if container is None:
            return self._fail(f"container '{node_name}' not found", expected_failure)

        path = f"{data_dir}/{node_name}/{blobs_subdir}/{blob_id}"

        try:
            existed = container.exec_run(["test", "-e", path]).exit_code == 0
            rm = container.exec_run(["rm", "-f", path])
            if rm.exit_code != 0:
                detail = _decode(rm.output)
                return self._fail(
                    f"rm -f {path} exited {rm.exit_code}: {detail}", expected_failure
                )
            # Confirm the file is actually gone — a silently-surviving blob would
            # make the strand a no-op and the workflow false-pass.
            still_present = container.exec_run(["test", "-e", path]).exit_code == 0
        except Exception as e:
            return self._fail(f"exec failed on {node_name}: {e}", expected_failure)

        if still_present:
            return self._fail(
                f"blob {blob_id} still present at {path} after rm", expected_failure
            )

        if not existed and not missing_ok:
            return self._fail(
                f"blob {blob_id} was not present at {path} (missing_ok=false)",
                expected_failure,
            )

        result = {
            "blob_id": blob_id,
            "path": path,
            "existed": existed,
            "removed": existed,
        }
        workflow_results[f"delete_blob_on_disk_{node_name}"] = result
        if "outputs" in self.config:
            self._export_variables(result, node_name, dynamic_values)

        verb = "removed" if existed else "already absent"
        console.print(
            f"[green]✓ delete_blob_on_disk on {node_name}: blob {blob_id} "
            f"{verb} ({path})[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True

    def _fail(self, message: str, expected_failure: bool) -> bool:
        if expected_failure:
            self._report_expected_failure(message)
            return True
        console.print(f"[red]delete_blob_on_disk failed: {message}[/red]")
        return False


def _decode(output: Any) -> str:
    """Best-effort decode of exec_run output bytes for an error message."""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace").strip()
    return str(output).strip() if output is not None else ""
