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

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from rich.console import Console

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


def _stringify(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v)


def _pick(result: Any, path: str) -> Any:
    if path in ("", "."):
        return result
    return _lookup(result if isinstance(result, dict) else {"value": result}, path)


# ----------------------------------------------------------------- loading

STEP_TYPES: dict[str, tuple[str, ...]] = {
    # type: required fields
    "deploy": ("wasm",),
    "upload_bundle": ("wasm",),
    "create_app": ("bundle",),
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
    "http": ("method", "path"),
    "ops": ("op",),
    "find_leader": ("app",),
    "kill_node": ("node",),
    "pause_node": ("node",),
    "resume_node": ("node",),
    "start_node": ("node",),
    "restart_node": ("node",),
    "wait": ("seconds",),
    "assert": ("statements",),
    "repeat": ("count", "steps"),
}

#: Steps that act as someone: an `account` (session) or an `api_key`.
_ACTING = {
    "deploy",
    "upload_bundle",
    "create_app",
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
    "http",
}


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
        raise WorkflowError("not a release-v2 workflow (expected 'backend: cloud-v2')")
    cluster = wf.setdefault("cluster", {})
    if not isinstance(cluster, dict):
        raise WorkflowError("'cluster' must be a mapping")
    accounts = wf.setdefault("accounts", [])
    if not isinstance(accounts, list) or not all(isinstance(a, str) for a in accounts):
        raise WorkflowError("'accounts' must be a list of names")
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

    # -- clients

    def _node_order(self, node: int | None) -> list[int]:
        running = self.cluster.running()
        if node is not None:
            return [int(node)]
        return running or self.cluster.ids

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
        account = step.get("account")
        api_key = step.get("api_key")
        last: Exception | None = None
        for node in self._node_order(step.get("node")):
            try:
                return f(self._client(node, account, api_key))
            except self._CloudError as e:
                if e.args[2] != "TRANSPORT" or step.get("node") is not None:
                    raise
                last = e
        raise last or WorkflowError("no node to reach")

    def _sign_up(self) -> None:
        node = self._node_order(None)[0]
        accounts = {}
        for name in self.wf["accounts"]:
            pk = self._SoftPasskey()
            c = self._CloudClient(self.cluster.url(node), allow_mock=True)
            session = c.sign_up(pk, name)
            self.passkeys[name] = pk
            self.sessions[name] = session
            accounts[name] = {"account_id": session["accountId"]}
        self.variables["accounts"] = accounts
        self.variables["ops_token"] = self.cluster.ops_token

    # -- steps

    def _wasm(self, step: dict) -> bytes:
        p = Path(step["wasm"])
        return (p if p.is_absolute() else self.base_dir / p).read_bytes()

    @staticmethod
    def _app_options(step: dict) -> dict:
        opts: dict[str, Any] = {}
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
            except self._CloudError as e:
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
            except self._CloudError as e:
                last = e.args[0]
            if time.time() > deadline:
                raise WorkflowError(
                    f"wait_for {step['method']}: still {last!r} after {timeout}s"
                )
            time.sleep(interval)

    def _run_one(self, step: dict) -> Any:
        kind = step["type"]
        c = self.cluster
        if kind == "deploy":
            wasm = self._wasm(step)

            def deploy(cl):
                bundle = cl.upload_bundle(wasm)
                app = cl.create_app(
                    step.get("app_name", "app"), bundle, self._app_options(step)
                )
                return {"appId": app, "bundle": bundle}

            return self._with_client(step, deploy)
        if kind == "upload_bundle":
            wasm = self._wasm(step)
            return {"hash": self._with_client(step, lambda cl: cl.upload_bundle(wasm))}
        if kind == "create_app":
            return {
                "appId": self._with_client(
                    step,
                    lambda cl: cl.create_app(
                        step.get("app_name", "app"),
                        step["bundle"],
                        self._app_options(step),
                    ),
                )
            }
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
        if kind == "view":
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
        if kind == "whoami":
            return self._with_client(step, lambda cl: cl.me())
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
                    {"name": step["team_name"], "join": step.get("join", "invite")},
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
                lambda cl: cl.delete(f"orgs/{step['org']}/members/{step['member']}"),
            )
        if kind == "http":
            method = step["method"].lower()
            path = step["path"].lstrip("/")
            if method == "get":
                return self._with_client(step, lambda cl: cl.get(path))
            if method == "delete":
                return self._with_client(step, lambda cl: cl.delete(path))
            return self._with_client(step, lambda cl: cl.post(path, step.get("body")))
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
        raise WorkflowError(f"unhandled step type {kind}")

    def _assert(self, step: dict) -> None:
        from merobox.commands.bootstrap.steps.assertion import AssertStep

        ok = asyncio.run(AssertStep(step).execute({}, self.variables))
        if not ok:
            raise WorkflowError("assertion failed")

    def run_steps(self, steps: list, depth: int = 0) -> None:
        for _i, raw in enumerate(steps, start=1):
            label = raw.get("name") or raw["type"]
            if raw["type"] == "assert":
                # The assert step resolves its own placeholders.
                self._assert(raw)
                continue
            if raw["type"] == "repeat":
                for n in range(int(resolve(raw["count"], self.variables))):
                    self.variables["iteration"] = n
                    self.variables["iteration_node"] = n + 1
                    self.run_steps(raw["steps"], depth + 1)
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

    def _step(self, step: dict, label: str) -> str:
        """Run one resolved step and check what it promised; returns a note."""
        expected = step.get("expected_error")
        try:
            result = self._run_one(step)
        except self._CloudError as e:
            if expected and e.args[2] == expected:
                return f" (refused: {expected})"
            raise WorkflowError(f"{label}: {e.args[0]}") from e
        if expected:
            raise WorkflowError(f"{label}: expected {expected}, but it succeeded")
        if "expect" in step:
            got = result.get("result") if isinstance(result, dict) else result
            if got != step["expect"]:
                raise WorkflowError(
                    f"{label}: expected {step['expect']!r}, got {got!r}"
                )
        for var, path in (step.get("outputs") or {}).items():
            self.variables[var] = _pick(result, path)
        return ""

    def run(self) -> None:
        self._sign_up()
        self.run_steps(self.wf["steps"])


def run_workflow(
    path: str,
    binary: str | None = None,
    keep_data: str | None = None,
    logs: bool = False,
) -> bool:
    """Start the workflow's cluster, run it, stop the cluster. True on success."""
    wf = load_workflow(path)
    spec = wf["cluster"]
    cluster = ProcessCluster(
        nodes=int(spec.get("nodes", 3)),
        base_port=int(spec.get("base_port", 2700)),
        binary=binary or spec.get("binary"),
        data_dir=keep_data,
        logs=logs,
    )
    console.print(
        f"[bold]{wf.get('name', path)}[/bold] on {cluster.size} merod-v2 nodes"
    )
    cluster.start()
    try:
        Runner(wf, cluster).run()
        console.print("[bold green]Workflow passed[/bold green]")
        return True
    except WorkflowError as e:
        console.print(f"[bold red]Workflow failed:[/bold red] {e}")
        return False
    finally:
        cluster.stop()
