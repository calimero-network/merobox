"""
Run Workflows step - Execute multiple workflows in parallel or sequential mode.
"""

import asyncio
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.bootstrap.steps.run_workflow import RunWorkflowStep
from merobox.commands.utils import console


class RunWorkflowsStep(BaseStep):
    """Execute multiple workflows in parallel or sequential mode."""

    def _get_required_fields(self) -> list[str]:
        """Define which fields are required for this step."""
        return ["workflows"]

    def _validate_field_types(self) -> None:
        """Validate that fields have the correct types."""
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        if not isinstance(self.config.get("workflows"), list):
            raise ValueError(f"Step '{step_name}': 'workflows' must be a list")

        mode = self.config.get("mode", "parallel")
        if mode not in ["parallel", "sequential"]:
            raise ValueError(
                f"Step '{step_name}': 'mode' must be either 'parallel' or 'sequential'"
            )

        if "fail_fast" in self.config and not isinstance(
            self.config.get("fail_fast"), bool
        ):
            raise ValueError(f"Step '{step_name}': 'fail_fast' must be a boolean")

    def _get_exportable_variables(self):
        """Define which variables this step can export."""
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

        workflows = self.config.get("workflows", [])
        mode = self.config.get("mode", "parallel")
        fail_fast = self.config.get("fail_fast", True)

        if not workflows:
            console.print("[yellow]No workflows specified[/yellow]")
            return True

        console.print(
            f"[cyan]🔄 Executing {len(workflows)} workflows in {mode} mode...[/cyan]"
        )

        if mode == "parallel":
            return await self._execute_parallel(
                workflows,
                workflow_results,
                dynamic_values,
                global_variables,
                local_variables,
                fail_fast,
            )
        else:  # sequential
            return await self._execute_sequential(
                workflows,
                workflow_results,
                dynamic_values,
                global_variables,
                local_variables,
                fail_fast,
            )

    async def _execute_parallel(
        self,
        workflows: list[dict[str, Any]],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        global_variables: dict[str, Any],
        local_variables: dict[str, Any],
        fail_fast: bool,
    ) -> bool:
        """Execute workflows in parallel."""
        console.print(
            f"[cyan]🚀 Starting {len(workflows)} workflows in parallel...[/cyan]"
        )

        # Check for duplicate output names across workflows
        all_output_names = set()
        for workflow_config in workflows:
            outputs = workflow_config.get("outputs", {})
            for output_name in outputs.keys():
                if output_name in all_output_names:
                    console.print(
                        f"[yellow]⚠️  Warning: Multiple workflows export to '{output_name}' - last one wins[/yellow]"
                    )
                all_output_names.add(output_name)

        # Create tasks for all workflows
        tasks = []
        for idx, workflow_config in enumerate(workflows, 1):
            task = self._execute_single_workflow(
                idx,
                workflow_config,
                workflow_results,
                dynamic_values,
                global_variables,
                local_variables,
            )
            tasks.append(task)

        # Execute all workflows concurrently
        # Note: asyncio is single-threaded, so dictionary writes are atomic
        # However, if multiple workflows export to the same variable, last writer wins
        if fail_fast:
            # Stop on first failure
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check for failures
                success_count = 0
                failure_count = 0

                for idx, result in enumerate(results, 1):
                    if isinstance(result, Exception):
                        console.print(
                            f"[red]❌ Workflow {idx} failed with exception: {str(result)}[/red]"
                        )
                        failure_count += 1
                        # Set count variables before returning
                        global_variables["workflows_success_count"] = success_count
                        global_variables["workflows_failure_count"] = failure_count
                        global_variables["workflows_total_count"] = len(workflows)
                        dynamic_values["workflows_success_count"] = success_count
                        dynamic_values["workflows_failure_count"] = failure_count
                        dynamic_values["workflows_total_count"] = len(workflows)
                        return False
                    elif not result:
                        console.print(f"[red]❌ Workflow {idx} failed[/red]")
                        failure_count += 1
                        # Set count variables before returning
                        global_variables["workflows_success_count"] = success_count
                        global_variables["workflows_failure_count"] = failure_count
                        global_variables["workflows_total_count"] = len(workflows)
                        dynamic_values["workflows_success_count"] = success_count
                        dynamic_values["workflows_failure_count"] = failure_count
                        dynamic_values["workflows_total_count"] = len(workflows)
                        return False
                    else:
                        success_count += 1

                console.print(
                    f"[green]✓ All {len(workflows)} workflows completed successfully[/green]"
                )

                # Set count variables for successful completion
                global_variables["workflows_success_count"] = success_count
                global_variables["workflows_failure_count"] = failure_count
                global_variables["workflows_total_count"] = len(workflows)
                # Also set in dynamic_values for backward compatibility
                dynamic_values["workflows_success_count"] = success_count
                dynamic_values["workflows_failure_count"] = failure_count
                dynamic_values["workflows_total_count"] = len(workflows)

                return True
            except Exception as e:
                console.print(f"[red]❌ Parallel execution failed: {str(e)}[/red]")
                return False
        else:
            # Continue even if some fail
            results = await asyncio.gather(*tasks, return_exceptions=True)

            success_count = 0
            failure_count = 0

            for idx, result in enumerate(results, 1):
                if isinstance(result, Exception):
                    console.print(
                        f"[yellow]⚠️  Workflow {idx} failed with exception: {str(result)}[/yellow]"
                    )
                    failure_count += 1
                elif not result:
                    console.print(f"[yellow]⚠️  Workflow {idx} failed[/yellow]")
                    failure_count += 1
                else:
                    success_count += 1

            console.print(
                f"[cyan]📊 Parallel execution completed: {success_count} succeeded, {failure_count} failed[/cyan]"
            )

            # Store results in global variables
            global_variables["workflows_success_count"] = success_count
            global_variables["workflows_failure_count"] = failure_count
            global_variables["workflows_total_count"] = len(workflows)
            # Also set in dynamic_values for backward compatibility
            dynamic_values["workflows_success_count"] = success_count
            dynamic_values["workflows_failure_count"] = failure_count
            dynamic_values["workflows_total_count"] = len(workflows)

            return True  # Don't fail even if some workflows failed

    async def _execute_sequential(
        self,
        workflows: list[dict[str, Any]],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        global_variables: dict[str, Any],
        local_variables: dict[str, Any],
        fail_fast: bool,
    ) -> bool:
        """Execute workflows sequentially."""
        console.print(
            f"[cyan]🚀 Starting {len(workflows)} workflows sequentially...[/cyan]"
        )

        success_count = 0
        failure_count = 0

        for idx, workflow_config in enumerate(workflows, 1):
            console.print(
                f"\n[bold blue]📋 Executing workflow {idx}/{len(workflows)}...[/bold blue]"
            )

            success = await self._execute_single_workflow(
                idx,
                workflow_config,
                workflow_results,
                dynamic_values,
                global_variables,
                local_variables,
            )

            if success:
                success_count += 1
                console.print(f"[green]✓ Workflow {idx} completed successfully[/green]")
            else:
                failure_count += 1
                console.print(f"[red]❌ Workflow {idx} failed[/red]")

                if fail_fast:
                    console.print(
                        "[red]❌ Stopping sequential execution due to failure (fail_fast=true)[/red]"
                    )
                    # Set count variables before early return
                    global_variables["workflows_success_count"] = success_count
                    global_variables["workflows_failure_count"] = failure_count
                    global_variables["workflows_total_count"] = len(workflows)
                    dynamic_values["workflows_success_count"] = success_count
                    dynamic_values["workflows_failure_count"] = failure_count
                    dynamic_values["workflows_total_count"] = len(workflows)
                    return False
                else:
                    console.print(
                        "[yellow]⚠️  Continuing despite failure (fail_fast=false)[/yellow]"
                    )

        console.print(
            f"[cyan]📊 Sequential execution completed: {success_count} succeeded, {failure_count} failed[/cyan]"
        )

        # Store results in global variables
        global_variables["workflows_success_count"] = success_count
        global_variables["workflows_failure_count"] = failure_count
        global_variables["workflows_total_count"] = len(workflows)
        # Also set in dynamic_values for backward compatibility
        dynamic_values["workflows_success_count"] = success_count
        dynamic_values["workflows_failure_count"] = failure_count
        dynamic_values["workflows_total_count"] = len(workflows)

        return failure_count == 0 or not fail_fast

    async def _execute_single_workflow(
        self,
        idx: int,
        workflow_config: dict[str, Any],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        global_variables: dict[str, Any],
        local_variables: dict[str, Any],
    ) -> bool:
        """Execute a single workflow from the list."""
        workflow_path = workflow_config.get("path")
        if not workflow_path:
            console.print(f"[red]❌ Workflow {idx}: 'path' is required[/red]")
            return False

        # Create a run_workflow step configuration
        step_config = {
            "type": "run_workflow",
            "workflow_path": workflow_path,
            "inputs": workflow_config.get("inputs", {}),
            "outputs": workflow_config.get("outputs", {}),
            "inherit_variables": workflow_config.get("inherit_variables", False),
            "on_failure": workflow_config.get("on_failure", {"continue": False}),
        }

        # Create and execute the run_workflow step with parent_executor
        run_workflow_step = RunWorkflowStep(
            step_config, manager=self.manager, parent_executor=self.parent_executor
        )

        try:
            success = await run_workflow_step.execute(
                workflow_results, dynamic_values, global_variables, local_variables
            )
            return success
        except Exception as e:
            console.print(
                f"[red]❌ Workflow {idx} failed with exception: {str(e)}[/red]"
            )
            return False
