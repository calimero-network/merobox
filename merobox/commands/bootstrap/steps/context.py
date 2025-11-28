"""
Create context step executor.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import DEFAULT_PROTOCOL
from merobox.commands.result import fail, ok
from merobox.commands.utils import console, get_node_rpc_url


class CreateContextStep(BaseStep):
    """Execute a create context step."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["node", "application_id"]

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

        # Validate application_id is a string
        if not isinstance(self.config.get("application_id"), str):
            raise ValueError(f"Step '{step_name}': 'application_id' must be a string")

        # Validate params is JSON string if provided
        if "params" in self.config and not isinstance(self.config["params"], str):
            raise ValueError(f"Step '{step_name}': 'params' must be a JSON string")

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Note: These variables are NOT automatically exported.
        They must be explicitly specified in the 'outputs' configuration.

        Available variables from create_context API response:
        - contextId: Context ID (this is what the API actually returns)
        - memberPublicKey: Public key of the context member
        """
        return [
            (
                "contextId",
                "context_id_{node_name}",
                "Context ID - primary identifier for the created context",
            ),
            (
                "memberPublicKey",
                "context_member_public_key_{node_name}",
                "Public key of the context member",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        application_id = self._resolve_dynamic_value(
            self.config["application_id"], workflow_results, dynamic_values
        )

        # Debug: show resolution context
        try:
            console.print("[blue]Debug: CreateContext resolved values:[/blue]")
            console.print(f"  node: {node_name}")
            console.print(f"  application_id (resolved): {application_id}")
            console.print(f"  dynamic_values keys: {list(dynamic_values.keys())}")
            console.print(f"  workflow_results keys: {list(workflow_results.keys())}")
        except Exception:
            pass

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Context step export configuration validation failed[/yellow]"
            )

        params_json: str | None = None
        if "params" in self.config:
            try:
                import json

                params_json = self.config["params"]
                # Validate JSON
                json.loads(params_json)
                console.print("[blue]Using initialization params JSON[/blue]")
            except json.JSONDecodeError as e:
                console.print(f"[red]Failed to parse params JSON: {str(e)}[/red]")
                return False

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

        # Execute context creation using calimero-client-py
        try:
            client = get_client_for_rpc_url(rpc_url)

            protocol = self.config.get("protocol", DEFAULT_PROTOCOL)
            api_result = client.create_context(
                application_id=application_id,
                protocol=protocol,
                params=params_json,
            )

            result = ok(api_result)
        except Exception as e:
            result = fail("create_context failed", error=e)

        # Log detailed API response
        import json as json_lib

        console.print(f"[cyan]🔍 Context Creation API Response for {node_name}:[/cyan]")
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
            # Print exception details if available
            if "exception" in result:
                exception = result["exception"]
                if isinstance(exception, dict):
                    console.print(
                        f"  Exception Type: {exception.get('type', 'Unknown')}"
                    )
                    console.print(
                        f"  Exception Message: {exception.get('message', 'No message')}"
                    )
                    # Only print traceback in verbose mode or if it's short
                    traceback_str = exception.get("traceback", "")
                    if traceback_str:
                        # Print last few lines of traceback for context
                        lines = traceback_str.strip().split("\n")
                        if len(lines) > 5:
                            console.print("  Traceback (last 5 lines):")
                            for line in lines[-5:]:
                                console.print(f"    {line}")
                        else:
                            console.print(f"  Traceback:\n{traceback_str}")

        if result["success"]:
            # Check if the JSON-RPC response contains an error
            if self._check_jsonrpc_error(result["data"]):
                return False

            # Store result for later use
            step_key = f"context_{node_name}"
            workflow_results[step_key] = result["data"]

            # Export variables using the new standardized approach
            self._export_variables(result["data"], node_name, dynamic_values)

            # Legacy support: ensure context_id is always available for backward compatibility
            if f"context_id_{node_name}" not in dynamic_values:
                # Try to extract from the raw response as fallback
                # Handle nested structure: result["data"] may be {"data": {"contextId": "..."}}
                if isinstance(result["data"], dict):
                    # First try to get from nested "data" field
                    nested_data = result["data"].get("data", result["data"])
                    context_id = nested_data.get(
                        "id",
                        nested_data.get("contextId", nested_data.get("name")),
                    )
                    if context_id:
                        dynamic_values[f"context_id_{node_name}"] = context_id
                        console.print(
                            f"[blue]📝 Fallback: Captured context ID for {node_name}: {context_id}[/blue]"
                        )
                    else:
                        # Only show warning if we really couldn't find it
                        available_keys = list(result["data"].keys())
                        if "data" in result["data"] and isinstance(
                            result["data"]["data"], dict
                        ):
                            available_keys.extend(
                                [f"data.{k}" for k in result["data"]["data"].keys()]
                            )
                        console.print(
                            f"[yellow]⚠️  No context ID found in response. Available keys: {available_keys}[/yellow]"
                        )
                else:
                    console.print(
                        f"[yellow]⚠️  Context result is not a dict: {type(result['data'])}[/yellow]"
                    )

            return True
        else:
            console.print(
                f"[red]Context creation failed: {result.get('error', 'Unknown error')}[/red]"
            )
            # Print node logs to help with debugging
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
