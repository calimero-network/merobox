"""
Raw admin-API response assertion.

``calimero-client-py`` is compiled from a pinned core revision and its DTOs drop
unknown response keys silently on deserialize, so a field newly added to core
reads as missing through every typed step. This step issues the HTTP request
itself and asserts against the parsed body.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import requests

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.constants import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from merobox.commands.errors import UnresolvedPlaceholderError
from merobox.commands.result import fail, ok
from merobox.commands.utils import console

# Core marks optional fields `skip_serializing_if = "Option::is_none"`, so an
# absent key is the observable difference between None and a value.
_MISSING = object()


def _lookup(payload: Any, path: str) -> Any:
    """Value at a dotted path, or ``_MISSING`` if the path does not exist.

    Unlike ``BaseStep._get_value`` this neither re-parses JSON on the way down
    nor collapses a missing path to None, so the assertion sees the body verbatim.
    """
    current = payload
    for segment in path.split("."):
        if isinstance(current, list) and segment.isdigit():
            if (index := int(segment)) >= len(current):
                return _MISSING
            current = current[index]
        elif isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return _MISSING
    return current


def _walk_lists(value: Any):
    """Every dict inside any list in the body, at any depth."""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
            yield from _walk_lists(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_lists(item)


class AssertApiResponseStep(BaseStep):
    """GET a path on a node's admin API and assert against the JSON it returned.

    ``expected_failure`` / ``expected_error`` govern the request outcome only.
    A missed assertion always fails the step, or a typo'd path would read as a
    green negative test.

    ``where`` picks one element out of a list before the assertions run, which is
    what lets a body like ``{"devices": [...]}`` be asserted by device rather than
    by position. ``retries`` re-issues the whole request until it passes, for the
    states no barrier can wait on: an install writes no DAG state, and a paired
    device is a member of nothing so `wait_for_sync` has nothing to compare.
    """

    strict_placeholders = True

    def _get_required_fields(self) -> list[str]:
        return ["node", "path"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_string_field("path")
        self._validate_string_field("token", required=False)
        self._validate_dict_field("match", required=False)
        self._validate_list_field("present", required=False, element_type=str)
        self._validate_list_field("absent", required=False, element_type=str)
        self._validate_dict_field("where", required=False)
        for field in ("retries", "interval"):
            value = self.config.get(field)
            if value is not None and (
                not isinstance(value, (int, float)) or value <= 0
            ):
                raise ValueError(
                    f"Step '{self._get_step_name()}': '{field}' must be a positive number"
                )
        for field in ("match", "present", "absent"):
            for path in self.config.get(field) or []:
                if not isinstance(path, str) or not path.strip():
                    raise ValueError(
                        f"Step '{self._get_step_name()}': '{field}' entries must "
                        f"be non-empty dotted paths into the response body"
                    )
        # Without an assertion the step would pass unconditionally.
        if not self._assertion_count() and not self._is_expected_failure():
            raise ValueError(
                f"Step '{self._get_step_name()}': needs at least one of 'match', "
                f"'present' or 'absent' - without one it asserts nothing"
            )

    def _assertion_count(self) -> int:
        return sum(
            len(self.config.get(f) or []) for f in ("match", "present", "absent")
        )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        # A placeholder that never bound is this step's own verdict, not a
        # crash. `path` and `match` resolve at different depths, so wrap both.
        try:
            attempts = int(self.config.get("retries") or 1)
            interval = float(self.config.get("interval") or 1)
            for attempt in range(1, attempts + 1):
                # Quiet until the budget runs out: a body that has not converged
                # yet is the expected state, not something to report each time.
                last = attempt == attempts
                if await self._assert(workflow_results, dynamic_values, quiet=not last):
                    return True
                if not last:
                    await asyncio.sleep(interval)
            return False
        except UnresolvedPlaceholderError as e:
            console.print(
                f"✗ assert_api_response on {self.config['node']}: {e.message}",
                style="red",
                markup=False,
            )
            return False

    async def _assert(
        self,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
        quiet: bool = False,
    ) -> bool:
        node_name = self.config["node"]
        path = self._resolve_dynamic_value(
            self.config["path"], workflow_results, dynamic_values
        )

        try:
            rpc_url, cache_node_name = self._resolve_node_target(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        token = self._resolve_token(cache_node_name, workflow_results, dynamic_values)
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        try:
            # Off the loop: `requests` is synchronous, and this is the one step
            # issuing its own HTTP, so a `parallel:` sibling must keep running.
            response = await asyncio.to_thread(
                requests.get,
                f"{rpc_url.rstrip('/')}/{path.lstrip('/')}",
                headers=headers,
                timeout=(DEFAULT_CONNECTION_TIMEOUT, DEFAULT_READ_TIMEOUT),
            )
            # The whole 2xx range: a route answering 201/204 has served the
            # request, and failing it would report a working endpoint as broken.
            if not 200 <= response.status_code < 300:
                result = fail(
                    f"GET {path} on {node_name} returned HTTP "
                    f"{response.status_code}: {response.text}"
                )
            else:
                # Raw bytes, not response.text, whose charset requests guesses;
                # json.loads, not _parse_json, whose repairs would hide a bad body.
                result = ok(json.loads(response.content))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            result = fail(f"GET {path} on {node_name} returned a non-JSON body: {e}")
        # Only what `requests` raises for a transport fault: a TypeError from
        # this step's own plumbing must not pass as an ordinary request failure.
        except requests.RequestException as e:
            result = fail(f"GET {path} on {node_name} failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            detail = self._failure_detail(result)
            if expected_failure:
                return self._report_expected_failure(detail)
            if quiet:
                return False
            # markup=False so a response body containing brackets survives Rich.
            console.print(
                f"✗ assert_api_response: {detail}",
                style="red",
                markup=False,
            )
            return False

        payload = result["data"]
        workflow_results[f"api_response_{node_name}"] = payload

        selected = self._select(payload, workflow_results, dynamic_values)
        if selected is _MISSING:
            if not quiet:
                console.print(
                    f"✗ assert_api_response on {node_name}: GET {path} returned no "
                    f"element matching {self.config.get('where')!r}",
                    style="red",
                    markup=False,
                )
                console.print(
                    f"  body: {json.dumps(payload, sort_keys=True)}",
                    style="red",
                    markup=False,
                )
            return False
        payload = selected

        failures = self._assertion_failures(payload, workflow_results, dynamic_values)
        if failures:
            if quiet:
                return False
            console.print(
                f"✗ assert_api_response on {node_name}: GET {path} missed "
                f"{len(failures)} of {self._assertion_count()} assertion(s)",
                style="red",
                markup=False,
            )
            for failure in failures:
                console.print(f"    {failure}", style="red", markup=False)
            console.print(
                f"  body: {json.dumps(payload, sort_keys=True)}",
                style="red",
                markup=False,
            )
            return False

        if expected_failure:
            return self._report_unexpected_success()

        console.print(
            f"[green]✓ assert_api_response: GET {path} on {node_name} satisfied "
            f"{self._assertion_count()} assertion(s)[/green]"
        )
        return True

    def _select(
        self,
        payload: Any,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> Any:
        """The one element `where` names, or the whole body when it is absent.

        Searches every list in the body rather than taking a path to it, because
        the caller already identifies the element by its own fields and a second
        way to say where it lives is a second thing to keep in step with the API.
        """
        where = self.config.get("where")
        if not where:
            return payload
        wanted = {
            key: (
                self._resolve_dynamic_value(value, workflow_results, dynamic_values)
                if isinstance(value, str)
                else value
            )
            for key, value in where.items()
        }
        for candidate in _walk_lists(payload):
            if isinstance(candidate, dict) and all(
                candidate.get(k) == v for k, v in wanted.items()
            ):
                return candidate
        return _MISSING

    def _assertion_failures(
        self,
        payload: Any,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> list[str]:
        """Per-path verdicts; empty means the body satisfied every assertion."""
        failures = []

        for path, expected in (self.config.get("match") or {}).items():
            if isinstance(expected, str):
                expected = self._resolve_dynamic_value(
                    expected, workflow_results, dynamic_values
                )
            actual = _lookup(payload, path)
            if actual is _MISSING:
                failures.append(f"{path}: expected {expected!r}, but the key is absent")
            elif actual != expected:
                failures.append(f"{path}: expected {expected!r}, got {actual!r}")

        for path in self.config.get("present") or []:
            if _lookup(payload, path) is _MISSING:
                failures.append(f"{path}: expected the key to be present, it is absent")

        for path in self.config.get("absent") or []:
            actual = _lookup(payload, path)
            if actual is not _MISSING:
                failures.append(
                    f"{path}: expected the key to be absent, it is present "
                    f"with {actual!r}"
                )

        return failures
