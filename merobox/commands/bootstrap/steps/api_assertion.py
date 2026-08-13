"""
Raw admin-API response assertion.

``calimero-client-py`` is compiled from a pinned core revision and its DTOs drop
any response key that build does not know about, silently, on deserialize. So a
field newly added to core reads as missing through every typed step, and a
brand-new field is indistinguishable from a broken one. This step issues the
HTTP request itself and asserts against the parsed body, so the assertion sees
what the node actually sent.
"""

from __future__ import annotations

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

# Distinguishes an absent key from one present with a null value. Core marks
# optional fields `skip_serializing_if = "Option::is_none"`, so omission is the
# observable difference between None and a value, and collapsing the two would
# blind the step to exactly the case it exists to test.
_MISSING = object()


def _lookup(payload: Any, path: str) -> Any:
    """Value at a dotted path, or ``_MISSING`` if the path does not exist.

    Unlike ``BaseStep._get_value`` this re-parses nothing on the way down: a
    string stays a string, so the assertion sees the body verbatim.
    """
    current = payload
    for segment in path.split("."):
        if isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if index >= len(current):
                return _MISSING
            current = current[index]
        elif isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return _MISSING
    return current


class AssertApiResponseStep(BaseStep):
    """GET a path on a node's admin API and assert against the JSON it returned.

    Required fields: ``node``, ``path``, and at least one of ``match`` /
    ``present`` / ``absent``.

    Optional fields:
    - ``match`` (dict): dotted paths mapped to expected values. ``null`` asserts
      the key is present AND null, which an absent key does not satisfy.
    - ``present`` (list): dotted paths that must exist, whatever their value.
    - ``absent`` (list): dotted paths that must not exist.
    - ``token`` (str): explicit JWT (supports ``{{placeholders}}``); otherwise
      the token a prior ``login`` cached for the node, or none at all on a node
      running without auth.

    Paths address the body verbatim, envelope included: the admin API wraps
    payloads in ``data`` and serializes camelCase, so a field reads
    ``data.fleetCompletedAt``.

    One request, no polling - the other assertion steps do not poll either.
    Compose with ``repeat`` or ``wait_for_sync`` when the assertion has to wait
    for state to settle.

    ``expected_failure`` / ``expected_error`` govern the request outcome only.
    An assertion that misses always fails the step: a miss satisfying
    ``expected_failure`` would turn a typo in a path into a green negative
    test, which is the failure the flag exists to prevent.
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
        # Iterating a dict yields its keys and a list its elements, so one loop
        # covers all three fields.
        for field in ("match", "present", "absent"):
            for path in self.config.get(field) or []:
                if not isinstance(path, str) or not path.strip():
                    raise ValueError(
                        f"Step '{self._get_step_name()}': '{field}' entries must "
                        f"be non-empty dotted paths into the response body"
                    )
        if not self._assertion_count() and not self._is_expected_failure():
            # An assertion step with nothing to assert passes unconditionally,
            # the silent no-op `expected_error` exists to close.
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
            response = requests.get(
                f"{rpc_url.rstrip('/')}/{path.lstrip('/')}",
                headers=headers,
                timeout=(DEFAULT_CONNECTION_TIMEOUT, DEFAULT_READ_TIMEOUT),
            )
            # The whole 2xx range, not just 200: a route answering 201/204 has
            # served the request, and rejecting it would report a working
            # endpoint as an HTTP failure.
            if not 200 <= response.status_code < 300:
                result = fail(
                    f"GET {path} on {node_name} returned HTTP "
                    f"{response.status_code}: {response.text}"
                )
            else:
                # json.loads, not _parse_json: its fallbacks would repair a body
                # the node should not have sent, and hide that it did.
                result = ok(json.loads(response.text))
        except json.JSONDecodeError as e:
            result = fail(f"GET {path} on {node_name} returned a non-JSON body: {e}")
        except Exception as e:
            result = fail(f"GET {path} on {node_name} failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                return self._report_expected_failure(self._failure_detail(result))
            # markup=False so a response body containing brackets survives Rich.
            console.print(
                f"✗ assert_api_response: {result['error']}",
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
