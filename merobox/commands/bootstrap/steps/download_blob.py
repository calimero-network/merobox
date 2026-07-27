"""
Blob download step executor.

The counterpart to `upload_blob`. Without this, a workflow can put a blob on one
node and read its *metadata* elsewhere, but can never assert that the blob's
**bytes** reach another node — so the whole cross-node blob path (announce → DHT
provider lookup → signed member request → chunked transfer) goes unexercised.
That gap is how blob authorization broke for every namespace-backed context
without a single workflow turning red.

Pass `context_id` to exercise network discovery: the node fetches from a peer
that announced the blob instead of only reading local storage.

Pass `expected_failure: true` for the negative control that makes a cross-node
assertion meaningful: run it on the fetching node *without* `context_id` first,
so the workflow proves the bytes were not already in local storage. Blobs are
content-addressed — a node holding identical bytes serves them locally and the
network path is never exercised.
"""

import hashlib
import os
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console

# The only two shapes that legitimately mean "these bytes are not retrievable".
# calimero-client-py's `download_blob` (src/client.rs) raises PyRuntimeError
# ("Client error: ...") for anything the node refuses or cannot find, and
# PyValueError for a malformed blob id. Everything else — TypeError,
# AttributeError, a bad client signature — is a bug in the plumbing, and must
# NOT be catchable here: converting it to a `fail()` would report a real defect
# as an ordinary download failure and, worse, let `expected_failure: true`
# swallow it as a passing negative control.
_RETRIEVAL_ERRORS = (RuntimeError, ValueError)


class DownloadBlobStep(BaseStep):
    """Download a blob and optionally assert its size / sha256 / saved copy."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "blob_id"]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f"Unnamed {self.config.get('type', 'Unknown')} step"
        )

        for field in ("node", "blob_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

        for field in ("context_id", "output_path", "expected_sha256"):
            value = self.config.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

        expected_size = self.config.get("expected_size")
        if expected_size is not None and not isinstance(expected_size, (int, str)):
            raise ValueError(
                f"Step '{step_name}': 'expected_size' must be an integer "
                "(or a placeholder string resolving to one)"
            )

    def _get_exportable_variables(self):
        """
        Available variables after a successful download:
        - size: number of bytes actually received
        - sha256: hex sha256 of the received bytes
        """
        return [
            (
                "size",
                "downloaded_blob_size_{node_name}",
                "Downloaded blob size in bytes",
            ),
            (
                "sha256",
                "downloaded_blob_sha256_{node_name}",
                "Hex sha256 of the downloaded blob bytes",
            ),
        ]

    def _failed(self, reason: str) -> bool:
        """Report a retrieval failure, honouring `expected_failure`.

        A negative control — "these bytes must NOT be retrievable here" — is how
        a workflow proves the node did not already hold the blob before the
        cross-node fetch under test. Without it, a passing fetch says nothing:
        blobs are content-addressed, so a node that happens to hold identical
        bytes serves them from local storage and the network path is never
        touched.
        """
        if self._is_expected_failure():
            self._report_expected_failure(reason)
            return True
        console.print(f"[red]✗ {reason}[/red]")
        return False

    @with_retry(config=NETWORK_RETRY_CONFIG)
    async def _download_blob_from_node(
        self,
        rpc_url: str,
        blob_id: str,
        context_id: str | None = None,
        node_name: str | None = None,
    ) -> dict:
        console.print(f"[cyan]📥 Downloading blob {blob_id}[/cyan]")
        if context_id:
            console.print(
                f"[cyan]   Context ID: {context_id} (network discovery enabled)[/cyan]"
            )

        try:
            client = get_client_for_rpc_url(rpc_url, node_name=node_name)
            blob_data = client.download_blob(blob_id, context_id)
        except NETWORK_RETRY_CONFIG.exceptions:
            # Re-raise the transient faults so `with_retry` can actually see
            # them. Converting these to a `fail()` dict here would make the
            # decorator dead code: it only retries what escapes the call, so a
            # single connection blip on the very cross-node fetch this step
            # exists to exercise would fail the step outright. Matching on the
            # config's own tuple keeps the two from drifting apart. After the
            # last attempt `with_retry` re-raises, and `execute` converts it.
            raise
        except _RETRIEVAL_ERRORS as e:
            # A genuine "cannot retrieve" verdict. Anything outside this tuple is
            # deliberately left to propagate — see _RETRIEVAL_ERRORS.
            console.print(f"[red]✗ Blob download failed: {type(e).__name__}: {e}[/red]")
            return fail("download_blob failed", error=e)
        return ok(blob_data)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]

        blob_id = self._resolve_dynamic_value(
            self.config["blob_id"], workflow_results, dynamic_values
        )

        context_id = self.config.get("context_id")
        if context_id:
            context_id = self._resolve_dynamic_value(
                context_id, workflow_results, dynamic_values
            )

        output_path = self.config.get("output_path")
        if output_path:
            output_path = self._resolve_dynamic_value(
                output_path, workflow_results, dynamic_values
            )

        expected_size = self.config.get("expected_size")
        if isinstance(expected_size, str):
            expected_size = self._resolve_dynamic_value(
                expected_size, workflow_results, dynamic_values
            )

        expected_sha256 = self.config.get("expected_sha256")
        if expected_sha256:
            expected_sha256 = self._resolve_dynamic_value(
                expected_sha256, workflow_results, dynamic_values
            )

        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Download blob step export configuration validation failed[/yellow]"
            )

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        try:
            result = await self._download_blob_from_node(
                rpc_url, blob_id, context_id, node_name=client_node_name
            )
        except NETWORK_RETRY_CONFIG.exceptions as e:
            # `with_retry` re-raises the last transient fault once every attempt
            # is spent, and that is the ONLY thing it can re-raise. Land it as a
            # normal step failure instead of a traceback that aborts the whole
            # run. Deliberately not `except Exception`: a programming error has
            # to keep travelling so it surfaces as the bug it is.
            console.print(
                f"[red]✗ Blob download failed after retries: "
                f"{type(e).__name__}: {e}[/red]"
            )
            result = fail("download_blob failed", error=e)

        if not result["success"]:
            return self._failed(str(result.get("error", "Unknown error")))

        blob_data = result["data"]
        if not isinstance(blob_data, (bytes, bytearray)):
            return self._failed(f"Expected blob bytes, got {type(blob_data).__name__}")

        blob_data = bytes(blob_data)
        size = len(blob_data)
        digest = hashlib.sha256(blob_data).hexdigest()

        console.print(
            f"[green]✓ Blob downloaded: {size} bytes ({size / 1024:.2f} KB)[/green]"
        )
        console.print(f"[green]   sha256: {digest}[/green]")

        # A blob that arrives truncated or empty is a real failure, not a pass —
        # assert before exporting so a later `assert` step can't be fooled.
        if expected_size is not None:
            try:
                expected_size = int(expected_size)
            except (TypeError, ValueError):
                # A placeholder that never resolved is a workflow bug, not an
                # expected failure — fail it even under `expected_failure`, or a
                # typo'd `{{blob_size}}` would read as a passing negative test.
                console.print(
                    f"[red]✗ 'expected_size' did not resolve to an integer: "
                    f"{expected_size!r}[/red]"
                )
                return False
            if size != expected_size:
                return self._failed(
                    f"Size mismatch: expected {expected_size} bytes, got {size}"
                )
            console.print(f"[green]✓ Size matches expected {expected_size}[/green]")

        if expected_sha256 and digest.lower() != expected_sha256.lower():
            return self._failed(
                f"sha256 mismatch: expected {expected_sha256}, got {digest}"
            )
        if expected_sha256:
            console.print("[green]✓ sha256 matches expected[/green]")

        if output_path:
            try:
                parent = os.path.dirname(os.path.abspath(output_path))
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(blob_data)
                console.print(f"[blue]💾 Saved to {output_path}[/blue]")
            except Exception as e:
                console.print(f"[red]Failed to write {output_path}: {str(e)}[/red]")
                return False

        blob_info = {"blob_id": blob_id, "size": size, "sha256": digest}
        workflow_results[f"downloaded_blob_{node_name}"] = blob_info
        self._export_variables(blob_info, node_name, dynamic_values)

        if self._is_expected_failure():
            # Warn rather than hard-fail, matching every other step's contract.
            # Worth spelling out for this one: a negative control that succeeds
            # means the node already had the bytes, so any cross-node assertion
            # after it is proving nothing.
            console.print(
                f"[yellow]⚠️  {blob_id} WAS retrievable on {node_name} — a "
                f"following cross-node fetch cannot prove the network path[/yellow]"
            )
            self._report_unexpected_success()

        return True
