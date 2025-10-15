"""
JSON assert step - Compare values inside two JSON-like objects.

Supports two configs (statement-style preferred):

- statements (preferred):
  - "json_equal({{get_result}}, {'output': 'v'})"
  - "json_subset({{some_json}}, {'nested': {'k': 'v'}})"
  - aliases: equal(A,B), subset(A,B)

- legacy fields:
  - left/right/mode retained for backward compatibility
"""

from __future__ import annotations

import json
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


def _normalize_json(value: Any) -> Any:
    """Best-effort parse/normalize JSON-like input to Python structures."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


class JsonAssertStep(BaseStep):
    """Compare two JSON objects (or JSON strings) for equality or subset.

    Configuration examples:

    - name: Assert JSON equal
      type: json_assert
      left: "{{get_result}}"
      right: {"output": null}
      mode: equal   # equal (default) | subset

    - name: Assert JSON subset
      type: json_assert
      left: "{{some_json}}"
      right: {"nested": {"key": "value"}}
      mode: subset
    """

    def _get_required_fields(self) -> list[str]:
        # Statement syntax only
        return ["statements"]

    def __init__(self, config: dict[str, Any], manager: object | None = None):
        super().__init__(config, manager=manager)

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

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        # Statement-only mode
        statements = self.config.get("statements", [])
        all_ok = True
        for idx, stmt in enumerate(statements, start=1):
            message = None
            if isinstance(stmt, dict):
                message = stmt.get("message")
                stmt = stmt.get("statement", "")
            if not isinstance(stmt, str):
                console.print(f"[red]✗ Invalid JSON assertion at #{idx}[/red]")
                all_ok = False
                continue
            passed, desc, left_val, right_val = self._eval_statement(
                stmt, workflow_results, dynamic_values
            )
            description = message or desc
            if passed:
                console.print(f"[green]✓ {description} passed[/green]")
            else:
                console.print(
                    f"[red]✗ {description} failed[/red]\n  left={left_val!r}\n  right={right_val!r}"
                )
                all_ok = False
        return all_ok

    def _is_subset(self, left: Any, right: Any) -> bool:
        """Return True if 'right' is a subset of 'left'."""
        if isinstance(left, dict) and isinstance(right, dict):
            for k, v in right.items():
                if k not in left:
                    return False
                if not self._is_subset(left[k], v):
                    return False
            return True
        if isinstance(left, list) and isinstance(right, list):
            # All elements of right must appear in left (order-insensitive, simple containment)
            for item in right:
                if item not in left:
                    return False
            return True
        # Fallback to equality for scalars
        return left == right

    def _eval_statement(
        self,
        statement: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> tuple[bool, str, Any, Any]:
        """Evaluate a single JSON assertion statement.

        Supported forms:
        - json_equal(A, B) / equal(A, B)
        - json_subset(A, B) / subset(A, B)
        Arguments can be placeholders or JSON strings.
        """
        expr = statement.strip()

        def _call_like(name: str) -> bool:
            return expr.lower().startswith(name + "(") and expr.endswith(")")

        def _args(body: str) -> list[str]:
            return [p.strip() for p in body.split(",", 1)] if "," in body else [body]

        for func, desc, mode in (
            ("json_equal", "JSON equality", "equal"),
            ("equal", "JSON equality", "equal"),
            ("json_subset", "JSON subset", "subset"),
            ("subset", "JSON subset", "subset"),
        ):
            if _call_like(func):
                body = expr[len(func) + 1 : -1]
                parts = _args(body)
                if len(parts) != 2:
                    return False, desc, None, None
                left_raw, right_raw = parts[0], parts[1]
                left_val = (
                    self._resolve_dynamic_value(
                        left_raw, workflow_results, dynamic_values
                    )
                    if isinstance(left_raw, str)
                    else left_raw
                )
                right_val = (
                    self._resolve_dynamic_value(
                        right_raw, workflow_results, dynamic_values
                    )
                    if isinstance(right_raw, str)
                    else right_raw
                )
                left_norm = _normalize_json(left_val)
                right_norm = _normalize_json(right_val)
                passed = (
                    (left_norm == right_norm)
                    if mode == "equal"
                    else self._is_subset(left_norm, right_norm)
                )
                return passed, desc, left_val, right_val

        # Fallback: try simple equality if pattern not matched
        return False, "Unrecognized JSON assertion", None, None
