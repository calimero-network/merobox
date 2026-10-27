# Merobox CLI

A Python CLI that starts a release-v2 (Calimero Cloud) cluster of `merod` nodes,
one process per node, and drives it through declarative YAML workflows: clients
signed in with passkeys and API keys deploy apps and call them while steps
crash, freeze, restart and upgrade nodes.

> **Full documentation**: <https://calimero-network.github.io/merobox/>

v1's peer-to-peer nodes are gone, and with them merobox's Docker, binary and
remote node managers. A v1 workflow converts with `merobox cloud port`.

## Quick Start

```bash
git clone https://github.com/calimero-network/merobox.git
cd merobox
pip install -e .          # builds calimero-client-py from source: needs Rust
```

Build `merod` and the kv-store app from
[core](https://github.com/calimero-network/core), branch `release-v2`
(`cargo build -p merod`, `cargo build -p kv-store --target
wasm32-unknown-unknown --profile app-release`), then:

```bash
export MEROD_BINARY=…/core/target/debug/merod
export KV_STORE_WASM=…/core/target/wasm32-unknown-unknown/app-release/kv_store.wasm
merobox cloud run workflow-examples/cloud-v2/kv-store-failover.yml
```

## Commands

| Command | What it does |
| --- | --- |
| `merobox cloud run WORKFLOW` | Start the workflow's cluster, run its steps, stop the cluster (`--binary`, `--data-dir`, `--logs`) |
| `merobox cloud validate WORKFLOW` | Check a workflow without starting anything |
| `merobox cloud port SOURCES... --out-dir DIR` | Port v1 workflows to cloud-v2 workflows |
| `merobox bootstrap run` / `bootstrap validate` / `run` | v1's names, routed to `cloud run` / `cloud validate` / `cloud run` |

## Example Workflow

```yaml
name: KV store across a node failure
backend: cloud-v2
cluster: { nodes: 3, base_port: 2700 }
accounts: [alice]
steps:
  - type: deploy
    account: alice
    wasm: "{{env.KV_STORE_WASM}}"
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

Every step type is listed in
[`workflow-examples/cloud-v2/README.md`](workflow-examples/cloud-v2/README.md).

## Requirements

- **Python** 3.9+, and a Rust toolchain to build `calimero-client-py`
- **merod** from core's `release-v2` branch
- **OS**: Linux, macOS

## Documentation

| Topic | Page |
|-------|------|
| How a run works | [System Overview](https://calimero-network.github.io/merobox/understand/system-overview/) |
| The workflow format and every step type | [Workflow YAML](https://calimero-network.github.io/merobox/workflows/yaml/) |
| The shipped examples and their inputs | [Examples](https://calimero-network.github.io/merobox/workflows/examples/) |
| Converting v1 workflows | [Port a v1 workflow](https://calimero-network.github.io/merobox/guides/porting/) |
| pytest, unit tests, CI | [Testing](https://calimero-network.github.io/merobox/guides/testing/) |
| Every command and flag | [CLI Reference](https://calimero-network.github.io/merobox/reference/cli/) |
| Common failures | [Troubleshooting](https://calimero-network.github.io/merobox/reference/troubleshooting/) |

## Release Process

```bash
# Update version in ONE place
vim merobox/__init__.py  # Change __version__ = "X.Y.Z"

# Commit and push — automation handles tagging, builds, GitHub release, and PyPI
git add merobox/__init__.py
git commit -m "chore: bump version to X.Y.Z"
git push origin master
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes (`make format-check`, `python -m pytest merobox/tests/unit`)
4. Submit a pull request

## License

MIT — see [LICENSE](LICENSE) for details.

## Links

- [Documentation](https://calimero-network.github.io/merobox/)
- [Example Workflows](workflow-examples/cloud-v2/)
- [GitHub Issues](https://github.com/calimero-network/merobox/issues)
- [PyPI](https://pypi.org/project/merobox/)
