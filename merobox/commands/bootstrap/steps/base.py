"""
Base step class for all workflow steps.
"""

from typing import Any

from merobox.commands.utils import console


class BaseStep:
    """Base class for all workflow steps."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
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
        actual_data = (
            response_data.get("data", response_data)
            if isinstance(response_data, dict)
            else response_data
        )

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
    ) -> None:
        """
        Export variables based on custom outputs configuration specified by the user.

        Args:
            response_data: The API response data
            node_name: The name of the node
            dynamic_values: The dynamic values dictionary to update
        """
        outputs_config = self.config.get("outputs", {})
        if not outputs_config:
            return

        # Extract the actual data (handle nested structures)
        actual_data = (
            response_data.get("data", response_data)
            if isinstance(response_data, dict)
            else response_data
        )

        def _try_parse_json(value: Any) -> Any:
            """If value is a JSON string, parse and return the object; otherwise return as-is."""
            if isinstance(value, str):
                s = value.strip()
                if (s.startswith("{") and s.endswith("}")) or (
                    s.startswith("[") and s.endswith("]")
                ):
                    try:
                        import json

                        return json.loads(s)
                    except Exception:
                        return value
            return value

        def _extract_path(obj: Any, path: str) -> Any:
            """Extract a dotted path from a dict-like object. If intermediate is JSON string, parse it."""
            if not isinstance(path, str) or not path:
                return None
            current = obj
            for segment in path.split("."):
                current = _try_parse_json(current)
                if isinstance(current, dict) and segment in current:
                    current = current[segment]
                else:
                    return None
            return current

        console.print(
            f"[cyan]🔧 Processing custom outputs configuration for {node_name}:[/cyan]"
        )

        for exported_variable, assigned_var in outputs_config.items():
            # Handle different types of assigned_var
            if isinstance(assigned_var, str):
                # Support dotted paths (e.g., "result.value").
                value = (
                    _extract_path(actual_data, assigned_var)
                    if "." in assigned_var
                    else (
                        actual_data.get(assigned_var)
                        if isinstance(actual_data, dict)
                        else None
                    )
                )
                if value is None and isinstance(actual_data, dict):
                    console.print(
                        f"[yellow]⚠️  Custom export failed: field '{assigned_var}' not found in response[/yellow]"
                    )
                    console.print(
                        f"[yellow]   Available fields: {list(actual_data.keys()) if isinstance(actual_data, dict) else 'N/A'}[/yellow]"
                    )
                else:
                    target_key = exported_variable
                    dynamic_values[target_key] = value
                    console.print(
                        f"[blue]📝 Custom export: {assigned_var} → {target_key}: {value}[/blue]"
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
                        base_value = _try_parse_json(base_value)
                    # Optional nested path within the base value: path: a.b.c
                    if isinstance(assigned_var.get("path"), str):
                        base_value = _extract_path(base_value, assigned_var["path"])

                    if base_value is None and isinstance(actual_data, dict):
                        console.print(
                            f"[yellow]⚠️  Custom export failed: field '{field_name}' not found in response or path unresolved[/yellow]"
                        )
                        console.print(
                            f"[yellow]   Available fields: {list(actual_data.keys()) if isinstance(actual_data, dict) else 'N/A'}[/yellow]"
                        )
                    else:
                        # Handle node name replacement in the target key
                        target_key = assigned_var.get("target", exported_variable)
                        target_key = target_key.replace("{node_name}", node_name)
                        dynamic_values[target_key] = base_value
                        console.print(
                            f"[blue]📝 Custom export: {field_name}{'.' + assigned_var.get('path') if assigned_var.get('path') else ''} → {target_key}: {base_value}[/blue]"
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
    ) -> None:
        """
        Main export method that handles only custom outputs (explicit exports).

        Args:
            response_data: The API response data
            node_name: The name of the node
            dynamic_values: The dynamic values dictionary to update
        """
        # Only handle custom outputs - no automatic exports
        if "outputs" in self.config:
            self._export_custom_outputs(response_data, node_name, dynamic_values)
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

    def _resolve_dynamic_value(
        self,
        value: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> str:
        """Resolve dynamic values using placeholders and captured results."""
        if not isinstance(value, str):
            return value

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
