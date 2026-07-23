# Merobox CLI

A Python CLI for managing Calimero nodes (Docker or native) and orchestrating YAML workflows.

> **Full documentation**: <https://calimero-network.github.io/merobox/>

## Quick Start

### Installation

**PyPI (recommended):**

```bash
pipx install merobox
```

**From source:**

```bash
git clone https://github.com/calimero-network/merobox.git
cd merobox
pipx install -e .
```

### Basic Usage

```bash
# Start Calimero nodes
merobox run --count 2

# Start with auth service (Docker mode)
merobox run --auth-service

# Start with embedded auth (binary mode)
merobox run --no-docker --binary-path /path/to/merod --auth-mode embedded

# Pass extra arguments straight to `merod run` (binary mode only)
merobox bootstrap run workflow.yml --no-docker --binary-path ./merod \
  --merod-args="--sync-strategy delta --state-sync-strategy hash"

# Check node status
merobox health

# Execute a workflow
merobox bootstrap run workflow.yml

# Stop all nodes
merobox stop --all
```

### Example Workflow

```yaml
name: "Hello World"
nodes:
  count: 2
  prefix: "node"

steps:
  - name: "Install App"
    type: "install_application"
    node: "node-1"
    path: "./app.wasm"
    outputs:
      app_id: applicationId

  - name: "Create Context"
    type: "create_context"
    node: "node-1"
    application_id: "{{app_id}}"
    outputs:
      ctx_id: contextId

  - name: "Call Method"
    type: "call"
    node: "node-1"
    context_id: "{{ctx_id}}"
    method: "hello"
```

## Features

- **Node Management** — Start, stop, and monitor nodes in Docker or as native processes
- **Workflow Orchestration** — ~100 step types for complex multi-step YAML workflows
- **Remote Nodes** — Connect to remote Calimero nodes with user/password or API key auth
- **Auth Integration** — Traefik proxy (Docker) or embedded JWT auth (binary mode)
- **Fuzzy Testing** — Long-duration randomized load tests with weighted operations
- **pytest Integration** — `cluster()` and `workflow()` context managers for test harnesses

## Requirements

- **Python**: 3.9 – 3.11 (3.12+ not supported due to `py-near` / `ed25519` dependency)
- **Docker**: 20.10+ (for Docker mode)
- **OS**: Linux, macOS (Windows via WSL only)

## Documentation

All detailed documentation lives in the **[Documentation](https://calimero-network.github.io/merobox/)**:

| Topic | Page |
|-------|------|
| System architecture & data flow | [System Overview](https://calimero-network.github.io/merobox/understand/system-overview/) |
| WorkflowExecutor, BaseStep, step factory | [Workflow Engine](https://calimero-network.github.io/merobox/workflows/engine/) |
| DockerManager, BinaryManager, Traefik | [Node Management](https://calimero-network.github.io/merobox/guides/node-management/) |
| Remote node CLI & auth methods | [Remote Nodes](https://calimero-network.github.io/merobox/guides/remote-nodes/) |
| Complete YAML schema & all step types | [Workflow YAML](https://calimero-network.github.io/merobox/workflows/yaml/) |
| All CLI commands with flags & options | [CLI Reference](https://calimero-network.github.io/merobox/reference/cli/) |
| MeroboxError hierarchy, retry patterns | [Error Handling](https://calimero-network.github.io/merobox/reference/error-handling/) |
| cluster(), workflow(), pytest fixtures | [Testing Guide](https://calimero-network.github.io/merobox/guides/testing/) |
| Common issues & debugging tips | [Troubleshooting](https://calimero-network.github.io/merobox/reference/troubleshooting/) |
| Terms & definitions | [Glossary](https://calimero-network.github.io/merobox/understand/glossary/) |

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
3. Make your changes
4. Submit a pull request

See the [Testing Guide](https://calimero-network.github.io/merobox/guides/testing/) for development setup and adding new commands/step types.

## License

MIT — see [LICENSE](LICENSE) for details.

## Links

- [Documentation](https://calimero-network.github.io/merobox/)
- [Example Workflows](workflow-examples/)
- [GitHub Issues](https://github.com/calimero-network/merobox/issues)
- [PyPI](https://pypi.org/project/merobox/)
