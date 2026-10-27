"""YAML workflows against a release-v2 cluster.

```yaml
name: KV store across a node failure
backend: cloud-v2
cluster: { nodes: 3, base_port: 2700 }
accounts: [alice, bob]
steps:
  - type: deploy
    account: alice
    wasm: res/kv_store.wasm
    outputs: { app_id: appId }
  - type: call
    account: alice
    app: "{{app_id}}"
    method: set
    args: { key: a, value: "1" }
  - type: kill_node
    node: 1
  - type: wait_for
    account: alice
    app: "{{app_id}}"
    method: get
    args: { key: a }
    equals: "1"
```

Conventions match v1 workflows: `{{var}}` placeholders (a whole-string
placeholder keeps its type; dotted paths reach into results), `outputs` map
variables to paths in a step's result, `expected_error` makes a refusal with
that code the step's success, and `assert` is merobox's own assertion step.
Each account in `accounts` is signed up with its own software passkey; its id
is `{{accounts.<name>.account_id}}`. `{{env.NAME}}` reads the environment, and
`{{ops_token}}` is the cluster's operator token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import yaml
from rich.console import Console

from merobox.cloud_v2.assertions import AssertionConfigError, run_assert_step
from merobox.cloud_v2.cluster import ProcessCluster

console = Console()

_PLACEHOLDER = re.compile(r"\{\{\s*([\w.\-]+(?:\s*\?\?\s*[\w.\-]+)*)\s*\}\}")


class WorkflowError(Exception):
    """The workflow file is invalid, or a step failed."""


# ----------------------------------------------------------------- variables


def _lookup(variables: dict, expr: str) -> Any:
    """`a.b.c`, or `a ?? b`: the first alternative that resolves."""
    alternatives = [a.strip() for a in expr.split("??")]
    for alt in alternatives[:-1]:
        try:
            return _lookup_path(variables, alt)
        except WorkflowError:
            continue
    return _lookup_path(variables, alternatives[-1])


def _lookup_path(variables: dict, path: str) -> Any:
    cur: Any = variables
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise WorkflowError(f"unresolved placeholder {{{{{path}}}}}")
    return cur


def resolve(value: Any, variables: dict) -> Any:
    """Substitute `{{var}}` placeholders in strings, dicts and lists."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value.strip())
        if whole:
            return _lookup(variables, whole.group(1))
        return _PLACEHOLDER.sub(
            lambda m: _stringify(_lookup(variables, m.group(1))), value
        )
    if isinstance(value, dict):
        return {k: resolve(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, variables) for v in value]
    return value


def context_id(app_id: str) -> str:
    """The id an app sees as `env::context_id()`; the node accepts it for the app."""
    return hashlib.sha256(app_id.encode()).hexdigest()


def account_key(account_id: str) -> str:
    """An account's public key as apps see it (`env::executor_id`): its id's
    32 bytes in hex."""
    padded = account_id + "=" * (-len(account_id) % 4)
    return base64.urlsafe_b64decode(padded).hex()


def with_member_keys(rows: Any) -> Any:
    """Member rows (`{"account": id, ...}`) with each account's public key as
    `publicKey`: v1 listed members by their public key, and the workflows
    ported from it check a listing for the key they hold."""
    if not isinstance(rows, list):
        return rows
    out = []
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("account"), str):
            try:
                r = {**r, "publicKey": account_key(r["account"])}
            except ValueError:
                pass
        out.append(r)
    return out


def _stringify(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v)


def _pick(result: Any, path: str) -> Any:
    if path in ("", "."):
        return result
    try:
        return _lookup(result if isinstance(result, dict) else {"value": result}, path)
    except WorkflowError as e:
        shown = json.dumps(result)[:300]
        raise WorkflowError(
            f"output path '{path}' is not in the step's result: {shown}"
        ) from e


# ----------------------------------------------------------------- loading

STEP_TYPES: dict[str, tuple[str, ...]] = {
    # type: required fields
    "deploy": (),
    "migration_status": ("apps",),
    "upload_bundle": ("wasm",),
    "create_app": (),
    "install_package": ("mpk",),
    "upgrade_app": ("app",),
    "uninstall_package": ("package",),
    "delete_app": ("app",),
    "packages": (),
    "call": ("app", "method"),
    "view": ("app", "method"),
    "wait_for": ("app", "method"),
    "events": ("app",),
    "whoami": (),
    "create_api_key": (),
    "create_org": ("org_name",),
    "create_team": ("org", "team_name"),
    "invite": ("org",),
    "redeem_invite": ("token",),
    "remove_member": ("org", "member"),
    "team_add": ("org", "team", "members"),
    "team_remove": ("org", "team", "members"),
    "team_update": ("org", "team"),
    "team_get": ("org", "team"),
    "team_list": ("org",),
    "team_join": ("org", "team"),
    "team_delete": ("org", "team"),
    "capabilities": ("org", "capabilities"),
    "member_capabilities": ("org", "member"),
    "add_device": ("account", "device"),
    "set_role": ("org", "member", "role"),
    "http": ("method", "path"),
    "ops": ("op",),
    "find_leader": ("app",),
    "upload_blob": ("app", "file_path"),
    "download_blob": ("app", "blob_id"),
    "kill_node": ("node",),
    "pause_node": ("node",),
    "resume_node": ("node",),
    "start_node": ("node",),
    "restart_node": ("node",),
    "wait": ("seconds",),
    "set": ("values",),
    "assert": ("statements",),
    "json_assert": ("statements",),
    "repeat": ("count", "steps"),
    "fuzzy_test": ("operations",),
}


class _Refused(Exception):
    """A refusal the runner decides itself, shaped like a client's
    `CloudError` (message, status, code, details)."""


_TEAM_STEPS = {
    "team_remove",
    "team_update",
    "team_get",
    "team_list",
    "team_join",
    "team_delete",
    "capabilities",
}

#: Steps that act as someone: an `account` (session) or an `api_key`.
_ACTING = {
    "deploy",
    "migration_status",
    "upload_blob",
    "download_blob",
    "upload_bundle",
    "create_app",
    "install_package",
    "upgrade_app",
    "uninstall_package",
    "delete_app",
    "packages",
    "call",
    "view",
    "wait_for",
    "events",
    "whoami",
    "create_api_key",
    "create_org",
    "create_team",
    "invite",
    "redeem_invite",
    "remove_member",
    "team_add",
    "team_remove",
    "team_update",
    "team_get",
    "team_list",
    "team_join",
    "team_delete",
    "capabilities",
    "member_capabilities",
    "add_device",
    "set_role",
    "http",
}


def _version_key(v: str) -> tuple:
    """Semver-ish order: numeric parts compare as numbers."""
    return tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.+-]", v)
    )


def _validate_steps(steps: Any, accounts: set, where: str) -> None:
    if not isinstance(steps, list) or not steps:
        raise WorkflowError(f"{where}: 'steps' must be a non-empty list")
    for i, step in enumerate(steps, start=1):
        label = f"{where} step {i}"
        if not isinstance(step, dict) or "type" not in step:
            raise WorkflowError(f"{label}: each step needs a 'type'")
        kind = step["type"]
        if kind not in STEP_TYPES:
            raise WorkflowError(
                f"{label}: unknown type '{kind}' (known: {', '.join(sorted(STEP_TYPES))})"
            )
        missing = [f for f in STEP_TYPES[kind] if f not in step]
        if missing:
            raise WorkflowError(f"{label} ({kind}): missing {', '.join(missing)}")
        if kind in _ACTING:
            if "account" not in step and "api_key" not in step:
                raise WorkflowError(f"{label} ({kind}): needs 'account' or 'api_key'")
            acct = step.get("account")
            if isinstance(acct, str) and "{{" not in acct and acct not in accounts:
                raise WorkflowError(f"{label}: account '{acct}' is not in 'accounts'")
        if kind == "deploy" and ("wasm" in step) == ("package" in step):
            raise WorkflowError(f"{label} (deploy): name a 'wasm' or a 'package'")
        if kind == "create_app" and ("bundle" in step) == ("package" in step):
            raise WorkflowError(f"{label} (create_app): name a 'bundle' or a 'package'")
        if kind == "upgrade_app" and not ({"bundle", "version"} & set(step)):
            raise WorkflowError(
                f"{label} (upgrade_app): name a 'version' or a 'bundle'"
            )
        if kind == "repeat":
            _validate_steps(step["steps"], accounts, f"{label} (repeat)")
        if "outputs" in step and not isinstance(step["outputs"], dict):
            raise WorkflowError(f"{label}: 'outputs' must map variable -> result path")


def load_workflow(path: str) -> dict:
    """Read and check a `backend: cloud-v2` workflow."""
    with open(path) as f:
        wf = yaml.safe_load(f) or {}
    if not isinstance(wf, dict):
        raise WorkflowError("a workflow is a mapping")
    if wf.get("backend") != "cloud-v2":
        raise WorkflowError(
            "not a release-v2 workflow (expected 'backend: cloud-v2'); "
            "a v1 workflow ports with `merobox cloud port`"
        )
    cluster = wf.setdefault("cluster", {})
    if not isinstance(cluster, dict):
        raise WorkflowError("'cluster' must be a mapping")
    accounts = wf.setdefault("accounts", [])
    if not isinstance(accounts, list) or not all(isinstance(a, str) for a in accounts):
        raise WorkflowError("'accounts' must be a list of names")
    # A port marked not_applicable is never run: it may keep no steps at all.
    if not (wf.get("not_applicable") and not wf.get("steps")):
        _validate_steps(wf.get("steps"), set(accounts), "workflow")
    wf["_dir"] = str(Path(path).resolve().parent)
    return wf


# ----------------------------------------------------------------- running


class Runner:
    def __init__(self, wf: dict, cluster: ProcessCluster):
        from calimero_client_py import CloudClient, CloudError, SoftPasskey

        self._CloudClient = CloudClient
        self._CloudError = CloudError
        self._SoftPasskey = SoftPasskey
        self.wf = wf
        self.cluster = cluster
        self.base_dir = Path(wf["_dir"])
        self.variables: dict[str, Any] = {"env": dict(os.environ)}
        self.variables.update(resolve(wf.get("variables") or {}, self.variables))
        self.passkeys: dict[str, Any] = {}
        self.sessions: dict[str, dict] = {}
        # account public key (hex) -> account name, for steps that pick their
        # caller by key (`as_key`, as v1 workflows did with executor_public_key)
        self.by_key: dict[str, str] = {}
        self.ids: dict[str, str] = {}

    def _file(self, path: str) -> Path:
        """A file a step names: as given, else beside the workflow or in any
        directory above it (ported workflows keep their v1 relative paths)."""
        p = Path(path)
        if p.is_absolute() or p.exists():
            return p
        for d in [self.base_dir, *self.base_dir.parents]:
            if (d / p).exists():
                return d / p
        raise WorkflowError(f"file not found: {path}")

    # -- clients

    def _node_order(self, node: int | None, account: str | None = None) -> list[int]:
        running = self.cluster.running() or self.cluster.ids
        if node is not None:
            return [int(node)]
        # Each account starts at its own node, as each v1 member talked to its
        # own node: requests spread over the cluster, so forwarding to the
        # leader and replication are exercised, not just node 1.
        names = list(self.wf.get("accounts") or [])
        if account in names and running:
            home = names.index(account) % len(running)
            return running[home:] + running[:home]
        return running

    def _client(self, node: int, account: str | None, api_key: str | None):
        c = self._CloudClient(self.cluster.url(node), allow_mock=True)
        if api_key:
            c.use_api_key(api_key)
        elif account:
            c.use_session(self.sessions[account])
        return c

    def _with_client(self, step: dict, f: Callable[[Any], Any]) -> Any:
        """Run `f` on a client for the step's actor; without an explicit node,
        a node that cannot be reached is skipped for the next running one."""
        account = self.by_key.get(str(step.get("as_key") or "")) or step.get("account")
        api_key = step.get("api_key")
        last: Exception | None = None
        for node in self._node_order(step.get("node"), account):
            try:
                return f(self._client(node, account, api_key))
            except (self._CloudError, _Refused) as e:
                if e.args[2] != "TRANSPORT" or step.get("node") is not None:
                    raise
                last = e
        raise last or WorkflowError("no node to reach")

    def _post_approved(self, step: dict, path: str, body: Any) -> Any:
        """POST, and where the node asks the account's owner to approve this
        exact request (`412 CONFIRM_REQUIRED`: an upgrade, an approval), answer
        with the account's passkey, as its owner would."""
        try:
            return self._with_client(step, lambda cl: cl.post(path, body))
        except self._CloudError as e:
            pk = self.passkeys.get(str(step.get("account")))
            if e.args[2] != "CONFIRM_REQUIRED" or pk is None:
                raise
            return self._with_client(step, lambda cl: cl.post_confirmed(path, body, pk))

    def _sign_up(self) -> None:
        node = self._node_order(None)[0]
        accounts = {}
        for name in self.wf["accounts"]:
            pk = self._SoftPasskey()
            c = self._CloudClient(self.cluster.url(node), allow_mock=True)
            session = c.sign_up(pk, name)
            self.passkeys[name] = pk
            self.sessions[name] = session
            key = account_key(session["accountId"])
            self.by_key[key] = name
            self.ids[name] = session["accountId"]
            accounts[name] = {"account_id": session["accountId"], "public_key": key}
            # What v1's create_mesh exported for each member.
            self.variables[f"public_key_{name}"] = key
        self.variables["accounts"] = accounts
        self.variables["ops_token"] = self.cluster.ops_token

    def _account_id(self, who: str) -> str:
        """An account name, an account's public key, or an account id -> the id."""
        if who in self.ids:
            return self.ids[who]
        if who in self.by_key:
            return self.ids[self.by_key[who]]
        return who

    def _acting(self, step: dict) -> str | None:
        return self.by_key.get(str(step.get("as_key") or "")) or step.get("account")

    # -- steps

    def _team_step(self, kind: str, step: dict) -> Any:
        org, team = step["org"], step.get("team")
        base = f"orgs/{org}/teams/{team}"
        if kind == "team_remove":
            for m in step["members"]:
                self._with_client(
                    step,
                    lambda cl, m=m: cl.delete(
                        f"{base}/members/{self._account_id(str(m))}"
                    ),
                )
            return {}
        if kind == "team_update":
            body = {k: step[k] for k in ("name", "join", "metadata") if k in step}
            if "parent" in step:
                body["parent"] = step["parent"]
            return self._with_client(step, lambda cl: cl.post(base, body))
        if kind == "team_delete":
            return self._with_client(step, lambda cl: cl.delete(base))
        if kind == "capabilities":
            body = {"capabilities": int(step["capabilities"])}
            if step.get("member"):
                body["account"] = self._account_id(str(step["member"]))
            if team:
                body["team"] = team
            return self._with_client(
                step, lambda cl: cl.post(f"orgs/{org}/capabilities", body)
            )
        if kind == "team_list":
            # The teams the caller sees below `team` (the organization's top
            # level without one), and, at the top, any team whose parent they
            # do not see (v1's "Shared with me").
            o = self._with_client(step, lambda cl: cl.get(f"orgs/{org}"))
            teams = o.get("teams") or {}
            if team:
                kids = [t for t, r in teams.items() if r.get("parent") == team]
            else:
                kids = [t for t, r in teams.items() if r.get("parent") not in teams]
            members = [
                {"account": a, "role": r} for a, r in (o.get("members") or {}).items()
            ]
            return {
                "subgroups": kids,
                "teams": kids,
                "members": with_member_keys(members),
            }
        t = self._with_client(step, lambda cl: cl.get(base))
        meta = t.get("metadata") or {}
        out = {
            **t,
            "members": with_member_keys(t.get("members")),
            "groupId": t.get("teamId"),
            "memberCount": len(t.get("members") or []),
            "subgroups": t.get("children") or [],
            "name": (
                meta.get("name", t.get("name"))
                if isinstance(meta, dict)
                else t.get("name")
            ),
            "contexts": [a.get("contextId") for a in t.get("apps") or []],
            # v1's names for a subgroup's join rule and its metadata record.
            "subgroupVisibility": (
                "open" if t.get("join") == "everyone" else "restricted"
            ),
            "data": meta.get("data") if isinstance(meta, dict) else None,
        }
        if kind == "team_join":
            # Joining an open team one reaches writes one's own plain row (v1's
            # join_subgroup_inheritance materialised the membership); one does
            # not reach is refused, as in v1.
            if not t.get("member"):
                raise _Refused(f"not a member of {team}", 404, "NOT_FOUND", None)
            me = self._with_client(step, lambda cl: cl.me())
            inherited = t.get("myRole") is None
            if inherited:
                self._with_client(
                    step,
                    lambda cl: cl.post(
                        f"{base}/members", {"account": me["accountId"], "lead": False}
                    ),
                )
            out["wasInherited"] = inherited
            out["memberPublicKey"] = account_key(me["accountId"])
        return out

    def _wasm(self, step: dict) -> bytes:
        p = Path(step["wasm"])
        return (p if p.is_absolute() else self.base_dir / p).read_bytes()

    def _app_options(self, step: dict) -> dict:
        opts: dict[str, Any] = {}
        access = step.get("access")
        if isinstance(access, dict) and isinstance(access.get("accounts"), list):
            step = dict(step)
            step["access"] = {
                **access,
                "accounts": [self._account_id(str(a)) for a in access["accounts"]],
            }
        if isinstance(step.get("init_args"), str):
            # v1 workflows pass init params as a JSON string.
            try:
                step = {**step, "init_args": json.loads(step["init_args"])}
            except json.JSONDecodeError:
                pass
        for key, field in (
            ("init_args", "initArgs"),
            ("org", "org"),
            ("access", "access"),
            ("confirm_methods", "confirmMethods"),
        ):
            if key in step:
                opts[field] = step[key]
        return opts

    def _ops(self, step: dict) -> Any:
        op = step["op"]
        body = step.get("body") or {}
        node = self._node_order(step.get("node"))[0]
        c = self._CloudClient(self.cluster.url(node), allow_mock=True)
        c.use_api_key(self.cluster.ops_token)
        if op == "status":
            return c.get("ops/status")
        if op == "quotas":
            return c.get("ops/quotas")
        if op == "set_quotas":
            return c.post("ops/quotas", body)
        if op == "backup":
            return c.post("ops/backup", {})
        if op == "move":
            path = f"ops/apps/{step['app']}/replicas"
            payload = {"replicas": step["replicas"]}
            try:
                return c.post(path, payload)
            except (self._CloudError, _Refused) as e:
                if e.args[2] != "NOT_LEADER":
                    raise
                leader = json.loads(e.args[3])["leaderUrl"]
                lc = self._CloudClient(leader, allow_mock=True)
                lc.use_api_key(self.cluster.ops_token)
                return lc.post(path, payload)
        raise WorkflowError(f"unknown ops op '{op}'")

    def _wait_for(self, step: dict) -> Any:
        timeout = float(step.get("timeout", 20))
        interval = float(step.get("interval", 0.25))
        deadline = time.time() + timeout
        last: Any = None
        while True:
            try:
                out = self._with_client(
                    step,
                    lambda c: c.view(
                        step["app"],
                        step["method"],
                        step.get("args"),
                        bool(step.get("stale", False)),
                        int(step.get("min_index", 0)),
                    ),
                )
                last = out["result"]
                if "equals" in step and last == step["equals"]:
                    return out
                if "contains" in step and step["contains"] in (last or ""):
                    return out
                if "equals" not in step and "contains" not in step and last is not None:
                    return out
            except (self._CloudError, _Refused) as e:
                last = e.args[0]
            if time.time() > deadline:
                raise WorkflowError(
                    f"wait_for {step['method']}: still {last!r} after {timeout}s"
                )
            time.sleep(interval)

    def _run_one(self, step: dict) -> Any:
        kind = step["type"]
        if kind == "deploy":
            wasm = self._wasm(step) if "wasm" in step else None

            def deploy(cl):
                if wasm is None:
                    # A release of an installed package.
                    created = cl.post(
                        "apps",
                        {
                            "name": step.get("app_name", "app"),
                            "package": step["package"],
                            **{
                                k: step[k]
                                for k in ("version", "service")
                                if step.get(k)
                            },
                            **self._app_options(step),
                        },
                    )
                    app, bundle = created["appId"], None
                else:
                    bundle = cl.upload_bundle(wasm)
                    app = cl.create_app(
                        step.get("app_name", "app"), bundle, self._app_options(step)
                    )
                actor = self._acting(step)
                return {
                    "appId": app,
                    "contextId": context_id(app),
                    "bundle": bundle,
                    "memberPublicKey": account_key(self.ids[actor]) if actor else None,
                }

            return self._with_client(step, deploy)
        if kind == "upload_bundle":
            wasm = self._wasm(step)
            return {"hash": self._with_client(step, lambda cl: cl.upload_bundle(wasm))}
        if kind == "create_app":
            if "package" in step:
                # From a signed release; the node installs it from the
                # registry first if the fleet lacks it.
                body = {
                    "name": step.get("app_name", "app"),
                    "package": step["package"],
                    **{k: step[k] for k in ("version", "service") if step.get(k)},
                    **self._app_options(step),
                }
                created = self._with_client(step, lambda cl: cl.post("apps", body))
                app = created["appId"]
            else:
                app = self._with_client(
                    step,
                    lambda cl: cl.create_app(
                        step.get("app_name", "app"),
                        step["bundle"],
                        self._app_options(step),
                    ),
                )
            return {"appId": app, "contextId": context_id(app)}
        if kind == "install_package":
            # Publish the signed .mpk to the cluster's registry, then have the
            # fleet install it by name: the node verifies it, and every node
            # that runs it fetches and verifies its own copy.
            if self.cluster.registry is None:
                raise WorkflowError(
                    "install_package: this merod build has no --registry-url"
                )
            mpk = self._file(str(step["mpk"])).read_bytes()
            package, version = self.cluster.registry.publish(mpk)
            out = self._with_client(
                step,
                lambda cl: cl.post(
                    "packages/install", {"package": package, "version": version}
                ),
            )
            return {**out, "package": package, "version": version}
        if kind == "upgrade_app":
            body = {k: step[k] for k in ("version", "bundle", "migrate") if step.get(k)}
            out = self._post_approved(step, f"apps/{step['app']}/upgrade", body)
            if out.get("status") not in (None, "ok"):
                raise _Refused(
                    f"upgrade refused: {out.get('error')}",
                    422,
                    "UPGRADE_FAILED",
                    out,
                )
            return out
        if kind == "uninstall_package":
            path = f"packages/{step['package']}"
            if step.get("version"):
                path += f"/{step['version']}"
            return self._with_client(step, lambda cl: cl.delete(path))
        if kind == "delete_app":
            return self._with_client(step, lambda cl: cl.delete(f"apps/{step['app']}"))
        if kind == "migration_status":
            # Each app's release: on the newest version they reach is migrated.
            apps = list(step["apps"])
            listed = self._with_client(step, lambda cl: cl.get("apps"))
            # Steps name an app by its id or its context id (what a ported
            # workflow holds); an app not found must not count as migrated.
            versions = {}
            for a in listed.get("apps", []):
                versions[a["appId"]] = a.get("version")
                versions[context_id(a["appId"])] = a.get("version")
            unknown = [a for a in apps if a not in versions]
            if unknown:
                raise _Refused(
                    f"migration_status: no such app {unknown[0]}",
                    404,
                    "NOT_FOUND",
                    None,
                )
            mine = [versions.get(a) for a in apps]
            known = [v for v in mine if v]
            target = max(known, key=_version_key) if known else None
            migrated = sum(1 for v in mine if v is not None and v == target)
            return {
                "target_version": target,
                "total": len(apps),
                "migrated": migrated,
                "failed": len(apps) - migrated,
                "residue_total": 0,
            }
        if kind == "packages":
            return self._with_client(step, lambda cl: cl.get("packages"))
        if kind in ("call", "view"):
            out = self._call_or_view(kind, step)
            # v1 workflows read a call's JSON-RPC envelope: {"output": value}.
            return {**out, "rpc": {"output": out.get("result")}}
        if kind == "upload_blob":
            data = self._file(step["file_path"]).read_bytes()
            up = self._with_client(step, lambda cl: cl.upload_blob(step["app"], data))
            # v1's output names (`blob_id`, `size`) beside the node's.
            return {**up, "blob_id": up["blobId"]}
        if kind == "download_blob":
            data = self._with_client(
                step, lambda cl: cl.download_blob(step["app"], step["blob_id"])
            )
            label = step.get("name", "download_blob")
            want = step.get("expected_size")
            if want is not None and len(data) != int(want):
                raise WorkflowError(f"{label}: expected {want} bytes, got {len(data)}")
            digest = hashlib.sha256(data).hexdigest()
            want = step.get("expected_sha256")
            if want and str(want).lower() != digest:
                raise WorkflowError(f"{label}: expected sha256 {want}, got {digest}")
            return {"size": len(data), "sha256": digest, "data": data}
        if kind == "whoami":
            me = self._with_client(step, lambda cl: cl.me())
            return {**me, "publicKey": account_key(me["accountId"])}
        if kind == "team_add":
            out = None
            for m in step["members"]:
                out = self._with_client(
                    step,
                    lambda cl, m=m: cl.post(
                        f"orgs/{step['org']}/teams/{step['team']}/members",
                        {
                            "account": self._account_id(str(m)),
                            "lead": bool(step.get("lead")),
                            **({"role": step["role"]} if step.get("role") else {}),
                            **(
                                {"capabilities": int(step["capabilities"])}
                                if step.get("capabilities") is not None
                                else {}
                            ),
                        },
                    ),
                )
            return out
        if kind in _TEAM_STEPS:
            return self._team_step(kind, step)
        if kind == "add_device":
            # Another passkey on `account`'s account; `device` signs in with it
            # and acts as that account from here on (v1: a paired device).
            pk = self._SoftPasskey()
            cred = self._with_client(step, lambda cl: cl.add_passkey(pk))
            c = self._CloudClient(
                self.cluster.url(self._node_order(None)[0]), allow_mock=True
            )
            session = c.sign_in(pk)
            device = step["device"]
            self.passkeys[device] = pk
            self.sessions[device] = session
            self.ids[device] = session["accountId"]
            return {
                "passkeyId": cred,
                "accountId": session["accountId"],
                "keyDelivered": True,
            }
        if kind == "member_capabilities":
            who = self._account_id(str(step["member"]))
            if step.get("team"):
                t = self._with_client(
                    step, lambda cl: cl.get(f"orgs/{step['org']}/teams/{step['team']}")
                )
                row = next(
                    (m for m in t.get("members", []) if m["account"] == who), None
                )
                if row is None:
                    raise _Refused("not a member of the team", 404, "NOT_FOUND", t)
                return {"capabilities": row["capabilities"]}
            o = self._with_client(step, lambda cl: cl.get(f"orgs/{step['org']}"))
            if who not in (o.get("members") or {}):
                raise _Refused("not a member of the organization", 404, "NOT_FOUND", o)
            caps = o.get("capabilities") or {}
            default = o.get("defaultCapabilities", o.get("default_capabilities"))
            return {"capabilities": caps.get(who, default)}
        if kind == "set_role":
            return self._with_client(
                step,
                lambda cl: cl.post(
                    f"orgs/{step['org']}/members",
                    {
                        "account": self._account_id(str(step["member"])),
                        "role": step["role"],
                    },
                ),
            )
        return self._run_rest(step)

    def _call_or_view(self, kind: str, step: dict) -> Any:
        if kind == "call":
            if step.get("confirm"):
                pk = self.passkeys[step["account"]]
                return self._with_client(
                    step,
                    lambda cl: cl.call_confirmed(
                        step["app"], step["method"], step.get("args"), pk
                    ),
                )
            return self._with_client(
                step,
                lambda cl: cl.call(
                    step["app"],
                    step["method"],
                    step.get("args"),
                    step.get("idempotency_key"),
                ),
            )
        return self._with_client(
            step,
            lambda cl: cl.view(
                step["app"],
                step["method"],
                step.get("args"),
                bool(step.get("stale", False)),
                int(step.get("min_index", 0)),
            ),
        )

    def _run_rest(self, step: dict) -> Any:
        kind = step["type"]
        c = self.cluster
        if kind == "wait_for":
            return self._wait_for(step)
        if kind == "events":
            return {
                "events": self._with_client(
                    step,
                    lambda cl: cl.events(
                        step["app"],
                        int(step.get("from", 0)),
                        int(step.get("count", 100)),
                        float(step.get("timeout", 5)),
                    ),
                )
            }
        if kind == "create_api_key":
            opts = {
                "label": step.get("label", "merobox"),
                "readOnly": bool(step.get("read_only", False)),
            }
            if "apps" in step:
                opts["apps"] = step["apps"]
            key_id, key = self._with_client(step, lambda cl: cl.create_api_key(opts))
            return {"keyId": key_id, "key": key}
        if kind == "create_org":
            return self._with_client(
                step, lambda cl: cl.post("orgs", {"name": step["org_name"]})
            )
        if kind == "create_team":
            return self._with_client(
                step,
                lambda cl: cl.post(
                    f"orgs/{step['org']}/teams",
                    {
                        "name": step["team_name"],
                        "join": step.get("join", "invite"),
                        **({"parent": step["parent"]} if step.get("parent") else {}),
                    },
                ),
            )
        if kind == "invite":
            return self._with_client(
                step,
                lambda cl: cl.post(
                    f"orgs/{step['org']}/invites",
                    {
                        "role": step.get("role", "member"),
                        "teams": step.get("teams", []),
                    },
                ),
            )
        if kind == "redeem_invite":
            return self._with_client(
                step, lambda cl: cl.post(f"invites/{step['token']}/redeem")
            )
        if kind == "remove_member":
            return self._with_client(
                step,
                lambda cl: cl.delete(
                    f"orgs/{step['org']}/members/{self._account_id(str(step['member']))}"
                ),
            )
        if kind == "http":
            method = step["method"].lower()
            path = step["path"].lstrip("/")
            if method == "get":
                return self._with_client(step, lambda cl: cl.get(path))
            if method == "delete":
                return self._with_client(step, lambda cl: cl.delete(path))
            return self._post_approved(step, path, step.get("body"))
        if kind == "ops":
            return self._ops(step)
        if kind == "find_leader":
            # Ask each running node (Ops API) which node leads the app's group.
            for node in self.cluster.running():
                status = self._ops({"op": "status", "node": node})
                for g in status.get("groups", []):
                    leader = g.get("status", {}).get("leader")
                    if g.get("app") == step["app"] and leader:
                        voters = g.get("status", {}).get("voters", [])
                        followers = [v for v in voters if v != leader]
                        return {
                            "leader": leader,
                            "follower": followers[0] if followers else None,
                        }
            raise WorkflowError(f"no node reports a leader for {step['app']}")
        if kind == "kill_node":
            c.kill_node(int(step["node"]))
            return None
        if kind == "pause_node":
            c.pause_node(int(step["node"]))
            return None
        if kind == "resume_node":
            c.resume_node(int(step["node"]))
            return None
        if kind == "start_node":
            c.start_node(int(step["node"]), binary=step.get("binary"))
            return None
        if kind == "restart_node":
            c.restart_node(int(step["node"]), binary=step.get("binary"))
            return None
        if kind == "wait":
            time.sleep(float(step["seconds"]))
            return None
        if kind == "set":
            self.variables.update(step["values"])
            return None
        raise WorkflowError(f"unhandled step type {kind}")

    def _assert(self, step: dict) -> None:
        """v1's own `assert` and `json_assert`, over this workflow's variables."""
        try:
            ok = run_assert_step(step, self.variables)
        except AssertionConfigError as e:
            raise WorkflowError(str(e)) from e
        if not ok:
            raise WorkflowError(f"{step.get('name') or step['type']}: assertion failed")

    def run_steps(self, steps: list, depth: int = 0) -> None:
        for _i, raw in enumerate(steps, start=1):
            label = raw.get("name") or raw["type"]
            if raw["type"] in ("assert", "json_assert"):
                # The assert step resolves its own placeholders.
                self._assert(raw)
                continue
            if raw["type"] == "repeat":
                for n in range(int(resolve(raw["count"], self.variables))):
                    self.variables["iteration"] = n
                    self.variables["iteration_node"] = n + 1
                    # v1's `outputs: {var: iteration}` names the counter.
                    for var, path in (raw.get("outputs") or {}).items():
                        if path in ("iteration", "iteration_node"):
                            self.variables[var] = self.variables[path]
                    self.run_steps(raw["steps"], depth + 1)
                continue
            if raw["type"] == "fuzzy_test":
                self._fuzzy(raw, label)
                continue
            step = resolve(
                {k: v for k, v in raw.items() if k != "steps"}, self.variables
            )
            indent = "  " * depth
            # `retry_for: N` keeps trying for N seconds, for checks that wait on
            # a change to reach the node asked (a removal, a new leader).
            started = time.time()
            deadline = started + float(step.get("retry_for", 0))
            while True:
                try:
                    note = self._step(step, label)
                    break
                except WorkflowError:
                    if time.time() >= deadline:
                        raise
                    time.sleep(0.25)
            took = time.time() - started
            # `max_seconds: N` is a latency budget: a step that succeeds late fails.
            if "max_seconds" in step and took > float(step["max_seconds"]):
                raise WorkflowError(
                    f"{label}: took {took:.1f}s, over its {step['max_seconds']}s budget"
                )
            console.print(f"{indent}[green]✓[/green] {label}{note}")

    def _fuzzy(self, raw: dict, label: str) -> None:
        """v1's soak test: weighted operations, picked at random, for a while.
        Each operation's steps run as ordinary steps with `{{random_account}}`,
        `{{random_int(a, b)}}`, `{{uuid}}`, `{{timestamp}}` and, after a call,
        `{{fuzzy_<arg>}}` for each argument it sent. An operation passes when
        every step does; the run passes at `success_threshold` percent.
        `MEROBOX_FUZZY_SECONDS` overrides the duration (CI runs it short)."""
        import random
        import uuid

        seconds = float(
            os.environ.get("MEROBOX_FUZZY_SECONDS")
            or float(raw.get("duration_minutes", 1)) * 60
        )
        accounts = raw.get("accounts") or list(self.wf["accounts"])
        ops = raw["operations"]
        weights = [float(o.get("weight", 1)) for o in ops]
        delay = float(raw.get("operation_delay_ms", 0)) / 1000
        threshold = float(raw.get("success_threshold", 95.0))
        gen = re.compile(
            r"\{\{\s*(random_int\((\d+)\s*,\s*(\d+)\)|uuid|timestamp)\s*\}\}"
        )

        def fill(v: Any) -> Any:
            if isinstance(v, str):

                def one(m: re.Match) -> str:
                    if m.group(2):
                        return str(random.randint(int(m.group(2)), int(m.group(3))))
                    return (
                        str(uuid.uuid4())
                        if m.group(1) == "uuid"
                        else str(int(time.time()))
                    )

                return gen.sub(one, v)
            if isinstance(v, list):
                return [fill(x) for x in v]
            if isinstance(v, dict):
                return {k: fill(x) for k, x in v.items()}
            return v

        passed = failed = 0
        failures: Counter = Counter()
        deadline = time.time() + seconds
        while time.time() < deadline:
            op = random.choices(ops, weights)[0]
            pick = random.choice(accounts)
            self.variables["random_account"] = (
                pick["account"] if isinstance(pick, dict) else pick
            )
            self.variables["random_key"] = (
                pick.get("as_key") if isinstance(pick, dict) else None
            )
            try:
                for st in op["steps"]:
                    st = fill(st)
                    if st["type"] in ("assert", "json_assert"):
                        self._assert(st)
                        continue
                    if st["type"] == "wait":
                        time.sleep(min(float(st.get("seconds", 0)), 1.0))
                        continue
                    step = resolve(st, self.variables)
                    if step["type"] == "call":
                        for k, v in (step.get("args") or {}).items():
                            self.variables[f"fuzzy_{k}"] = v
                    self._step(step, op.get("name", "fuzzy"))
                passed += 1
            except WorkflowError as e:
                failed += 1
                failures[f"{op.get('name', 'fuzzy')}: {str(e)[:120]}"] += 1
            if delay:
                time.sleep(delay)
        total = passed + failed
        rate = 100.0 * passed / total if total else 0.0
        console.print(
            f"[green]✓[/green] {label}: {passed}/{total} operations passed ({rate:.1f}%)"
        )
        for why, n in failures.most_common(5):
            console.print(f"    {n}x {why}")
        if rate < threshold:
            raise WorkflowError(f"{label}: {rate:.1f}% passed, under {threshold}%")

    def _step(self, step: dict, label: str) -> str:
        """Run one resolved step and check what it promised; returns a note."""
        expected = step.get("expected_error")
        # v1's options: `expected_failure` (any refusal is success) and
        # `allow_failure` (either outcome is), with the refusal in `error`.
        any_failure = bool(step.get("expected_failure"))
        tolerated = bool(step.get("allow_failure"))
        try:
            result = self._run_one(step)
        except (self._CloudError, _Refused) as e:
            if (expected and e.args[2] == expected) or any_failure or tolerated:
                details = e.args[3] if len(e.args) > 3 else ""
                failure = {"error": f"{e.args[0]} {details}", "code": e.args[2]}
                for var, path in (step.get("outputs") or {}).items():
                    if path == "error" or path.startswith("error"):
                        self.variables[var] = failure["error"]
                return f" (refused: {e.args[2]})"
            details = e.args[3] if len(e.args) > 3 else None
            why = details.get("error") if isinstance(details, dict) else details
            raise WorkflowError(
                f"{label}: {e.args[0]}" + (f" ({why})" if why else "")
            ) from e
        if expected or any_failure:
            raise WorkflowError(f"{label}: expected a refusal, but it succeeded")
        if "expect" in step:
            got = result.get("result") if isinstance(result, dict) else result
            if got != step["expect"]:
                raise WorkflowError(
                    f"{label}: expected {step['expect']!r}, got {got!r}"
                )
        for var, path in (step.get("outputs") or {}).items():
            if path == "error":
                self.variables[var] = None
                continue
            self.variables[var] = _pick(result, path)
        return ""

    def run(self) -> None:
        self._sign_up()
        self.run_steps(self.wf["steps"])


def run_workflow_outcome(
    path: str,
    binary: str | None = None,
    keep_data: str | None = None,
    logs: bool = False,
) -> str:
    """Start the workflow's cluster, run it, stop the cluster.

    Answers "passed", "failed", or "not_applicable" for a port that does not
    test what its v1 workflow did (`not_applicable` in the file, set by
    `merobox cloud port`): it is not run, and it is not a pass.
    """
    wf = load_workflow(path)
    if wf.get("not_applicable"):
        console.print(
            f"[bold]{wf.get('name', path)}[/bold]: [yellow]not applicable[/yellow] "
            f"({wf['not_applicable']})"
        )
        return "not_applicable"
    spec = wf["cluster"]
    cluster = ProcessCluster(
        nodes=int(spec.get("nodes", 3)),
        base_port=int(spec.get("base_port", 2700)),
        binary=binary or spec.get("binary"),
        data_dir=keep_data,
        logs=logs,
    )
    console.print(f"[bold]{wf.get('name', path)}[/bold] on {cluster.size} merod nodes")
    cluster.start()
    try:
        Runner(wf, cluster).run()
        console.print("[bold green]Workflow passed[/bold green]")
        return "passed"
    except WorkflowError as e:
        console.print(f"[bold red]Workflow failed:[/bold red] {e}")
        return "failed"
    finally:
        cluster.stop()


def run_workflow(
    path: str,
    binary: str | None = None,
    keep_data: str | None = None,
    logs: bool = False,
) -> bool:
    """`run_workflow_outcome` == "passed": a not-applicable port is no pass."""
    return run_workflow_outcome(path, binary, keep_data, logs) == "passed"
