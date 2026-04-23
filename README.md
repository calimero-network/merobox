# Merobox CLI

A Python CLI for managing Calimero nodes (Docker or native) and orchestrating YAML workflows.

> **Full documentation**: [Architecture Reference](https://calimero-network.github.io/merobox/)

## Quick Start

### Installation

**APT (Ubuntu/Debian):**

```bash
curl -fsSL https://calimero-network.github.io/merobox/gpg.key \
  | sudo tee /usr/share/keyrings/merobox.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/merobox.gpg] https://calimero-network.github.io/merobox stable main" \
  | sudo tee /etc/apt/sources.list.d/merobox.list
sudo apt update
sudo apt install merobox
```

**PyPI:**

```bash
pipx install merobox
```

**Homebrew:**

```bash
brew install merobox
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
- **Workflow Orchestration** — 35+ step types for complex multi-step YAML workflows
- **Remote Nodes** — Connect to remote Calimero nodes with user/password or API key auth
- **Auth Integration** — Traefik proxy (Docker) or embedded JWT auth (binary mode)
- **Fuzzy Testing** — Long-duration randomized load tests with weighted operations
- **pytest Integration** — `cluster()` and `workflow()` context managers for test harnesses

## Requirements

- **Python**: 3.9 – 3.11 (3.12+ not supported due to `py-near` / `ed25519` dependency)
- **Docker**: 20.10+ (for Docker mode)
- **OS**: Linux, macOS (Windows via WSL only)

## Documentation

All detailed documentation lives in the **[Architecture Reference](https://calimero-network.github.io/merobox/)**:

| Topic | Page |
|-------|------|
| System architecture & data flow | [System Overview](https://calimero-network.github.io/merobox/system-overview.html) |
| WorkflowExecutor, BaseStep, step factory | [Workflow Engine](https://calimero-network.github.io/merobox/workflow-engine.html) |
| DockerManager, BinaryManager, Traefik | [Node Management](https://calimero-network.github.io/merobox/node-management.html) |
| Remote node CLI & auth methods | [Remote Nodes](https://calimero-network.github.io/merobox/remote-nodes.html) |
| Complete YAML schema & all step types | [Workflow YAML](https://calimero-network.github.io/merobox/workflow-yaml.html) |
| All CLI commands with flags & options | [CLI Reference](https://calimero-network.github.io/merobox/cli-reference.html) |
| MeroboxError hierarchy, retry patterns | [Error Handling](https://calimero-network.github.io/merobox/error-handling.html) |
| cluster(), workflow(), pytest fixtures | [Testing Guide](https://calimero-network.github.io/merobox/testing.html) |
| Common issues & debugging tips | [Troubleshooting](https://calimero-network.github.io/merobox/troubleshooting.html) |
| Terms & definitions | [Glossary](https://calimero-network.github.io/merobox/glossary.html) |

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

See the [Testing Guide](https://calimero-network.github.io/merobox/testing.html) for development setup and adding new commands/step types.

## License

MIT — see [LICENSE](LICENSE) for details.

## Links

- [Architecture Reference](https://calimero-network.github.io/merobox/)
- [Example Workflows](workflow-examples/)
- [GitHub Issues](https://github.com/calimero-network/merobox/issues)
- [PyPI](https://pypi.org/project/merobox/)
