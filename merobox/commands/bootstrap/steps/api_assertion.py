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


class AssertApiResponseStep(BaseStep):
    """GET a path on a node's admin API and assert against the JSON it returned.

    ``expected_failure`` / ``expected_error`` govern the request outcome only.
    A missed assertion always fails the step, or a typo'd path would read as a
    green negative test.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "path"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_string_field("path")
        self._validate_string_field("token", required=False)
        self._validate_dict_field("match", required=False)
        self._validate_list_field("present", required=False, element_type=str)
        self._validate_list_field("absent", required=False, element_type=str)
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
            # markup=False so a response body containing brackets survives Rich.
            console.print(
                f"✗ assert_api_response: {detail}",
                style="red",
                markup=False,
            )
            return False

        payload = result["data"]
        workflow_results[f"api_response_{node_name}"] = payload

        failures = self._assertion_failures(payload, workflow_results, dynamic_values)
        if failures:
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
