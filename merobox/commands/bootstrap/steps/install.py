"""
Install application step executor.
"""

import os
import shutil
from typing import Any, Optional

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import CONTAINER_DATA_DIR_PATTERNS, DEFAULT_METADATA
from merobox.commands.result import fail, ok
from merobox.commands.utils import console, get_node_rpc_url


class InstallApplicationStep(BaseStep):
    """Execute an install application step."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["node"]

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate node is a string
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")

        # Validate path is a string if provided
        if "path" in self.config and not isinstance(self.config["path"], str):
            raise ValueError(f"Step '{step_name}': 'path' must be a string")

        # Validate url is a string if provided
        if "url" in self.config and not isinstance(self.config["url"], str):
            raise ValueError(f"Step '{step_name}': 'url' must be a string")

        # Validate dev is a boolean if provided
        if "dev" in self.config and not isinstance(self.config["dev"], bool):
            raise ValueError(f"Step '{step_name}': 'dev' must be a boolean")

        # Validate that either path or url is provided
        if "path" not in self.config and "url" not in self.config:
            raise ValueError(
                f"Step '{step_name}': Either 'path' or 'url' must be provided"
            )

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from install_application API response:
        - applicationId: Application ID (this is what the API actually returns)
        """
        return [
            (
                "applicationId",
                "app_id_{node_name}",
                "Application ID - primary identifier for the installed application",
            ),
        ]

    def _is_binary_mode(self) -> bool:
        manager = getattr(self, "manager", None)
        if manager is None:
            return False
        return hasattr(manager, "binary_path") and manager.binary_path is not None

    def _resolve_application_path(self, path: str) -> str:
        expanded_path = os.path.expanduser(path)
        return os.path.abspath(expanded_path)

    def _prepare_container_path(
        self, node_name: str, source_path: str
    ) -> Optional[str]:
        container_data_dir: Optional[str] = None

        for pattern in CONTAINER_DATA_DIR_PATTERNS:
            if "{prefix}-{node_num}-{chain_id}" in pattern:
                parts = node_name.split("-")
                if len(parts) >= 3:
                    candidate = pattern.format(
                        prefix=parts[0], node_num=parts[1], chain_id=parts[2]
                    )
                else:
                    candidate = None
            elif "{node_name}" in pattern:
                candidate = pattern.format(node_name=node_name)
            else:
                candidate = None

            if candidate and os.path.exists(candidate):
                container_data_dir = candidate
                break

        if not container_data_dir or not os.path.exists(container_data_dir):
            console.print(
                f"[red]Container data directory not found for {node_name}[/red]"
            )
            return None
        try:
            abs_container_data_dir = os.path.abspath(container_data_dir)
            abs_source_path = os.path.abspath(source_path)
            if (
                os.path.commonpath([abs_source_path, abs_container_data_dir])
                == abs_container_data_dir
            ):
                filename = os.path.basename(source_path)
                return f"/app/data/{filename}"
        except ValueError:
            pass

        filename = os.path.basename(source_path)
        try:
            os.makedirs(container_data_dir, exist_ok=True)
            container_file_path = os.path.join(container_data_dir, filename)
            shutil.copy2(source_path, container_file_path)
            console.print(
                f"[blue]Copied file to container data directory: {container_file_path}[/blue]"
            )
            return f"/app/data/{filename}"
        except (OSError, shutil.Error) as error:
            console.print(
                f"[red]Failed to copy file to container data directory: {error}[/red]"
            )
            return None

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        raw_application_path = self.config.get("path")
        application_path = (
            self._resolve_application_path(raw_application_path)
            if raw_application_path
            else None
        )
        application_url = self.config.get("url")
        is_dev = self.config.get("dev", False)

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Install step export configuration validation failed[/yellow]"
            )

        if not application_path and not application_url:
            console.print("[red]No application path or URL specified[/red]")
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

        # Execute installation using calimero-client-py
        try:
            client = get_client_for_rpc_url(rpc_url)

            if is_dev and application_path:
                if not os.path.isfile(application_path):
                    console.print(
                        f"[red]Application path not found or not a file: {application_path}[/red]"
                    )
                    return False

                if self._is_binary_mode():
                    console.print(
                        f"[cyan]Installing dev application from host filesystem path: {application_path}[/cyan]"
                    )
                    api_result = client.install_dev_application(
                        path=application_path, metadata=DEFAULT_METADATA
                    )
                else:
                    container_path = self._prepare_container_path(
                        node_name, application_path
                    )
                    if not container_path:
                        console.print(
                            "[red]Unable to prepare application file inside container data directory[/red]"
                        )
                        # Print node logs to help with debugging
                        self._print_node_logs_on_failure(node_name=node_name, lines=50)
                        return False

                    console.print(
                        f"[cyan]Installing dev application using container path: {container_path}[/cyan]"
                    )
                    api_result = client.install_dev_application(
                        path=container_path, metadata=DEFAULT_METADATA
                    )
            else:
                api_result = client.install_application(
                    url=application_url, metadata=DEFAULT_METADATA
                )

            result = ok(api_result)
        except Exception as e:
            result = fail("install_application failed", error=e)

        # Log detailed API response
        import json as json_lib

        console.print(f"[cyan]🔍 Install API Response for {node_name}:[/cyan]")
        console.print(f"  Success: {result.get('success')}")

        data = result.get("data")
        if isinstance(data, dict):
            try:
                formatted_data = json_lib.dumps(data, indent=2)
                console.print(f"  Data:\n{formatted_data}")
            except Exception:
                console.print(f"  Data: {data}")
        else:
            console.print(f"  Data: {data}")

        if not result.get("success"):
            console.print(f"  Error: {result.get('error')}")

        if result["success"]:
            # Check if the JSON-RPC response contains an error
            if self._check_jsonrpc_error(result["data"]):
                # Print node logs to help with debugging
                self._print_node_logs_on_failure(node_name=node_name, lines=50)
                return False

            # Store result for later use
            step_key = f"install_{node_name}"
            workflow_results[step_key] = result["data"]

            # Debug: Show what we actually received
            console.print(f"[blue]📝 Install result data: {result['data']}[/blue]")

            # Export variables using the new standardized approach
            self._export_variables(result["data"], node_name, dynamic_values)

            # Legacy support: ensure app_id is always available for backward compatibility
            if f"app_id_{node_name}" not in dynamic_values:
                # Try to extract from the raw response as fallback
                if isinstance(result["data"], dict):
                    actual_data = result["data"].get("data", result["data"])
                    app_id = actual_data.get(
                        "id", actual_data.get("applicationId", actual_data.get("name"))
                    )
                    if app_id:
                        dynamic_values[f"app_id_{node_name}"] = app_id
                        console.print(
                            f"[blue]📝 Fallback: Captured application ID for {node_name}: {app_id}[/blue]"
                        )
                    else:
                        console.print(
                            f"[yellow]⚠️  No application ID found in response. Available keys: {list(actual_data.keys())}[/yellow]"
                        )
                else:
                    console.print(
                        f"[yellow]⚠️  Install result is not a dict: {type(result['data'])}[/yellow]"
                    )

            return True
        else:
            console.print(
                f"[red]Installation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            # Print node logs to help with debugging
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
