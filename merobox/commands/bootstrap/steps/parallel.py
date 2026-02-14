"""
Parallel step executor for executing multiple step groups concurrently.

Supports configurable failure modes for error handling:
- fail-fast: If one parallel branch fails, immediately cancel all others
- fail-slow: Wait for all branches to complete, then report failures (default)
- continue-on-error: Continue executing all branches, return success if at least one succeeded
"""

import asyncio
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

# Valid failure modes for parallel execution
VALID_FAILURE_MODES = ("fail-fast", "fail-slow", "continue-on-error")


class ParallelStep(BaseStep):
    """Execute multiple step groups concurrently.

    Failure Modes:
    - fail-fast: When a group fails, cancel all other running groups immediately
                 and return failure. Use when early termination is desired.
    - fail-slow: Wait for all groups to complete, then return failure if any
                 group failed. Use when you want all groups to attempt execution.
                 This is the default for backward compatibility.
    - continue-on-error: Wait for all groups, return success if at least one
                         succeeded. Use for resilient workflows where partial
                         success is acceptable.
    """

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.

        Returns:
            List of required field names
        """
        return ["groups"]

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        """
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate groups is a list
        if not isinstance(self.config.get("groups"), list):
            raise ValueError(f"Step '{step_name}': 'groups' must be a list")

        # Validate groups list is not empty
        if not self.config.get("groups"):
            raise ValueError(f"Step '{step_name}': 'groups' list cannot be empty")

        # Validate failure_mode if provided (optional, defaults to "fail-slow")
        failure_mode = self.config.get("failure_mode")
        if failure_mode is not None:
            if not isinstance(failure_mode, str):
                raise ValueError(f"Step '{step_name}': 'failure_mode' must be a string")
            if failure_mode not in VALID_FAILURE_MODES:
                raise ValueError(
                    f"Step '{step_name}': 'failure_mode' must be one of "
                    f"{VALID_FAILURE_MODES}, got '{failure_mode}'"
                )

        # Validate each group has required fields
        for i, group in enumerate(self.config.get("groups", [])):
            if not isinstance(group, dict):
                raise ValueError(
                    f"Step '{step_name}': Group {i+1} must be a dictionary"
                )
            if "steps" not in group:
                raise ValueError(
                    f"Step '{step_name}': Group {i+1} must have 'steps' field"
                )
            if not isinstance(group.get("steps"), list):
                raise ValueError(
                    f"Step '{step_name}': Group {i+1} 'steps' must be a list"
                )
            if not group.get("steps"):
                raise ValueError(
                    f"Step '{step_name}': Group {i+1} 'steps' list cannot be empty"
                )

            # Validate count field if provided (optional, defaults to 1)
            if "count" in group:
                count = group.get("count")
                if not isinstance(count, int):
                    raise ValueError(
                        f"Step '{step_name}': Group {i+1} 'count' must be an integer"
                    )
                if count <= 0:
                    raise ValueError(
                        f"Step '{step_name}': Group {i+1} 'count' must be a positive integer"
                    )

    def _get_exportable_variables(self):
        """
        Define which variables this step can export.

        Available variables from parallel execution:
        - group_count: Total number of groups executed
        - parallel_success_count: Number of groups that succeeded
        - parallel_failure_count: Number of groups that failed
        - overall_duration_seconds: Total execution time for all groups (float)
        - overall_duration_ms: Total execution time in milliseconds (float)
        - overall_duration_ns: Total execution time in nanoseconds (int)
        - group_{index}_duration_seconds: Duration for each group (if group has name)
        - group_{index}_duration_ms: Duration in ms for each group
        - group_{index}_duration_ns: Duration in ns for each group
        """
        groups = self.config.get("groups", [])
        variables = [
            ("group_count", "group_count", "Total number of groups executed"),
            (
                "parallel_success_count",
                "parallel_success_count",
                "Number of groups that succeeded",
            ),
            (
                "parallel_failure_count",
                "parallel_failure_count",
                "Number of groups that failed",
            ),
            (
                "overall_duration_seconds",
                "overall_duration_seconds",
                "Total execution time for all groups (float)",
            ),
            (
                "overall_duration_ms",
                "overall_duration_ms",
                "Total execution time in milliseconds (float)",
            ),
            (
                "overall_duration_ns",
                "overall_duration_ns",
                "Total execution time in nanoseconds (int)",
            ),
        ]

        # Add per-group timing variables
        for i, group in enumerate(groups):
            group_name = group.get("name", f"group_{i+1}")
            variables.extend(
                [
                    (
                        f"group_{i}_duration_seconds",
                        f"{group_name}_duration_seconds",
                        f"Duration for {group_name} in seconds",
                    ),
                    (
                        f"group_{i}_duration_ms",
                        f"{group_name}_duration_ms",
                        f"Duration for {group_name} in milliseconds",
                    ),
                    (
                        f"group_{i}_duration_ns",
                        f"{group_name}_duration_ns",
                        f"Duration for {group_name} in nanoseconds",
                    ),
                ]
            )

        return variables

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        groups = self.config.get("groups", [])
        mode = self.config.get("mode", "burst")  # burst, sustained, mixed
        failure_mode = self.config.get("failure_mode", "fail-slow")

        # Validate export configuration
        if not self._validate_export_config():
            console.print(
                "[yellow]⚠️  Parallel step export configuration validation failed[/yellow]"
            )

        if not groups:
            console.print("[yellow]No groups specified for parallel execution[/yellow]")
            return True

        console.print(
            f"[cyan]⚡ Executing {len(groups)} groups in parallel "
            f"(mode: {mode}, failure_mode: {failure_mode})...[/cyan]"
        )

        # Export group count
        dynamic_values["group_count"] = len(groups)

        # Track overall timing
        overall_start_time = time.perf_counter()

        # Execute all groups concurrently based on failure mode
        group_results = await self._execute_groups_with_failure_mode(
            groups, workflow_results, dynamic_values, mode, failure_mode
        )

        # Calculate overall duration
        overall_end_time = time.perf_counter()
        overall_duration_seconds = overall_end_time - overall_start_time
        overall_duration_ms = overall_duration_seconds * 1000.0
        overall_duration_ns = int(overall_duration_seconds * 1_000_000_000)

        # Export overall timing metrics
        dynamic_values["overall_duration_seconds"] = round(overall_duration_seconds, 6)
        dynamic_values["overall_duration_ms"] = round(overall_duration_ms, 3)
        dynamic_values["overall_duration_ns"] = overall_duration_ns

        # Process group results and export per-group metrics
        success_count = 0
        failure_count = 0
        for i, (result, group) in enumerate(zip(group_results, groups)):
            group_name = group.get("name", f"Group {i+1}")

            # Check for CancelledError first (inherits from BaseException, not Exception)
            if isinstance(result, asyncio.CancelledError):
                console.print(
                    f"[yellow]⚠️ Group '{group_name}' was cancelled "
                    f"(fail-fast triggered by another group)[/yellow]"
                )
                failure_count += 1
                continue

            if isinstance(result, Exception):
                console.print(
                    f"[red]❌ Group '{group_name}' failed with error: "
                    f"{str(result)}[/red]"
                )
                failure_count += 1
                continue

            if isinstance(result, dict):
                success = result.get("success", False)
                duration_seconds = result.get("duration_seconds", 0.0)
                duration_ms = duration_seconds * 1000.0
                duration_ns = int(duration_seconds * 1_000_000_000)

                # Export per-group timing
                dynamic_values[f"group_{i}_duration_seconds"] = round(
                    duration_seconds, 6
                )
                dynamic_values[f"group_{i}_duration_ms"] = round(duration_ms, 3)
                dynamic_values[f"group_{i}_duration_ns"] = duration_ns

                # Also export by group name if provided
                if group.get("name"):
                    dynamic_values[f"{group['name']}_duration_seconds"] = round(
                        duration_seconds, 6
                    )
                    dynamic_values[f"{group['name']}_duration_ms"] = round(
                        duration_ms, 3
                    )
                    dynamic_values[f"{group['name']}_duration_ns"] = duration_ns

                if not success:
                    console.print(f"[red]❌ Group '{group_name}' failed[/red]")
                    failure_count += 1
                else:
                    console.print(
                        f"[green]✓ Group '{group_name}' completed successfully[/green]"
                    )
                    console.print(
                        f"  [cyan]Duration:[/cyan] {duration_seconds:.3f} seconds "
                        f"({duration_ms:.2f} ms)"
                    )
                    success_count += 1

        # Export timing variables based on custom outputs configuration
        self._export_timing_variables(dynamic_values)

        # Export failure mode statistics
        dynamic_values["parallel_success_count"] = success_count
        dynamic_values["parallel_failure_count"] = failure_count

        # Determine final result based on failure mode
        all_success = failure_count == 0

        if all_success:
            console.print(
                f"[green]✓ All {len(groups)} groups completed successfully[/green]"
            )
        else:
            console.print(
                f"[red]❌ {failure_count} of {len(groups)} groups failed during "
                f"parallel execution[/red]"
            )

        console.print("[blue] Overall Timing Metrics:[/blue]")
        console.print(
            f"  [cyan]Total Duration:[/cyan] {overall_duration_seconds:.3f} seconds "
            f"({overall_duration_ms:.2f} ms, {overall_duration_ns:,} ns)"
        )

        # Apply failure mode logic to determine return value
        if failure_mode == "continue-on-error":
            # Return success if at least one group succeeded
            if success_count > 0:
                console.print(
                    f"[cyan]ℹ️  continue-on-error mode: Returning success "
                    f"({success_count} groups succeeded)[/cyan]"
                )
                return True
            else:
                console.print("[red]❌ continue-on-error mode: All groups failed[/red]")
                return False
        else:
            # fail-fast and fail-slow: return True only if all succeeded
            return all_success

    async def _execute_groups_with_failure_mode(
        self,
        groups: list[dict[str, Any]],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        mode: str,
        failure_mode: str,
    ) -> list[Any]:
        """Execute groups based on the configured failure mode.

        Args:
            groups: List of group configurations
            workflow_results: Workflow results dictionary
            dynamic_values: Dynamic values dictionary
            mode: Execution mode (burst, sustained, mixed)
            failure_mode: How to handle failures (fail-fast, fail-slow, continue-on-error)

        Returns:
            List of results (dict with success/duration or Exception)
        """
        if failure_mode == "fail-fast":
            return await self._execute_groups_fail_fast(
                groups, workflow_results, dynamic_values, mode
            )
        else:
            # fail-slow and continue-on-error both run all tasks to completion
            return await self._execute_groups_fail_slow(
                groups, workflow_results, dynamic_values, mode
            )

    async def _execute_groups_fail_fast(
        self,
        groups: list[dict[str, Any]],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        mode: str,
    ) -> list[Any]:
        """Execute groups with fail-fast behavior.

        When any group fails, immediately cancel all other running groups.

        Args:
            groups: List of group configurations
            workflow_results: Workflow results dictionary
            dynamic_values: Dynamic values dictionary
            mode: Execution mode (burst, sustained, mixed)

        Returns:
            List of results (dict with success/duration, Exception, or CancelledError)
        """
        # Create tasks for all groups
        tasks = [
            asyncio.create_task(
                self._execute_group(i, group, workflow_results, dynamic_values.copy()),
                name=f"group_{i}_{group.get('name', f'Group {i+1}')}",
            )
            for i, group in enumerate(groups)
        ]

        results: list[Any] = [None] * len(groups)
        pending = set(tasks)
        task_to_index = {task: i for i, task in enumerate(tasks)}

        try:
            while pending:
                # Wait for the first task to complete
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )

                # First, process ALL completed tasks to avoid race condition
                # where results are lost when multiple tasks complete simultaneously
                should_cancel = False
                cancel_reason = None

                for task in done:
                    idx = task_to_index[task]
                    try:
                        result = task.result()
                        results[idx] = result

                        # Check if this task failed (mark for cancellation but continue processing)
                        if isinstance(result, dict) and not result.get("success", True):
                            if not should_cancel:
                                should_cancel = True
                                cancel_reason = groups[idx].get(
                                    "name", f"Group {idx+1}"
                                )
                    except asyncio.CancelledError as e:
                        # Preserve the original exception context
                        results[idx] = e
                    except Exception as e:
                        # Preserve the original exception
                        results[idx] = e
                        if not should_cancel:
                            should_cancel = True
                            cancel_reason = f"exception in group {idx+1}"

                # After processing all done tasks, cancel remaining if needed
                if should_cancel and pending:
                    console.print(
                        f"[yellow]⚡ fail-fast: '{cancel_reason}' triggered failure, "
                        f"cancelling {len(pending)} remaining group(s)...[/yellow]"
                    )
                    # Convert pending to list to ensure consistent ordering
                    pending_list = list(pending)
                    for pending_task in pending_list:
                        pending_task.cancel()
                    # Collect cancellation results with consistent ordering
                    cancelled_results = await asyncio.gather(
                        *pending_list, return_exceptions=True
                    )
                    for pending_task, cancelled_result in zip(
                        pending_list, cancelled_results
                    ):
                        cancelled_idx = task_to_index[pending_task]
                        results[cancelled_idx] = cancelled_result
                    pending = set()

        except Exception as e:
            console.print(f"[red]❌ Parallel execution failed: {str(e)}[/red]")
            # Cancel any remaining tasks
            pending_list = list(pending)
            for task in pending_list:
                task.cancel()
            # Wait for cancellations to complete
            if pending_list:
                await asyncio.gather(*pending_list, return_exceptions=True)
            raise

        return results

    async def _execute_groups_fail_slow(
        self,
        groups: list[dict[str, Any]],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        mode: str,
    ) -> list[Any]:
        """Execute groups with fail-slow behavior.

        Wait for all groups to complete regardless of failures.

        Args:
            groups: List of group configurations
            workflow_results: Workflow results dictionary
            dynamic_values: Dynamic values dictionary
            mode: Execution mode (burst, sustained, mixed)

        Returns:
            List of results (dict with success/duration or Exception)
        """
        try:
            # Print warning for unimplemented modes
            # TODO: Implement different execution modes
            # - "burst" (current): All groups start simultaneously
            # - "sustained": Rate-limited execution with controlled concurrency
            # - "mixed": Combination of burst and sustained
            if mode == "sustained":
                console.print(
                    "[yellow]⚠️  'sustained' mode not yet implemented, "
                    "falling back to 'burst' mode[/yellow]"
                )
            elif mode == "mixed":
                console.print(
                    "[yellow]⚠️  'mixed' mode not yet implemented, "
                    "falling back to 'burst' mode[/yellow]"
                )
            elif mode != "burst":
                console.print(
                    f"[yellow]⚠️  Unknown mode '{mode}', "
                    "defaulting to 'burst' mode[/yellow]"
                )

            # Create and execute tasks (common logic for all modes)
            tasks = [
                self._execute_group(i, group, workflow_results, dynamic_values.copy())
                for i, group in enumerate(groups)
            ]
            return await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            console.print(f"[red]❌ Parallel execution failed: {str(e)}[/red]")
            raise

    async def _execute_group(
        self,
        group_index: int,
        group_config: dict[str, Any],
        workflow_results: dict[str, Any],
        group_dynamic_values: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single group of steps."""
        group_name = group_config.get("name", f"Group {group_index + 1}")
        nested_steps = group_config.get("steps", [])
        # Number of times to repeat this group
        count = group_config.get("count", 1)

        console.print(
            f"[cyan]🔄 Starting group '{group_name}' ({count} iteration(s))...[/cyan]"
        )

        start_time = time.perf_counter()

        try:
            # Execute the group count times (sequentially within the group)
            for iteration in range(count):
                if count > 1:
                    console.print(
                        f"[cyan]  📋 Group '{group_name}' iteration {iteration + 1}/{count}[/cyan]"
                    )

                # Create iteration-specific dynamic values
                iteration_dynamic_values = group_dynamic_values.copy()
                iteration_dynamic_values.update(
                    {
                        "iteration": iteration + 1,
                        "iteration_index": iteration,
                        "group_index": group_index,
                        "group_name": group_name,
                    }
                )

                # Execute each nested step in sequence
                for step_idx, step in enumerate(nested_steps):
                    step_type = step.get("type")
                    nested_step_name = step.get("name", f"Nested Step {step_idx + 1}")

                    iteration_dynamic_values["current_step"] = nested_step_name
                    iteration_dynamic_values["current_step_index"] = step_idx + 1

                    try:
                        # Create appropriate step executor for the nested step
                        step_executor = self._create_nested_step_executor(
                            step_type, step
                        )
                        if not step_executor:
                            console.print(
                                f"[red]Unknown nested step type in group '{group_name}': {step_type}[/red]"
                            )
                            return {
                                "success": False,
                                "duration_seconds": 0.0,
                                "error": f"Unknown step type: {step_type}",
                            }

                        # Execute the nested step
                        success = await step_executor.execute(
                            workflow_results, iteration_dynamic_values
                        )

                        if not success:
                            console.print(
                                f"[red]❌ Step '{nested_step_name}' failed in group '{group_name}'[/red]"
                            )
                            end_time = time.perf_counter()
                            return {
                                "success": False,
                                "duration_seconds": end_time - start_time,
                                "error": f"Step '{nested_step_name}' failed",
                            }

                    except Exception as e:
                        console.print(
                            f"[red]❌ Step '{nested_step_name}' failed with error in group '{group_name}': {str(e)}[/red]"
                        )
                        end_time = time.perf_counter()
                        return {
                            "success": False,
                            "duration_seconds": end_time - start_time,
                            "error": str(e),
                        }

            end_time = time.perf_counter()
            duration_seconds = end_time - start_time

            console.print(
                f"[green]✓ Group '{group_name}' completed successfully in {duration_seconds:.3f}s[/green]"
            )

            return {"success": True, "duration_seconds": duration_seconds}

        except Exception as e:
            end_time = time.perf_counter()
            console.print(
                f"[red]❌ Group '{group_name}' failed with exception: {str(e)}[/red]"
            )
            return {
                "success": False,
                "duration_seconds": end_time - start_time,
                "error": str(e),
            }

    def _export_timing_variables(self, dynamic_values: dict[str, Any]) -> None:
        """Export timing variables based on custom outputs configuration."""
        outputs_config = self.config.get("outputs", {})
        if not outputs_config:
            return

        for export_name, export_config in outputs_config.items():
            if isinstance(export_config, str):
                # Simple field assignment
                source_field = export_config
                if source_field in dynamic_values:
                    source_value = dynamic_values[source_field]
                    dynamic_values[export_name] = source_value
                    console.print(
                        f"  📝 Timing export: {source_field} → {export_name}: {source_value}"
                    )
            elif isinstance(export_config, dict):
                # Complex field assignment
                source_field = export_config.get("field")
                target_template = export_config.get("target")
                if source_field and target_template:
                    if source_field in dynamic_values:
                        source_value = dynamic_values[source_field]
                        dynamic_values[export_name] = source_value
                        console.print(
                            f"  📝 Timing export: {source_field} → {export_name}: {source_value}"
                        )

    def _create_nested_step_executor(self, step_type: str, step_config: dict[str, Any]):
        """Create a nested step executor based on the step type."""
        # Common kwargs for all step types - includes auth_mode for embedded auth support
        common_kwargs = {
            "manager": self.manager,
            "resolver": self.resolver,
            "auth_mode": self.auth_mode,
        }

        if step_type == "install_application":
            return InstallApplicationStep(step_config, **common_kwargs)
        elif step_type == "create_context":
            return CreateContextStep(step_config, **common_kwargs)
        elif step_type == "create_identity":
            return CreateIdentityStep(step_config, **common_kwargs)
        elif step_type == "invite_identity":
            return InviteIdentityStep(step_config, **common_kwargs)
        elif step_type == "join_context":
            return JoinContextStep(step_config, **common_kwargs)
        elif step_type == "invite_open":
            from merobox.commands.bootstrap.steps.invite_open import InviteOpenStep

            return InviteOpenStep(step_config, **common_kwargs)
        elif step_type == "join_open":
            from merobox.commands.bootstrap.steps.join_open import JoinOpenStep

            return JoinOpenStep(step_config, **common_kwargs)
        elif step_type == "call":
            return ExecuteStep(step_config, **common_kwargs)
        elif step_type == "wait":
            return WaitStep(step_config, **common_kwargs)
        elif step_type == "wait_for_sync":
            from merobox.commands.bootstrap.steps.wait_for_sync import WaitForSyncStep

            return WaitForSyncStep(step_config, **common_kwargs)
        elif step_type == "script":
            return ScriptStep(step_config, **common_kwargs)
        elif step_type == "get_proposal":
            return GetProposalStep(step_config, **common_kwargs)
        elif step_type == "list_proposals":
            return ListProposalsStep(step_config, **common_kwargs)
        elif step_type == "get_proposal_approvers":
            return GetProposalApproversStep(step_config, **common_kwargs)
        elif step_type == "repeat":
            from merobox.commands.bootstrap.steps.repeat import RepeatStep

            return RepeatStep(step_config, **common_kwargs)
        else:
            console.print(f"[red]Unknown nested step type: {step_type}[/red]")
            return None
