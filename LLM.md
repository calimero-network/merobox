# Merobox - LLM Usage Guide

## What is Merobox?

Merobox runs YAML workflows against a release-v2 (Calimero Cloud) cluster. In
release-v2 there is no node per user: a cluster of `merod` nodes replicates
every app by Raft, and users are clients signed in with passkeys or API keys.
merobox starts that cluster on 127.0.0.1 with one `merod` process per node, so
a workflow can crash (`kill_node`), freeze (`pause_node`), restart and upgrade
(`restart_node` with `binary`) nodes, and drives it through calimero-client-py's
`CloudClient`.

v1 (peer-to-peer nodes in Docker or as binaries, namespaces, groups, contexts)
is gone. `merobox cloud port` converts a v1 workflow.

## Install

```bash
git clone https://github.com/calimero-network/merobox.git && cd merobox
pip install -e .   # needs Rust: calimero-client-py is built from source
```

`merod` comes from core's `release-v2` branch (`cargo build -p merod`);
merobox finds it through `--binary`, `MEROD_BINARY`, `MEROD_V2_BINARY`, then
`PATH`.

## Commands

```bash
merobox cloud run WORKFLOW [--binary merod] [--data-dir DIR] [--logs]
merobox cloud validate WORKFLOW
merobox cloud port v1/*.yml --out-dir ported/ [--wasm-map map.json] [--base-port 2800]

# v1's names, routed to the commands above
merobox bootstrap run WORKFLOW
merobox bootstrap validate WORKFLOW
merobox run WORKFLOW
```

`cloud run` exits 0 passed, 1 failed, 2 invalid file (including a v1 workflow),
3 a port marked `not_applicable`.

## Workflow format

```yaml
name: KV store across a node failure
backend: cloud-v2                      # required
cluster: { nodes: 3, base_port: 2700 } # node i on 127.0.0.1:base_port+i
                                      # settings: TOML or a path, each node's --settings
accounts: [alice, bob]                 # each signed up with its own passkey
variables: { greeting: hello }
steps:
  - type: deploy
    account: alice
    wasm: "{{env.KV_STORE_WASM}}"
    outputs: { app_id: appId }
  - type: call
    account: alice
    app: "{{app_id}}"
    method: set
    args: { key: a, value: "{{greeting}}" }
  - type: kill_node
    node: 1
  - type: wait_for
    account: alice
    app: "{{app_id}}"
    method: get
    args: { key: a }
    equals: hello
  - type: assert
    statements: ["is_set({{app_id}})"]
```

Placeholders: `{{var}}` (whole-string keeps its type), dotted paths into
results, `{{a ?? b}}`, `{{env.NAME}}`, `{{accounts.<name>.account_id}}`,
`{{ops_token}}`, `{{iteration}}` inside `repeat`.

Every step takes `name`, `outputs`, `expected_error`, `expect`, `retry_for`,
`max_seconds`; acting steps take `account` or `api_key`, and optionally `node`.

The full step list (deploy, packages, calls and views, organizations, teams,
API keys, devices, blobs, ops, faults, fuzzy_test, assert/json_assert) is in
`workflow-examples/cloud-v2/README.md`; the authoritative table is
`STEP_TYPES` in `merobox/cloud_v2/workflow.py`.

## Python API

```python
from merobox.cloud_v2 import ProcessCluster, load_workflow, run_workflow, run_workflow_outcome

assert run_workflow("wf.yml")              # True only when it passed
cluster = ProcessCluster(nodes=3, base_port=2900)
cluster.start(); cluster.kill_node(2); cluster.stop()
```

## Layout

- `merobox/cli.py`, `merobox/commands/cloud.py`: the CLI
- `merobox/cloud_v2/workflow.py`: loader and runner
- `merobox/cloud_v2/cluster.py`: `ProcessCluster`, `PackageRegistry`
- `merobox/cloud_v2/assertions.py`: `assert` / `json_assert`
- `merobox/cloud_v2/port.py`: v1 → cloud-v2 porter (its docstring maps every v1 step)
- `workflow-examples/cloud-v2/`: the examples
