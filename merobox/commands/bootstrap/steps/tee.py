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
  account in a group's member list.

These steps talk to the admin API over raw HTTP (``requests``), mirroring the
other admin-API helpers (``application.py``, ``join.py``). The merod admin API
serializes/deserializes in camelCase (serde ``rename_all = "camelCase"``), so the
admission-policy body must use ``allowedMrtd`` etc., not snake_case — a snake_case
body silently falls back to the server's empty default policy and rejects the
quote (see ``workflow-examples/scripts/set-tee-admission-policy.sh``).
"""

import json
import re
from typing import Any

import requests
import toml
from rich.markup import escape

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.constants import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from merobox.commands.result import fail, ok
from merobox.commands.utils import console

# Mock TDX quote measurements are all zero. MRTD is a 48-byte (SHA-384) value,
# hex-encoded as 96 characters.
# Every measurement in core's mock quote is the same 48 zero bytes
# (`create_mock_quote` in calimero-tee-attestation), so one constant covers MRTD
# and all four RTMRs.
ZERO_MEASUREMENT = "0" * 96
# The historical name, kept because a policy that pins MRTD reads better with it.
ZERO_MRTD = ZERO_MEASUREMENT

# How long to wait for the fleet-join response. The node answers only after its
# own bounded waits: a direct admission request (up to ~35 s since core
# 0.11.0-rc.79), then up to 30 s for admission (MAX_ADMISSION_WAIT), then joining
# the group's contexts and publishing auto-follow. That can legitimately run past
# a minute, so 60 s cut off a join the node was still completing. This matches
# core's own client (`FLEET_JOIN_REQUEST_TIMEOUT`, 3 min, core#4455), so a step
# gives up no sooner than `meroctl tee fleet-join` does.
_FLEET_JOIN_READ_TIMEOUT = 180.0

# The admission policy's `mode`: "replica" admits TEEs as ReadOnlyTee, "relay"
# as RelayTee. Core treats an absent mode as "replica".
TEE_ADMISSION_MODES = ("replica", "relay")

# Both roles an attestation admission can mint. Only RelayTee relays members'
# delegated writes; either one is an attested TEE member.
TEE_ROLES = ("ReadOnlyTee", "RelayTee")
GROUP_MEMBER_ROLES = ("Admin", "Member", "ReadOnly", *TEE_ROLES)


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
    mock MRTD, RTMR1, RTMR2 and RTMR3 (``ZERO_MEASUREMENT``). Overriding
    ``allowed_mrtd`` / ``allowed_rtmrN`` / ``allowed_tcb_statuses`` lets
    workflows pin real measurements instead.

    RTMR1-3 are defaulted and RTMR0 is not, because core requires at least one
    value for each of RTMR1-3 and accepts an empty list for RTMR0. MRTD
    identifies the firmware, which every image profile of a release shares, so
    the image is in RTMR1-3: RTMR3 since core 0.11.0-rc.42, and RTMR1 (the
    kernel) and RTMR2 (the command line and initrd) since rc.45 (core#4062),
    because RTMR3 alone is reproducible by a custom kernel. A default of ``[]``
    made every workflow using this step fail with a 400 the moment each check
    landed.

    ``mode`` ("replica" | "relay") is sent only when the step sets it. Core
    before 0.11.0-rc.62 rejects any body carrying ``mode`` (serde
    ``deny_unknown_fields``), so omitting it keeps existing workflows working
    against older images; core reads an absent mode as "replica".
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
        mode = self.config.get("mode")
        if "mode" in self.config and mode not in TEE_ADMISSION_MODES:
            raise ValueError(
                f"Step '{step_name}': 'mode' must be one of "
                f"{', '.join(repr(m) for m in TEE_ADMISSION_MODES)}, "
                f"got {mode!r}"
            )

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
                "allowed_rtmr1", [ZERO_MEASUREMENT], workflow_results, dynamic_values
            ),
            "allowedRtmr2": self._resolve_list(
                "allowed_rtmr2", [ZERO_MEASUREMENT], workflow_results, dynamic_values
            ),
            "allowedRtmr3": self._resolve_list(
                "allowed_rtmr3", [ZERO_MEASUREMENT], workflow_results, dynamic_values
            ),
            "allowedTcbStatuses": self._resolve_list(
                "allowed_tcb_statuses", [], workflow_results, dynamic_values
            ),
        }
        mode = self.config.get("mode")
        if mode is not None:
            body["mode"] = mode

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
                hint = ""
                if (
                    mode is not None
                    and response.status_code == 400
                    and "mode" in response.text
                ):
                    hint = (
                        " (this node predates the policy 'mode' field; "
                        "admitting relays needs core >= 0.11.0-rc.62)"
                    )
                result = fail(
                    f"set_tee_admission_policy returned HTTP {response.status_code}: "
                    f"{response.text}{hint}"
                )
            else:
                # Tolerate an empty/`null` body (e.g. a bare 200) so downstream
                # readers always get a dict, not None.
                result = ok(self._parse_json(response.text) or {})
        except Exception as e:
            result = fail(f"set_tee_admission_policy failed: {e}", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                return self._report_expected_failure(self._failure_detail(result))
            console.print(
                f"[red]set_tee_admission_policy failed on {node_name}: "
                f"{escape(str(result.get('error')))}[/red]"
            )
            return False

        workflow_results[f"set_tee_admission_policy_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Set TEE admission policy (acceptMock={accept_mock}"
            f"{f', mode={mode}' if mode is not None else ''}) "
            f"for group {group_id} on {node_name}[/green]"
        )
        if expected_failure:
            return self._report_unexpected_success()
        return True


_TCP_LISTEN = re.compile(r"^/ip4/[^/]+/tcp/(\d+)$")


def _loopback_p2p_addr(manager: Any, node_name: str) -> str:
    """``/ip4/127.0.0.1/tcp/<swarm port>/p2p/<peer id>`` for a local binary node.

    Read from the node's own ``config.toml`` (``identity.peer_id`` and the TCP
    entry of ``swarm.listen``), which ``merod init`` wrote before the node ever
    ran — so it names the port the node really listens on, not the one the
    workflow asked for. Binary mode only: a Docker node is reached on its
    container IP, which this does not resolve; pass ``admitter_addrs`` there.
    """
    from merobox.commands.binary_manager import BinaryManager

    if not isinstance(manager, BinaryManager):
        raise ValueError(
            f"admitter_nodes: cannot resolve '{node_name}' outside binary mode "
            "(--no-docker); pass its multiaddr in admitter_addrs instead"
        )
    config_file = getattr(manager, "node_config_files", {}).get(node_name)
    if not config_file:
        raise ValueError(f"admitter_nodes: '{node_name}' has no recorded config.toml")
    with open(config_file, encoding="utf-8") as f:
        config = toml.load(f)
    peer_id = config.get("identity", {}).get("peer_id")
    if not peer_id:
        raise ValueError(f"admitter_nodes: no identity.peer_id in {config_file}")
    for listen in config.get("swarm", {}).get("listen", []):
        match = _TCP_LISTEN.match(str(listen))
        if match:
            return f"/ip4/127.0.0.1/tcp/{match.group(1)}/p2p/{peer_id}"
    raise ValueError(f"admitter_nodes: no TCP swarm.listen entry in {config_file}")


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

    Direct admission (``admitterAddrs``): besides the gossip broadcast, the
    replica can ask named peers for admission directly. Name them as

    - ``admitter_addrs`` — literal libp2p multiaddrs ending in ``/p2p/<peer>``;
    - ``admitter_nodes`` — workflow node names, resolved (binary mode only) to
      ``/ip4/127.0.0.1/tcp/<swarm port>/p2p/<peer id>`` from the node's own
      ``config.toml``.

    Both may be combined. Omitting both sends no ``admitterAddrs`` at all, so
    the body stays byte-for-byte what older steps sent (broadcast only).
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        for field in ("admitter_addrs", "admitter_nodes"):
            value = self.config.get(field)
            if value is not None and not (
                isinstance(value, list) and all(isinstance(v, str) for v in value)
            ):
                raise ValueError(
                    f"Step '{step_name}': '{field}' must be a list of strings"
                )

    def _admitter_addrs(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> list[str]:
        """The literal addresses, then one per named node, in that order."""
        addrs = [
            self._resolve_dynamic_value(a, workflow_results, dynamic_values)
            for a in self.config.get("admitter_addrs") or []
        ]
        for node in self.config.get("admitter_nodes") or []:
            addrs.append(_loopback_p2p_addr(self.manager, node))
        return addrs

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )

        try:
            body: dict[str, Any] = {"groupId": group_id}
            admitter_addrs = self._admitter_addrs(workflow_results, dynamic_values)
            if admitter_addrs:
                body["admitterAddrs"] = admitter_addrs
                console.print(
                    f"[cyan]tee_fleet_join on {node_name}: asking admitters "
                    f"{escape(str(admitter_addrs))} directly[/cyan]"
                )
            admin_url = self._get_node_rpc_url(node_name)
            url = f"{admin_url}/admin-api/tee/fleet-join"
            # The admin API deserializes camelCase (serde rename_all =
            # "camelCase"), so the body field must be `groupId` — a snake_case
            # `group_id` is rejected with HTTP 400 "missing field `groupId`".
            response = _admin_request(
                "POST",
                url,
                json=body,
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
            result = fail(f"tee_fleet_join failed: {e}", error=e)

        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                return self._report_expected_failure(self._failure_detail(result))
            console.print(
                f"[red]tee_fleet_join failed on {node_name}: "
                f"{escape(str(result.get('error')))}[/red]"
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
            return self._report_unexpected_success()
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
    """Assert that an ACCOUNT is present in a group's member list with a role.

    Takes the account, not the signing key. Membership is recorded against the
    account a key speaks for, so the member listing reports accounts — a key
    matches nothing, and no endpoint maps one to the other from outside the node
    that owns it. `tee_fleet_join` reports both; capture `account`.

    The field is named `account` rather than `identity` so a scenario written
    against the old shape fails required-field validation instead of silently
    comparing a key against an account and reporting the member absent.

    With ``role`` omitted, either TEE role passes — ``ReadOnlyTee`` (policy
    mode "replica") or ``RelayTee`` (mode "relay") — since both are what a
    successful fleet-join admission mints. Set ``role`` to pin one exactly.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "account"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id", "account"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        if "role" not in self.config:
            return
        role = self.config.get("role")
        if not isinstance(role, str):
            raise ValueError(f"Step '{step_name}': 'role' must be a string")
        # A placeholder resolves at run time; a literal must name a real role,
        # or a typo would only surface as "member not found".
        if "{{" not in role and role not in GROUP_MEMBER_ROLES:
            raise ValueError(
                f"Step '{step_name}': 'role' must be one of "
                f"{', '.join(GROUP_MEMBER_ROLES)}, got {role!r}"
            )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        account = self._resolve_dynamic_value(
            self.config["account"], workflow_results, dynamic_values
        )
        if "role" in self.config:
            role = self._resolve_dynamic_value(
                self.config["role"], workflow_results, dynamic_values
            )
            accepted_roles = (role,)
        else:
            role = " or ".join(TEE_ROLES)
            accepted_roles = TEE_ROLES

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
                and m.get("identity") == account
                and m.get("role") in accepted_roles
            ):
                console.print(
                    f"[green]✓ {account} is a '{m.get('role')}' member of group "
                    f"{group_id} on {node_name}[/green]"
                )
                return True

        console.print(
            f"[red]assert_tee_member: account {account} with role '{role}' "
            f"NOT found in group {group_id} on {node_name}. "
            f"Members: {json.dumps(members)}[/red]"
        )
        return False


class AssertNotMemberStep(BaseStep):
    """Assert that an ACCOUNT is ABSENT from a group's member list.

    Takes the account, for the same reason as `assert_tee_member` — and here the
    consequence of passing a key would be worse: a key matches nothing, so the
    assertion would PASS for a member that is present."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "account"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id", "account"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        account = self._resolve_dynamic_value(
            self.config["account"], workflow_results, dynamic_values
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
            m for m in members if isinstance(m, dict) and m.get("identity") == account
        ]
        if present:
            console.print(
                f"[red]assert_not_member: account {account} IS present in group "
                f"{group_id} on {node_name} (expected absent). "
                f"Entry: {json.dumps(present)}[/red]"
            )
            return False

        console.print(
            f"[green]✓ {account} is absent from group {group_id} on {node_name}[/green]"
        )
        return True
