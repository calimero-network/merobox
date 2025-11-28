"""
Execute step executor for contract calls.
"""

import asyncio
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.call import call_function
from merobox.commands.utils import console, get_node_rpc_url


class ExecuteStep(BaseStep):
    """Execute a contract execution step."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["node", "context_id", "method"]

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

        # Validate context_id is a string
        if not isinstance(self.config.get("context_id"), str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        # Validate method is a string
        if not isinstance(self.config.get("method"), str):
            raise ValueError(f"Step '{step_name}': 'method' must be a string")

        # Validate args is a dict if provided
        if "args" in self.config and not isinstance(self.config["args"], dict):
            raise ValueError(f"Step '{step_name}': 'args' must be a dictionary")

        # Validate executor_public_key is a string if provided
        if "executor_public_key" in self.config and not isinstance(
            self.config["executor_public_key"], str
        ):
            raise ValueError(
                f"Step '{step_name}': 'executor_public_key' must be a string"
            )

        # Validate exec_type is a string if provided
        if "exec_type" in self.config and not isinstance(self.config["exec_type"], str):
            raise ValueError(f"Step '{step_name}': 'exec_type' must be a string")

        # Validate expected_failure is a boolean if provided
        if "expected_failure" in self.config and not isinstance(
            self.config["expected_failure"], bool
        ):
            raise ValueError(
                f"Step '{step_name}': 'expected_failure' must be a boolean"
            )

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Note: The execute step API response structure varies based on success/failure.
        For successful calls, the response may contain result data.
        For failed calls, the response contains error information.
        Custom outputs are recommended for this step.
        """
        return []

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )
        exec_type = self.config.get(
            "exec_type"
        )  # Get exec_type if specified, otherwise will default to function_call
        method = self.config.get("method")
        args = self.config.get("args", {})

        # Resolve dynamic values in args recursively
        resolved_args = self._resolve_args_dynamic_values(
            args, workflow_results, dynamic_values
        )

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Execute step export configuration validation failed[/yellow]"
            )

        # Get executor public key from config or extract from context
        executor_public_key = (
            self._resolve_dynamic_value(
                self.config.get("executor_public_key"), workflow_results, dynamic_values
            )
            if self.config.get("executor_public_key")
            else None
        )

        # If not provided in config, try to extract from context data (fallback)
        if not executor_public_key:
            # Extract node name from the original context_id placeholder (e.g., {{context.calimero-node-1}})
            original_context_id = self.config["context_id"]
            if "{{context." in original_context_id and "}}" in original_context_id:
                context_node = original_context_id.split("{{context.")[1].split("}}")[0]
                context_key = f"context_{context_node}"
                console.print(
                    f"[blue]Debug: Looking for context key: {context_key}[/blue]"
                )
                if context_key in workflow_results:
                    context_data = workflow_results[context_key]
                    console.print(f"[blue]Debug: Context data: {context_data}[/blue]")
                    if isinstance(context_data, dict) and "data" in context_data:
                        executor_public_key = context_data["data"].get(
                            "memberPublicKey"
                        )
                        console.print(
                            f"[blue]Debug: Found executor public key: {executor_public_key}[/blue]"
                        )
                    else:
                        console.print(
                            f"[blue]Debug: Context data structure: {type(context_data)}[/blue]"
                        )
                else:
                    console.print(
                        f"[blue]Debug: Context key {context_key} not found in workflow_results[/blue]"
                    )
                    console.print(
                        f"[blue]Debug: Available keys: {list(workflow_results.keys())}[/blue]"
                    )

        # Debug: Show resolved values
        console.print("[blue]Debug: Resolved values for execute step:[/blue]")
        console.print(f"  context_id: {context_id}")
        console.print(f"  exec_type: {exec_type}")
        console.print(f"  method: {method}")
        console.print(f"  args: {resolved_args}")
        console.print(f"  executor_public_key: {executor_public_key}")

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

        # Execute based on type
        try:
            # Default to function_call if exec_type is not specified
            if not exec_type:
                exec_type = "function_call"

            # Check if this step expects failure
            expected_failure = self.config.get("expected_failure", False)

            max_state_retries = int(self.config.get("state_retry_attempts", 5))
            state_retry_delay = float(self.config.get("state_retry_delay", 3.0))
            retry_attempt = 1

            while retry_attempt <= max_state_retries:
                if exec_type in ["contract_call", "view_call", "function_call"]:
                    result = await call_function(
                        rpc_url, context_id, method, resolved_args, executor_public_key
                    )
                else:
                    console.print(f"[red]Unknown execution type: {exec_type}[/red]")
                    return False

                # Log detailed API response
                import json as json_lib

                console.print(
                    f"[cyan]🔍 Execute API Response for {node_name} (attempt {retry_attempt}/{max_state_retries}):[/cyan]"
                )
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
                    # Call failed (network/connection/API level failure)
                    error_message = result.get("error", "Unknown error")

                    if expected_failure:
                        # This is an expected failure - capture error details and continue
                        console.print(
                            f"[yellow]✓ Expected failure occurred: {error_message}[/yellow]"
                        )

                        # Structure error information for export
                        error_info = self._extract_error_info(
                            result, expected=expected_failure
                        )

                        # Store error information for later use
                        step_key = f"execute_{node_name}_{method}"
                        workflow_results[step_key] = error_info

                        # Always export error details when expected_failure is True
                        self._export_error_variables(
                            error_info, node_name, dynamic_values
                        )

                        return True
                    else:
                        # Unexpected failure - stop workflow
                        console.print(
                            f"[red]❌ Execution failed: {error_message}[/red]"
                        )
                        # Print node logs to help with debugging
                        self._print_node_logs_on_failure(node_name=node_name, lines=50)
                        return False

                # Check if the JSON-RPC response contains an error
                if self._check_jsonrpc_error(result["data"]):
                    # Check for transient missing state error - retry if applicable
                    if self._is_missing_state_error(result["data"]):
                        if retry_attempt < max_state_retries:
                            console.print(
                                f"[yellow]App state not available yet on {node_name}; retrying in {state_retry_delay}s...[/yellow]"
                            )
                            retry_attempt += 1
                            await asyncio.sleep(state_retry_delay)
                            continue
                        else:
                            # Exhausted retries for missing state
                            console.print(
                                "[red]Execution failed: app state not available after retries[/red]"
                            )
                            # Print node logs to help with debugging
                            self._print_node_logs_on_failure(
                                node_name=node_name, lines=50
                            )
                            return False

                    # Handle JSON-RPC error
                    error_info = self._extract_error_info(
                        result["data"], expected=expected_failure
                    )
                    if expected_failure:
                        console.print(
                            "[yellow]✓ Expected failure occurred (JSON-RPC error detected)[/yellow]"
                        )

                        step_key = f"execute_{node_name}_{method}"
                        workflow_results[step_key] = error_info

                        self._export_error_variables(
                            error_info, node_name, dynamic_values
                        )

                        return True
                    else:
                        console.print(
                            "[red]❌ Unexpected JSON-RPC error detected[/red]"
                        )
                        # Print node logs to help with debugging
                        self._print_node_logs_on_failure(node_name=node_name, lines=50)
                        return False

                # Store result for later use
                step_key = f"execute_{node_name}_{method}"
                workflow_results[step_key] = result["data"]

                # If failure was expected but call succeeded, warn the user
                if expected_failure:
                    console.print(
                        "[yellow]⚠️  Warning: Expected failure but call succeeded[/yellow]"
                    )
                    # Create error_info with None values to maintain consistency with actual failures
                    # This ensures error fields are auto-exported even when no outputs are configured
                    error_info = {
                        "success": False,
                        "expected": True,
                        "error_code": None,
                        "error_type": None,
                        "error_message": None,
                        "error": None,
                    }
                    # Export error fields (will export None values) using the same method as actual failures
                    # This maintains consistency: when no outputs are configured, error fields are auto-exported
                    # with keys like execute_node_method_error_code
                    # Get protected keys (error field exports) to prevent overwriting
                    protected_keys = self._export_error_variables(
                        error_info, node_name, dynamic_values
                    )
                    # Also export the successful result data, but protect error field exports
                    # This prevents successful result fields from overwriting error field None values
                    self._export_variables(
                        result["data"],
                        node_name,
                        dynamic_values,
                        protected_keys=protected_keys,
                    )
                    return True

                # Export variables using the new standardized approach
                # Note: We need to handle the method dynamically for the export
                self._export_variables(result["data"], node_name, dynamic_values)

                return True

        except Exception as e:
            console.print(f"[red]Execution failed with error: {str(e)}[/red]")
            return False

    def _extract_error_info(
        self, error_data: Any, expected: bool = False
    ) -> dict[str, Any]:
        """
        Extract and normalize error information from Calimero API error formats.

        Args:
            error_data: The error data from the API response.
                       Can be:
                       - Full result dict with success=False (network/API failure)
                       - result["data"] dict containing {"error": {...}} (JSON-RPC error)
            expected: Whether this error was expected

        Returns:
            Normalized error information dictionary with consistent structure:
            {
                "success": False,
                "expected": bool,
                "error_code": int | None,
                "error_type": str | None,
                "error_message": str | None,
                "error": dict | str,
                "data": Any
            }
        """
        error_info = {
            "success": False,
            "expected": expected,
        }

        # Network/API level failure (call_function caught an exception)
        if (
            isinstance(error_data, dict)
            and "success" in error_data
            and not error_data.get("success")
        ):
            error_info["error"] = error_data.get("error", "Unknown error")
            error_info["error_message"] = error_info["error"]

            # Extract exception details if present
            if "exception" in error_data:
                exception = error_data["exception"]
                if isinstance(exception, dict):
                    error_info["exception_type"] = exception.get("type")
                    error_info["exception_message"] = exception.get("message")
                    error_info["exception_traceback"] = exception.get("traceback")

            # Check if there's a nested JSON-RPC error in the data field
            if "data" in error_data and isinstance(error_data["data"], dict):
                if "error" in error_data["data"]:
                    rpc_error = error_data["data"]["error"]
                    error_info.update(self._extract_jsonrpc_error_details(rpc_error))

            error_info["data"] = error_data.get("data")

        # JSON-RPC error (function call succeeded but returned error)
        elif isinstance(error_data, dict) and "error" in error_data:
            rpc_error = error_data["error"]
            error_info.update(self._extract_jsonrpc_error_details(rpc_error))
            error_info["data"] = error_data
            error_info["error"] = rpc_error

        else:
            error_info["error"] = str(error_data)
            error_info["error_message"] = str(error_data)
            error_info["data"] = error_data

        return error_info

    def _extract_jsonrpc_error_details(self, rpc_error: Any) -> dict[str, Any]:
        """
        Extract details from a Calimero JSON-RPC error object.

        Calimero JSON-RPC errors have the structure:
        {
            "type": "FunctionCallError" | "UnauthorizedError" | etc.,
            "code": <numeric_code>,
            "message": "<error_message>",
            "data": <optional_additional_data>
        }

        Args:
            rpc_error: JSON-RPC error object (dict)

        Returns:
            Dictionary with extracted error details:
            {
                "error_code": int | None,
                "error_type": str | None,
                "error_message": str | None,
                "error_data": Any,
                "error": dict
            }
        """
        details = {}

        if isinstance(rpc_error, dict):
            details["error_code"] = rpc_error.get("code")
            details["error_type"] = rpc_error.get("type")
            details["error_data"] = rpc_error.get("data")
            error_msg = rpc_error.get("message")
            if error_msg:
                details["error_message"] = error_msg
            else:
                data_value = rpc_error.get("data")
                if data_value is not None:
                    details["error_message"] = (
                        data_value if isinstance(data_value, str) else str(data_value)
                    )
                else:
                    details["error_message"] = None
            details["error"] = rpc_error
        else:
            details["error"] = rpc_error
            details["error_message"] = str(rpc_error)

        return details

    def _export_error_variables(
        self,
        error_info: dict[str, Any],
        node_name: str,
        dynamic_values: dict[str, Any],
    ) -> set[str]:
        """
        Export error information to dynamic_values for use in subsequent steps or assertions.

        Args:
            error_info: Normalized error information dictionary
            node_name: Name of the node where the error occurred
            dynamic_values: Dictionary to export variables to

        Returns:
            Set of protected keys (error field exports) that should not be overwritten
        """
        method = self.config.get("method", "unknown")
        step_key = f"execute_{node_name}_{method}"
        protected_keys = set()

        if "outputs" in self.config:
            outputs_config = self.config.get("outputs", {})
            # First, export error_info to set error field values (including None)
            # We don't pass protected_keys here so all fields can be exported
            self._export_variables(error_info, node_name, dynamic_values)

            # After exporting error fields, identify and mark them as protected
            # This prevents subsequent result data exports from overwriting error fields
            # Error fields should always be protected, especially when they are None
            # (when expected_failure is True but call succeeds)
            for exported_var, assigned_var in outputs_config.items():
                error_field_name = None
                if isinstance(assigned_var, str):
                    if assigned_var in [
                        "error_code",
                        "error_type",
                        "error_message",
                        "error",
                    ]:
                        error_field_name = assigned_var

                elif isinstance(assigned_var, dict) and "field" in assigned_var:
                    field_name = assigned_var["field"]
                    if field_name in [
                        "error_code",
                        "error_type",
                        "error_message",
                        "error",
                    ]:
                        error_field_name = field_name

                if error_field_name:
                    if isinstance(assigned_var, str):
                        target_key = exported_var
                    else:
                        target_key = assigned_var.get("target", exported_var)
                        target_key = target_key.replace("{node_name}", node_name)

                    # Always mark error field exports as protected
                    # This prevents subsequent result data from overwriting them
                    # This is critical when expected_failure is True but call succeeds,
                    # where error fields are None and must remain None
                    protected_keys.add(target_key)
        else:
            error_fields = {
                "error_code": error_info.get("error_code"),
                "error_type": error_info.get("error_type"),
                "error_message": error_info.get("error_message"),
                "error": error_info.get("error"),
            }

            for field, value in error_fields.items():
                export_key = f"{step_key}_{field}"
                protected_keys.add(export_key)
                dynamic_values[export_key] = value
                console.print(
                    f"[blue]📝 Exported error field {field} → {export_key}: {value}[/blue]"
                )

        return protected_keys

    def _resolve_args_dynamic_values(
        self,
        args: Any,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> Any:
        """Recursively resolve dynamic values in args dictionary or other data structures."""
        if isinstance(args, dict):
            resolved_args = {}
            for key, value in args.items():
                resolved_args[key] = self._resolve_args_dynamic_values(
                    value, workflow_results, dynamic_values
                )
            return resolved_args
        elif isinstance(args, list):
            return [
                self._resolve_args_dynamic_values(
                    item, workflow_results, dynamic_values
                )
                for item in args
            ]
        elif isinstance(args, str):
            return self._resolve_dynamic_value(args, workflow_results, dynamic_values)
        else:
            return args

    def _is_missing_state_error(self, result_data: Any) -> bool:
        """Detect transient missing app state errors to allow retry after propagation."""
        if not isinstance(result_data, dict):
            return False

        error_info = result_data.get("error")
        error_type = ""
        message = ""

        if isinstance(error_info, dict):
            error_type = str(error_info.get("type") or "")
            message = str(error_info.get("data") or error_info.get("message") or "")
        elif error_info:
            message = str(error_info)

        # Check for Uninitialized error type (context state not synced yet)
        if error_type == "Uninitialized":
            return True

        # Check for missing state error messages
        return (
            "Failed to find or read app state" in message
            or "state not initialized" in message.lower()
            or "awaiting state sync" in message.lower()
        )
