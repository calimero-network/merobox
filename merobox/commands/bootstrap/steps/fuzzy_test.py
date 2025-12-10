"""
Fuzzy Test step executor for long-running load testing with randomized operations.

This step runs time-based load tests with:
- Weighted random operation patterns
- Non-blocking assertions for correctness validation
- Periodic progress summaries
- Comprehensive final reports

Example usage:
    - name: Fuzzy Load Test
      type: fuzzy_test
      duration_minutes: 30
      context_id: '{{context_id}}'
      nodes:
        - name: calimero-node-1
          executor_key: '{{member_public_key}}'
        - name: calimero-node-2
          executor_key: '{{public_key_node2}}'
      operations:
        - name: "set_and_verify"
          weight: 40
          steps:
            - type: call
              node: "{{random_node}}"
              method: set
              ...
"""

import asyncio
import random
import re
import string
import time
import uuid as uuid_module
from typing import Any

from merobox.commands.bootstrap.steps.assertion import (
    AssertStep,
    FuzzyTestResultsTracker,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep
from merobox.commands.bootstrap.steps.wait import WaitStep
from merobox.commands.utils import console


class FuzzyTestStep(BaseStep):
    """Execute long-running fuzzy load tests with randomized operations."""

    def __init__(self, config: dict[str, Any], manager: object | None = None):
        # Initialize instance attributes used during execution
        self._context_id: str = ""
        self._nodes: list[dict] = []
        self._operations: list[dict] = []
        self._total_weight: float = 0
        super().__init__(config, manager)

    def _get_required_fields(self) -> list[str]:
        """Define required fields for this step."""
        return ["duration_minutes", "context_id", "nodes", "operations"]

    def _validate_field_types(self) -> None:
        """Validate field types."""
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

        # Validate duration_minutes
        duration = self.config.get("duration_minutes")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise ValueError(
                f"Step '{step_name}': 'duration_minutes' must be a positive number"
            )

        # Validate context_id
        if not isinstance(self.config.get("context_id"), str):
            raise ValueError(f"Step '{step_name}': 'context_id' must be a string")

        # Validate nodes
        nodes = self.config.get("nodes", [])
        if not isinstance(nodes, list) or len(nodes) == 0:
            raise ValueError(f"Step '{step_name}': 'nodes' must be a non-empty list")
        for idx, node in enumerate(nodes):
            if not isinstance(node, dict):
                raise ValueError(
                    f"Step '{step_name}': 'nodes[{idx}]' must be a dictionary with 'name' and 'executor_key'"
                )
            if "name" not in node or "executor_key" not in node:
                raise ValueError(
                    f"Step '{step_name}': 'nodes[{idx}]' must have 'name' and 'executor_key'"
                )

        # Validate operations
        operations = self.config.get("operations", [])
        if not isinstance(operations, list) or len(operations) == 0:
            raise ValueError(
                f"Step '{step_name}': 'operations' must be a non-empty list"
            )
        for idx, op in enumerate(operations):
            if not isinstance(op, dict):
                raise ValueError(
                    f"Step '{step_name}': 'operations[{idx}]' must be a dictionary"
                )
            if "name" not in op:
                raise ValueError(
                    f"Step '{step_name}': 'operations[{idx}]' must have a 'name'"
                )
            if "weight" not in op:
                raise ValueError(
                    f"Step '{step_name}': 'operations[{idx}]' must have a 'weight'"
                )
            # Validate weight is numeric and positive
            if not isinstance(op["weight"], (int, float)):
                raise ValueError(
                    f"Step '{step_name}': 'operations[{idx}]' weight must be a number, "
                    f"got {type(op['weight']).__name__}"
                )
            if op["weight"] <= 0:
                raise ValueError(
                    f"Step '{step_name}': 'operations[{idx}]' weight must be positive, "
                    f"got {op['weight']}"
                )
            if "steps" not in op or not isinstance(op["steps"], list):
                raise ValueError(
                    f"Step '{step_name}': 'operations[{idx}]' must have a 'steps' list"
                )
            # Validate each step in the steps list is a dict
            if len(op["steps"]) == 0:
                raise ValueError(
                    f"Step '{step_name}': 'operations[{idx}]' steps list cannot be empty"
                )
            for step_idx, step in enumerate(op["steps"]):
                if not isinstance(step, dict):
                    raise ValueError(
                        f"Step '{step_name}': 'operations[{idx}]' step {step_idx} must be a "
                        f"dictionary, got {type(step).__name__}"
                    )
                if "type" not in step:
                    raise ValueError(
                        f"Step '{step_name}': 'operations[{idx}]' step {step_idx} "
                        f"must have a 'type' field"
                    )

    def _get_exportable_variables(self):
        """Define exportable variables."""
        return [
            (
                "fuzzy_test_total_patterns",
                "fuzzy_test_total_patterns",
                "Total patterns executed",
            ),
            (
                "fuzzy_test_total_assertions",
                "fuzzy_test_total_assertions",
                "Total assertions run",
            ),
            (
                "fuzzy_test_assertions_passed",
                "fuzzy_test_assertions_passed",
                "Assertions that passed",
            ),
            (
                "fuzzy_test_assertions_failed",
                "fuzzy_test_assertions_failed",
                "Assertions that failed",
            ),
            (
                "fuzzy_test_pass_rate",
                "fuzzy_test_pass_rate",
                "Overall pass rate percentage",
            ),
            (
                "fuzzy_test_had_exception",
                "fuzzy_test_had_exception",
                "Whether an exception occurred during execution",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        """Execute the fuzzy load test."""
        duration_minutes = self.config.get("duration_minutes", 30)
        duration_seconds = duration_minutes * 60
        summary_interval = self.config.get("summary_interval_seconds", 60)
        operation_delay_ms = self.config.get("operation_delay_ms", 100)
        success_threshold = self.config.get("success_threshold", 95.0)

        # Resolve context_id
        context_id = self._resolve_dynamic_value(
            self.config["context_id"], workflow_results, dynamic_values
        )

        # Resolve node configurations
        nodes = []
        for node_config in self.config["nodes"]:
            resolved_node = {
                "name": self._resolve_dynamic_value(
                    node_config["name"], workflow_results, dynamic_values
                ),
                "executor_key": self._resolve_dynamic_value(
                    node_config["executor_key"], workflow_results, dynamic_values
                ),
            }
            nodes.append(resolved_node)

        # Build weighted operation list
        operations = self.config.get("operations", [])
        total_weight = sum(op.get("weight", 1) for op in operations)

        # Initialize results tracker
        results_tracker = FuzzyTestResultsTracker()
        dynamic_values["_fuzzy_test_results"] = results_tracker

        # Store resolved values for pattern execution
        self._context_id = context_id
        self._nodes = nodes
        self._operations = operations
        self._total_weight = total_weight

        console.print(f"\n[bold magenta]{'=' * 60}[/bold magenta]")
        console.print("[bold magenta]🔬 Starting Fuzzy Load Test[/bold magenta]")
        console.print(f"[bold magenta]{'=' * 60}[/bold magenta]")
        console.print(f"[cyan]Duration: {duration_minutes} minutes[/cyan]")
        console.print(f"[cyan]Context ID: {context_id}[/cyan]")
        console.print(f"[cyan]Nodes: {', '.join(n['name'] for n in nodes)}[/cyan]")
        console.print(f"[cyan]Operation patterns: {len(operations)}[/cyan]")
        console.print(f"[cyan]Success threshold: {success_threshold}%[/cyan]")
        console.print(f"[bold magenta]{'=' * 60}[/bold magenta]\n")

        start_time = time.time()
        last_summary_time = start_time
        iteration = 0
        had_exception = False

        try:
            while (time.time() - start_time) < duration_seconds:
                iteration += 1

                # Select random operation based on weights
                operation = self._select_weighted_operation()
                pattern_name = operation.get("name", f"Pattern {iteration}")

                # Set current pattern in tracker
                results_tracker.set_current_pattern(pattern_name)

                # Execute the pattern
                await self._execute_pattern(
                    operation,
                    workflow_results,
                    dynamic_values,
                    iteration,
                )

                # Increment pattern count
                results_tracker.increment_pattern_count()

                # Print periodic summary
                current_time = time.time()
                if (current_time - last_summary_time) >= summary_interval:
                    self._print_progress_summary(
                        results_tracker,
                        current_time - start_time,
                        duration_seconds,
                    )
                    last_summary_time = current_time

                # Delay between operations
                if operation_delay_ms > 0:
                    await asyncio.sleep(operation_delay_ms / 1000)

        except KeyboardInterrupt:
            console.print("\n[yellow]⚠️  Fuzzy test interrupted by user[/yellow]")
            had_exception = True
        except Exception as e:
            console.print(f"\n[red]❌ Fuzzy test error: {str(e)}[/red]")
            had_exception = True
            # Continue to print final report even on error

        # Print final report
        elapsed = time.time() - start_time
        self._print_final_report(
            results_tracker, elapsed, success_threshold, had_exception
        )

        # Export results to dynamic values
        summary = results_tracker.get_summary()
        dynamic_values["fuzzy_test_total_patterns"] = summary["total_patterns_executed"]
        dynamic_values["fuzzy_test_total_assertions"] = summary["total_assertions"]
        dynamic_values["fuzzy_test_assertions_passed"] = summary["assertions_passed"]
        dynamic_values["fuzzy_test_assertions_failed"] = summary["assertions_failed"]
        dynamic_values["fuzzy_test_pass_rate"] = summary["pass_rate"]
        dynamic_values["fuzzy_test_had_exception"] = had_exception

        # Test fails if: exception occurred, no assertions ran, or pass rate below threshold
        if had_exception:
            passed = False
        elif summary["total_assertions"] == 0:
            # No assertions ran - nothing was validated, test must fail
            console.print(
                "[yellow]⚠️  Warning: No assertions were executed during fuzzy test![/yellow]"
            )
            console.print(
                "[yellow]   The test cannot pass without validating any results.[/yellow]"
            )
            passed = False
        else:
            passed = summary["pass_rate"] >= success_threshold

        return passed

    def _select_weighted_operation(self) -> dict:
        """Select a random operation based on weights."""
        rand_val = random.random() * self._total_weight
        cumulative = 0

        for op in self._operations:
            cumulative += op.get("weight", 1)
            if rand_val <= cumulative:
                return op

        # Fallback to last operation
        return self._operations[-1]

    async def _execute_pattern(
        self,
        operation: dict,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        iteration: int,
    ) -> bool:
        """Execute a single operation pattern."""
        pattern_name = operation.get("name", "unnamed")
        steps = operation.get("steps", [])

        # Create pattern-specific dynamic values
        pattern_dynamic_values = dynamic_values.copy()
        pattern_dynamic_values["_pattern_name"] = pattern_name
        pattern_dynamic_values["_iteration"] = iteration

        # Select random node for this pattern
        random_node = random.choice(self._nodes)
        pattern_dynamic_values["random_node"] = random_node["name"]
        pattern_dynamic_values["random_executor"] = random_node["executor_key"]

        for step_idx, step_config in enumerate(steps):
            # Validate step is a dict (extra safety check)
            if not isinstance(step_config, dict):
                console.print(
                    f"[yellow]⚠️  Step {step_idx + 1} in pattern '{pattern_name}' "
                    f"is not a dictionary (got {type(step_config).__name__}), "
                    f"skipping (iteration {iteration})[/yellow]"
                )
                continue

            step_type = step_config.get("type")
            if not step_type:
                console.print(
                    f"[yellow]⚠️  Step {step_idx + 1} in pattern '{pattern_name}' "
                    f"missing 'type' field, skipping (iteration {iteration})[/yellow]"
                )
                continue

            # Deep copy step config to avoid mutation
            resolved_step_config = self._resolve_step_config(
                step_config, workflow_results, pattern_dynamic_values
            )

            # For call steps, capture resolved args for use in assertions
            # Store them in pattern_dynamic_values with a special prefix
            if step_type == "call" and "args" in resolved_step_config:
                resolved_args = resolved_step_config["args"]
                if isinstance(resolved_args, dict):
                    for arg_name, arg_value in resolved_args.items():
                        # Store resolved args with _fuzzy_args prefix
                        pattern_dynamic_values[f"_fuzzy_args_{arg_name}"] = arg_value
                        # Also store without prefix for convenience
                        pattern_dynamic_values[f"fuzzy_{arg_name}"] = arg_value

            # Force non_blocking mode for assertions in fuzzy test
            if step_type == "assert":
                resolved_step_config["non_blocking"] = True

            # Create and execute step (wrap creation to handle validation errors)
            try:
                step_executor = self._create_pattern_step_executor(
                    step_type, resolved_step_config
                )
                if step_executor is None:
                    console.print(
                        f"[yellow]⚠️  Unknown step type in pattern: {step_type}[/yellow]"
                    )
                    continue
            except ValueError as e:
                # Step configuration validation failed - log and skip this step
                console.print(
                    f"[yellow]⚠️  Step {step_idx + 1} in pattern '{pattern_name}' "
                    f"has invalid configuration: {str(e)} (iteration {iteration})[/yellow]"
                )
                continue
            except Exception as e:
                # Unexpected error during step creation - log and skip
                console.print(
                    f"[yellow]⚠️  Failed to create step {step_idx + 1} in pattern '{pattern_name}': "
                    f"{str(e)} (iteration {iteration})[/yellow]"
                )
                continue

            try:
                success = await step_executor.execute(
                    workflow_results, pattern_dynamic_values
                )
                if not success and step_type != "assert":
                    # Non-assertion step failed - log but continue
                    console.print(
                        f"[yellow]⚠️  Step {step_idx + 1} in pattern '{pattern_name}' "
                        f"returned failure (iteration {iteration})[/yellow]"
                    )
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Step {step_idx + 1} in pattern '{pattern_name}' "
                    f"raised exception during execution: {str(e)} (iteration {iteration})[/yellow]"
                )

        return True

    def _resolve_step_config(
        self,
        step_config: dict,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> dict:
        """Recursively resolve dynamic values and random generators in step config."""
        resolved = {}

        for key, value in step_config.items():
            if isinstance(value, str):
                # First resolve random generators, then dynamic values
                resolved_value = self._resolve_random_generators(value)
                resolved_value = self._resolve_dynamic_value(
                    resolved_value, workflow_results, dynamic_values
                )
                resolved[key] = resolved_value
            elif isinstance(value, dict):
                resolved[key] = self._resolve_step_config(
                    value, workflow_results, dynamic_values
                )
            elif isinstance(value, list):
                resolved[key] = [
                    (
                        self._resolve_step_config(
                            item, workflow_results, dynamic_values
                        )
                        if isinstance(item, dict)
                        else (
                            self._resolve_dynamic_value(
                                self._resolve_random_generators(item),
                                workflow_results,
                                dynamic_values,
                            )
                            if isinstance(item, str)
                            else item
                        )
                    )
                    for item in value
                ]
            else:
                resolved[key] = value

        return resolved

    def _resolve_random_generators(self, value: str) -> str:
        """Resolve random value generators in a string.

        Supported generators:
        - {{random_int(min, max)}} - Random integer in range
        - {{random_string(length)}} - Random alphanumeric string
        - {{random_float(min, max)}} - Random float in range
        - {{random_choice([a, b, c])}} - Random choice from list
        - {{timestamp}} - Current Unix timestamp
        - {{uuid}} - Random UUID
        """

        # Define generator handlers
        def handle_random_int(args: str) -> str:
            parts = args.split(",")
            min_val, max_val = int(parts[0].strip()), int(parts[1].strip())
            return str(random.randint(min_val, max_val))

        def handle_random_string(args: str) -> str:
            length = int(args.strip())
            return "".join(
                random.choices(string.ascii_letters + string.digits, k=length)
            )

        def handle_random_float(args: str) -> str:
            parts = args.split(",")
            min_val, max_val = float(parts[0].strip()), float(parts[1].strip())
            return str(random.uniform(min_val, max_val))

        def handle_random_choice(args: str) -> str:
            # Parse [a, b, c] format
            choices_str = args.strip("[]")
            choices = [c.strip().strip("'\"") for c in choices_str.split(",")]
            return random.choice(choices)

        def handle_timestamp(_args: str) -> str:
            return str(int(time.time()))

        def handle_uuid(_args: str) -> str:
            return str(uuid_module.uuid4())

        # Dispatch table
        generators = {
            "random_int": handle_random_int,
            "random_string": handle_random_string,
            "random_float": handle_random_float,
            "random_choice": handle_random_choice,
            "timestamp": handle_timestamp,
            "uuid": handle_uuid,
        }

        # Matches: {{generator_name(args)}} or {{generator_name}}
        pattern = r"\{\{(\w+)(?:\(([^)]*)\))?\}\}"

        def replacer(match):
            generator_name = match.group(1)
            args = match.group(2) or ""

            handler = generators.get(generator_name)
            if handler:
                try:
                    return handler(args)
                except Exception as e:
                    # If generation fails, return original
                    console.print(
                        f"[yellow]Warning: Failed to resolve {{{{{generator_name}}}}}: {e}[/yellow]"
                    )
                    return match.group(0)

            # Not a generator, return as-is (allows other {{variables}} to pass through)
            return match.group(0)

        return re.sub(pattern, replacer, value)

    def _create_pattern_step_executor(self, step_type: str, step_config: dict):
        """Create a step executor for pattern steps."""
        if step_type == "call":
            return ExecuteStep(step_config, manager=self.manager)
        elif step_type == "assert":
            return AssertStep(step_config, manager=self.manager)
        elif step_type == "wait":
            return WaitStep(step_config, manager=self.manager)
        else:
            return None

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        if minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def _print_progress_summary(
        self,
        tracker: FuzzyTestResultsTracker,
        elapsed: float,
        total_duration: float,
    ) -> None:
        """Print periodic progress summary."""
        summary = tracker.get_summary()
        elapsed_str = self._format_duration(elapsed)
        total_str = self._format_duration(total_duration)
        pass_rate = summary["pass_rate"]

        console.print(f"\n[bold cyan]{'=' * 60}[/bold cyan]")
        console.print(
            f"[bold cyan]Fuzzy Test Progress - {elapsed_str} / {total_str} elapsed[/bold cyan]"
        )
        console.print(f"[bold cyan]{'=' * 60}[/bold cyan]")
        console.print(f"Patterns Executed: {summary['total_patterns_executed']}")
        console.print(f"Total Assertions: {summary['total_assertions']}")
        console.print(
            f"  [green]Passed: {summary['assertions_passed']} "
            f"({pass_rate:.1f}%)[/green]"
        )
        console.print(
            f"  [{'red' if summary['assertions_failed'] > 0 else 'green'}]"
            f"Failed: {summary['assertions_failed']} "
            f"({100 - pass_rate:.1f}%)[/{'red' if summary['assertions_failed'] > 0 else 'green'}]"
        )

        # Pattern breakdown
        if summary["patterns_by_name"]:
            console.print("\n[bold]Pattern Breakdown:[/bold]")
            for name, data in summary["patterns_by_name"].items():
                total_asserts = data["assertions_passed"] + data["assertions_failed"]
                pattern_pass_rate = (
                    (data["assertions_passed"] / total_asserts * 100)
                    if total_asserts > 0
                    else 100
                )
                console.print(
                    f"  {name}: {data['count']} runs | "
                    f"Assertions: {data['assertions_passed']} passed, "
                    f"{data['assertions_failed']} failed ({pattern_pass_rate:.1f}%)"
                )

        # Operations per minute
        if elapsed > 0:
            ops_per_min = summary["total_patterns_executed"] / (elapsed / 60)
            console.print(f"\nOperations/min: {ops_per_min:.1f}")

        console.print(f"[bold cyan]{'=' * 60}[/bold cyan]\n")

    def _print_final_report(
        self,
        tracker: FuzzyTestResultsTracker,
        elapsed: float,
        success_threshold: float,
        had_exception: bool = False,
    ) -> None:
        """Print comprehensive final report."""
        summary = tracker.get_summary()
        elapsed_str = self._format_duration(elapsed)
        pass_rate = summary["pass_rate"]

        if had_exception:
            passed = False
        elif summary["total_assertions"] == 0:
            passed = False
        else:
            passed = pass_rate >= success_threshold

        console.print(f"\n[bold magenta]{'=' * 60}[/bold magenta]")
        console.print(
            f"[bold magenta]Fuzzy Test Complete - Ran for {elapsed_str}[/bold magenta]"
        )
        console.print(f"[bold magenta]{'=' * 60}[/bold magenta]")

        console.print(f"Total Patterns Executed: {summary['total_patterns_executed']}")
        console.print(f"Total Assertions: {summary['total_assertions']}")
        console.print(
            f"  [green]✓ Passed: {summary['assertions_passed']} "
            f"({pass_rate:.1f}%)[/green]"
        )
        console.print(
            f"  [{'red' if summary['assertions_failed'] > 0 else 'green'}]"
            f"✗ Failed: {summary['assertions_failed']} "
            f"({100 - pass_rate:.1f}%)[/{'red' if summary['assertions_failed'] > 0 else 'green'}]"
        )

        # Pattern results
        if summary["patterns_by_name"]:
            console.print("\n[bold]Pattern Results:[/bold]")
            for name, data in summary["patterns_by_name"].items():
                total_asserts = data["assertions_passed"] + data["assertions_failed"]
                pattern_pass_rate = (
                    (data["assertions_passed"] / total_asserts * 100)
                    if total_asserts > 0
                    else 100
                )
                console.print(f"\n  [bold]{name}[/bold]: {data['count']} runs")
                console.print(
                    f"    Assertions: {data['assertions_passed']} passed, "
                    f"{data['assertions_failed']} failed ({pattern_pass_rate:.1f}% pass rate)"
                )

                # Show common failures for this pattern
                if data["failure_messages"]:
                    console.print("    Common failures:")
                    sorted_failures = sorted(
                        data["failure_messages"].items(),
                        key=lambda x: x[1],
                        reverse=True,
                    )[:3]
                    for msg, count in sorted_failures:
                        console.print(f"      - {msg}: {count}")

        # Recent failures
        if summary["recent_failures"]:
            console.print("\n[bold]Recent Failures (last 20):[/bold]")
            for failure in summary["recent_failures"][-20:]:
                console.print(
                    f"  [{failure['timestamp']}] {failure['pattern']}: "
                    f"{failure['description']} {failure['detail']}"
                )

        # Node log locations
        console.print("\n[bold]For detailed debugging, check node logs in:[/bold]")
        for node in self._nodes:
            console.print(f"  data/{node['name']}/logs/")

        # Final conclusion
        console.print(f"\n[bold magenta]{'=' * 60}[/bold magenta]")
        if passed:
            console.print(
                f"[bold green]Test Conclusion: PASSED "
                f"({pass_rate:.1f}% success rate >= {success_threshold}% threshold)[/bold green]"
            )
        else:
            if had_exception:
                console.print(
                    "[bold red]Test Conclusion: FAILED "
                    "(Exception occurred during execution)[/bold red]"
                )
                if summary["total_assertions"] == 0:
                    console.print(
                        "[red]Note: No assertions were executed before the exception[/red]"
                    )
                else:
                    console.print(
                        f"[red]Partial results: {pass_rate:.1f}% pass rate "
                        f"from {summary['total_assertions']} assertions before failure[/red]"
                    )
            elif summary["total_assertions"] == 0:
                console.print(
                    "[bold red]Test Conclusion: FAILED "
                    "(No assertions were executed - nothing validated)[/bold red]"
                )
                console.print(
                    "[red]The test ran but no validation occurred. "
                    "Add assertion steps to your patterns.[/red]"
                )
            else:
                console.print(
                    f"[bold red]Test Conclusion: FAILED "
                    f"({pass_rate:.1f}% success rate < {success_threshold}% threshold)[/bold red]"
                )
        console.print(f"[bold magenta]{'=' * 60}[/bold magenta]\n")
