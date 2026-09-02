"""
Install application step executor.
"""

import os
import shutil
from typing import Any, Optional

from rich.markup import escape

from merobox.commands.bootstrap.config import validate_workflow_step
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import CONTAINER_DATA_DIR_PATTERNS
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class InstallApplicationStep(BaseStep):
    """Execute an install application step."""

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        # The schema layer already states every rule for this step, so it is the
        # one that enforces them; a second hand-rolled copy would drift.
        errors = validate_workflow_step(self.config, 0)
        if errors:
            raise ValueError("; ".join(errors))

    def _get_exportable_variables(self):
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
            if "{node_name}" in pattern:
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
        package = self.config.get("package")
        version = self.config.get("version")

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Install step export configuration validation failed[/yellow]"
            )

        if not application_path and not package:
            console.print(
                "[red]No application path or package coordinates specified[/red]"
            )
            return False

        # Resolve node (gets URL and ensures authentication)
        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        # Execute installation using calimero-client-py
        try:
            console.print(
                f"[cyan]Connecting to {rpc_url} (node: {node_name})...[/cyan]"
            )
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)

            if application_path:
                if not os.path.isfile(application_path):
                    console.print(
                        f"[red]Application path not found or not a file: {application_path}[/red]"
                    )
                    return False

                if self._is_binary_mode():
                    console.print(
                        f"[cyan]Installing dev application from host filesystem path: {application_path}[/cyan]"
                    )
                    api_result = client.install_dev_application(path=application_path)
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
                    api_result = client.install_dev_application(path=container_path)
            else:
                console.print(
                    f"[cyan]Installing {package}@{version} from the node's "
                    f"configured registry[/cyan]"
                )
                api_result = client.install_application(
                    package=package, version=version
                )

            result = ok(api_result)
        except Exception as e:
            import traceback

            console.print(
                f"[red]Exception during install_application: "
                f"{type(e).__name__}: {escape(str(e))}[/red]"
            )
            console.print(f"[dim]{escape(traceback.format_exc())}[/dim]")
            result = fail(f"install_application failed: {e}", error=e)

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

        expected_failure = self._is_expected_failure()

        if result["success"]:
            # Check if the JSON-RPC response contains an error
            if self._check_jsonrpc_error(result["data"]):
                if expected_failure:
                    return self._report_expected_failure(
                        self._jsonrpc_error_detail(result["data"])
                    )
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

            if expected_failure:
                return self._report_unexpected_success()
            return True
        else:
            if expected_failure:
                return self._report_expected_failure(self._failure_detail(result))
            console.print(
                f"[red]Installation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            # Print node logs to help with debugging
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
