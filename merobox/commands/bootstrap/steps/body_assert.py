"""Assert against a response body: pick an element, check paths, retry.

Shared by `assert_api_response`, which GETs a raw admin-API path, and by the
account read steps, which reach the same bodies through calimero-client-py. One
copy so a scenario means the same thing whichever it uses, and so a read step
can assert without a second call to the endpoint it just read.
"""

from __future__ import annotations

from typing import Any, Callable

MISSING = object()

Resolve = Callable[[Any], Any]


def lookup(payload: Any, path: str) -> Any:
    """Walk a dotted path, treating a numeric segment as a list index."""
    current = payload
    for segment in path.split("."):
        if isinstance(current, list) and segment.isdigit():
            if (index := int(segment)) >= len(current):
                return MISSING
            current = current[index]
        elif isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return MISSING
    return current


def walk_lists(value: Any):
    """Every dict inside any list in the body, at any depth."""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
            yield from walk_lists(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk_lists(item)


def select(payload: Any, where: dict[str, Any] | None, resolve: Resolve) -> Any:
    """The one element `where` names, or the whole body when it is absent.

    Searches every list in the body rather than taking a path to it: the caller
    already identifies the element by its own fields, and a second way to say
    where it lives is a second thing to keep in step with the API.
    """
    if not where:
        return payload
    wanted = {
        key: (resolve(value) if isinstance(value, str) else value)
        for key, value in where.items()
    }
    for candidate in walk_lists(payload):
        if isinstance(candidate, dict) and all(
            candidate.get(k) == v for k, v in wanted.items()
        ):
            return candidate
    return MISSING


def failures(
    payload: Any,
    match: dict[str, Any] | None,
    present: list[str] | None,
    absent: list[str] | None,
    resolve: Resolve,
) -> list[str]:
    """Per-path verdicts; empty means the body satisfied every assertion."""
    found = []

    for path, expected in (match or {}).items():
        if isinstance(expected, str):
            expected = resolve(expected)
        actual = lookup(payload, path)
        if actual is MISSING:
            found.append(f"{path}: expected {expected!r}, but the key is absent")
        elif actual != expected:
            found.append(f"{path}: expected {expected!r}, got {actual!r}")

    for path in present or []:
        if lookup(payload, path) is MISSING:
            found.append(f"{path}: expected the key to be present, it is absent")

    for path in absent or []:
        actual = lookup(payload, path)
        if actual is not MISSING:
            found.append(
                f"{path}: expected the key to be absent, it is present with {actual!r}"
            )

    return found


def count(config: dict[str, Any]) -> int:
    """How many assertions a step config carries."""
    return sum(len(config.get(field) or []) for field in ("match", "present", "absent"))


def validate(config: dict[str, Any], step_name: str) -> None:
    """Reject a shape the step could not act on, at load time."""
    where = config.get("where")
    if where is not None and not isinstance(where, dict):
        raise ValueError(f"Step '{step_name}': 'where' must be a mapping of fields")
    for field in ("retries", "interval"):
        value = config.get(field)
        if value is not None and (not isinstance(value, (int, float)) or value <= 0):
            raise ValueError(f"Step '{step_name}': '{field}' must be a positive number")
    for field in ("match",):
        value = config.get(field)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"Step '{step_name}': '{field}' must be a mapping")
    for field in ("present", "absent"):
        value = config.get(field)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(p, str) for p in value)
        ):
            raise ValueError(f"Step '{step_name}': '{field}' must be a list of strings")
