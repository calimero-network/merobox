"""
Repeat step executor for executing nested steps multiple times.
"""

import time
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
        - duration_seconds: Total execution time in seconds (float)
        - duration_ms: Total execution time in milliseconds (float)
        - duration_ns: Total execution time in nanoseconds (int)
        - throughput_ops_per_sec: Operations per second (count / duration_seconds)
        - avg_time_per_op_ms: Average time per operation in milliseconds
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
            (
                "duration_seconds",
                "duration_seconds",
                "Total execution time in seconds (float)",
            ),
            (
                "duration_ms",
                "duration_ms",
                "Total execution time in milliseconds (float)",
            ),
            ("duration_ns", "duration_ns", "Total execution time in nanoseconds (int)"),
            (
                "throughput_ops_per_sec",
                "throughput_ops_per_sec",
                "Operations per second (count / duration_seconds)",
            ),
            (
                "avg_time_per_op_ms",
                "avg_time_per_op_ms",
                "Average time per operation in milliseconds",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        repeat_count = self.config.get("count", 1)
        nested_steps = self.config.get("steps", [])

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

        # Export repeat configuration variables
        dynamic_values["total_iterations"] = repeat_count
        dynamic_values["step_count"] = len(nested_steps)
        console.print(
            f"[blue]📝 Exported repeat configuration: total_iterations={repeat_count}, step_count={len(nested_steps)}[/blue]"
        )

        # Track timing for throughput calculation
        start_time = time.perf_counter()  # Use perf_counter for better precision

        for iteration in range(repeat_count):
            console.print(
                f"\n[bold blue]📋 Iteration {iteration + 1}/{repeat_count}[/bold blue]"
            )

            # Create iteration-specific dynamic values
            iteration_dynamic_values = dynamic_values.copy()
            iteration_dynamic_values.update(
                {
                    "iteration": iteration + 1,
                    "iteration_index": iteration,
                    "iteration_zero_based": iteration,
                    "iteration_one_based": iteration + 1,
                }
            )

            # Process custom outputs configuration for this iteration
            self._export_iteration_variables(iteration + 1, iteration_dynamic_values)

            # Execute each nested step in sequence
            for step_idx, step in enumerate(nested_steps):
                step_type = step.get("type")
                nested_step_name = step.get("name", f"Nested Step {step_idx + 1}")

                # Update current step information
                iteration_dynamic_values["current_step"] = nested_step_name
                iteration_dynamic_values["current_step_index"] = step_idx + 1

                console.print(
                    f"  [cyan]Executing {nested_step_name} ({step_type})...[/cyan]"
                )

                try:
                    # Create appropriate step executor for the nested step
                    step_executor = self._create_nested_step_executor(step_type, step)
                    if not step_executor:
                        console.print(
                            f"[red]Unknown nested step type: {step_type}[/red]"
                        )
                        return False

                    # Execute the nested step with iteration-specific dynamic values
                    success = await step_executor.execute(
                        workflow_results, iteration_dynamic_values
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

        # Calculate duration and throughput
        end_time = time.perf_counter()
        duration_seconds = end_time - start_time
        duration_ms = duration_seconds * 1000.0
        duration_ns = int(duration_seconds * 1_000_000_000)

        if duration_seconds > 0:
            throughput_ops_per_sec = repeat_count / duration_seconds
            avg_time_per_op_ms = duration_ms / repeat_count
        else:
            throughput_ops_per_sec = 0.0
            avg_time_per_op_ms = 0.0

        # Export timing metrics to dynamic_values
        dynamic_values["duration_seconds"] = round(duration_seconds, 6)
        dynamic_values["duration_ms"] = round(duration_ms, 3)
        dynamic_values["duration_ns"] = duration_ns
        dynamic_values["throughput_ops_per_sec"] = round(throughput_ops_per_sec, 2)
        dynamic_values["avg_time_per_op_ms"] = round(avg_time_per_op_ms, 3)

        # Export timing variables based on custom outputs configuration
        self._export_timing_variables(dynamic_values)

        # Log timing information
        console.print(
            f"[green]✓ All {repeat_count} iterations completed successfully[/green]"
        )
        console.print("[blue]⏱️  Timing Metrics:[/blue]")
        console.print(
            f"  [cyan]Duration:[/cyan] {duration_seconds:.3f} seconds ({duration_ms:.2f} ms, {duration_ns:,} ns)"
        )
        console.print(
            f"  [cyan]Throughput:[/cyan] {throughput_ops_per_sec:.2f} operations/second"
        )
        console.print(
            f"  [cyan]Average Latency:[/cyan] {avg_time_per_op_ms:.3f} milliseconds/operation"
        )

        return True

    def _export_iteration_variables(
        self, iteration: int, dynamic_values: dict[str, Any]
    ) -> None:
        """Export iteration variables based on custom outputs configuration."""
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
                if source_field in dynamic_values:
                    source_value = dynamic_values[source_field]
                    dynamic_values[export_name] = source_value
                    console.print(
                        f"  📝 Custom export: {source_field} → {export_name}: {source_value}"
                    )
                else:
                    console.print(
                        f"[yellow]Warning: Source field {source_field} not found in dynamic values[/yellow]"
                    )
            elif isinstance(export_config, dict):
                # Complex field assignment with node name replacement
                source_field = export_config.get("field")
                target_template = export_config.get("target")
                if source_field and target_template and "node_name" in target_template:
                    if source_field in dynamic_values:
                        source_value = dynamic_values[source_field]
                        # For repeat steps, we don't have node names, so just use the source value
                        dynamic_values[export_name] = source_value
                        console.print(
                            f"  📝 Custom export: {source_field} → {export_name}: {source_value}"
                        )
                    else:
                        console.print(
                            f"[yellow]Warning: Source field {source_field} not found in dynamic values[/yellow]"
                        )

    def _export_timing_variables(self, dynamic_values: dict[str, Any]) -> None:
        """Export variables based on custom outputs configuration."""
        outputs_config = self.config.get("outputs", {})
        if not outputs_config:
            return

        timing_variables = {
            "duration_seconds",
            "duration_ms",
            "duration_ns",
            "throughput_ops_per_sec",
            "avg_time_per_op_ms",
        }

        for export_name, export_config in outputs_config.items():
            if isinstance(export_config, str):
                # Simple field assignment (e.g., duration: duration_seconds)
                source_field = export_config
                if source_field in dynamic_values:
                    source_value = dynamic_values[source_field]
                    dynamic_values[export_name] = source_value
                    # Use "Timing export" label for known timing variables, "Export" for others
                    label = (
                        "Timing export"
                        if source_field in timing_variables
                        else "Export"
                    )
                    console.print(
                        f"  📝 {label}: {source_field} → {export_name}: {source_value}"
                    )
                else:
                    console.print(
                        f"[yellow]⚠️  Warning: Source field '{source_field}' not found in dynamic values for export '{export_name}'[/yellow]"
                    )
            elif isinstance(export_config, dict):
                # Complex field assignment with node name replacement
                source_field = export_config.get("field")
                target_template = export_config.get("target")
                if source_field and target_template and "node_name" in target_template:
                    if source_field in dynamic_values:
                        source_value = dynamic_values[source_field]
                        # For repeat steps, we don't have node names, so just use the source value
                        dynamic_values[export_name] = source_value
                        label = (
                            "Timing export"
                            if source_field in timing_variables
                            else "Export"
                        )
                        console.print(
                            f"  📝 {label}: {source_field} → {export_name}: {source_value}"
                        )
                    else:
                        console.print(
                            f"[yellow]⚠️  Warning: Source field '{source_field}' not found in dynamic values for export '{export_name}'[/yellow]"
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
        elif step_type == "wait_for_sync":
            from merobox.commands.bootstrap.steps.wait_for_sync import WaitForSyncStep

            return WaitForSyncStep(step_config, manager=self.manager)
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
