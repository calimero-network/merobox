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
from merobox.commands.errors import UnresolvedPlaceholderError
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

    The `statements:` form additionally supports inequality, which `mode:` does
    not: `json_not_equal(A, B)`. Use it for properties only expressible as a
    difference — a re-enrolled device must not reuse a revoked id, and the new id
    is unpredictable, so there is no literal to compare against.
    """

    strict_placeholders = True

    def _get_required_fields(self) -> list[str]:
        # Statement syntax only
        return ["statements"]

    def __init__(
        self,
        config: dict[str, Any],
        manager: object | None = None,
        resolver: object | None = None,
        auth_mode: str | None = None,
    ):
        super().__init__(
            config, manager=manager, resolver=resolver, auth_mode=auth_mode
        )

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
            try:
                passed, desc, left_val, right_val = self._eval_statement(
                    stmt, workflow_results, dynamic_values
                )
            except UnresolvedPlaceholderError as e:
                passed, desc, left_val, right_val = False, e.message, None, None
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
        - json_not_equal(A, B) / not_equal(A, B)
        - json_subset(A, B) / subset(A, B)
        Arguments can be placeholders or JSON strings.

        `json_not_equal` exists because some properties are only expressible as a
        difference. A revocation is terminal, so a re-enrolled device must get a
        NEW id — and the new value is unpredictable, so there is no literal to
        compare it against. Without this, scenarios either shelled out to a script
        or asserted equality against a stand-in, which tests the stand-in.

        Order in the table below does not affect matching — `_call_like` is a
        `startswith` test anchored at position 0, so `equal(` cannot match
        `json_not_equal(...)` or `not_equal(...)`. The negatives are listed first
        only so the pairs read together.
        """
        expr = statement.strip()

        def _call_like(name: str) -> bool:
            return expr.lower().startswith(name + "(") and expr.endswith(")")

        def _args(body: str) -> list[str]:
            """Split on the comma separating the two operands, not the first one.

            `body.split(",", 1)` happens to work whenever the LEFT operand has no
            comma of its own — which is the common shape, a captured placeholder
            against a JSON literal. It silently mis-parses the moment the left side
            is itself a multi-key object or an array: `{"a": 1, "b": 2}, {"a": 1}`
            splits into `{"a": 1` and `"b": 2}, {"a": 1}`, neither of which is JSON,
            and the assertion then fails for a reason that has nothing to do with
            the values.

            So: split at the first comma that is at depth zero and outside a string.
            """
            depth = 0
            in_string = False
            escaped = False
            for index, char in enumerate(body):
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = not in_string
                elif in_string:
                    continue
                elif char in "{[":
                    depth += 1
                elif char in "}]":
                    depth -= 1
                elif char == "," and depth == 0:
                    return [body[:index].strip(), body[index + 1 :].strip()]
            return [body.strip()]

        for func, desc, mode in (
            # Grouped with their positive counterparts; order is not significant.
            ("json_not_equal", "JSON inequality", "not_equal"),
            ("not_equal", "JSON inequality", "not_equal"),
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
                if mode == "equal":
                    passed = left_norm == right_norm
                elif mode == "not_equal":
                    passed = left_norm != right_norm
                else:
                    passed = self._is_subset(left_norm, right_norm)
                return passed, desc, left_val, right_val

        # Fallback: try simple equality if pattern not matched
        return False, "Unrecognized JSON assertion", None, None
