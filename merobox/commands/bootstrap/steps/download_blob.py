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
"""

import hashlib
import os
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console


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
            return ok(blob_data)
        except Exception as e:
            console.print(f"[red]✗ Blob download failed: {type(e).__name__}: {e}[/red]")
            return fail("download_blob failed", error=e)

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

        result = await self._download_blob_from_node(
            rpc_url, blob_id, context_id, node_name=client_node_name
        )

        if not result["success"]:
            return False

        blob_data = result["data"]
        if not isinstance(blob_data, (bytes, bytearray)):
            console.print(
                f"[red]✗ Expected blob bytes, got {type(blob_data).__name__}[/red]"
            )
            return False

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
                console.print(
                    f"[red]✗ 'expected_size' did not resolve to an integer: "
                    f"{expected_size!r}[/red]"
                )
                return False
            if size != expected_size:
                console.print(
                    f"[red]✗ Size mismatch: expected {expected_size} bytes, "
                    f"got {size}[/red]"
                )
                return False
            console.print(f"[green]✓ Size matches expected {expected_size}[/green]")

        if expected_sha256 and digest.lower() != expected_sha256.lower():
            console.print(
                f"[red]✗ sha256 mismatch: expected {expected_sha256}, got {digest}[/red]"
            )
            return False
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

        return True
