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
quote. ``enabled`` is DERIVED server-side and is rejected as an unknown field,
so it is never sent.
"""

import json
from typing import Any

import requests

from merobox.commands.bootstrap.steps._env import EnvRefError, resolve_env_refs
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
                # `${ENV_VAR}` support so a workflow can pin a real published
                # measurement (read out of a release artifact into the env at
                # run time) without either hardcoding it in YAML or dropping to
                # a `script` step. An unset variable raises, which is the point:
                # an empty `allowedRtmr*` list means UNCONSTRAINED, so silently
                # sending an empty list would admit a node whose runtime
                # measurements do not match (mdma#297 / #309).
                resolve_env_refs(
                    self._resolve_dynamic_value(v, workflow_results, dynamic_values)
                )
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

        try:
            body = self._build_policy_body(
                accept_mock, workflow_results, dynamic_values
            )
        except EnvRefError as e:
            # A missing measurement must fail the step, never fall back to an
            # empty (== unconstrained) allowlist.
            console.print(f"[red]set_tee_admission_policy on {node_name}: {e}[/red]")
            return False

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

    def _build_policy_body(
        self,
        accept_mock: bool,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the camelCase policy body.

        ``enabled`` is deliberately absent: merod DERIVES it from the rest of
        the policy and rejects it as an unknown field.
        """
        return {
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
        if admitted:
            console.print(
                f"[green]✓ tee_fleet_join on {node_name}: status={status} "
                f"admitted=True[/green]"
            )
        else:
            # Not a failure (the HTTP call succeeded), but make the not-yet-
            # admitted case visually distinct from an admission so CI logs don't
            # read a green ✓ as "admitted". assert_tee_member is the real gate.
            console.print(
                f"[yellow]• tee_fleet_join on {node_name}: status={status} "
                f"admitted=False (no admission this window — "
                f"assert_tee_member is the gate)[/yellow]"
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
    """Assert a group has a member with a given role (and optionally identity).

    Defaults to ``role="ReadOnlyTee"`` — the role a TEE fleet node holds after a
    successful fleet-join admission.

    ``identity`` is OPTIONAL. Omit it to assert only that *some* member holds
    the role, and export the one that does:

        - name: Assert a fleet node was admitted
          type: assert_tee_member
          node: ha-owner-1
          group_id: "{{ns}}"
          role: ReadOnlyTee
          outputs:
            fleet_identity: identity

    That export is what makes the cloud cross-check possible: the admitted
    member's identity must equal the cloud's ``executor_account`` for the same
    namespace, which is the cheapest end-to-end proof that the node the owner
    admitted and the node the cloud is advertising are the same node.

    When more than one member holds the role the FIRST is exported and the
    step says so — pass an explicit ``identity`` if a workflow ever needs to
    disambiguate.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        for field in ("identity", "role"):
            if field in self.config and not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        if "non_blocking" in self.config and not isinstance(
            self.config.get("non_blocking"), bool
        ):
            raise ValueError(f"Step '{step_name}': 'non_blocking' must be a boolean")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        identity = (
            self._resolve_dynamic_value(
                self.config["identity"], workflow_results, dynamic_values
            )
            if self.config.get("identity") is not None
            else None
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

        matches = [
            m
            for m in members
            if isinstance(m, dict)
            and m.get("role") == role
            and (identity is None or m.get("identity") == identity)
        ]

        if matches:
            match = matches[0]
            if identity is None and len(matches) > 1:
                console.print(
                    f"[yellow]• {len(matches)} members hold role '{role}' in "
                    f"group {group_id}; exporting the first "
                    f"({match.get('identity')})[/yellow]"
                )
            workflow_results[f"assert_tee_member_{node_name}"] = match
            self._export_variables(match, node_name, dynamic_values)
            console.print(
                f"[green]✓ {match.get('identity')} is a '{role}' member of group "
                f"{group_id} on {node_name}[/green]"
            )
            return True

        wanted = (
            f"identity {identity} with role '{role}'" if identity else f"role '{role}'"
        )
        message = (
            f"assert_tee_member: no member with {wanted} "
            f"in group {group_id} on {node_name}. "
            f"Members: {json.dumps(members)}"
        )
        # `non_blocking` exists for the same reason the `assert` step has it:
        # a workflow that mutates remote state (e.g. a cloud HA request) has to
        # reach its cleanup steps even when the assertion it came to make fails.
        if self.config.get("non_blocking", False):
            console.print(f"[yellow]⚠️  {message} (non_blocking)[/yellow]")
            return True
        console.print(f"[red]{message}[/red]")
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
