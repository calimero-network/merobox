# Cursor Contributing Guide for Merobox

This guide helps contributors get the best from Cursor when working on merobox bounties.

## Opening the Repository

### Prerequisites

1. **Python 3.9-3.11** (Python 3.12+ is not supported due to `ed25519` dependency)
2. **Docker 20.10+** for containerized node management
3. **Git** for version control

### Setup Steps

```bash
# Clone the repository
git clone https://github.com/calimero-network/merobox.git
cd merobox

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install merobox in development mode
pip install -e .

# Verify installation
merobox --version
merobox --help
```

### Opening in Cursor

1. Open Cursor and select **File > Open Folder**
2. Navigate to your cloned `merobox` directory
3. Cursor will automatically detect the Python project

## Cursor Best Practices

### Using Rules

Merobox doesn't yet have a `.cursorrules` file (creating one is a bounty!). When contributing:

- **Python Style**: Follow PEP 8, use Black formatting (line length 88)
- **Async/Await**: Most workflow operations are async - use `async def` and `await`
- **Error Handling**: Use typed errors from `merobox/commands/errors.py`
- **Logging**: Use `rich.console.Console` for output, not print()

### Composer vs Agent Mode

**Use Composer (Cmd+K / Ctrl+K) for:**
- Quick edits to single files
- Adding type hints
- Writing docstrings
- Small refactors

**Use Agent (Cmd+I / Ctrl+I) for:**
- Multi-file changes (like extracting common code)
- Implementing new features
- Debugging test failures
- Understanding codebase architecture

### Terminal Integration

Prefer in-editor terminal for:
```bash
# Format code (required before commit)
make format

# Check formatting without changing files
make format-check

# Run unit tests
python -m pytest merobox/tests/unit -v

# Run a workflow for testing
merobox bootstrap run workflow-examples/workflow-example.yml --no-docker --e2e-mode

# Clean up test state
merobox stop --all
merobox nuke -f
```

## Repository Structure

```
merobox/
├── merobox/                    # Main package
│   ├── cli.py                  # CLI entry point - start here
│   ├── commands/               # All CLI commands
│   │   ├── manager.py          # DockerManager - container lifecycle
│   │   ├── binary_manager.py   # BinaryManager - native process lifecycle
│   │   ├── auth.py             # Authentication handling
│   │   ├── client.py           # Calimero API client creation
│   │   ├── errors.py           # Typed error hierarchy
│   │   ├── node_resolver.py    # Node resolution (local/remote)
│   │   ├── bootstrap/          # Workflow execution
│   │   │   ├── run/executor.py # Main workflow executor
│   │   │   ├── steps/          # Step implementations
│   │   │   │   ├── base.py     # Base step class with validation
│   │   │   │   └── *.py        # Individual step types
│   │   └── near/               # NEAR blockchain integration
│   │       ├── sandbox.py      # Local NEAR sandbox
│   │       └── client.py       # NEAR RPC client
│   └── tests/                  # Test files
├── workflow-examples/          # Example workflow YAML files
├── bounties.json               # Available bounties
└── docs/                       # Documentation
```

### Key Entry Points

1. **CLI Commands**: `merobox/cli.py` - All commands registered here
2. **Workflow Execution**: `merobox/commands/bootstrap/run/executor.py` - Main orchestrator
3. **Node Management**: `merobox/commands/manager.py` (Docker) or `binary_manager.py` (native)
4. **API Calls**: `merobox/commands/client.py` - Client creation helpers

## Working on Bounties

### 1. Pick a Bounty

Review `bounties.json` in the repo root. Each bounty has:
- `title`: Brief description
- `description`: Detailed explanation with file/function references
- `pathHint`: Starting file location
- `estimatedMinutes`: Time estimate
- `category`: Type of work (security, bug, design-flaw, etc.)
- `severity`: Priority (critical, high, medium, low)

### 2. Understand the Context

Use Cursor's AI to explore:
```
@bounties.json Show me the bounty about [topic]
@merobox/commands/[pathHint] Explain this file's role
```

### 3. Make Minimal Changes

- Focus on the specific issue
- Don't refactor unrelated code
- Keep PR scope tight

### 4. Test Your Changes

```bash
# Always run format before commit
make format

# Run relevant tests
python -m pytest merobox/tests/unit/test_[relevant].py -v

# For workflow changes, run an example workflow
merobox bootstrap run workflow-examples/workflow-example.yml --no-docker --e2e-mode

# Clean up
merobox nuke -f
```

### 5. Commit and PR

Use conventional commit format:
```bash
git add -A
git commit -m "fix(security): validate absolute paths in ScriptStep"
# or
git commit -m "feat(auth): add rate limiting for login attempts"
# or
git commit -m "docs: add architecture overview"
```

**Commit types:**
- `fix`: Bug fixes
- `feat`: New features
- `docs`: Documentation
- `refactor`: Code changes that don't fix bugs or add features
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

## Environment Variables

| Variable | Description |
|----------|-------------|
| `CALIMERO_IMAGE` | Docker image for Calimero nodes |
| `MEROBOX_USERNAME` | Default username for remote auth |
| `MEROBOX_PASSWORD` | Default password for remote auth |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Troubleshooting

### Common Issues

**Format check fails:**
```bash
make format  # Auto-fix formatting
```

**Tests fail with Docker errors:**
```bash
# Ensure Docker is running
docker ps
# Clean up stale containers
merobox nuke -f
```

**Import errors:**
```bash
# Reinstall in dev mode
pip install -e .
```

**Workflow hangs:**
```bash
# Check node logs
merobox logs calimero-node-1 --follow
# Force cleanup
merobox stop --all
merobox nuke -f
```

### Getting Help

1. Check existing workflow examples in `workflow-examples/`
2. Review unit tests for usage patterns
3. Ask Cursor AI about specific functions or patterns
4. Open an issue on GitHub for persistent problems

## CI/CD Integration

Before submitting a PR, ensure:

1. `make format-check` passes
2. `python -m pytest merobox/tests/unit -v` passes
3. At least one workflow example runs successfully

The CI pipeline will run:
- Format checking
- Unit tests
- Integration tests with Docker
- Full workflow execution

---

Happy contributing! If you have questions, open an issue or ask in the PR discussion.
