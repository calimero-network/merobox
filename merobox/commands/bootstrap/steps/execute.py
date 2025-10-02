"""
Execute step executor for contract calls.
"""

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

        # Get node RPC URL
        try:
            from merobox.commands.manager import CalimeroManager

            manager = CalimeroManager()
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

            if exec_type in ["contract_call", "view_call", "function_call"]:
                result = await call_function(
                    rpc_url, context_id, method, resolved_args, executor_public_key
                )
            else:
                console.print(f"[red]Unknown execution type: {exec_type}[/red]")
                return False

            # Log detailed API response
            console.print(f"[cyan]🔍 Execute API Response for {node_name}:[/cyan]")
            console.print(f"  Success: {result.get('success')}")
            console.print(f"  Data: {result.get('data')}")
            if not result.get("success"):
                console.print(f"  Error: {result.get('error')}")

            if result["success"]:
                # Check if the JSON-RPC response contains an error
                if self._check_jsonrpc_error(result["data"]):
                    return False

                # Store result for later use
                step_key = f"execute_{node_name}_{method}"
                workflow_results[step_key] = result["data"]

                # Export variables using the new standardized approach
                # Note: We need to handle the method dynamically for the export
                self._export_variables(result["data"], node_name, dynamic_values)

                return True
            else:
                console.print(
                    f"[red]Execution failed: {result.get('error', 'Unknown error')}[/red]"
                )
                return False

        except Exception as e:
            console.print(f"[red]Execution failed with error: {str(e)}[/red]")
            return False

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
