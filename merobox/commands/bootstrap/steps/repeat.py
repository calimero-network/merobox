"""
Repeat step executor for executing nested steps multiple times.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.bootstrap.steps.context import CreateContextStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.identity import (
    CreateIdentityStep,
    InviteIdentityStep,
)
from merobox.commands.bootstrap.steps.install import InstallApplicationStep
from merobox.commands.bootstrap.steps.join import JoinContextStep
from merobox.commands.bootstrap.steps.proposals import (
    GetProposalApproversStep,
    GetProposalStep,
    ListProposalsStep,
)
from merobox.commands.bootstrap.steps.script import ScriptStep
from merobox.commands.bootstrap.steps.wait import WaitStep
from merobox.commands.utils import console


class RepeatStep(BaseStep):
    """Execute nested steps multiple times."""

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["count", "steps"]

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate count is an integer
        if not isinstance(self.config.get("count"), int):
            raise ValueError(f"Step '{step_name}': 'count' must be an integer")

        # Validate count is positive
        if self.config.get("count", 0) <= 0:
            raise ValueError(f"Step '{step_name}': 'count' must be a positive integer")

        # Validate steps is a list
        if not isinstance(self.config.get("steps"), list):
            raise ValueError(f"Step '{step_name}': 'steps' must be a list")

        # Validate steps list is not empty
        if not self.config.get("steps"):
            raise ValueError(f"Step '{step_name}': 'steps' list cannot be empty")

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from repeat execution:
        - iteration: Current iteration number (1-based)
        - iteration_index: Current iteration index (0-based)
        - iteration_zero_based: Current iteration index (0-based, alias)
        - iteration_one_based: Current iteration number (1-based, alias)
        - total_iterations: Total number of iterations
        - current_step: Current step being executed
        - step_count: Total number of nested steps
        """
        return [
            ("iteration", "iteration", "Current iteration number (1-based)"),
            ("iteration_index", "iteration_index", "Current iteration index (0-based)"),
            (
                "iteration_zero_based",
                "iteration_zero_based",
                "Current iteration index (0-based, alias)",
            ),
            (
                "iteration_one_based",
                "iteration_one_based",
                "Current iteration number (1-based, alias)",
            ),
            ("total_iterations", "total_iterations", "Total number of iterations"),
            ("current_step", "current_step", "Current step being executed"),
            ("step_count", "step_count", "Total number of nested steps"),
        ]

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

        repeat_count = self.config.get("count", 1)
        nested_steps = self.config.get("steps", [])

        # Store reference to original dynamic_values for propagating changes
        original_dynamic_values = dynamic_values

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Repeat step export configuration validation failed[/yellow]"
            )

        if not nested_steps:
            console.print("[yellow]No nested steps specified for repeat[/yellow]")
            return True

        console.print(
            f"[cyan]🔄 Executing {len(nested_steps)} nested steps {repeat_count} times...[/cyan]"
        )

        # Export repeat configuration variables to global scope
        global_variables["total_iterations"] = repeat_count
        global_variables["step_count"] = len(nested_steps)
        # Also update dynamic_values for backward compatibility
        dynamic_values["total_iterations"] = repeat_count
        dynamic_values["step_count"] = len(nested_steps)
        console.print(
            f"[blue]📝 Exported repeat configuration: total_iterations={repeat_count}, step_count={len(nested_steps)}[/blue]"
        )

        for iteration in range(repeat_count):
            console.print(
                f"\n[bold blue]📋 Iteration {iteration + 1}/{repeat_count}[/bold blue]"
            )

            # Create iteration-specific local variables (scoped to this iteration)
            iteration_local_variables = {
                "iteration": iteration + 1,
                "iteration_index": iteration,
                "iteration_zero_based": iteration,
                "iteration_one_based": iteration + 1,
            }

            # Create iteration-specific dynamic values for backward compatibility
            iteration_dynamic_values = dynamic_values.copy()
            iteration_dynamic_values.update(iteration_local_variables)

            # Process custom outputs configuration for this iteration
            self._export_iteration_variables(
                iteration + 1,
                iteration_dynamic_values,
                global_variables,
                iteration_local_variables,
                original_dynamic_values,  # Pass original for backward compat
            )

            # Execute each nested step in sequence
            for step_idx, step in enumerate(nested_steps):
                step_type = step.get("type")
                nested_step_name = step.get("name", f"Nested Step {step_idx + 1}")

                # Update current step information in local variables
                iteration_local_variables["current_step"] = nested_step_name
                iteration_local_variables["current_step_index"] = step_idx + 1
                # Also update dynamic_values for backward compatibility
                iteration_dynamic_values["current_step"] = nested_step_name
                iteration_dynamic_values["current_step_index"] = step_idx + 1

                console.print(
                    f"  [cyan]Executing {nested_step_name} ({step_type})...[/cyan]"
                )

                try:
                    # Process inline variables for nested step before execution
                    if "variables" in step:
                        self._process_nested_inline_variables(
                            step,
                            workflow_results,
                            iteration_dynamic_values,
                            global_variables,
                            iteration_local_variables,
                            original_dynamic_values,  # Pass original for backward compat
                        )

                    # Create appropriate step executor for the nested step
                    step_executor = self._create_nested_step_executor(step_type, step)
                    if not step_executor:
                        console.print(
                            f"[red]Unknown nested step type: {step_type}[/red]"
                        )
                        return False

                    # Execute the nested step with both variable scopes
                    success = await step_executor.execute(
                        workflow_results,
                        iteration_dynamic_values,
                        global_variables,
                        iteration_local_variables,
                    )

                    if not success:
                        console.print(
                            f"[red]❌ Nested step '{nested_step_name}' failed in iteration {iteration + 1}[/red]"
                        )
                        return False

                    console.print(
                        f"  [green]✓ Nested step '{nested_step_name}' completed in iteration {iteration + 1}[/green]"
                    )

                except Exception as e:
                    console.print(
                        f"[red]❌ Nested step '{nested_step_name}' failed with error in iteration {iteration + 1}: {str(e)}[/red]"
                    )
                    return False

        console.print(
            f"[green]✓ All {repeat_count} iterations completed successfully[/green]"
        )
        return True

    def _process_nested_inline_variables(
        self,
        step_config: dict[str, Any],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        global_variables: dict[str, Any],
        local_variables: dict[str, Any],
        original_dynamic_values: dict[str, Any],
    ) -> None:
        """Process inline variables field in nested step configuration.

        Args:
            step_config: Step configuration
            workflow_results: Workflow results
            dynamic_values: Iteration-specific dynamic values (copy)
            global_variables: Global variables
            local_variables: Local variables
            original_dynamic_values: Original dynamic_values to update for backward compatibility
        """
        variables = step_config.get("variables", {})
        if not isinstance(variables, dict):
            return

        for var_name, var_value in variables.items():
            # Check if this is a scoped variable (local: or global: prefix)
            if var_name.startswith("local:"):
                actual_name = var_name[6:]  # Remove "local:" prefix
                resolved_value = self._resolve_dynamic_value(
                    str(var_value) if not isinstance(var_value, str) else var_value,
                    workflow_results,
                    dynamic_values,
                    global_variables,
                    local_variables,
                )
                local_variables[actual_name] = resolved_value
                console.print(
                    f"    [blue]📝 Set local variable '{actual_name}' = {resolved_value}[/blue]"
                )
            elif var_name.startswith("global:"):
                actual_name = var_name[7:]  # Remove "global:" prefix
                resolved_value = self._resolve_dynamic_value(
                    str(var_value) if not isinstance(var_value, str) else var_value,
                    workflow_results,
                    dynamic_values,
                    global_variables,
                    local_variables,
                )
                global_variables[actual_name] = resolved_value
                # Update ORIGINAL
                original_dynamic_values[actual_name] = resolved_value
                console.print(
                    f"    [blue]📝 Set global variable '{actual_name}' = {resolved_value}[/blue]"
                )
            else:
                # Default: set as global variable
                resolved_value = self._resolve_dynamic_value(
                    str(var_value) if not isinstance(var_value, str) else var_value,
                    workflow_results,
                    dynamic_values,
                    global_variables,
                    local_variables,
                )
                global_variables[var_name] = resolved_value
                # Update ORIGINAL
                original_dynamic_values[var_name] = resolved_value
                console.print(
                    f"    [blue]📝 Set global variable '{var_name}' = {resolved_value}[/blue]"
                )

    def _export_iteration_variables(
        self,
        iteration: int,
        dynamic_values: dict[str, Any],
        global_variables: dict[str, Any],
        local_variables: dict[str, Any],
        original_dynamic_values: dict[str, Any],
    ) -> None:
        """Export iteration variables based on custom outputs configuration.

        Args:
            iteration: Current iteration number
            dynamic_values: Iteration-specific dynamic values (copy)
            global_variables: Global variables
            local_variables: Local variables
            original_dynamic_values: Original dynamic_values to update for backward compatibility
        """
        outputs_config = self.config.get("outputs", {})
        if not outputs_config:
            return

        console.print(
            f"[blue]📝 Processing custom outputs for iteration {iteration}...[/blue]"
        )

        for export_name, export_config in outputs_config.items():
            if isinstance(export_config, str):
                # Simple field assignment (e.g., current_iteration: iteration)
                source_field = export_config
                # Check local variables first, then dynamic values
                if source_field in local_variables:
                    source_value = local_variables[source_field]
                    global_variables[export_name] = source_value
                    original_dynamic_values[export_name] = (
                        source_value  # Update ORIGINAL
                    )
                    console.print(
                        f"  📝 Custom export: {source_field} → {export_name}: {source_value}"
                    )
                elif source_field in dynamic_values:
                    source_value = dynamic_values[source_field]
                    global_variables[export_name] = source_value
                    original_dynamic_values[export_name] = (
                        source_value  # Update ORIGINAL
                    )
                    console.print(
                        f"  📝 Custom export: {source_field} → {export_name}: {source_value}"
                    )
                else:
                    console.print(
                        f"[yellow]Warning: Source field {source_field} not found in variables[/yellow]"
                    )
            elif isinstance(export_config, dict):
                # Complex field assignment with node name replacement
                source_field = export_config.get("field")
                target_template = export_config.get("target")
                if source_field and target_template and "node_name" in target_template:
                    # Check local variables first, then dynamic values
                    if source_field in local_variables:
                        source_value = local_variables[source_field]
                        global_variables[export_name] = source_value
                        original_dynamic_values[export_name] = (
                            source_value  # Update ORIGINAL
                        )
                        console.print(
                            f"  📝 Custom export: {source_field} → {export_name}: {source_value}"
                        )
                    elif source_field in dynamic_values:
                        source_value = dynamic_values[source_field]
                        global_variables[export_name] = source_value
                        original_dynamic_values[export_name] = (
                            source_value  # Update ORIGINAL
                        )
                        console.print(
                            f"  📝 Custom export: {source_field} → {export_name}: {source_value}"
                        )
                    else:
                        console.print(
                            f"[yellow]Warning: Source field {source_field} not found in variables[/yellow]"
                        )

    def _create_nested_step_executor(self, step_type: str, step_config: dict[str, Any]):
        """Create a nested step executor based on the step type."""
        if step_type == "install_application":
            return InstallApplicationStep(step_config, manager=self.manager)
        elif step_type == "create_context":
            return CreateContextStep(step_config, manager=self.manager)
        elif step_type == "create_identity":
            return CreateIdentityStep(step_config, manager=self.manager)
        elif step_type == "invite_identity":
            return InviteIdentityStep(step_config, manager=self.manager)
        elif step_type == "join_context":
            return JoinContextStep(step_config, manager=self.manager)
        elif step_type == "invite_open":
            from merobox.commands.bootstrap.steps.invite_open import InviteOpenStep

            return InviteOpenStep(step_config, manager=self.manager)
        elif step_type == "join_open":
            from merobox.commands.bootstrap.steps.join_open import JoinOpenStep

            return JoinOpenStep(step_config, manager=self.manager)
        elif step_type == "call":
            return ExecuteStep(step_config, manager=self.manager)
        elif step_type == "wait":
            return WaitStep(step_config, manager=self.manager)
        elif step_type == "script":
            return ScriptStep(step_config, manager=self.manager)
        elif step_type == "get_proposal":
            return GetProposalStep(step_config, manager=self.manager)
        elif step_type == "list_proposals":
            return ListProposalsStep(step_config, manager=self.manager)
        elif step_type == "get_proposal_approvers":
            return GetProposalApproversStep(step_config, manager=self.manager)
        else:
            console.print(f"[red]Unknown nested step type: {step_type}[/red]")
            return None
