"""Port a v1 workflow (nodes, namespaces, contexts) to a cloud-v2 workflow.

The two models line up like this:

| v1 | cloud-v2 |
| --- | --- |
| a node (`nodes: {count, prefix}`) | an account: a person, signed in with a passkey |
| `create_identity` on a node | another account |
| `install_application` | `upload_bundle` (the WASM, from the wasm map) |
| `create_namespace` | `create_org` |
| `create_namespace_invitation` / `join_namespace` | `invite` / `redeem_invite` |
| `create_group_in_namespace` / `add_group_members` | `create_team` / `team_add` |
| `update_member_role` / `remove_group_members` on the namespace | `set_role` / `remove_member` |
| `create_context` | `deploy` (in the org when created in a namespace) |
| `create_mesh` | `deploy` for the listed members |
| `join_context` | nothing to do: access comes from the org or the app's account list |
| `call` with `executor_public_key` | `call` with `as_key` |
| `wait_for_sync` | nothing to wait for: a read sees every committed write |

A call's `result` (`{"output": value}`) stays readable as `rpc`, so `json_assert`
statements written against v1 results keep working.

Steps with no counterpart are dropped and listed in the ported file's header:
subgroup visibility and capabilities, metadata, reparenting, sync controls,
device pairing, blobs, node restarts (a user's node going offline has no
counterpart in a cluster), admin-API and log assertions, scripts.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

#: v1 step types that have no counterpart, with the reason recorded in the port.
DROPPED: dict[str, str] = {
    "wait_for_sync": "replication is synchronous: a read sees every committed write",
    "sync_group": "replication is synchronous",
    "set_group_metadata": "group metadata is v1 governance",
    "get_group_metadata": "group metadata is v1 governance",
    "set_member_metadata": "member metadata is v1 governance",
    "get_member_metadata": "member metadata is v1 governance",
    "set_subgroup_visibility": "subgroup visibility is v1 governance",
    "set_default_visibility": "subgroup visibility is v1 governance",
    "join_subgroup_inheritance": "subgroup inheritance is v1 governance",
    "set_default_capabilities": "capabilities are v1 governance",
    "set_member_capabilities": "capabilities are v1 governance",
    "get_member_capabilities": "capabilities are v1 governance",
    "reparent_group": "reparenting is v1 governance",
    "list_subgroups": "v1 group listing",
    "list_group_members": "v1 group listing",
    "list_group_contexts": "v1 group listing",
    "list_namespaces": "v1 namespace listing",
    "list_namespace_groups": "v1 namespace listing",
    "get_group_info": "v1 group info",
    "delete_group": "v1 group deletion",
    "delete_namespace": "v1 namespace deletion",
    "leave_group": "v1 group membership",
    "create_group": "v1 standalone group",
    "join_group": "v1 standalone group",
    "create_group_invitation": "v1 standalone group",
    "invite_open": "v1 open invitations",
    "join_open": "v1 open invitations",
    "upgrade_group": "app upgrades are per app instance (http POST apps/{app}/upgrade)",
    "get_migration_status": "v1 group migration",
    "assert_migration_complete": "v1 group migration",
    "account_pair": "devices: in release-v2 a person is their account",
    "stop_node": "a user's node going offline has no counterpart in a cluster",
    "start_node": "a user's node going offline has no counterpart in a cluster",
    "upload_blob": "release-v2 has no blob API yet",
    "download_blob": "release-v2 has no blob API yet",
    "assert_api_response": "v1 admin API",
    "assert_log_present": "v1 node logs",
    "assert_log_absent": "v1 node logs",
    "script": "scripts drive v1 node RPC URLs",
    "fuzzy_test": "v1 fuzzy runner",
}

_V1_ROLE = {
    "admin": "admin",
    "member": "member",
    "readonly": "guest",
    "read_only": "guest",
}


class Porter:
    def __init__(self, wf: dict, wasm_map: dict, base_port: int):
        self.wf = wf
        self.wasm_map = wasm_map
        self.base_port = base_port
        self.accounts: list[str] = list(self._node_names())
        self.apps: dict[str, str] = {}  # var holding an application id -> bundle path
        self.orgs: set[str] = set()  # vars holding a namespace (an org)
        self.teams: dict[str, str] = {}  # var holding a subgroup (team) -> its org var
        self.identities = Counter()
        self.dropped: Counter = Counter()
        self.notes: list[str] = []

    # ---------------------------------------------------------------- helpers

    def _node_names(self) -> list[str]:
        nodes = self.wf.get("nodes") or {}
        if "count" in nodes:
            prefix = nodes.get("prefix", "calimero-node")
            return [f"{prefix}-{i}" for i in range(1, int(nodes["count"]) + 1)]
        config = {
            "image",
            "mdns",
            "chain_id",
            "log_level",
            "base_port",
            "base_rpc_port",
        }
        return [k for k in nodes if k not in config]

    @staticmethod
    def _var(expr: Any) -> str | None:
        """`'{{x}}'` -> `x`."""
        if isinstance(expr, str):
            e = expr.strip()
            if e.startswith("{{") and e.endswith("}}") and e.count("{{") == 1:
                return e[2:-2].strip()
        return None

    def _org_of(self, group_expr: Any) -> str:
        """The org a v1 group reference belongs to (a namespace is an org; a
        subgroup is a team of one)."""
        var = self._var(group_expr)
        if var in self.teams:
            return "{{" + self.teams[var] + "}}"
        return group_expr

    def _wasm(self, app_expr: Any, service: str | None) -> str:
        var = self._var(app_expr)
        bundle = self.apps.get(var or "")
        entry = self.wasm_map.get(bundle or "", {})
        wasm = entry.get(service or "") or entry.get("")
        if not wasm:
            self.notes.append(
                f"no wasm for bundle {bundle!r} service {service!r}: add it to the wasm map"
            )
            return "{{env.APP_WASM}}"
        return wasm

    @staticmethod
    def _call_outputs(outputs: dict | None) -> dict | None:
        if not outputs:
            return None
        out = {}
        for var, path in outputs.items():
            p = str(path)
            if p == "result":
                out[var] = "rpc"
            elif p == "result.output":
                out[var] = "result"
            elif p.startswith("result.output."):
                out[var] = "result." + p[len("result.output.") :]
            elif p.startswith("result."):
                out[var] = "rpc." + p[len("result.") :]
            else:
                out[var] = p
        return out

    @staticmethod
    def _keep(src: dict, dst: dict, *keys: str) -> dict:
        for k in keys:
            if k in src:
                dst[k] = src[k]
        return dst

    # ---------------------------------------------------------------- steps

    def port_steps(self, steps: list) -> list:
        out: list = []
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            out.extend(self.port_step(step))
        return out

    def port_step(self, s: dict) -> list:
        kind = s.get("type")
        base = {"name": s["name"]} if "name" in s else {}
        node = s.get("node")

        def acting(**fields: Any) -> dict:
            step = {**base, **{k: v for k, v in fields.items() if v is not None}}
            if node:
                step["account"] = node
            if s.get("executor_public_key"):
                step["as_key"] = s["executor_public_key"]
            for k in ("expected_failure", "allow_failure"):
                if s.get(k):
                    step[k] = True
            return step

        if kind in DROPPED:
            self.dropped[kind] += 1
            return []
        if kind in ("assert", "json_assert", "wait"):
            step = dict(s)
            if kind == "wait":
                # v1 waited for peers to find each other and sync; a cluster
                # needs at most a moment.
                step["seconds"] = min(float(s.get("seconds", 0)), 1.0)
            return [step]
        if kind == "repeat":
            step = {**base, "type": "repeat", "count": s["count"]}
            step["steps"] = self.port_steps(s.get("steps"))
            return [step] if step["steps"] else []
        if kind == "install_application":
            outs = s.get("outputs") or {}
            var = next((v for v, p in outs.items() if p == "applicationId"), None)
            path = str(s.get("path"))
            if var:
                self.apps[var] = path
            entry = self.wasm_map.get(path, {})
            if not var:
                return []  # v1 installed on every node; one upload is enough
            return [
                acting(
                    type="upload_bundle",
                    wasm=entry.get("")
                    or next(iter(entry.values()), "{{env.APP_WASM}}"),
                    outputs={var: "hash"},
                )
            ]
        if kind == "create_namespace":
            outs = s.get("outputs") or {}
            var = next((v for v, p in outs.items() if p == "namespaceId"), None)
            if var:
                self.orgs.add(var)
            return [
                acting(
                    type="create_org",
                    org_name=s.get("name", "namespace"),
                    outputs={var: "orgId"} if var else None,
                )
            ]
        if kind == "create_group_in_namespace":
            outs = s.get("outputs") or {}
            var = next((v for v, p in outs.items() if p == "groupId"), None)
            org_var = self._var(s.get("namespace_id"))
            if var and org_var:
                self.teams[var] = org_var
            return [
                acting(
                    type="create_team",
                    org=s["namespace_id"],
                    team_name=s.get("group_name", "team"),
                    outputs={var: "teamId"} if var else None,
                )
            ]
        if kind == "create_namespace_invitation":
            group = s.get("group_id") or s.get("namespace_id")
            gvar = self._var(group)
            step = acting(type="invite", org=self._org_of(group), role="member")
            if gvar in self.teams:
                step["teams"] = [group]
            outs = s.get("outputs") or {}
            var = next((v for v, p in outs.items() if p == "invitation"), None)
            if var:
                step["outputs"] = {var: "token"}
            return [step]
        if kind == "join_namespace":
            steps = [acting(type="redeem_invite", token=s["invitation"])]
            outs = dict.fromkeys(s.get("outputs") or {}, "publicKey")
            if outs:
                steps.append({"type": "whoami", "account": node, "outputs": outs})
            return steps
        if kind == "add_group_members":
            group = s.get("group_id")
            members = [
                (
                    m.get("identity") or m.get("public_key") or m.get("member_id")
                    if isinstance(m, dict)
                    else m
                )
                for m in s.get("members") or []
            ]
            gvar = self._var(group)
            if gvar in self.teams:
                return [
                    acting(
                        type="team_add",
                        org=self._org_of(group),
                        team=group,
                        members=members,
                    )
                ]
            return [
                acting(type="set_role", org=group, member=m, role="member")
                for m in members
            ]
        if kind == "update_member_role":
            role = _V1_ROLE.get(str(s.get("role", "member")).lower(), "member")
            return [
                acting(
                    type="set_role",
                    org=self._org_of(s.get("group_id")),
                    member=s.get("member_id") or s.get("identity"),
                    role=role,
                )
            ]
        if kind == "remove_group_members":
            group = s.get("group_id")
            if self._var(group) in self.teams:
                self.dropped["remove_group_members (team)"] += 1
                return []
            return [
                acting(type="remove_member", org=group, member=m)
                for m in s.get("members") or []
            ]
        if kind == "create_identity":
            self.identities[node] += 1
            name = f"{node}-id{self.identities[node]}"
            self.accounts.append(name)
            outs = dict.fromkeys(s.get("outputs") or {}, "publicKey")
            return [{**base, "type": "whoami", "account": name, "outputs": outs}]
        if kind == "node_identity":
            outs = {}
            for v, p in (s.get("outputs") or {}).items():
                if p in ("accountId", "deviceId", "accountRootPublicKey"):
                    outs[v] = "publicKey"
            return [acting(type="whoami", outputs=outs)]
        if kind in ("create_context", "create_mesh"):
            creator = s.get("context_node") if kind == "create_mesh" else node
            group = s.get("group_id")
            step = {**base, "type": "deploy", "account": creator}
            step["wasm"] = self._wasm(s.get("application_id"), s.get("service_name"))
            step["app_name"] = s.get("service_name") or s.get("name", "app")
            if "params" in s:
                step["init_args"] = s["params"]
            if group:
                step["org"] = self._org_of(group)
            else:
                members = (
                    [creator, *(s.get("nodes") or [])] if kind == "create_mesh" else []
                )
                # Without a namespace, everyone in the scenario may reach it, as
                # anyone could be invited to a v1 context.
                step["access"] = {
                    "kind": "accounts",
                    "accounts": members or list(self.accounts),
                }
            outs = {}
            for v, p in (s.get("outputs") or {}).items():
                if p == "contextId":
                    outs[v] = "appId"
                elif p == "memberPublicKey":
                    outs[v] = "memberPublicKey"
            if outs:
                step["outputs"] = outs
            return [step]
        if kind == "join_context":
            steps: list = []
            outs = s.get("outputs") or {}
            keys = {v: "publicKey" for v, p in outs.items() if p == "memberPublicKey"}
            ctx = {v: s["context_id"] for v, p in outs.items() if p == "contextId"}
            if s.get("expected_failure"):
                steps.append(
                    acting(
                        type="http", method="get", path="apps/" + str(s["context_id"])
                    )
                )
            if keys:
                steps.append(
                    {**base, "type": "whoami", "account": node, "outputs": keys}
                )
            if ctx:
                steps.append({"type": "set", "values": ctx})
            return steps
        if kind == "call":
            step = acting(
                type="call",
                app=s["context_id"],
                method=s["method"],
                args=s.get("args") or {},
            )
            outs = self._call_outputs(s.get("outputs"))
            if outs:
                step["outputs"] = outs
            return [step]
        self.dropped[f"{kind} (unknown)"] += 1
        return []

    # ---------------------------------------------------------------- output

    def port(self) -> dict:
        steps = self.port_steps(self.wf.get("steps"))
        out: dict[str, Any] = {
            "name": self.wf.get("name", "ported workflow"),
        }
        if self.wf.get("description"):
            out["description"] = self.wf["description"]
        out["backend"] = "cloud-v2"
        out["cluster"] = {"nodes": 3, "base_port": self.base_port}
        out["accounts"] = self.accounts
        out["steps"] = steps
        return out

    def header(self, source: str) -> str:
        lines = [
            f"# Ported from {source} by `merobox cloud port`.",
            "# v1 nodes are accounts here; see merobox/cloud_v2/port.py for the mapping.",
        ]
        if self.dropped:
            lines.append("# Dropped (no release-v2 counterpart):")
            for kind, n in sorted(self.dropped.items()):
                reason = DROPPED.get(kind, "not supported by the port")
                lines.append(f"#   {kind} x{n}: {reason}")
        for note in self.notes:
            lines.append(f"# NOTE: {note}")
        return "\n".join(lines) + "\n"


def port_file(src: str, wasm_map: dict, base_port: int) -> tuple[str, Porter]:
    """Port one v1 workflow file; returns the YAML text and the porter (report)."""
    wf = yaml.safe_load(Path(src).read_text())
    porter = Porter(wf, wasm_map, base_port)
    body = yaml.safe_dump(porter.port(), sort_keys=False, allow_unicode=True, width=100)
    return porter.header(Path(src).name) + body, porter


def load_wasm_map(path: str | None) -> dict:
    """`{bundle path: {service: wasm path, "": default service}}`."""
    return json.loads(Path(path).read_text()) if path else {}
