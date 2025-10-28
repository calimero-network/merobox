"""
Blob upload step executor.
"""

import os
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.retry import NETWORK_RETRY_CONFIG, with_retry
from merobox.commands.utils import console, get_node_rpc_url


class UploadBlobStep(BaseStep):
    """Upload a file to blob storage and capture blob_id."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["node", "file_path"]

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f"Unnamed {self.config.get('type', 'Unknown')} step"
        )

        # Validate node is a string
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

        # Validate file_path is a string
        if not isinstance(self.config.get("file_path"), str):
            raise ValueError(f"Step '{step_name}': 'file_path' must be a string")

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from blob upload API response:
        - blob_id: The unique identifier for the uploaded blob
        - size: The size of the uploaded blob in bytes
        """
        return [
            (
                "blob_id",
                "blob_id_{node_name}",
                "Blob ID - unique identifier for the uploaded blob",
            ),
            (
                "size",
                "blob_size_{node_name}",
                "Blob size - size of the uploaded blob in bytes",
            ),
        ]

    @with_retry(config=NETWORK_RETRY_CONFIG)
    async def _upload_blob_to_node(
        self, rpc_url: str, file_data: bytes, context_id: str | None = None
    ) -> dict:
        """
        Upload blob to node .

        Args:
            rpc_url: The RPC URL of the node
            file_data: Binary file data to upload
            context_id: Optional context ID for the blob

        Returns:
            Dictionary with success status and response data
        """
        console.print("[cyan]📤 Uploading blob [/cyan]")
        console.print(
            f"[cyan]   Size: {len(file_data)} bytes ({len(file_data) / 1024:.2f} KB)[/cyan]"
        )
        if context_id:
            console.print(f"[cyan]   Context ID: {context_id}[/cyan]")

        try:
            client = get_client_for_rpc_url(rpc_url)
            result = client.upload_blob(file_data, context_id)

            console.print("[green]✓ Blob uploaded successfully![/green]")
            console.print(f"[green]   Response: {result}[/green]")

            return ok(result)
        except Exception as e:
            console.print(f"[red]✗ Blob upload failed: {type(e).__name__}: {e}[/red]")
            return fail("upload_blob failed", error=e)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        file_path = self.config["file_path"]

        # Optional: context_id can be provided to associate blob with a context
        context_id = self.config.get("context_id")
        if context_id:
            context_id = self._resolve_dynamic_value(
                context_id, workflow_results, dynamic_values
            )

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Upload blob step export configuration validation failed[/yellow]"
            )

        # Resolve dynamic values in file_path
        file_path = self._resolve_dynamic_value(
            file_path, workflow_results, dynamic_values
        )

        # Check if file exists
        if not os.path.exists(file_path):
            console.print(f"[red]File not found: {file_path}[/red]")
            return False

        if not os.path.isfile(file_path):
            console.print(f"[red]Path is not a file: {file_path}[/red]")
            return False

        # Read file data
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()
            console.print(
                f"[blue]📁 Read file: {file_path} ({len(file_data)} bytes)[/blue]"
            )
        except Exception as e:
            console.print(f"[red]Failed to read file {file_path}: {str(e)}[/red]")
            return False

        # Get node RPC URL
        try:
            if self.manager is not None:
                manager = self.manager
            else:
                from merobox.commands.manager import DockerManager

                manager = DockerManager()

            rpc_url = get_node_rpc_url(node_name, manager)
        except Exception as e:
            console.print(
                f"[red]Failed to get RPC URL for node {node_name}: {str(e)}[/red]"
            )
            return False

        # Upload blob
        result = await self._upload_blob_to_node(rpc_url, file_data, context_id)

        if result["success"]:
            # Extract blob info from response
            # Response format: {"payload": {"data": {"blob_id": "...", "size": 123}}}
            response_data = result["data"]

            # Handle nested response structure
            if isinstance(response_data, dict):
                # Check for payload.data structure
                if "payload" in response_data and "data" in response_data["payload"]:
                    blob_info = response_data["payload"]["data"]
                # Check for direct data structure
                elif "data" in response_data:
                    blob_info = response_data["data"]
                # Response is already the blob info
                else:
                    blob_info = response_data
            else:
                console.print(
                    f"[yellow]⚠️  Unexpected response format: {type(response_data)}[/yellow]"
                )
                return False

            # Store result for later use
            step_key = f"blob_{node_name}"
            workflow_results[step_key] = blob_info

            # Debug: Show what we received
            console.print(f"[blue]📝 Blob upload result: {blob_info}[/blue]")

            # Export variables using the standardized approach
            self._export_variables(blob_info, node_name, dynamic_values)

            # Legacy support: ensure blob_id is always available for backward compatibility
            if f"blob_id_{node_name}" not in dynamic_values:
                # Try to extract from the raw response as fallback
                if isinstance(blob_info, dict):
                    blob_id = blob_info.get("blob_id", blob_info.get("id"))
                    if blob_id:
                        dynamic_values[f"blob_id_{node_name}"] = blob_id
                        console.print(
                            f"[blue]📝 Fallback: Captured blob ID for {node_name}: {blob_id}[/blue]"
                        )
                    else:
                        console.print(
                            f"[yellow]⚠️  No blob_id found in response. Available keys: {list(blob_info.keys())}[/yellow]"
                        )

            return True
        else:
            console.print(
                f"[red]Blob upload failed: {result.get('error', 'Unknown error')}[/red]"
            )
            return False
