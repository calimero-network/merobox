"""
Base step class for all workflow steps.
"""

import ast
import json
import re
from typing import Any, Optional

from merobox.commands.utils import console


class BaseStep:
    """Base class for all workflow steps."""

    def __init__(self, config: dict[str, Any], manager: object | None = None):
        self.config = config

        self.manager = manager
        # Define which variables this step can export and their mapping
        self.exportable_variables = self._get_exportable_variables()
        # Validate required fields before proceeding
        self._validate_required_fields()
        # Validate field types
        self._validate_field_types()

    def _get_exportable_variables(self) -> list[tuple[str, str, str]]:
        """
        Define which variables this step can export.
        Returns a list of tuples: (source_field, target_key, description)

        Override this method in subclasses to specify exportable variables.
        """
        return []

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.
        Override this method in subclasses to specify required fields.

        Returns:
            List of required field names
        """
        return []

    def _validate_required_fields(self) -> None:
        """
        Validate that all required fields are present in the configuration.
        Raises ValueError if any required fields are missing.
        """
        required_fields = self._get_required_fields()
        missing_fields = []

        for field in required_fields:
            if field not in self.config or self.config[field] is None:
                missing_fields.append(field)

        if missing_fields:
            step_name = self.config.get(
                "name", f'Unnamed {self.config.get("type", "Unknown")} step'
            )
            step_type = self.config.get("type", "Unknown")
            raise ValueError(
                f"Step '{step_name}' (type: {step_type}) is missing required fields: {', '.join(missing_fields)}. "
                f"Required fields: {', '.join(required_fields)}"
            )

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        Override this method in subclasses to add type validation.
        """
        pass

    def _try_parse_json(self, value: Any) -> Any:
        """Parse JSON string to Python object with fallback strategies.

        Returns parsed object or original value if parsing fails.
        """
        if not isinstance(value, str):
            return value

        s = value.strip()
        if not s:
            return value

        # Attempt standard JSON parsing
        try:
            return json.loads(s)
        except Exception:
            pass

        # Handle double-encoded JSON
        if (s.startswith('"') and s.endswith('"')) or (
            s.startswith("'") and s.endswith("'")
        ):
            try:
                inner = json.loads(s)
                if isinstance(inner, str):
                    try:
                        return json.loads(inner)
                    except Exception:
                        try:
                            return ast.literal_eval(inner)
                        except Exception:
                            pass
            except Exception:
                pass

        # Handle Python-style literals
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, (dict, list, str, int, float, bool, type(None))):
                return parsed
        except Exception:
            pass

        # Clean trailing commas
        if "{" in s or "[" in s:
            try:
                cleaned = re.sub(r",(\s*[}\]])", r"\1", s)
                return json.loads(cleaned)
            except Exception:
                pass

        # Extract JSON substring from noisy input
        if "{" in s or "[" in s:
            try:
                candidate = self._find_json_substring(s)
                if candidate:
                    return json.loads(candidate)
            except Exception:
                pass

        return value

    def _find_json_substring(self, text: str) -> Optional[str]:
        """Extract first complete JSON object or array from text."""
        for match in re.finditer(r"[\{\[]", text):
            pos = match.start()
            opening = match.group()
            closing = "}" if opening == "{" else "]"
            stack = [opening]

            for i in range(pos + 1, len(text)):
                if text[i] == opening:
                    stack.append(opening)
                elif text[i] == closing:
                    stack.pop()
                    if not stack:
                        return text[pos : i + 1]
        return None

    def _extract_path(self, obj: Any, path: str) -> Any:
        """Extract dotted path from object with JSON parsing and array index support.

        This method traverses a dotted path through nested objects, parsing
        JSON strings encountered along the way. It supports:
        - Nested dict access: "result.data.value"
        - Array indexing: "items.0.id"
        - Deep nesting: "result.nested.deeply.nested.field"

        Examples:
            "field.nested" -> obj["field"]["nested"]
            "items.0.id" -> obj["items"][0]["id"]
            "result.data" where result="{\"data\":\"value\"}" -> "value"
            "result.user.name.first" -> obj["result"]["user"]["name"]["first"]

        Args:
            obj: The object to extract from (dict, list, or JSON string)
            path: Dot-separated path to the desired field

        Returns:
            The value at the specified path, or None if not found
        """
        if not isinstance(path, str) or not path:
            return None

        current = obj
        segments = path.split(".")

        for segment in segments:
            current = self._try_parse_json(current)

            # Handle array index
            if isinstance(current, list) and segment.isdigit():
                idx = int(segment)
                if 0 <= idx < len(current):
                    current = current[idx]
                    continue
                return None

            # Handle dict key
            if isinstance(current, dict) and segment in current:
                current = current[segment]
                continue

            return None

        return self._try_parse_json(current) if isinstance(current, str) else current

    def _export_variable(
        self,
        dynamic_values: dict[str, Any],
        source_field: str,
        target_key: str,
        value: Any,
        description: str = None,
    ) -> None:
        """
        Export a variable to dynamic_values with explicit documentation.

        Args:
            dynamic_values: The dynamic values dictionary to update
            source_field: The source field name from the API response
            target_key: The target key in dynamic_values
            value: The value to export
            description: Optional description of what this variable represents
        """
        if value is not None:
            dynamic_values[target_key] = value
            desc_text = f" ({description})" if description else ""
            console.print(
                f"[blue]📝 Exported {source_field} → {target_key}: {value}{desc_text}[/blue]"
            )
        else:
            console.print(
                f"[yellow]⚠️  Could not export {source_field} → {target_key} (value is None)[/yellow]"
            )

    def _export_variables_from_response(
        self,
        response_data: dict[str, Any],
        node_name: str,
        dynamic_values: dict[str, Any],
    ) -> None:
        """
        Export variables automatically based on exportable_variables configuration.
        This method is called for backward compatibility when no custom outputs are specified.

        Args:
            response_data: The API response data
            node_name: The name of the node
            dynamic_values: The dynamic values dictionary to update
        """
        if not self.exportable_variables:
            return

        # Extract the actual data (handle nested structures)
        # If response_data is error_info (has error fields at top level), use it directly
        # Check for success=False and at least one error-specific field to reliably detect error_info
        if isinstance(response_data, dict):
            is_error_info = response_data.get("success") is False and any(
                key in response_data
                for key in ["error_code", "error_type", "error_message"]
            )
            actual_data = (
                response_data
                if is_error_info
                else response_data.get("data", response_data)
            )
        else:
            actual_data = response_data

        for source_field, target_key_template, description in self.exportable_variables:
            # Replace placeholders in target_key_template
            target_key = target_key_template.replace("{node_name}", node_name)

            # Skip if this variable has already been exported by custom outputs
            if target_key in dynamic_values:
                continue

            # Extract value from response
            if isinstance(actual_data, dict):
                value = actual_data.get(source_field)
            else:
                value = None

            # Export the variable
            self._export_variable(
                dynamic_values, source_field, target_key, value, description
            )

    def _export_custom_outputs(
        self,
        response_data: dict[str, Any],
        node_name: str,
        dynamic_values: dict[str, Any],
        protected_keys: Optional[set[str]] = None,
    ) -> None:
        """
        Export variables based on custom outputs configuration specified by the user.

        Args:
            response_data: The API response data
            node_name: The name of the node
            dynamic_values: The dynamic values dictionary to update
            protected_keys: Set of keys that should not be overwritten (e.g., error field exports)
        """
        outputs_config = self.config.get("outputs", {})
        if not outputs_config:
            return

        if protected_keys is None:
            protected_keys = set()

        # Extract the actual data (handle nested structures)
        # If response_data is error_info (has error fields at top level), use it directly
        # Check for success=False and at least one error-specific field to reliably detect error_info
        if isinstance(response_data, dict):
            is_error_info = response_data.get("success") is False and any(
                key in response_data
                for key in ["error_code", "error_type", "error_message"]
            )
            actual_data = (
                response_data
                if is_error_info
                else response_data.get("data", response_data)
            )
        else:
            actual_data = response_data

        console.print(f"[cyan]� Exporting variables from {node_name} response:[/cyan]")

        for exported_variable, assigned_var in outputs_config.items():
            if isinstance(assigned_var, str):
                value = (
                    self._extract_path(actual_data, assigned_var)
                    if "." in assigned_var
                    else (
                        actual_data.get(assigned_var)
                        if isinstance(actual_data, dict)
                        else None
                    )
                )

                if (
                    value is not None
                    and isinstance(value, str)
                    and "." not in assigned_var
                ):
                    value = self._try_parse_json(value)

                field_missing = False
                if value is None and isinstance(actual_data, dict):
                    if "." in assigned_var:
                        field_missing = False
                    else:
                        field_missing = assigned_var not in actual_data

                if field_missing:
                    console.print(
                        f"[yellow]⚠️  Export failed: '{assigned_var}' not found[/yellow]"
                    )
                    console.print(
                        f"[dim]   Available: {', '.join(list(actual_data.keys())[:5])}{'...' if len(actual_data.keys()) > 5 else ''}[/dim]"
                    )
                else:
                    target_key = exported_variable
                    # Skip exporting if this key is protected (e.g., error field export)
                    if target_key in protected_keys:
                        console.print(
                            f"[yellow]⚠️  Skipped export to protected key '{target_key}' (error field export)[/yellow]"
                        )
                        continue
                    dynamic_values[target_key] = value

                    display_value = str(value)
                    if len(display_value) > 100:
                        display_value = display_value[:97] + "..."

                    console.print(
                        f"[green]   ✓[/green] [bold cyan]{exported_variable}[/bold cyan] [dim]=[/dim] {display_value}"
                    )

            elif isinstance(assigned_var, dict):
                # Complex assignment with node name replacement
                if "field" in assigned_var:
                    field_name = assigned_var["field"]
                    # Base value by field name (top-level)
                    base_value = (
                        actual_data.get(field_name)
                        if isinstance(actual_data, dict)
                        else None
                    )
                    # Optional parse JSON: json: true
                    if assigned_var.get("json"):
                        base_value = self._try_parse_json(base_value)
                    # Optional nested path within the base value: path: a.b.c
                    if isinstance(assigned_var.get("path"), str):
                        base_value = self._extract_path(
                            base_value, assigned_var["path"]
                        )

                    field_missing = False
                    if base_value is None and isinstance(actual_data, dict):
                        if isinstance(assigned_var.get("path"), str):
                            field_missing = False
                        else:
                            field_missing = field_name not in actual_data

                    if field_missing:
                        console.print(
                            f"[yellow]⚠️  Export failed: '{field_name}' not found or path unresolved[/yellow]"
                        )
                        console.print(
                            f"[dim]   Available: {', '.join(list(actual_data.keys())[:5])}{'...' if len(actual_data.keys()) > 5 else ''}[/dim]"
                        )
                    else:
                        target_key = assigned_var.get("target", exported_variable)
                        target_key = target_key.replace("{node_name}", node_name)
                        # Skip exporting if this key is protected (e.g., error field export)
                        if target_key in protected_keys:
                            console.print(
                                f"[yellow]⚠️  Skipped export to protected key '{target_key}' (error field export)[/yellow]"
                            )
                            continue
                        dynamic_values[target_key] = base_value

                        # Format the value for display (truncate if too long)
                        display_value = str(base_value)
                        if len(display_value) > 100:
                            display_value = display_value[:97] + "..."

                        console.print(
                            f"[green]   ✓[/green] [bold cyan]{target_key}[/bold cyan] [dim]=[/dim] {display_value}"
                        )
                else:
                    console.print(
                        f"[yellow]⚠️  Invalid custom export config: missing 'field' in {assigned_var}[/yellow]"
                    )

            else:
                console.print(
                    f"[yellow]⚠️  Invalid custom export config: {assigned_var} is not a string or dict[/yellow]"
                )

    def _export_variables(
        self,
        response_data: dict[str, Any],
        node_name: str,
        dynamic_values: dict[str, Any],
        protected_keys: Optional[set[str]] = None,
    ) -> None:
        """
        Main export method that handles only custom outputs (explicit exports).

        Args:
            response_data: The API response data
            node_name: The name of the node
            dynamic_values: The dynamic values dictionary to update
            protected_keys: Set of keys that should not be overwritten (e.g., error field exports)
        """
        # Only handle custom outputs - no automatic exports
        if "outputs" in self.config:
            self._export_custom_outputs(
                response_data, node_name, dynamic_values, protected_keys
            )
        else:
            console.print(
                "[yellow]⚠️  No outputs configured for this step. Variables will not be exported automatically.[/yellow]"
            )
            console.print(
                "[yellow]   To export variables, add an 'outputs' section to your step configuration.[/yellow]"
            )

    def _validate_export_config(self) -> bool:
        """
        Validate that the step configuration properly defines exportable variables.
        Returns True if valid, False otherwise.
        """
        # Check if custom outputs are configured
        if "outputs" in self.config:
            outputs_config = self.config["outputs"]
            if not isinstance(outputs_config, dict):
                console.print(
                    f"[yellow]⚠️  Step {self.__class__.__name__} has invalid outputs config: must be a dictionary[/yellow]"
                )
                return False

            # Validate each output configuration
            for exported_var, assigned_var in outputs_config.items():
                if not isinstance(exported_var, str):
                    console.print(
                        f"[yellow]⚠️  Invalid output key '{exported_var}': must be a string[/yellow]"
                    )
                    return False

                if isinstance(assigned_var, str):
                    # Simple string assignment is valid
                    pass
                elif isinstance(assigned_var, dict):
                    # Complex assignment must have 'field' key
                    if "field" not in assigned_var:
                        console.print(
                            f"[yellow]⚠️  Invalid output config for '{exported_var}': missing 'field' key[/yellow]"
                        )
                        return False
                else:
                    console.print(
                        f"[yellow]⚠️  Invalid output config for '{exported_var}': must be string or dict[/yellow]"
                    )
                    return False

            console.print(
                f"[blue]✅ Custom outputs configuration validated: {len(outputs_config)} outputs defined[/blue]"
            )
            return True

        # Check if automatic exports are configured
        if not hasattr(self, "exportable_variables") or not self.exportable_variables:
            console.print(
                f"[yellow]⚠️  Step {self.__class__.__name__} has no exportable variables defined[/yellow]"
            )
            return False

        console.print(
            f"[blue]✅ Automatic exports configuration validated: {len(self.exportable_variables)} variables defined[/blue]"
        )
        return True

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        """Execute the step. Must be implemented by subclasses."""
        raise NotImplementedError

    def _check_jsonrpc_error(self, result_data: Any) -> bool:
        """
        Check if the API response contains a JSON-RPC error.

        Args:
            result_data: The 'data' field from the API response

        Returns:
            True if a JSON-RPC error was found (workflow should fail), False otherwise
        """
        if isinstance(result_data, dict) and "error" in result_data:
            # JSON-RPC error - fail the workflow
            error_info = result_data["error"]
            if isinstance(error_info, dict):
                error_type = error_info.get("type", "Unknown")
                error_data = error_info.get("data", "No details")
                console.print(f"[red]JSON-RPC Error: {error_type} - {error_data}[/red]")
            else:
                console.print(f"[red]JSON-RPC Error: {error_info}[/red]")
            return True
        return False

    def _print_node_logs_on_failure(
        self, node_name: Optional[str] = None, lines: int = 50
    ) -> None:
        """
        Print recent node logs when a step fails to help with debugging.

        Args:
            node_name: Name of the node to get logs from. If None, tries to extract from step config.
            lines: Number of log lines to show (default: 50)
        """
        # Try to get node name from config if not provided
        if node_name is None:
            node_name = self.config.get("node")

        if not node_name:
            # No node associated with this step, skip log printing
            return

        if not self.manager:
            # No manager available, skip log printing
            return

        try:
            console.print(
                f"\n[yellow]📋 Recent logs from node '{node_name}' (last {lines} lines):[/yellow]"
            )
            console.print("[dim]" + "=" * 80 + "[/dim]")

            # Check if we're in binary mode
            is_binary_mode = (
                hasattr(self.manager, "binary_path")
                and self.manager.binary_path is not None
            )

            if is_binary_mode:
                # Binary mode - use BinaryManager's get_node_logs
                log_content = self.manager.get_node_logs(node_name, lines=lines)
                if log_content:
                    console.print(log_content)
                else:
                    console.print(f"[dim]No logs found for {node_name}[/dim]")
            else:
                # Docker mode - DockerManager.get_node_logs prints with its own formatting
                # We'll call it directly, but suppress its header since we have our own
                try:
                    # Get the container and logs directly to avoid double formatting
                    if node_name in self.manager.nodes:
                        container = self.manager.nodes[node_name]
                    else:
                        container = self.manager.client.containers.get(node_name)

                    logs = container.logs(tail=lines, timestamps=True).decode("utf-8")
                    if logs:
                        console.print(logs)
                    else:
                        console.print(f"[dim]No logs available for {node_name}[/dim]")
                except Exception as e:
                    console.print(f"[dim]Could not retrieve logs: {str(e)}[/dim]")

            console.print("[dim]" + "=" * 80 + "[/dim]\n")
        except Exception as e:
            # Don't let log printing failures break the workflow
            console.print(f"[dim]Could not print node logs: {str(e)}[/dim]")

    def _resolve_dynamic_value(
        self,
        value: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> str:
        """Resolve dynamic values using placeholders and captured results."""
        if not isinstance(value, str):
            return value

        # Strip quotes from simple string literals (e.g., 'value' or "value" -> value)
        # This helps with assertions where string literals are quoted
        if len(value) >= 2:
            if (value.startswith("'") and value.endswith("'")) or (
                value.startswith('"') and value.endswith('"')
            ):
                # Only strip if it's a simple string literal without placeholders
                if "{{" not in value and "}}" not in value:
                    return value[1:-1]

        # Check if there are any placeholders in the string (embedded or complete)
        if "{{" in value and "}}" in value:
            # Handle complete placeholder strings first (e.g., "{{current_iteration}}")
            if value.startswith("{{") and value.endswith("}}"):
                placeholder = value[2:-2].strip()

                # First, check if this is a simple custom output variable name
                if placeholder in dynamic_values:
                    return dynamic_values[placeholder]

                # Handle different placeholder types
                if placeholder.startswith("install."):
                    # Format: {{install.node_name}}
                    parts = placeholder.split(".", 1)
                    if len(parts) == 2:
                        node_name = parts[1]
                        # First try to get from dynamic values (captured application ID)
                        dynamic_key = f"app_id_{node_name}"
                        if dynamic_key in dynamic_values:
                            app_id = dynamic_values[dynamic_key]
                            return app_id

                        # Fallback to workflow results
                        install_key = f"install_{node_name}"
                        if install_key in workflow_results:
                            result = workflow_results[install_key]
                            # Try to extract application ID from the result
                            if isinstance(result, dict):
                                return result.get(
                                    "id",
                                    result.get(
                                        "applicationId", result.get("name", value)
                                    ),
                                )
                            return str(result)
                        else:
                            console.print(
                                f"[yellow]Warning: Install result for {node_name} not found, using placeholder[/yellow]"
                            )
                            return value

                elif placeholder.startswith("context."):
                    # Format: {{context.node_name}} or {{context.node_name.field}}
                    parts = placeholder.split(".", 1)
                    if len(parts) == 2:
                        node_part = parts[1]
                        # Check if there's a field specification (e.g., context.node_name.memberPublicKey)
                        if "." in node_part:
                            node_name, field_name = node_part.split(".", 1)
                        else:
                            node_name = node_part
                            field_name = None

                        if field_name:
                            # For field access (e.g., memberPublicKey), look in workflow_results
                            context_key = f"context_{node_name}"
                            if context_key in workflow_results:
                                result = workflow_results[context_key]
                                # Try to extract specific field from the result
                                if isinstance(result, dict):
                                    # Handle nested data structure
                                    actual_data = result.get("data", result)
                                    return actual_data.get(field_name, value)
                            else:
                                console.print(
                                    f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]"
                                )
                                return value
                        else:
                            # For context ID access, look in dynamic_values first
                            context_id_key = f"context_id_{node_name}"
                            if context_id_key in dynamic_values:
                                return dynamic_values[context_id_key]

                            # Fallback to workflow_results
                            context_key = f"context_{node_name}"
                            if context_key in workflow_results:
                                result = workflow_results[context_key]
                                # Try to extract context ID from the result
                                if isinstance(result, dict):
                                    # Handle nested data structure
                                    actual_data = result.get("data", result)
                                    return actual_data.get(
                                        "id",
                                        actual_data.get(
                                            "contextId", actual_data.get("name", value)
                                        ),
                                    )
                                return str(result)
                            else:
                                console.print(
                                    f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]"
                                )
                                return value

                elif placeholder.startswith("identity."):
                    # Format: {{identity.node_name}}
                    parts = placeholder.split(".", 1)
                    if len(parts) == 2:
                        node_name = parts[1]
                        identity_key = f"identity_{node_name}"
                        if identity_key in workflow_results:
                            result = workflow_results[identity_key]
                            # Try to extract public key from the result
                            if isinstance(result, dict):
                                # Handle nested data structure
                                actual_data = result.get("data", result)
                                return actual_data.get(
                                    "publicKey",
                                    actual_data.get(
                                        "id", actual_data.get("name", value)
                                    ),
                                )
                            return str(result)
                        else:
                            console.print(
                                f"[yellow]Warning: Identity result for {node_name} not found, using placeholder[/yellow]"
                            )
                            return value

                elif placeholder.startswith("invite."):
                    # Format: {{invite.node_name_identity.node_name}}
                    parts = placeholder.split(".", 1)
                    if len(parts) == 2:
                        invite_part = parts[1]
                        # Parse the format: node_name_identity.node_name
                        if "_identity." in invite_part:
                            inviter_node, identity_node = invite_part.split(
                                "_identity.", 1
                            )
                            # First resolve the identity to get the actual public key
                            identity_placeholder = f"{{{{identity.{identity_node}}}}}"
                            actual_identity = self._resolve_dynamic_value(
                                identity_placeholder, workflow_results, dynamic_values
                            )

                            # Now construct the invite key using the actual identity
                            invite_key = f"invite_{inviter_node}_{actual_identity}"

                            if invite_key in workflow_results:
                                result = workflow_results[invite_key]
                                # Try to extract invitation data from the result
                                if isinstance(result, dict):
                                    # Handle nested data structure
                                    actual_data = result.get("data", result)
                                    return actual_data.get(
                                        "invitation",
                                        actual_data.get(
                                            "id", actual_data.get("name", value)
                                        ),
                                    )
                                return str(result)
                            else:
                                console.print(
                                    f"[yellow]Warning: Invite result for {invite_key} not found, using placeholder[/yellow]"
                                )
                                return value
                        else:
                            console.print(
                                f"[yellow]Warning: Invalid invite placeholder format {placeholder}, using as-is[/yellow]"
                            )
                            return value

                elif placeholder in dynamic_values:
                    return dynamic_values[placeholder]

                # Handle iteration placeholders
                elif placeholder.startswith("iteration"):
                    # Format: {{iteration}}, {{iteration_index}}, etc.
                    if placeholder in dynamic_values:
                        return str(dynamic_values[placeholder])
                    else:
                        console.print(
                            f"[yellow]Warning: Iteration placeholder {placeholder} not found, using as-is[/yellow]"
                        )
                        return value

                else:
                    console.print(
                        f"[yellow]Warning: Unknown placeholder {placeholder}, using as-is[/yellow]"
                    )
                    return value

            else:
                # Handle embedded placeholders within strings (e.g., "complex_key_{{current_iteration}}_b")
                result = value
                start = 0
                while True:
                    # Find the next placeholder
                    placeholder_start = result.find("{{", start)
                    if placeholder_start == -1:
                        break

                    placeholder_end = result.find("}}", placeholder_start)
                    if placeholder_end == -1:
                        break

                    # Extract the placeholder content
                    placeholder = result[
                        placeholder_start + 2 : placeholder_end
                    ].strip()

                    # Resolve the placeholder
                    resolved_value = self._resolve_single_placeholder(
                        placeholder, workflow_results, dynamic_values
                    )

                    # Replace the placeholder in the result string
                    result = (
                        result[:placeholder_start]
                        + str(resolved_value)
                        + result[placeholder_end + 2 :]
                    )

                    # Update start position for next search
                    start = placeholder_start + len(str(resolved_value))

                return result

        return value

    def _resolve_single_placeholder(
        self,
        placeholder: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> str:
        """Resolve a single placeholder without the {{}} wrapper."""
        # First, check if this is a simple custom output variable name
        if placeholder in dynamic_values:
            return dynamic_values[placeholder]

        # Handle different placeholder types
        if placeholder.startswith("install."):
            # Format: install.node_name
            parts = placeholder.split(".", 1)
            if len(parts) == 2:
                node_name = parts[1]
                # First try to get from dynamic values (captured application ID)
                dynamic_key = f"app_id_{node_name}"
                if dynamic_key in dynamic_values:
                    app_id = dynamic_values[dynamic_key]
                    return app_id

                # Fallback to workflow results
                install_key = f"install_{node_name}"
                if install_key in workflow_results:
                    result = workflow_results[install_key]
                    # Try to extract application ID from the result
                    if isinstance(result, dict):
                        return result.get(
                            "id",
                            result.get(
                                "applicationId", result.get("name", placeholder)
                            ),
                        )
                    return str(result)
                else:
                    console.print(
                        f"[yellow]Warning: Install result for {node_name} not found, using placeholder[/yellow]"
                    )
                    return placeholder

        elif placeholder.startswith("context."):
            # Format: context.node_name or context.node_name.field
            parts = placeholder.split(".", 1)
            if len(parts) == 2:
                node_part = parts[1]
                # Check if there's a field specification (e.g., context.node_name.memberPublicKey)
                if "." in node_part:
                    node_name, field_name = node_part.split(".", 1)
                else:
                    node_name = node_part
                    field_name = None

                if field_name:
                    # For field access (e.g., memberPublicKey), look in workflow_results
                    context_key = f"context_{node_name}"
                    if context_key in workflow_results:
                        result = workflow_results[context_key]
                        # Try to extract specific field from the result
                        if isinstance(result, dict):
                            # Handle nested data structure
                            actual_data = result.get("data", result)
                            return actual_data.get(field_name, placeholder)
                    else:
                        console.print(
                            f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]"
                        )
                        return placeholder
                else:
                    # For context ID access, look in dynamic_values first
                    context_id_key = f"context_id_{node_name}"
                    if context_id_key in dynamic_values:
                        return dynamic_values[context_id_key]

                    # Fallback to workflow_results
                    context_key = f"context_{node_name}"
                    if context_key in workflow_results:
                        result = workflow_results[context_key]
                        # Try to extract context ID from the result
                        if isinstance(result, dict):
                            # Handle nested data structure
                            actual_data = result.get("data", result)
                            return actual_data.get(
                                "id",
                                actual_data.get(
                                    "contextId", actual_data.get("name", placeholder)
                                ),
                            )
                        return str(result)
                    else:
                        console.print(
                            f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]"
                        )
                        return placeholder

        elif placeholder.startswith("identity."):
            # Format: identity.node_name
            parts = placeholder.split(".", 1)
            if len(parts) == 2:
                node_name = parts[1]
                identity_key = f"identity_{node_name}"
                if identity_key in workflow_results:
                    result = workflow_results[identity_key]
                    # Try to extract public key from the result
                    if isinstance(result, dict):
                        # Handle nested data structure
                        actual_data = result.get("data", result)
                        return actual_data.get(
                            "publicKey",
                            actual_data.get("id", actual_data.get("name", placeholder)),
                        )
                    return str(result)
                else:
                    console.print(
                        f"[yellow]Warning: Identity result for {node_name} not found, using placeholder[/yellow]"
                    )
                    return placeholder

        elif placeholder.startswith("invite."):
            # Format: invite.node_name_identity.node_name
            parts = placeholder.split(".", 1)
            if len(parts) == 2:
                invite_part = parts[1]
                # Parse the format: node_name_identity.node_name
                if "_identity." in invite_part:
                    inviter_node, identity_node = invite_part.split("_identity.", 1)
                    # First resolve the identity to get the actual public key
                    identity_placeholder = f"{{{{identity.{identity_node}}}}}"
                    actual_identity = self._resolve_dynamic_value(
                        identity_placeholder, workflow_results, dynamic_values
                    )

                    # Now construct the invite key using the actual identity
                    invite_key = f"invite_{inviter_node}_{actual_identity}"

                    if invite_key in workflow_results:
                        result = workflow_results[invite_key]
                        # Try to extract invitation data from the result
                        if isinstance(result, dict):
                            # Handle nested data structure
                            actual_data = result.get("data", result)
                            return actual_data.get(
                                "invitation",
                                actual_data.get(
                                    "id", actual_data.get("name", placeholder)
                                ),
                            )
                        return str(result)
                    else:
                        console.print(
                            f"[yellow]Warning: Invite result for {invite_key} not found, using placeholder[/yellow]"
                        )
                        return placeholder
                else:
                    console.print(
                        f"[yellow]Warning: Invalid invite placeholder format {placeholder}, using as-is[/yellow]"
                    )
                    return placeholder

        # Handle iteration placeholders
        elif placeholder.startswith("iteration"):
            # Format: iteration, iteration_index, etc.
            if placeholder in dynamic_values:
                return str(dynamic_values[placeholder])
            else:
                console.print(
                    f"[yellow]Warning: Iteration placeholder {placeholder} not found, using as-is[/yellow]"
                )
                return placeholder

        else:
            console.print(
                f"[yellow]Warning: Unknown placeholder {placeholder}, using as-is[/yellow]"
            )
            return placeholder
