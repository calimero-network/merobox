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
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
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
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(
                    f"Step '{step_name}': '{field}' must be a string if provided"
                )
            # These land in the exec path verbatim, so reject `..` traversal
            # components outright (node_name/blob_id are validated in execute).
            if ".." in value.split("/"):
                raise ValueError(
                    f"Step '{step_name}': '{field}' must not contain '..' path components"
                )
        # `data_dir` is an absolute CALIMERO_HOME inside the container; a
        # relative value would resolve against the exec cwd, not the data root.
        data_dir = self.config.get("data_dir")
        if isinstance(data_dir, str) and not data_dir.startswith("/"):
            raise ValueError(f"Step '{step_name}': 'data_dir' must be an absolute path")
        # `blobs_subdir` is a subdir under <data_dir>/<node>; an absolute value
        # would escape that prefix entirely.
        blobs_subdir = self.config.get("blobs_subdir")
        if isinstance(blobs_subdir, str) and blobs_subdir.startswith("/"):
            raise ValueError(
                f"Step '{step_name}': 'blobs_subdir' must be a relative subdir"
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
        # `.get(..., True)` returns None (not the default) for an explicit
        # `missing_ok: null`, and `bool(None)` is False — so collapse None to
        # the documented default here.
        missing_ok_raw = self.config.get("missing_ok")
        missing_ok = True if missing_ok_raw is None else bool(missing_ok_raw)
        expected_failure = self._is_expected_failure()

        # Defence in depth: node_name + blob_id land in the exec path, so reject
        # a crafted value before building it. With data_dir/blobs_subdir also
        # `..`-validated (see _validate_field_types) the whole path is traversal-
        # free; argv is a list so there's no shell-injection surface either.
        if not _SAFE_NAME_RE.match(str(node_name)):
            return self._fail(f"unsafe node name {node_name!r}", expected_failure)
        if not _BASE58_RE.match(str(blob_id)):
            return self._fail(
                f"blob_id {blob_id!r} is not a base58 blob id", expected_failure
            )

        if not self.manager:
            return self._fail(
                "no manager available (remote-only mode)", expected_failure
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

        container = resolve_container(self.manager, node_name)
        if container is None:
            return self._fail(f"container '{node_name}' not found", expected_failure)

        # exec_run blocks indefinitely on a paused container (the process is
        # SIGSTOP'd and never drains the exec stream — same hazard documented
        # for the fault steps). Require a confirmed "running" state first: an
        # unknown state (reload raised) is treated as unsafe rather than risking
        # the hang the whole check exists to prevent.
        try:
            container.reload()
            state = container.attrs.get("State", {}).get("Status")
        except Exception:
            state = None
        if state != "running":
            return self._fail(
                f"container '{node_name}' is '{state}', not confirmed running "
                f"(exec would risk hanging)",
                expected_failure,
            )

        path = f"{data_dir}/{node_name}/{blobs_subdir}/{blob_id}"

        # `op` labels the in-flight exec so a daemon error names which call failed.
        op = "test -e (pre)"
        try:
            existed = container.exec_run(["test", "-e", path]).exit_code == 0
            # Fail before issuing rm when a required blob is absent — don't run a
            # no-op rm just to reject it afterward (clearer + avoids touching a
            # never-present path).
            if not existed and not missing_ok:
                return self._fail(
                    f"blob {blob_id} was not present at {path} (missing_ok=false)",
                    expected_failure,
                )
            op = "rm -f"
            rm = container.exec_run(["rm", "-f", path])
            if rm.exit_code != 0:
                detail = _decode(rm.output)
                return self._fail(
                    f"rm -f {path} exited {rm.exit_code}: {detail}", expected_failure
                )
            # Confirm the file is actually gone — a silently-surviving blob would
            # make the strand a no-op and the workflow false-pass.
            op = "test -e (post)"
            still_present = container.exec_run(["test", "-e", path]).exit_code == 0
        except Exception as e:
            return self._fail(
                f"exec ({op}) failed on {node_name}: {e}", expected_failure
            )

        if still_present:
            return self._fail(
                f"blob {blob_id} still present at {path} after rm", expected_failure
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


class DeleteBlobStep(BaseStep):
    """Delete a blob via the admin API (`DELETE admin-api/blobs/{id}`).

    Unlike `delete_blob_on_disk` — which `rm`s the base58 PARENT id off disk and
    is a NO-OP for any real (chunked) blob, because the parent id is RocksDB-only
    metadata and the bytes live in chunk files named by an unexposed chunk hash —
    this routes through the node's blob store (calimero-client-py `delete_blob`),
    which cascades the parent metadata + every chunk file + chunk metadata. That
    actually makes a rung's bytecode unobtainable, so it is the primitive the
    stranded-context resync e2e needs. Works in both docker and binary mode.

    Fields:
      node:       target node.
      blob_id:    base58 (parent) blob id to delete (e.g. from
                  `list_application_versions` `blobId` or `get_application`
                  `application.blob.bytecode`).
      missing_ok: optional (default true) — a blob already absent on this node is
                  still success (the goal is "absent here", reached either way).

    Stores `{blob_id, deleted}` under `delete_blob_{node}` and exposes it to
    `outputs:`.
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
        # `.get(..., True)` returns None (not the default) for an explicit
        # `missing_ok: null`, and `bool(None)` is False — so collapse None here.
        missing_ok_raw = self.config.get("missing_ok")
        missing_ok = True if missing_ok_raw is None else bool(missing_ok_raw)
        expected_failure = self._is_expected_failure()

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.delete_blob(blob_id)
            result = ok(api_result)
        except Exception as e:
            # The admin API errors ("Blob not found") when the blob's metadata is
            # already absent on this node; under missing_ok that IS the desired
            # end state, reached either way — report success without deleting.
            if missing_ok and "not found" in str(e).lower():
                stored = {"blob_id": blob_id, "deleted": False}
                workflow_results[f"delete_blob_{node_name}"] = stored
                if "outputs" in self.config:
                    self._export_variables(stored, node_name, dynamic_values)
                console.print(
                    f"[green]✓ delete_blob on {node_name}: blob {blob_id} "
                    f"already absent[/green]"
                )
                if expected_failure:
                    self._report_unexpected_success()
                return True
            result = fail("delete_blob failed", error=e)

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]delete_blob failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        # client-py returns the flat `BlobDeleteResponse` (`{blob_id, deleted}`).
        data = result["data"]
        workflow_results[f"delete_blob_{node_name}"] = data
        if "outputs" in self.config:
            self._export_variables(data, node_name, dynamic_values)

        deleted = data.get("deleted") if isinstance(data, dict) else None
        console.print(
            f"[green]✓ delete_blob {blob_id} on {node_name}: deleted={deleted}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True
