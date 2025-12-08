"""
Assert step - Perform assertions on exported dynamic variables (statement syntax only).

Examples:
- "is_set({{context_id}})"
- "contains({{get_result}}, 'hello')"
- "{{count}} >= 1"
- "regex({{value}}, '^abc')"
- "equal({{a}}, {{b}})" / "not_equal({{a}}, 'x')"

Non-blocking mode (for fuzzy testing):
- Set non_blocking: true to record failures without stopping the workflow
- Results are tracked in dynamic_values["_fuzzy_test_results"] if present
"""

from __future__ import annotations

import re
import time
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class FuzzyTestResultsTracker:
    """Tracks assertion results during fuzzy load testing.

    This tracker accumulates pass/fail counts and stores recent failures
    for reporting at the end of a fuzzy test run.
    """

    def __init__(self, max_failure_history: int = 100):
        self.start_time = time.time()
        self.total_assertions = 0
        self.assertions_passed = 0
        self.assertions_failed = 0
        self.total_patterns_executed = 0
        self.patterns_by_name: dict[str, dict] = {}
        self.failed_assertions: list[dict] = []
        self.max_failure_history = max_failure_history
        self.current_pattern_name: str | None = None

    def set_current_pattern(self, pattern_name: str) -> None:
        """Set the current pattern being executed."""
        self.current_pattern_name = pattern_name
        if pattern_name not in self.patterns_by_name:
            self.patterns_by_name[pattern_name] = {
                "count": 0,
                "assertions_passed": 0,
                "assertions_failed": 0,
                "failure_messages": {},
            }

    def increment_pattern_count(self) -> None:
        """Increment the count for the current pattern."""
        if self.current_pattern_name:
            self.patterns_by_name[self.current_pattern_name]["count"] += 1
        self.total_patterns_executed += 1

    def record_assertion(
        self,
        passed: bool,
        description: str,
        detail: str = "",
        pattern_name: str | None = None,
    ) -> None:
        """Record an assertion result.

        Args:
            passed: Whether the assertion passed
            description: Human-readable description of the assertion
            detail: Additional detail about the assertion result
            pattern_name: Name of the pattern this assertion belongs to
        """
        self.total_assertions += 1
        pattern = pattern_name or self.current_pattern_name

        if passed:
            self.assertions_passed += 1
            if pattern and pattern in self.patterns_by_name:
                self.patterns_by_name[pattern]["assertions_passed"] += 1
        else:
            self.assertions_failed += 1
            if pattern and pattern in self.patterns_by_name:
                self.patterns_by_name[pattern]["assertions_failed"] += 1
                # Track failure message frequency
                failure_key = description[:80]  # Truncate for grouping
                failure_msgs = self.patterns_by_name[pattern]["failure_messages"]
                failure_msgs[failure_key] = failure_msgs.get(failure_key, 0) + 1

            # Store recent failures for detailed reporting
            if len(self.failed_assertions) < self.max_failure_history:
                self.failed_assertions.append(
                    {
                        "pattern": pattern,
                        "description": description,
                        "detail": detail,
                        "time": time.time() - self.start_time,
                        "timestamp": time.strftime("%H:%M:%S"),
                    }
                )

    def get_elapsed_time(self) -> float:
        """Get elapsed time in seconds since tracking started."""
        return time.time() - self.start_time

    def get_pass_rate(self) -> float:
        """Get overall assertion pass rate as a percentage."""
        if self.total_assertions == 0:
            return 100.0
        return (self.assertions_passed / self.total_assertions) * 100

    def get_summary(self) -> dict:
        """Get a summary of all tracked results."""
        return {
            "elapsed_seconds": self.get_elapsed_time(),
            "total_patterns_executed": self.total_patterns_executed,
            "total_assertions": self.total_assertions,
            "assertions_passed": self.assertions_passed,
            "assertions_failed": self.assertions_failed,
            "pass_rate": self.get_pass_rate(),
            "patterns_by_name": self.patterns_by_name,
            # Last 20 failures
            "recent_failures": self.failed_assertions[-20:],
        }


class AssertStep(BaseStep):
    """Validate exported variables against a set of assertions.

    Configuration example:

    - name: Assert values
      type: assert
      assertions:
        - left: "{{context_id}}"
          op: is_set
        - left: "{{get_result}}"
          op: contains
          right: "hello"
        - left: "{{count}}"
          op: ">="
          right: 1

    Non-blocking mode (for fuzzy testing):

    - name: Assert values (non-blocking)
      type: assert
      non_blocking: true  # Don't stop workflow on failure
      statements:
        - statement: "is_set({{value}})"
          message: "Value should be set"
    """

    def _get_required_fields(self) -> list[str]:
        # Statement syntax only
        return ["statements"]

    def _validate_field_types(self) -> None:
        statements = self.config.get("statements", [])
        if not isinstance(statements, list) or len(statements) == 0:
            raise ValueError("'statements' must be a non-empty list")
        for idx, stmt in enumerate(statements):
            if not isinstance(stmt, (str, dict)):
                raise ValueError(
                    f"Statement #{idx+1} must be a string or dict with 'statement'"
                )
            if isinstance(stmt, dict) and "statement" not in stmt:
                raise ValueError(f"Statement #{idx+1} missing 'statement'")

        # Validate non_blocking is boolean if provided
        if "non_blocking" in self.config:
            if not isinstance(self.config["non_blocking"], bool):
                raise ValueError("'non_blocking' must be a boolean")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        # Check for non-blocking mode (used in fuzzy testing)
        non_blocking = self.config.get("non_blocking", False)

        # Get results tracker if available (for fuzzy test result aggregation)
        results_tracker: FuzzyTestResultsTracker | None = dynamic_values.get(
            "_fuzzy_test_results", None
        )

        # Statement-only mode
        statements = self.config.get("statements", [])
        all_ok = True

        for idx, stmt in enumerate(statements, start=1):
            message = None
            if isinstance(stmt, dict):
                message = stmt.get("message")
                stmt = stmt.get("statement", "")
            if not isinstance(stmt, str):
                console.print(f"[red]✗ Invalid statement at #{idx}[/red]")
                all_ok = False
                # Record invalid statement as failure in tracker
                if results_tracker is not None:
                    results_tracker.record_assertion(
                        passed=False,
                        description=f"Invalid statement at #{idx}",
                        detail="Statement must be a string",
                    )
                continue

            passed, detail = self._eval_statement(
                stmt, workflow_results, dynamic_values
            )
            description = message or f"Assertion #{idx}: {stmt}"

            # Record result in tracker if available
            if results_tracker is not None:
                results_tracker.record_assertion(
                    passed=passed,
                    description=description,
                    detail=detail,
                )

            if passed:
                console.print(f"[green]✓ {description}[/green]")
            else:
                console.print(f"[red]✗ {description} failed[/red] {detail}")
                all_ok = False

        # In non-blocking mode, always return True to continue workflow
        if non_blocking:
            return True

        return all_ok

    def _evaluate(self, left: Any, op: str, right: Any) -> bool:
        try:
            if op in ("==", "equals", "equal"):
                return left == right
            if op in ("!=", "not_equals"):
                return left != right
            if op in (">", "gt"):
                return float(left) > float(right)
            if op in (">=", "gte"):
                return float(left) >= float(right)
            if op in ("<", "lt"):
                return float(left) < float(right)
            if op in ("<=", "lte"):
                return float(left) <= float(right)
            if op == "contains":
                return str(right) in str(left)
            if op == "not_contains":
                return str(right) not in str(left)
            if op in ("regex", "matches"):
                return re.search(str(right), str(left)) is not None
            if op in ("is_set", "exists"):
                return left is not None and left != "" and left != [] and left != {}
            if op in ("is_empty", "empty"):
                return left in (None, "", [], {})
        except Exception:
            return False
        return False

    def _eval_statement(
        self,
        statement: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> tuple[bool, str]:
        """Evaluate a single statement string.

        Supported forms:
        - "A == B", "A != B", "A >= B", "A > B", "A <= B", "A < B"
        - "contains(A, B)", "not_contains(A, B)", "regex(A, PATTERN)"
        - "is_set(A)", "is_empty(A)"
        Placeholders like {{var}} are resolved in operands.
        """
        expr = statement.strip()

        # Function-like predicates first
        def _arg_list(body: str) -> list[str]:
            parts = [p.strip() for p in body.split(",")]
            return parts

        if expr.lower().startswith("contains(") and expr.endswith(")"):
            body = expr[len("contains(") : -1]
            args = _arg_list(body)
            if len(args) != 2:
                return False, "(invalid contains arg count)"
            a = self._resolve_dynamic_value(args[0], workflow_results, dynamic_values)
            b = self._resolve_dynamic_value(args[1], workflow_results, dynamic_values)
            return (str(b) in str(a), f"(left={a!r}, right={b!r})")

        if expr.lower().startswith("not_contains(") and expr.endswith(")"):
            body = expr[len("not_contains(") : -1]
            args = _arg_list(body)
            if len(args) != 2:
                return False, "(invalid not_contains arg count)"
            a = self._resolve_dynamic_value(args[0], workflow_results, dynamic_values)
            b = self._resolve_dynamic_value(args[1], workflow_results, dynamic_values)
            return (str(b) not in str(a), f"(left={a!r}, right={b!r})")

        if expr.lower().startswith("regex(") and expr.endswith(")"):
            body = expr[len("regex(") : -1]
            args = _arg_list(body)
            if len(args) != 2:
                return False, "(invalid regex arg count)"
            a = self._resolve_dynamic_value(args[0], workflow_results, dynamic_values)
            pattern = self._resolve_dynamic_value(
                args[1], workflow_results, dynamic_values
            )
            ok = re.search(str(pattern), str(a)) is not None
            return (ok, f"(left={a!r}, pattern={pattern!r})")

        if expr.lower().startswith("is_set(") and expr.endswith(")"):
            body = expr[len("is_set(") : -1].strip()
            a = self._resolve_dynamic_value(body, workflow_results, dynamic_values)
            ok = a is not None and a != "" and a != [] and a != {}
            return (ok, f"(value={a!r})")

        if expr.lower().startswith("is_empty(") and expr.endswith(")"):
            body = expr[len("is_empty(") : -1].strip()
            a = self._resolve_dynamic_value(body, workflow_results, dynamic_values)

            # Handle string literals that represent empty values
            if a == "''" or a == '""':
                ok = True
            elif a == "[]":
                ok = True
            elif a == "{}":
                ok = True
            else:
                ok = a in (None, "", [], {})
            return (ok, f"(value={a!r})")

        # Equality predicates: equal(A,B), equals(A,B), not_equal(A,B), not_equals(A,B)
        def _is_call(name: str) -> bool:
            return expr.lower().startswith(name + "(") and expr.endswith(")")

        def _call_args(body: str) -> list[str]:
            return [p.strip() for p in body.split(",", 1)] if "," in body else [body]

        for func, negate in (
            ("equal", False),
            ("equals", False),
            ("not_equal", True),
            ("not_equals", True),
        ):
            if _is_call(func):
                body = expr[len(func) + 1 : -1]
                args = _call_args(body)
                if len(args) != 2:
                    return False, "(invalid equality arg count)"
                a_raw, b_raw = args[0], args[1]
                a_val = self._resolve_dynamic_value(
                    a_raw, workflow_results, dynamic_values
                )
                b_val = self._resolve_dynamic_value(
                    b_raw, workflow_results, dynamic_values
                )
                passed = a_val == b_val
                if negate:
                    passed = not passed
                return passed, f"(left={a_val!r}, right={b_val!r})"

        # Operator-based
        import re as _re

        # Match the first occurrence of a comparison operator
        m = _re.search(r"\s(==|!=|>=|<=|>|<)\s", expr)
        if m:
            op = m.group(1)
            left_str = expr[: m.start()].strip()
            right_str = expr[m.end() :].strip()
            left_val = self._resolve_dynamic_value(
                left_str, workflow_results, dynamic_values
            )
            right_val = self._resolve_dynamic_value(
                right_str, workflow_results, dynamic_values
            )

            # Try numeric comparison when feasible
            def _to_num(x):
                try:
                    return float(x)
                except Exception:
                    return None

            ln = _to_num(left_val)
            rn = _to_num(right_val)
            if ln is not None and rn is not None:
                lcomp, rcomp = ln, rn
            else:
                lcomp, rcomp = str(left_val), str(right_val)

            passed = {
                "==": lcomp == rcomp,
                "!=": lcomp != rcomp,
                ">": lcomp > rcomp,
                ">=": lcomp >= rcomp,
                "<": lcomp < rcomp,
                "<=": lcomp <= rcomp,
            }[op]
            return (passed, f"(left={left_val!r}, right={right_val!r})")

        return False, "(unrecognized statement)"
