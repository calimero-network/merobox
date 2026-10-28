"""The `assert` and `json_assert` steps: statements over a workflow's variables.

These are v1's assertion steps, kept with v1's statement syntax so a ported
workflow asserts exactly what it did before:

- `assert`: `is_set(A)`, `is_empty(A)`, `contains(A, B)`, `not_contains(A, B)`,
  `regex(A, PATTERN)`, `equal(A, B)` / `not_equal(A, B)` (and the `equals` /
  `not_equals` spellings), and `A <op> B` for `==`, `!=`, `>=`, `>`, `<=`, `<`
  (numeric when both sides are numbers).
- `json_assert`: `json_equal(A, B)`, `json_not_equal(A, B)`, `json_subset(A, B)`
  and the unprefixed `equal`, `not_equal`, `subset`; operands are placeholders
  or JSON text.

Each statement is a string or `{statement, message}`. A step passes when every
statement does; `non_blocking: true` on an `assert` reports failures without
failing the step.

An operand is a quoted literal (quotes stripped), plain text, or holds
`{{name}}` placeholders naming workflow variables. A placeholder that names no
variable fails its statement rather than being compared as text, which would
pass almost anything.
"""

from __future__ import annotations

import json
import re
from typing import Any

from rich.console import Console

console = Console()


class AssertionConfigError(ValueError):
    """The step's `statements` are missing or malformed."""


class _Unresolved(Exception):
    """A `{{placeholder}}` in an operand names no variable."""


def _check_config(step: dict) -> list:
    name = step.get("name") or step.get("type")
    statements = step.get("statements")
    if not isinstance(statements, list) or not statements:
        raise AssertionConfigError(f"{name}: 'statements' must be a non-empty list")
    for idx, stmt in enumerate(statements, start=1):
        if not isinstance(stmt, (str, dict)):
            raise AssertionConfigError(
                f"{name}: statement #{idx} must be a string or dict with 'statement'"
            )
        if isinstance(stmt, dict) and "statement" not in stmt:
            raise AssertionConfigError(f"{name}: statement #{idx} missing 'statement'")
    if "non_blocking" in step and not isinstance(step["non_blocking"], bool):
        raise AssertionConfigError(f"{name}: 'non_blocking' must be a boolean")
    return statements


def resolve_operand(value: Any, variables: dict[str, Any]) -> Any:
    """An operand's value: a whole `{{name}}` keeps the variable's type, an
    embedded one is substituted as text, and a quoted literal loses its quotes."""
    if not isinstance(value, str):
        return value
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in "'\""
        and "{{" not in value
        and "}}" not in value
    ):
        return value[1:-1]
    if "{{" not in value or "}}" not in value:
        return value
    if value.startswith("{{") and value.endswith("}}") and value.count("{{") == 1:
        name = value[2:-2].strip()
        if name not in variables:
            raise _Unresolved(value)
        return variables[name]
    result, start = value, 0
    while True:
        open_at = result.find("{{", start)
        if open_at == -1:
            break
        close_at = result.find("}}", open_at)
        if close_at == -1:
            break
        name = result[open_at + 2 : close_at].strip()
        if name not in variables:
            raise _Unresolved("{{" + name + "}}")
        text = str(variables[name])
        result = result[:open_at] + text + result[close_at + 2 :]
        start = open_at + len(text)
    return result


def _unresolved_detail(placeholder: str) -> str:
    return (
        f"({placeholder!r} contains a placeholder that never resolved; "
        f"comparing against it compares against its own text)"
    )


# ------------------------------------------------------------------- assert


def _call(expr: str, name: str) -> bool:
    return expr.lower().startswith(name + "(") and expr.endswith(")")


def eval_statement(statement: str, variables: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate one `assert` statement: (passed, detail)."""
    expr = statement.strip()

    def r(operand: str) -> Any:
        return resolve_operand(operand, variables)

    for func, negate in (("contains", False), ("not_contains", True)):
        if _call(expr, func):
            args = [p.strip() for p in expr[len(func) + 1 : -1].split(",")]
            if len(args) != 2:
                return False, f"(invalid {func} arg count)"
            a, b = r(args[0]), r(args[1])
            return (str(b) in str(a)) != negate, f"(left={a!r}, right={b!r})"

    if _call(expr, "regex"):
        args = [p.strip() for p in expr[len("regex(") : -1].split(",")]
        if len(args) != 2:
            return False, "(invalid regex arg count)"
        a, pattern = r(args[0]), r(args[1])
        ok = re.search(str(pattern), str(a)) is not None
        return ok, f"(left={a!r}, pattern={pattern!r})"

    if _call(expr, "is_set"):
        a = r(expr[len("is_set(") : -1].strip())
        return a is not None and a != "" and a != [] and a != {}, f"(value={a!r})"

    if _call(expr, "is_empty"):
        a = r(expr[len("is_empty(") : -1].strip())
        ok = a in ("''", '""', "[]", "{}") or a in (None, "", [], {})
        return ok, f"(value={a!r})"

    for func, negate in (
        ("equal", False),
        ("equals", False),
        ("not_equal", True),
        ("not_equals", True),
    ):
        if _call(expr, func):
            body = expr[len(func) + 1 : -1]
            args = [p.strip() for p in body.split(",", 1)] if "," in body else [body]
            if len(args) != 2:
                return False, "(invalid equality arg count)"
            a, b = r(args[0]), r(args[1])
            return (a == b) != negate, f"(left={a!r}, right={b!r})"

    m = re.search(r"\s(==|!=|>=|<=|>|<)\s", expr)
    if m:
        op = m.group(1)
        left, right = r(expr[: m.start()].strip()), r(expr[m.end() :].strip())

        def num(x: Any) -> float | None:
            try:
                return float(x)
            except Exception:
                return None

        ln, rn = num(left), num(right)
        lc, rc = (
            (ln, rn) if ln is not None and rn is not None else (str(left), str(right))
        )
        passed = {
            "==": lc == rc,
            "!=": lc != rc,
            ">": lc > rc,
            ">=": lc >= rc,
            "<": lc < rc,
            "<=": lc <= rc,
        }[op]
        return passed, f"(left={left!r}, right={right!r})"

    return False, "(unrecognized statement)"


# -------------------------------------------------------------- json_assert


def _normalize_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _is_subset(left: Any, right: Any) -> bool:
    """Whether `right` is contained in `left` (dict keys recursively, list items)."""
    if isinstance(left, dict) and isinstance(right, dict):
        return all(k in left and _is_subset(left[k], v) for k, v in right.items())
    if isinstance(left, list) and isinstance(right, list):
        return all(item in left for item in right)
    return left == right


def _split_operands(body: str) -> list[str]:
    """Split at the first comma outside a string and outside `{}`/`[]`, so a
    multi-key object on the left is one operand."""
    depth, in_string, escaped = 0, False, False
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


def eval_json_statement(
    statement: str, variables: dict[str, Any]
) -> tuple[bool, str, Any, Any]:
    """Evaluate one `json_assert` statement: (passed, description, left, right)."""
    expr = statement.strip()
    for func, desc, mode in (
        ("json_not_equal", "JSON inequality", "not_equal"),
        ("not_equal", "JSON inequality", "not_equal"),
        ("json_equal", "JSON equality", "equal"),
        ("equal", "JSON equality", "equal"),
        ("json_subset", "JSON subset", "subset"),
        ("subset", "JSON subset", "subset"),
    ):
        if _call(expr, func):
            parts = _split_operands(expr[len(func) + 1 : -1])
            if len(parts) != 2:
                return False, desc, None, None
            left = resolve_operand(parts[0], variables)
            right = resolve_operand(parts[1], variables)
            ln, rn = _normalize_json(left), _normalize_json(right)
            if mode == "equal":
                passed = ln == rn
            elif mode == "not_equal":
                passed = ln != rn
            else:
                passed = _is_subset(ln, rn)
            return passed, desc, left, right
    return False, "Unrecognized JSON assertion", None, None


# --------------------------------------------------------------------- step


def run_assert_step(step: dict, variables: dict[str, Any]) -> bool:
    """Run an `assert` or `json_assert` step; print each statement's outcome.

    Raises `AssertionConfigError` for malformed `statements`."""
    statements = _check_config(step)
    is_json = step.get("type") == "json_assert"
    all_ok = True
    for idx, stmt in enumerate(statements, start=1):
        message = None
        if isinstance(stmt, dict):
            message = stmt.get("message")
            stmt = stmt.get("statement", "")
        if not isinstance(stmt, str):
            what = "JSON assertion" if is_json else "statement"
            console.print(f"[red]✗ Invalid {what} at #{idx}[/red]")
            all_ok = False
            continue
        if is_json:
            try:
                passed, desc, left, right = eval_json_statement(stmt, variables)
            except _Unresolved as e:
                passed, desc, left, right = (
                    False,
                    _unresolved_detail(str(e)),
                    None,
                    None,
                )
            description = message or desc
            if passed:
                console.print(f"[green]✓ {description} passed[/green]")
            else:
                console.print(
                    f"[red]✗ {description} failed[/red]\n  left={left!r}\n  right={right!r}"
                )
                all_ok = False
        else:
            try:
                passed, detail = eval_statement(stmt, variables)
            except _Unresolved as e:
                passed, detail = False, _unresolved_detail(str(e))
            description = message or f"Assertion #{idx}: {stmt}"
            if passed:
                console.print(f"[green]✓ {description}[/green]")
            else:
                console.print(f"[red]✗ {description} failed[/red] {detail}")
                all_ok = False
    if not is_json and step.get("non_blocking", False):
        return True
    return all_ok
