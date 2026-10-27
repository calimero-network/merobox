"""Port a v1 workflow (nodes, namespaces, contexts) to a cloud-v2 workflow.

The two models line up like this:

| v1 | cloud-v2 |
| --- | --- |
| a node (`nodes: {count, prefix}`) | an account: a person, signed in with a passkey |
| `create_identity` on a node | that node's account |
| `account_pair` (a second device) | `add_device`: another passkey on the holder's account, signed in on the paired node |
| `install_application` | `install_package` when the wasm map names the signed `.mpk` (`_mpk`): installed by name from the cluster's registry; else `upload_bundle` (the WASM) |
| `create_namespace` | `create_org` |
| `create_namespace_invitation` / `join_namespace` | `invite` / `redeem_invite` |
| `create_group_in_namespace` | `create_team` (restricted unless `visibility: open`) |
| `add_group_members` / `update_member_role` / `remove_group_members` | `team_add` (with the row's role) / `team_remove`, or org roles on the namespace |
| `set_subgroup_visibility` / `reparent_group` / `set_group_metadata` | `team_update` (`join`, `parent`, `metadata`) |
| `get_group_info` / `get_group_metadata` / `list_group_members` / `list_group_contexts` | `team_get` |
| `list_subgroups` / `join_subgroup_inheritance` / `delete_group` | `team_list` / `team_join` / `team_delete` |
| `set_member_capabilities` / `set_default_capabilities` | `capabilities` |
| `get_member_capabilities` | `member_capabilities` (the team's row, or the organization's) |
| `create_context` | `deploy`: in the org; scoped to the team in a subgroup |
| `create_mesh` | `deploy` for the listed members |
| `join_context` | nothing to do: access comes from the org, the team or the app's list |
| `invite_open` / `join_open` | the context the invitation names |
| `upload_blob` / `download_blob` | the same steps, on the app's blobs |
| `upgrade_group` | `upgrade_app` for each app deployed in the group: by `version` for a package, else by bundle (the node plans the migration) |
| `get_migration_status` | `migration_status`: each app's release (an upgrade runs at once) |
| `wait` | the same wait: a name cannot tell a sync wait from a lease or a timer |
| `fuzzy_test` | `fuzzy_test`: the same weighted operations, across accounts |
| `call` with `executor_public_key` | `call` with `as_key` |
| `wait_for_sync` | nothing to wait for: a read sees every committed write |
| `parallel` | its groups' steps, one group after another: the app's log orders concurrent calls |

A call's `result` (`{"output": value}`) stays readable as `rpc`, so `json_assert`
statements written against v1 results keep working.

Steps with no counterpart are dropped and listed in the ported file's header:
sync controls, a node going offline or cut off from its peers (`stop_node`,
`partition_peers`, `disconnect_node`, `pause_container`, `inject_network_fault`:
a cluster has no user replica to partition, so what a node read meanwhile is
not asserted), member metadata, admin-API and log assertions, scripts.

A port that no longer tests what its v1 workflow did is marked
`not_applicable` with the reason, and is neither run nor counted as a pass: one
that drops most of what the workflow reads or removes (`list_*`, `get_*`,
`delete_*`), keeps under half of v1's checks (a dropped log or script check
counts twice: it is how v1 saw what a result cannot show), or never runs the
app and keeps none.
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
    "set_member_metadata": "member metadata is v1 governance",
    "get_member_metadata": "member metadata is v1 governance",
    "set_default_visibility": "subgroup visibility is v1 governance",
    "list_namespaces": "v1 namespace listing",
    "list_namespace_groups": "v1 namespace listing",
    "delete_namespace": "v1 namespace deletion",
    "assert_migration_complete": "v1 group migration",
    "stop_node": "a user's node going offline has no counterpart in a cluster",
    "start_node": "a user's node going offline has no counterpart in a cluster",
    "partition_peers": "a user's node cut off from its peers has no counterpart in a cluster",
    "heal_peers": "a user's node cut off from its peers has no counterpart in a cluster",
    "disconnect_node": "a user's node cut off from its peers has no counterpart in a cluster",
    "connect_node": "a user's node cut off from its peers has no counterpart in a cluster",
    "pause_container": "a user's node going offline has no counterpart in a cluster",
    "unpause_container": "a user's node going offline has no counterpart in a cluster",
    "inject_network_fault": "a lossy link to a user's node has no counterpart in a cluster",
    "restart_container": "a user's node restarting has no counterpart in a cluster",
    "assert_api_response": "v1 admin API",
    "assert_log_present": "v1 node logs",
    "assert_log_absent": "v1 node logs",
    "script": "scripts drive v1 node RPC URLs",
}

#: v1 steps that cut a node off (`node`/`container` -> its partition) and
#: those that end it. While any is in effect, what a node reads is its side of
#: the partition, which a cluster never has.
_PARTITION_STARTS = {
    "stop_node",
    "partition_peers",
    "disconnect_node",
    "pause_container",
    "inject_network_fault",
}
_PARTITION_ENDS = {
    "start_node": "stop_node",
    "heal_peers": "partition_peers",
    "connect_node": "disconnect_node",
    "unpause_container": "pause_container",
}


def _partition_key(kind: str, s: dict) -> str:
    """The partition a v1 step starts or ends: its kind (the starting one) and
    the node it names, so `heal_peers` ends what `partition_peers` began."""
    start = _PARTITION_ENDS.get(kind, kind)
    who = s.get("node") or s.get("nodes") or s.get("container")
    if start == "stop_node":
        # v1 stopped and started a node by either name.
        return str(who)
    return f"{start}:{who}"


#: v1 group roles as a team row's role.
_TEAM_ROLE = {
    "admin": "lead",
    "member": "member",
    "readonly": "readOnly",
    "read_only": "readOnly",
}

#: v1 subgroup visibility as a team's join.
_JOIN = {"open": "everyone", "restricted": "invite"}

_V1_ROLE = {
    "admin": "admin",
    "member": "member",
    "readonly": "guest",
    "read_only": "guest",
}


#: v1 steps that check something; a port that drops most of them is hollow.
_CHECKS = {"assert_log_present", "assert_log_absent", "script"}

#: Below this share of v1's checks kept, a port does not test its subject.
MIN_CHECKS_KEPT = 0.5

#: How many assertion statements one dropped log or script check counts as.
LOG_CHECK_WEIGHT = 2


def _subject_kind(kind: str) -> bool:
    """A v1 step that reads or removes what a workflow is about."""
    return kind.startswith(("list_", "get_", "delete_"))


def _reads_state(steps: list) -> bool:
    """Whether a ported workflow ever runs the app (a call, view or event read)."""
    for st in steps:
        if st.get("type") in ("call", "view", "wait_for", "events", "download_blob"):
            return True
        if _reads_state(st.get("steps") or []):
            return True
        for op in st.get("operations") or []:
            if _reads_state(op.get("steps") or []):
                return True
    return False


class Porter:
    def __init__(self, wf: dict, wasm_map: dict, base_port: int):
        self.wf = wf
        self.wasm_map = wasm_map
        self.base_port = base_port
        self.accounts: list[str] = list(self._node_names())
        self.apps: dict[str, str] = {}  # var holding an application id -> bundle path
        # vars holding a package installed as a signed release (`_mpk` in the map)
        self.packages: set[str] = set()
        self.orgs: set[str] = set()  # vars holding a namespace (an org)
        self.invitations: dict[str, Any] = {}  # open-invitation var -> its context
        self.offline: set[str] = set()  # v1 partitions in effect at this point
        # an `inject_network_fault` partition -> the seconds of v1 waits left
        self.fault_left: dict[str, float] = {}
        self.group_contexts: dict[str, list[str]] = (
            {}
        )  # group var -> context vars in it

        self.teams: dict[str, str] = {}  # var holding a subgroup (team) -> its org var
        self.dropped: Counter = Counter()
        # v1's checks (assertion statements, log and script checks) and how
        # many survive the port: a port that keeps too few tests nothing.
        self.checks_v1 = 0
        self.checks_kept = 0
        self.subject_kinds: set[str] = set()  # v1 read/remove kinds seen
        self.subject_dropped: set[str] = set()  # ...and those the port drops
        # variables only a dropped step would have set
        self.dropped_vars: set[str] = set()
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

    def _team(self, group_expr: Any) -> bool:
        """Whether a v1 group reference is a subgroup (a team)."""
        return self._var(group_expr) in self.teams

    def _org_of(self, group_expr: Any) -> str:
        """The org a v1 group reference belongs to (a namespace is an org; a
        subgroup is a team of one)."""
        var = self._var(group_expr)
        if var in self.teams:
            return "{{" + self.teams[var] + "}}"
        return group_expr

    def _source(self, app_expr: Any, service: str | None) -> dict:
        """What a deploy runs: a release of an installed package, else a WASM."""
        var = self._var(app_expr)
        if var in self.packages:
            src = {
                "package": "{{" + var + "}}",
                "version": "{{" + f"{var}_version" + "}}",
            }
            if service:
                src["service"] = service
            return src
        return {"wasm": self._wasm(app_expr, service)}

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
            kind = step.get("type")
            if kind in _PARTITION_STARTS:
                key = _partition_key(kind, step)
                self.offline.add(key)
                if kind == "inject_network_fault":
                    # The fault clears on its own after `duration` seconds:
                    # the v1 waits that follow tell when.
                    self.fault_left[key] = float(step.get("duration", 0))
            elif kind in _PARTITION_ENDS:
                self.offline.discard(_partition_key(kind, step))
            elif kind == "wait" and self.fault_left:
                for key in list(self.fault_left):
                    self.fault_left[key] -= float(step.get("seconds", 0))
                    if self.fault_left[key] <= 0:
                        del self.fault_left[key]
                        self.offline.discard(key)
            if kind == "parallel":
                # v1 ran each group at once from its own node; the app's log
                # orders concurrent calls anyway, so the groups run one after
                # another and leave the state concurrent calls would.
                self.notes.append(
                    "parallel groups run one after another: the app's log orders them"
                )
                for g in step.get("groups") or []:
                    out.extend(self.port_steps(g.get("steps")))
                continue
            ported = self.port_step(step)
            if self.offline and kind not in ("assert", "json_assert"):
                # Read while a v1 node was offline: the partition's view, which
                # a cluster never has. The writes stand; what was read is only
                # checked once the nodes met again.
                for st in ported:
                    outs = set((st.get("outputs") or {}).keys())
                    if outs:
                        self.dropped_vars.update(outs)
                        self.dropped["output read during a v1 partition"] += len(outs)
                    # A write a partitioned v1 node took locally may be refused
                    # here, where the other side's writes are already seen; the
                    # state the nodes converged to is still checked after.
                    if st.get("type") == "call" and not st.get("expected_failure"):
                        st["allow_failure"] = True
            out.extend(ported)
        return out

    def port_step(self, s: dict) -> list:
        kind = s.get("type")
        if _subject_kind(str(kind)):
            self.subject_kinds.add(str(kind))
            if kind in DROPPED:
                self.subject_dropped.add(str(kind))
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
            if kind in _CHECKS:
                # A log or script check is a workflow's own subject (it is how
                # v1 saw what a call's result cannot show), so it weighs more.
                self.checks_v1 += LOG_CHECK_WEIGHT
            self.dropped[kind] += 1
            self.dropped_vars.update((s.get("outputs") or {}).keys())
            return []
        if kind in ("assert", "json_assert"):
            # A statement about a variable only a dropped step set checks v1
            # governance; it goes with that step.
            kept = []
            for st in s.get("statements") or []:
                text = st.get("statement", "") if isinstance(st, dict) else str(st)
                if any(
                    "{{" + v + "}}" in text.replace(" ", "") for v in self.dropped_vars
                ):
                    self.dropped[f"{kind} statement on a dropped step's output"] += 1
                else:
                    kept.append(st)
            self.checks_v1 += len(s.get("statements") or [])
            self.checks_kept += len(kept)
            return [{**s, "statements": kept}] if kept else []
        if kind == "wait":
            # Kept as long as v1 waited: a name does not tell a wait for peers
            # to sync from one for a lease or a timer to run out (hyperfeed's
            # lease needs its 18 s), and a shortened one fails the latter.
            return [{**s, "seconds": float(s.get("seconds", 0))}]
        if kind == "fuzzy_test":
            # v1's soak: the same weighted operations, as accounts instead of
            # nodes. `{{random_node}}` names a random account.
            ops = []
            for op in s.get("operations") or []:
                steps = self.port_steps(op.get("steps"))
                text = json.dumps(steps).replace(
                    "{{random_node}}", "{{random_account}}"
                )
                ops.append(
                    {
                        "name": op.get("name"),
                        "weight": op.get("weight", 1),
                        "steps": json.loads(text),
                    }
                )
            step = {
                **base,
                "type": "fuzzy_test",
                "operations": ops,
                "accounts": [
                    n.get("name") for n in s.get("nodes") or [] if n.get("name")
                ]
                or list(self.accounts),
            }
            for k in ("duration_minutes", "success_threshold", "operation_delay_ms"):
                if k in s:
                    step[k] = s[k]
            return [step]
        if kind == "repeat":
            step = {**base, "type": "repeat", "count": s["count"]}
            if s.get("outputs"):
                step["outputs"] = s["outputs"]
            step["steps"] = self.port_steps(s.get("steps"))
            return [step] if step["steps"] else []
        if kind == "install_application":
            outs = s.get("outputs") or {}
            var = next((v for v, p in outs.items() if p == "applicationId"), None)
            path = str(s.get("path"))
            if var:
                self.apps[var] = path
            entry = {
                k: v
                for k, v in self.wasm_map.get(path, {}).items()
                if not k.startswith("_")
            }
            if not var:
                return []  # v1 installed on every node; one install is enough
            mpk = self.wasm_map.get(path, {}).get("_mpk")
            if mpk:
                # A signed release, installed by name from the cluster's
                # registry: the node checks the publisher's signature.
                self.packages.add(var)
                return [
                    acting(
                        type="install_package",
                        mpk=mpk,
                        outputs={var: "package", f"{var}_version": "version"},
                    )
                ]
            steps = [
                acting(
                    type="upload_bundle",
                    wasm=entry.get("")
                    or next(iter(entry.values()), "{{env.APP_WASM}}"),
                    outputs={f"{var}_bundle": "hash"},
                )
            ]
            # v1's application id names the package, the same across its
            # versions; the bundle hash is the build. The map names the package.
            package = self.wasm_map.get(path, {}).get("_package")
            value = package if package else "{{" + f"{var}_bundle" + "}}"
            steps.append({"type": "set", "values": {var: value}})
            return steps
        if kind == "upgrade_group":
            # v1 upgraded every context of a group; here each app instance is
            # upgraded, and the node plans the migration from the two ABIs.
            target = self._var(s.get("target_application_id"))
            ctxs = self.group_contexts.get(self._var(s.get("group_id")) or "", [])
            if not target or not ctxs:
                self.dropped["upgrade_group (no context to upgrade)"] += 1
                return []
            # A release upgrades by its version, within the package; a raw
            # upload by its bundle hash.
            to = (
                {"version": "{{" + f"{target}_version" + "}}"}
                if target in self.packages
                else {"bundle": "{{" + f"{target}_bundle" + "}}"}
            )
            return [acting(type="upgrade_app", app="{{" + c + "}}", **to) for c in ctxs]
        if kind == "get_migration_status":
            # v1 migrated a group's contexts lazily and reported progress; an
            # upgrade here runs at once, so the report is each app's version.
            group = self._var(s.get("group_id") or s.get("namespace_id"))
            ctxs = self.group_contexts.get(group or "") or [
                c for cs in self.group_contexts.values() for c in cs
            ]
            step = acting(type="migration_status", apps=["{{" + c + "}}" for c in ctxs])
            if s.get("outputs"):
                step["outputs"] = s["outputs"]
            return [step]
        if kind == "create_group":
            # A standalone v1 group is a namespace: an organization.
            kind = "create_namespace"
            s = {**s, "outputs": dict.fromkeys(s.get("outputs") or {}, "namespaceId")}
        if kind == "create_group_invitation":
            kind = "create_namespace_invitation"
        if kind == "join_group":
            return [acting(type="redeem_invite", token=s.get("invitation"))]
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
            step = acting(
                type="create_team",
                org=s["namespace_id"],
                team_name=s.get("group_name", "team"),
                # v1 creates a subgroup restricted unless told otherwise.
                join=_JOIN.get(
                    str(s.get("visibility", "restricted")).lower(), "invite"
                ),
                outputs={var: "teamId"} if var else None,
            )
            if (
                s.get("parent_group_id")
                and self._var(s["parent_group_id"]) in self.teams
            ):
                step["parent"] = s["parent_group_id"]
            return [step]
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
                    (
                        (
                            m.get("identity")
                            or m.get("public_key")
                            or m.get("member_id")
                        ),
                        (
                            str(m.get("role", "member")).lower()
                            if isinstance(m, dict)
                            else "member"
                        ),
                    )
                    if isinstance(m, dict)
                    else (m, "member")
                )
                for m in s.get("members") or []
            ]
            if self._team(group):
                return [
                    acting(
                        type="team_add",
                        org=self._org_of(group),
                        team=group,
                        members=[m],
                        role=_TEAM_ROLE.get(role, "member"),
                    )
                    for m, role in members
                ]
            return [
                acting(
                    type="set_role",
                    org=group,
                    member=m,
                    role=_V1_ROLE.get(role, "member"),
                )
                for m, role in members
            ]
        if kind == "update_member_role":
            group = s.get("group_id")
            role = str(s.get("role", "member")).lower()
            member = s.get("member_id") or s.get("identity")
            if self._team(group):
                return [
                    acting(
                        type="team_add",
                        org=self._org_of(group),
                        team=group,
                        members=[member],
                        role=_TEAM_ROLE.get(role, "member"),
                    )
                ]
            return [
                acting(
                    type="set_role",
                    org=self._org_of(group),
                    member=member,
                    role=_V1_ROLE.get(role, "member"),
                )
            ]
        if kind == "remove_group_members":
            group = s.get("group_id")
            members = s.get("members") or []
            if self._team(group):
                return [
                    acting(
                        type="team_remove",
                        org=self._org_of(group),
                        team=group,
                        members=members,
                    )
                ]
            return [acting(type="remove_member", org=group, member=m) for m in members]
        if kind == "leave_group":
            group = s.get("group_id")
            if not self._team(group):
                return [acting(type="remove_member", org=group, member=node)]
            return [
                acting(
                    type="team_remove",
                    org=self._org_of(group),
                    team=group,
                    members=[node],
                )
            ]
        if kind == "set_subgroup_visibility":
            group = s.get("group_id")
            join = _JOIN.get(str(s.get("visibility", "")).lower())
            if not self._team(group) or not join:
                self.dropped["set_subgroup_visibility (not a subgroup)"] += 1
                return []
            return [
                acting(
                    type="team_update", org=self._org_of(group), team=group, join=join
                )
            ]
        if kind == "reparent_group":
            child = s.get("child_group_id") or s.get("group_id")
            parent = s.get("new_parent_id")
            to = parent if self._team(parent) else None
            return [
                acting(
                    type="team_update", org=self._org_of(child), team=child, parent=to
                )
            ]
        if kind == "set_group_metadata":
            group = s.get("group_id")
            meta = {"name": s.get("record_name"), "data": s.get("data") or {}}
            if not self._team(group):
                self.dropped["set_group_metadata (on a namespace)"] += 1
                return []
            return [
                acting(
                    type="team_update",
                    org=self._org_of(group),
                    team=group,
                    metadata=meta,
                )
            ]
        if kind in (
            "get_group_metadata",
            "get_group_info",
            "list_group_members",
            "list_group_contexts",
            "list_subgroups",
            "join_subgroup_inheritance",
        ):
            group = s.get("group_id")
            outs = s.get("outputs") or {}
            if self._team(group):
                step_type = (
                    "team_join"
                    if kind == "join_subgroup_inheritance"
                    else ("team_list" if kind == "list_subgroups" else "team_get")
                )
                step = acting(type=step_type, org=self._org_of(group), team=group)
            elif kind == "join_subgroup_inheritance":
                return []
            elif kind == "get_group_metadata":
                self.dropped["get_group_metadata (on a namespace)"] += 1
                self.dropped_vars.update(outs.keys())
                return []
            else:
                # The namespace itself: the organization's top level.
                step = acting(type="team_list", org=group)
            if outs:
                step["outputs"] = {
                    v: ("contexts" if kind == "list_group_contexts" else p)
                    for v, p in outs.items()
                }
            return [step]
        if kind == "delete_group":
            group = s.get("group_id")
            if not self._team(group):
                self.dropped["delete_group (a namespace)"] += 1
                return []
            return [acting(type="team_delete", org=self._org_of(group), team=group)]
        if kind == "get_member_capabilities":
            # The member's row: a team's for a subgroup, the organization's
            # (its default unless set for them) for the namespace.
            group = s.get("group_id")
            step = acting(
                type="member_capabilities",
                org=self._org_of(group),
                member=s.get("member_id") or s.get("identity"),
            )
            if self._team(group):
                step["team"] = group
            if s.get("outputs"):
                step["outputs"] = s["outputs"]
            return [step]
        if kind in ("set_member_capabilities", "set_default_capabilities"):
            group = s.get("group_id")
            team = group if self._team(group) else None
            step = acting(
                type="capabilities",
                org=self._org_of(group),
                capabilities=s.get("capabilities", 0),
            )
            if kind == "set_member_capabilities":
                step["member"] = s.get("member_id") or s.get("identity")
                if team:
                    step["team"] = team
            elif team:
                # A subgroup's default capabilities have no counterpart: a
                # team row starts from the organization's.
                self.dropped["set_default_capabilities (on a subgroup)"] += 1
                return []
            return [step]
        if kind == "create_identity":
            # In every v1 workflow this fetches the node's own member identity
            # (one per node) before it joins: here, the node's account.
            outs = dict.fromkeys(s.get("outputs") or {}, "publicKey")
            return [{**base, "type": "whoami", "account": node, "outputs": outs}]
        if kind == "node_identity":
            outs = {}
            for v, p in (s.get("outputs") or {}).items():
                if p in ("accountId", "accountRootPublicKey"):
                    outs[v] = "publicKey"
                elif p == "deviceId":
                    # A device is a passkey: the one this session signed in with.
                    outs[v] = "passkeyId"
            return [acting(type="whoami", outputs=outs)]
        if kind == "create_context" and "context_node" in s:
            kind = "create_mesh"  # the mesh form: a context and its members at once
        if kind == "create_mesh":
            return self._mesh(s, base)
        if kind == "create_context":
            creator = node
            group = s.get("group_id")
            step = {**base, "type": "deploy", "account": creator}
            step.update(self._source(s.get("application_id"), s.get("service_name")))
            step["app_name"] = s.get("service_name") or s.get("name", "app")
            if "params" in s:
                step["init_args"] = s["params"]
            if group:
                step["org"] = self._org_of(group)
                if self._team(group):
                    step["access"] = {"kind": "team", "team": group}
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
                    outs[v] = "contextId"
                    if group and self._var(group):
                        self.group_contexts.setdefault(self._var(group), []).append(v)
                elif p == "memberPublicKey":
                    outs[v] = "memberPublicKey"
            if outs:
                step["outputs"] = outs
            return [step]
        if kind == "account_pair":
            # v1 paired a second device onto an account. In release-v2 a device
            # is another passkey of the account: the paired node signs in with
            # its own and acts as the holder's account from here on.
            step = {
                **base,
                "type": "add_device",
                "account": s.get("holder"),
                "device": s.get("node"),
            }
            outs = {
                v: p for v, p in (s.get("outputs") or {}).items() if p == "keyDelivered"
            }
            if outs:
                step["outputs"] = outs
            return [step]
        if kind == "invite_open":
            # A context outside a namespace is open to every account of the
            # scenario already (see create_context); the invitation names it.
            outs = s.get("outputs") or {}
            var = next((v for v, p in outs.items() if p == "invitation"), None)
            if not var:
                return []
            self.invitations[var] = s.get("context_id")
            return [{**base, "type": "set", "values": {var: s.get("context_id")}}]
        if kind == "join_open":
            inv = str(s.get("invitation", ""))
            ctx = self.invitations.get(inv.strip("{} "), inv)
            outs = s.get("outputs") or {}
            steps = []
            keys = {v: "publicKey" for v, p in outs.items() if p == "memberPublicKey"}
            if keys:
                steps.append(
                    {**base, "type": "whoami", "account": node, "outputs": keys}
                )
            ctxs = {v: ctx for v, p in outs.items() if p == "contextId"}
            if ctxs:
                steps.append({"type": "set", "values": ctxs})
            return steps
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
        if (
            kind == "download_blob"
            and not s.get("context_id")
            and s.get("expected_failure")
        ):
            # v1's negative control: a node without the bytes, reading only its
            # own store. A cluster's blob store has no such node.
            self.dropped["download_blob (a node-local miss)"] += 1
            self.dropped_vars.update((s.get("outputs") or {}).keys())
            return []
        if kind in ("upload_blob", "download_blob"):
            # Blobs are an app's own in release-v2: v1 named the context only
            # to announce one, and a download without one means the mesh's.
            step = acting(
                type=kind,
                app=s.get("context_id") or "{{context_id}}",
                file_path=s.get("file_path"),
                blob_id=s.get("blob_id"),
                expected_size=s.get("expected_size"),
                expected_sha256=s.get("expected_sha256"),
            )
            outs = s.get("outputs")
            if outs:
                step["outputs"] = outs
            return [step]
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

    def _mesh(self, s: dict, base: dict) -> list:
        """v1's create_mesh made a namespace, a context in it, and joined the
        listed nodes; it published the namespace as `{{namespace_id}}`."""
        creator = s.get("context_node")
        members = list(s.get("nodes") or [])
        outs = s.get("outputs") or {}
        org_var = next((v for v, p in outs.items() if p == "groupId"), "namespace_id")
        self.orgs.add(org_var)
        steps: list = [
            {
                **base,
                "type": "create_org",
                "account": creator,
                "org_name": "mesh",
                "outputs": {org_var: "orgId"},
            },
        ]
        if org_var != "namespace_id":
            steps.append(
                {"type": "set", "values": {"namespace_id": "{{" + org_var + "}}"}}
            )
        for m in members:
            steps.append(
                {
                    "type": "invite",
                    "account": creator,
                    "org": "{{" + org_var + "}}",
                    "role": "member",
                    "outputs": {"_mesh_invite": "token"},
                }
            )
            steps.append(
                {"type": "redeem_invite", "account": m, "token": "{{_mesh_invite}}"}
            )
        deploy = {"type": "deploy", "account": creator, "org": "{{" + org_var + "}}"}
        deploy.update(self._source(s.get("application_id"), s.get("service_name")))
        deploy["app_name"] = s.get("service_name") or s.get("name", "app")
        if "params" in s:
            deploy["init_args"] = s["params"]
        # Without outputs, v1's create_mesh exported these names.
        d_outs = (
            {}
            if outs
            else {"context_id": "contextId", "member_public_key": "memberPublicKey"}
        )
        for v, p in outs.items():
            if p == "contextId":
                d_outs[v] = "contextId"
            elif p == "memberPublicKey":
                d_outs[v] = "memberPublicKey"
        if d_outs:
            deploy["outputs"] = d_outs
        steps.append({**base, **deploy})
        if not outs and len(members) == 1:
            # ...and the single joining member's key as {{public_key}}.
            steps.append(
                {
                    "type": "set",
                    "values": {"public_key": "{{public_key_" + members[0] + "}}"},
                }
            )
        return steps

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
        why = self.hollow(steps)
        if why:
            out["not_applicable"] = why
        out["steps"] = steps
        return out

    def hollow(self, steps: list) -> str | None:
        """Why the port does not test what the v1 workflow did, if it does not."""
        if self.subject_kinds and len(self.subject_dropped) * 2 > len(
            self.subject_kinds
        ):
            return "the port drops what the workflow reads or removes: " + ", ".join(
                sorted(self.subject_dropped)
            )
        if not _reads_state(steps) and not self.checks_kept:
            return "the port never runs the app and keeps none of v1's checks"
        if self.checks_v1 and self.checks_kept < MIN_CHECKS_KEPT * self.checks_v1:
            return (
                f"the port keeps {self.checks_kept} of v1's {self.checks_v1} checks; "
                "the rest checked what has no release-v2 counterpart"
            )
        return None

    def header(self, source: str) -> str:
        lines = [
            f"# Ported from {source} by `merobox cloud port`.",
            "# v1 nodes are accounts here; see merobox/cloud_v2/port.py for the mapping.",
        ]
        if self.dropped:
            lines.append("# Dropped (no release-v2 counterpart):")
            for kind, n in sorted(self.dropped.items()):
                reason = DROPPED.get(kind, "checks what the dropped steps did")
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
