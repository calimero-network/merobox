"""
TEE (mock-fleet) workflow step executors.

Drive the local mock-TEE fleet lifecycle against a node's merod admin API:

- ``set_tee_admission_policy`` — set the namespace-root TeeAdmissionPolicy so the
  owner/verifier accepts mock attestations (all-zero MRTD). HTTP only; merod has
  no ``meroctl tee policy set`` subcommand.
- ``tee_fleet_join`` — run fleet-join from a ``--mock-tee`` replica
  (``POST /admin-api/tee/fleet-join``). Designed to compose with ``repeat`` since
  a single call covers one mesh window and may need re-invoking until admitted.
- ``assert_tee_member`` / ``assert_not_member`` — assert (presence|absence) of an
  identity in a group's member list.

These steps talk to the admin API over raw HTTP (``requests``), mirroring the
other admin-API helpers (``application.py``, ``join.py``). The merod admin API
serializes/deserializes in camelCase (serde ``rename_all = "camelCase"``), so the
admission-policy body must use ``allowedMrtd`` etc., not snake_case — a snake_case
body silently falls back to the server's empty default policy and rejects the
quote (see ``workflow-examples/scripts/set-tee-admission-policy.sh``).
"""

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

# Mock TDX quote measurements are all zero. MRTD is a 48-byte (SHA-384) value,
# hex-encoded as 96 characters.
ZERO_MRTD = "0" * 96

# Fleet-join blocks server-side for one admission window (core MAX_ADMISSION_WAIT
# ~= 30s) before returning admitted/announced, so allow a generous read timeout.
_FLEET_JOIN_READ_TIMEOUT = 60.0


def _admin_request(method: str, url: str, **kwargs) -> requests.Response:
    """Issue an admin-API HTTP request with merobox's default timeouts.

    These TEE steps target a node's admin API on loopback for local mock-TEE /
    e2e workflows, so no Authorization header is attached (same posture as the
    other raw admin-API helpers in the harness). If a workflow ever needs to
    drive an auth-enabled node, attach a token via ``kwargs['headers']``.
    """
    kwargs.setdefault("timeout", (DEFAULT_CONNECTION_TIMEOUT, DEFAULT_READ_TIMEOUT))
    return requests.request(method, url, **kwargs)


class SetTeeAdmissionPolicyStep(BaseStep):
    """Set a namespace root's TeeAdmissionPolicy via the admin API.

    Defaults accept mock attestations: ``accept_mock=True`` plus the all-zero
    mock MRTD (``ZERO_MRTD``). Overriding ``allowed_mrtd`` / ``allowed_rtmrN`` /
    ``allowed_tcb_statuses`` lets workflows pin real measurements instead.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        if "accept_mock" in self.config and not isinstance(
            self.config.get("accept_mock"), bool
        ):
            raise ValueError(f"Step '{step_name}': 'accept_mock' must be a boolean")
        for field in (
            "allowed_mrtd",
            "allowed_rtmr0",
            "allowed_rtmr1",
            "allowed_rtmr2",
            "allowed_rtmr3",
            "allowed_tcb_statuses",
        ):
            if field in self.config and not isinstance(self.config.get(field), list):
                raise ValueError(f"Step '{step_name}': '{field}' must be a list")

    def _resolve_list(
        self,
        field: str,
        default: list,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> list:
        values = self.config.get(field, default)
        return [
            (
                self._resolve_dynamic_value(v, workflow_results, dynamic_values)
                if isinstance(v, str)
                else v
            )
            for v in values
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        accept_mock = self.config.get("accept_mock", True)

        body = {
            "acceptMock": accept_mock,
            "allowedMrtd": self._resolve_list(
                "allowed_mrtd", [ZERO_MRTD], workflow_results, dynamic_values
            ),
            "allowedRtmr0": self._resolve_list(
                "allowed_rtmr0", [], workflow_results, dynamic_values
            ),
            "allowedRtmr1": self._resolve_list(
                "allowed_rtmr1", [], workflow_results, dynamic_values
            ),
            "allowedRtmr2": self._resolve_list(
                "allowed_rtmr2", [], workflow_results, dynamic_values
            ),
            "allowedRtmr3": self._resolve_list(
                "allowed_rtmr3", [], workflow_results, dynamic_values
            ),
            "allowedTcbStatuses": self._resolve_list(
                "allowed_tcb_statuses", [], workflow_results, dynamic_values
            ),
        }

        try:
            admin_url = self._get_node_rpc_url(node_name)
            url = (
                f"{admin_url}/admin-api/groups/{group_id}/settings/tee-admission-policy"
            )
            # No fleet-join-style timeout override: this PUT is a fast
            # owner-local policy write, not a blocking admission window, so the
            # default read timeout is intentional.
            response = _admin_request("PUT", url, json=body)
            if response.status_code != 200:
                result = fail(
                    f"set_tee_admission_policy returned HTTP {response.status_code}: "
                    f"{response.text}"
                )
            else:
                # Tolerate an empty/`null` body (e.g. a bare 200) so downstream
                # readers always get a dict, not None.
                result = ok(self._parse_json(response.text) or {})
        except Exception as e:
            result = fail("set_tee_admission_policy failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]set_tee_admission_policy failed on {node_name}: "
                f"{result.get('error')}[/red]"
            )
            return False

        workflow_results[f"set_tee_admission_policy_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Set TEE admission policy (acceptMock={accept_mock}) "
            f"for group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class TeeFleetJoinStep(BaseStep):
    """Run fleet-join from a TEE replica (``meroctl tee fleet-join``).

    Calls ``POST /admin-api/tee/fleet-join`` — the same admin endpoint the
    ``meroctl tee fleet-join <GROUP_ID>`` command invokes. The response's
    ``admitted`` flag is parsed and stored. A single call covers one mesh
    window, so compose this with the ``repeat`` step to retry until admitted.

    The step returns ``True`` (the HTTP call succeeded) even when
    ``admitted=False`` — a single window simply may not have admitted yet. Use
    ``assert_tee_member`` as the authoritative admission gate, not the per-call
    ``admitted`` flag.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )

        try:
            admin_url = self._get_node_rpc_url(node_name)
            url = f"{admin_url}/admin-api/tee/fleet-join"
            # The admin API deserializes camelCase (serde rename_all =
            # "camelCase"), so the body field must be `groupId` — a snake_case
            # `group_id` is rejected with HTTP 400 "missing field `groupId`".
            response = _admin_request(
                "POST",
                url,
                json={"groupId": group_id},
                timeout=(DEFAULT_CONNECTION_TIMEOUT, _FLEET_JOIN_READ_TIMEOUT),
            )
            if response.status_code != 200:
                result = fail(
                    f"tee_fleet_join returned HTTP {response.status_code}: "
                    f"{response.text}"
                )
            else:
                payload = self._parse_json(response.text)
                if not isinstance(payload, dict):
                    # A 200 with a non-dict body means the response envelope
                    # changed — surface it instead of reporting admitted=False.
                    result = fail(
                        "tee_fleet_join: unexpected response shape: "
                        f"{response.text[:200]}"
                    )
                else:
                    result = ok(payload)
        except Exception as e:
            result = fail("tee_fleet_join failed", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]tee_fleet_join failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        data = result["data"] if isinstance(result["data"], dict) else {}
        admitted = bool(data.get("admitted", False))
        # Write the coerced bool back so an `outputs:`-driven export of `admitted`
        # sees the same canonical value as `tee_fleet_join_admitted_{node}` (the
        # server may omit the key or send null, which would otherwise export as None).
        data["admitted"] = admitted

        workflow_results[f"tee_fleet_join_{node_name}"] = data
        workflow_results[f"tee_fleet_join_admitted_{node_name}"] = admitted
        self._export_variables(data, node_name, dynamic_values)

        status = data.get("status", "unknown")
        console.print(
            f"[green]✓ tee_fleet_join on {node_name}: status={status} "
            f"admitted={admitted}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


def _fetch_members(step: BaseStep, node_name: str, group_id: str) -> list[dict]:
    """GET a group's member list from the admin API.

    Returns the ``members`` array (each entry has ``identity`` / ``role`` /
    ``name``). Raises on HTTP error so callers report a clear failure.
    """
    admin_url = step._get_node_rpc_url(node_name)
    url = f"{admin_url}/admin-api/groups/{group_id}/members"
    response = _admin_request("GET", url)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    payload = step._parse_json(response.text)
    if not isinstance(payload, dict):
        # A 200 with an unexpected (non-dict) body means the server returned
        # something other than the members envelope — surface it instead of
        # reporting the identity as absent.
        raise RuntimeError(f"Unexpected members response shape: {response.text[:200]}")
    members = payload.get("members", [])
    return members if isinstance(members, list) else []


class AssertTeeMemberStep(BaseStep):
    """Assert that an identity is present in a group's member list with a role.

    Defaults to ``role="ReadOnlyTee"`` — the role a TEE fleet node holds after a
    successful fleet-join admission.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "identity"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id", "identity"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        if "role" in self.config and not isinstance(self.config.get("role"), str):
            raise ValueError(f"Step '{step_name}': 'role' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        identity = self._resolve_dynamic_value(
            self.config["identity"], workflow_results, dynamic_values
        )
        role = self._resolve_dynamic_value(
            self.config.get("role", "ReadOnlyTee"), workflow_results, dynamic_values
        )

        try:
            members = _fetch_members(self, node_name, group_id)
        except Exception as e:
            console.print(
                f"[red]assert_tee_member failed to list members on "
                f"{node_name}: {str(e)}[/red]"
            )
            return False

        for m in members:
            if (
                isinstance(m, dict)
                and m.get("identity") == identity
                and m.get("role") == role
            ):
                console.print(
                    f"[green]✓ {identity} is a '{role}' member of group "
                    f"{group_id} on {node_name}[/green]"
                )
                return True

        console.print(
            f"[red]assert_tee_member: identity {identity} with role '{role}' "
            f"NOT found in group {group_id} on {node_name}. "
            f"Members: {json.dumps(members)}[/red]"
        )
        return False


class AssertNotMemberStep(BaseStep):
    """Assert that an identity is ABSENT from a group's member list."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "identity"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id", "identity"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        identity = self._resolve_dynamic_value(
            self.config["identity"], workflow_results, dynamic_values
        )

        try:
            members = _fetch_members(self, node_name, group_id)
        except Exception as e:
            console.print(
                f"[red]assert_not_member failed to list members on "
                f"{node_name}: {str(e)}[/red]"
            )
            return False

        present = [
            m for m in members if isinstance(m, dict) and m.get("identity") == identity
        ]
        if present:
            console.print(
                f"[red]assert_not_member: identity {identity} IS present in group "
                f"{group_id} on {node_name} (expected absent). "
                f"Entry: {json.dumps(present)}[/red]"
            )
            return False

        console.print(
            f"[green]✓ {identity} is absent from group {group_id} on {node_name}[/green]"
        )
        return True
