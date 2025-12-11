"""
Run Workflow step - Execute child workflows from parent workflows.
"""

import asyncio
import os
from typing import Any

from merobox.commands.bootstrap.config import load_workflow_config
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class RunWorkflowStep(BaseStep):
    """Execute a child workflow from a parent workflow."""

    def _get_required_fields(self) -> list[str]:
        """Define which fields are required for this step."""
        return ["workflow_path"]

    def _validate_field_types(self) -> None:
        """Validate that fields have the correct types."""
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("workflow_path"), str):
            raise ValueError(f"Step '{step_name}': 'workflow_path' must be a string")

        if "inputs" in self.config and not isinstance(self.config.get("inputs"), dict):
            raise ValueError(f"Step '{step_name}': 'inputs' must be a dictionary")

        if "outputs" in self.config and not isinstance(
            self.config.get("outputs"), dict
        ):
            raise ValueError(f"Step '{step_name}': 'outputs' must be a dictionary")

        if "inherit_variables" in self.config and not isinstance(
            self.config.get("inherit_variables"), bool
        ):
            raise ValueError(
                f"Step '{step_name}': 'inherit_variables' must be a boolean"
            )

    def _get_exportable_variables(self):
        """Define which variables this step can export."""
        # Exportable variables are defined dynamically based on outputs config
        return []

    async def execute(
        self,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        global_variables: dict[str, Any] = None,
        local_variables: dict[str, Any] = None,
    ) -> bool:
        # Initialize scope variables if not provided
        if global_variables is None:
            global_variables = {}
        if local_variables is None:
            local_variables = {}

        workflow_path = self.config["workflow_path"]
        inputs = self.config.get("inputs", {})
        outputs_config = self.config.get("outputs", {})
        inherit_variables = self.config.get("inherit_variables", False)
        on_failure = self.config.get("on_failure", {})
        continue_on_failure = on_failure.get("continue", False)

        # Get parent executor (passed during step creation)
        parent_executor = self.parent_executor

        # Check nesting depth
        current_nesting = 0
        if parent_executor:
            current_nesting_raw = getattr(parent_executor, "nesting_level", 0) or 0
            try:
                current_nesting = int(current_nesting_raw)
            except (ValueError, TypeError):
                current_nesting = 0

        max_nesting = 5  # Default
        if parent_executor:
            max_nesting_raw = getattr(parent_executor, "max_nesting_depth", 5) or 5
            try:
                max_nesting = int(max_nesting_raw)
            except (ValueError, TypeError):
                max_nesting = 5

        if current_nesting >= max_nesting:
            console.print(
                f"[red]❌ Maximum workflow nesting depth ({max_nesting}) exceeded[/red]"
            )
            return False

        # Resolve workflow path (support relative paths)
        if not os.path.isabs(workflow_path):
            # Resolve relative to current working directory
            workflow_path = os.path.abspath(workflow_path)

        if not os.path.exists(workflow_path):
            console.print(f"[red]❌ Workflow file not found: {workflow_path}[/red]")
            return False

        console.print(f"[cyan]🔄 Loading child workflow: {workflow_path}[/cyan]")

        try:
            # Load child workflow configuration
            child_config = load_workflow_config(workflow_path)

            # Prepare input variables for child workflow
            child_variables = {}

            # Inherit parent variables if requested
            if inherit_variables:
                child_variables.update(global_variables)
                console.print(
                    f"[blue]📝 Inherited {len(global_variables)} variables from parent workflow[/blue]"
                )

            # Add/override with explicit inputs
            for input_name, input_value in inputs.items():
                # Only resolve if it's a string with placeholders, otherwise preserve type
                if isinstance(input_value, str):
                    resolved_value = self._resolve_dynamic_value(
                        input_value,
                        workflow_results,
                        dynamic_values,
                        global_variables,
                        local_variables,
                    )
                else:
                    # Preserve original type (int, float, bool, None)
                    resolved_value = input_value
                child_variables[input_name] = resolved_value
                console.print(
                    f"[blue]📝 Input variable '{input_name}' = {resolved_value}[/blue]"
                )

            # Merge input variables with child workflow's top-level variables
            if "variables" not in child_config:
                child_config["variables"] = {}
            child_config["variables"].update(child_variables)

            # Create child workflow executor
            from merobox.commands.bootstrap.run.executor import WorkflowExecutor

            # Inherit runtime options from parent executor (CLI flags, etc.)
            # This ensures child workflows run with the same settings as parent
            parent_image = (
                getattr(parent_executor, "image", None) if parent_executor else None
            )
            parent_auth_service = (
                getattr(parent_executor, "auth_service", False)
                if parent_executor
                else False
            )
            parent_auth_image = (
                getattr(parent_executor, "auth_image", None)
                if parent_executor
                else None
            )
            parent_auth_use_cached = (
                getattr(parent_executor, "auth_use_cached", False)
                if parent_executor
                else False
            )
            parent_webui_use_cached = (
                getattr(parent_executor, "webui_use_cached", False)
                if parent_executor
                else False
            )
            parent_log_level = (
                getattr(parent_executor, "log_level", "debug")
                if parent_executor
                else "debug"
            )
            parent_rust_backtrace = (
                getattr(parent_executor, "rust_backtrace", "0")
                if parent_executor
                else "0"
            )
            parent_mock_relayer = (
                getattr(parent_executor, "mock_relayer", False)
                if parent_executor
                else False
            )
            parent_e2e_mode = (
                getattr(parent_executor, "e2e_mode", False)
                if parent_executor
                else False
            )
            parent_near_devnet = (
                getattr(parent_executor, "near_devnet", False)
                if parent_executor
                else False
            )
            parent_contracts_dir = (
                getattr(parent_executor, "contracts_dir", None)
                if parent_executor
                else None
            )

            child_executor = WorkflowExecutor(
                config=child_config,
                manager=self.manager,
                image=parent_image,
                auth_service=parent_auth_service,
                auth_image=parent_auth_image,
                auth_use_cached=parent_auth_use_cached,
                webui_use_cached=parent_webui_use_cached,
                log_level=parent_log_level,
                rust_backtrace=parent_rust_backtrace,
                mock_relayer=parent_mock_relayer,
                e2e_mode=parent_e2e_mode,
                parent_executor=parent_executor,
                nesting_level=current_nesting + 1,
                near_devnet=parent_near_devnet,
                contracts_dir=parent_contracts_dir,
            )

            # Determine timeout: child config overrides parent, otherwise use parent or default
            timeout_seconds = child_config.get("workflow_timeout")
            if timeout_seconds is None and parent_executor:
                timeout_raw = getattr(parent_executor, "workflow_timeout", 3600) or 3600
                try:
                    timeout_seconds = int(timeout_raw)
                except (ValueError, TypeError):
                    timeout_seconds = 3600
            elif timeout_seconds is None:
                timeout_seconds = 3600  # Default 1 hour
            else:
                # Ensure timeout_seconds from child config is an integer (YAML might parse as string)
                try:
                    timeout_seconds = int(timeout_seconds)
                except (ValueError, TypeError):
                    console.print(
                        f"[yellow]⚠️  Invalid workflow_timeout value '{timeout_seconds}', using default 3600s[/yellow]"
                    )
                    timeout_seconds = 3600

            console.print(
                f"[cyan]🚀 Executing child workflow (nesting level {current_nesting + 1}, timeout: {timeout_seconds}s)...[/cyan]"
            )

            # Execute child workflow with timeout enforcement
            try:
                success = await asyncio.wait_for(
                    child_executor.execute_workflow(), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                console.print(
                    f"[red]❌ Child workflow timed out after {timeout_seconds} seconds: {workflow_path}[/red]"
                )
                if continue_on_failure:
                    console.print(
                        "[yellow]⚠️  Continuing despite timeout (continue_on_failure=true)[/yellow]"
                    )
                    # Set failure variables if configured
                    if "set_variables" in on_failure:
                        for var_name, var_value in on_failure["set_variables"].items():
                            global_variables[var_name] = var_value
                            dynamic_values[var_name] = var_value
                            console.print(
                                f"[blue]📝 Set failure variable '{var_name}' = {var_value}[/blue]"
                            )
                    return True
                return False

            if not success:
                console.print(f"[red]❌ Child workflow failed: {workflow_path}[/red]")

                # Handle failure based on configuration
                if continue_on_failure:
                    console.print(
                        "[yellow]⚠️  Continuing despite child workflow failure (continue_on_failure=true)[/yellow]"
                    )
                    # Set failure variables if configured
                    if "set_variables" in on_failure:
                        for var_name, var_value in on_failure["set_variables"].items():
                            global_variables[var_name] = var_value
                            dynamic_values[var_name] = var_value
                            console.print(
                                f"[blue]📝 Set failure variable '{var_name}' = {var_value}[/blue]"
                            )
                    return True
                else:
                    return False

            console.print(
                f"[green]✓ Child workflow completed successfully: {workflow_path}[/green]"
            )

            # Export outputs from child workflow to parent
            # Collect outputs first, then write atomically to avoid race conditions
            if outputs_config:
                console.print(
                    f"[blue]📝 Exporting {len(outputs_config)} outputs from child workflow...[/blue]"
                )

                for parent_var_name, child_var_name in outputs_config.items():
                    # Check if this is a scoped variable (global: prefix)
                    if parent_var_name.startswith("global:"):
                        actual_parent_name = parent_var_name[
                            7:
                        ]  # Remove "global:" prefix
                        target_scope = "global"
                    else:
                        actual_parent_name = parent_var_name
                        target_scope = "global"  # Default to global for outputs

                    # Get value from child workflow's variables (check multiple sources)
                    child_value = None
                    found = False

                    # Check global_variables first
                    if child_var_name in child_executor.global_variables:
                        child_value = child_executor.global_variables[child_var_name]
                        found = True
                    # Then check dynamic_values (where step outputs go)
                    elif child_var_name in child_executor.dynamic_values:
                        child_value = child_executor.dynamic_values[child_var_name]
                        found = True

                    if found:
                        # Write directly - safe because asyncio is single-threaded
                        # Each await point serializes the writes
                        if target_scope == "global":
                            global_variables[actual_parent_name] = child_value
                            dynamic_values[actual_parent_name] = (
                                child_value  # Backward compatibility
                            )

                        console.print(
                            f"[blue]📝 Exported '{child_var_name}' → '{actual_parent_name}': {child_value}[/blue]"
                        )
                    else:
                        console.print(
                            f"[yellow]⚠️  Child variable '{child_var_name}' not found in child workflow[/yellow]"
                        )

            return True

        except Exception as e:
            console.print(f"[red]❌ Failed to execute child workflow: {str(e)}[/red]")
            if continue_on_failure:
                console.print(
                    "[yellow]⚠️  Continuing despite error (continue_on_failure=true)[/yellow]"
                )
                # Set failure variables if configured
                if "set_variables" in on_failure:
                    for var_name, var_value in on_failure["set_variables"].items():
                        global_variables[var_name] = var_value
                        dynamic_values[var_name] = var_value
                        console.print(
                            f"[blue]📝 Set failure variable '{var_name}' = {var_value}[/blue]"
                        )
                return True
            return False
