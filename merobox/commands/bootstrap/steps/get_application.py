"""
get_application workflow step — read an application's current row (id, version,
and blob ids) over the admin API.

Wraps `GET admin-api/applications/{application_id}` (calimero-client-py
`get_application`). The app row points at the LATEST installed bytecode, so
calling this right after an install captures that version's `blob.bytecode`
(base58) — the same id the on-disk blob store and `delete_blob_on_disk` use.
Same-package bundles share one `applicationId`, so this is how a workflow
distinguishes versions: read the row's `version` + `blob.bytecode` while a
given version is current.

Response shape `{data: {application: {id, version, blob: {bytecode, compiled},
...}}}`; the export machinery unwraps the top-level `data`, so `outputs:` paths
start at `application` (e.g. `application.blob.bytecode`, `application.version`).
"""

from __future__ import annotations

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class GetApplicationStep(BaseStep):
    """Read an application's current row (id / version / blob ids).

    Fields:
      node:           target node.
      application_id: the application id to read.

    Stores the response under `get_application_{node}`; `outputs:` paths start
    at `application` (e.g. `application.blob.bytecode`).
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "application_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "application_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolve_dynamic_value(
            self.config["node"], workflow_results, dynamic_values
        )
        application_id = self._resolve_dynamic_value(
            self.config["application_id"], workflow_results, dynamic_values
        )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            api_result = client.get_application(application_id)
            result = ok(api_result)
        except Exception as e:
            result = fail("get_application failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]get_application failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        data = result["data"]
        # Unwrap the `{data: {application: {...}}}` envelope explicitly so the
        # `outputs:` contract (paths rooted at `application`) holds regardless of
        # whether the base-class export machinery also unwraps a top-level
        # `data` key. The unwrapped object is what we store AND export, so a
        # direct workflow_results read and an `outputs:` path agree.
        inner = data.get("data") if isinstance(data, dict) else None
        inner = inner if isinstance(inner, dict) else data
        workflow_results[f"get_application_{node_name}"] = inner
        if "outputs" in self.config:
            self._export_variables(inner, node_name, dynamic_values)

        # Pull a compact summary for the run log (best-effort).
        app = inner.get("application") or {} if isinstance(inner, dict) else {}
        version = app.get("version") if isinstance(app, dict) else None
        blob = app.get("blob") if isinstance(app, dict) else None
        bytecode = blob.get("bytecode") if isinstance(blob, dict) else None
        console.print(
            f"[green]✓ get_application {application_id} on {node_name}: "
            f"version={version} bytecode={bytecode}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True
