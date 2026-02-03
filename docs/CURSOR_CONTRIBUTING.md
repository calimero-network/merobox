# Cursor Contributor Guide for Merobox

This guide helps contributors get the best experience when working on merobox using Cursor AI.

## Quick Start

### 1. Clone and Open in Cursor

```bash
git clone https://github.com/calimero-network/merobox.git
cd merobox
cursor .
```

### 2. Set Up Development Environment

Merobox requires Python 3.9-3.11 (not 3.12+ due to ed25519 package dependency).

```bash
# Ensure correct Python version
python3 --version  # Should be 3.9.x, 3.10.x, or 3.11.x

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Verify installation
merobox --version
```

### 3. Verify Tooling

```bash
# Ensure rustfmt is available for any Rust interactions
rustup component add rustfmt

# Run formatters and linters
make format        # Format code with black
make format-check  # Check formatting without changing
make lint          # Run ruff linter (if configured)

# Run tests
make test          # Run all tests
pytest -v          # Run tests with verbose output
```

## Cursor Best Practices

### Using Cursor Rules

This repository should have a `.cursorrules` file (or you can create one) that helps Cursor understand:
- Project uses black for formatting (line-length 88)
- Use async/await for I/O operations
- Error handling should use the result.py ok/fail pattern
- Type hints are expected on public functions

### Composer vs Agent Mode

**Use Composer for:**
- Multi-file refactoring tasks
- Creating new step types (requires multiple files)
- Large architectural changes
- Implementing new CLI commands

**Use Agent (Chat) for:**
- Understanding existing code
- Debugging specific issues
- Small fixes and improvements
- Code review discussions

### Terminal Integration

Use Cursor's integrated terminal for:
```bash
# Running tests for a specific module
pytest merobox/tests/unit/test_binary_manager.py -v

# Running a workflow to test changes
merobox bootstrap run workflow-examples/workflow-example.yml --verbose

# Checking node status
merobox list
merobox health
```

## Repository Structure

```
merobox/
├── merobox/                    # Main Python package
│   ├── cli.py                 # CLI entry point (Click-based)
│   ├── __init__.py            # Package version
│   ├── testing.py             # Test framework helpers
│   └── commands/              # Command implementations
│       ├── manager.py         # DockerManager - Docker node management
│       ├── binary_manager.py  # BinaryManager - Native binary mode
│       ├── auth.py            # Authentication handling
│       ├── node_resolver.py   # Node URL resolution
│       ├── remote_nodes.py    # Remote node registry
│       ├── constants.py       # Shared constants
│       ├── utils.py           # Utility functions
│       ├── result.py          # Result type for API responses
│       ├── retry.py           # Retry logic utilities
│       └── bootstrap/         # Workflow orchestration
│           ├── config.py      # YAML config loading
│           ├── run/
│           │   └── executor.py # Workflow executor (main orchestration)
│           └── steps/         # Step implementations
│               ├── base.py    # BaseStep class
│               ├── execute.py # Call step
│               ├── install.py # Install step
│               └── ...        # Other steps
├── workflow-examples/          # Example workflow YAML files
├── merobox/tests/              # Test suite
├── bounties.json              # Available bounties
├── pyproject.toml             # Project configuration
└── Makefile                   # Build automation
```

## Working on Bounties

### 1. Pick a Bounty

View available bounties in `bounties.json`:
```bash
cat bounties.json | python -m json.tool | head -100
```

Or in Cursor, open `bounties.json` and use the AI to explain bounties.

### 2. Use pathHint

Each bounty has a `pathHint` field pointing to the relevant file. Open it in Cursor:
```
Cmd/Ctrl + P → type the path
```

### 3. Make Minimal Changes

- Focus on the specific issue described
- Don't refactor unrelated code
- Add tests for your changes when appropriate

### 4. Run Tests and Format

**Before committing:**
```bash
# Format code
make format

# Run tests
make test

# Check for linting issues
ruff check merobox/
```

### 5. Commit with Conventional Format

Use conventional commit format for PR titles and commits:

```bash
# Bug fixes
git commit -m "fix: correct JWT padding calculation in auth.py"

# New features
git commit -m "feat: add dry-run mode for workflow execution"

# Documentation
git commit -m "docs: add architecture documentation"

# Refactoring
git commit -m "refactor: extract BaseNodeManager from DockerManager"

# Tests
git commit -m "test: add integration tests for workflow execution"
```

## Key Files for AI Context

When working on specific areas, include these files for better AI context:

### Workflow System
- `merobox/commands/bootstrap/run/executor.py` - Main orchestrator
- `merobox/commands/bootstrap/steps/base.py` - Step base class
- `merobox/commands/bootstrap/config.py` - YAML loading

### Node Management
- `merobox/commands/manager.py` - Docker mode
- `merobox/commands/binary_manager.py` - Binary mode
- `merobox/commands/node_resolver.py` - Node resolution

### Authentication
- `merobox/commands/auth.py` - Token management
- `merobox/commands/remote_nodes.py` - Remote registry

### CLI Commands
- `merobox/cli.py` - Command registration
- `merobox/commands/run.py` - `merobox run` command
- `merobox/commands/call.py` - `merobox call` command

## Common Development Tasks

### Adding a New Step Type

1. Create `merobox/commands/bootstrap/steps/your_step.py`
2. Inherit from `BaseStep` in `base.py`
3. Implement `_get_required_fields()`, `_validate_field_types()`, `execute()`
4. Register in `merobox/commands/bootstrap/steps/__init__.py`
5. Add to executor's `_create_step_executor()` in `executor.py`
6. Add tests in `merobox/tests/unit/test_your_step.py`

### Adding a New CLI Command

1. Create `merobox/commands/your_command.py`
2. Use Click decorators for argument parsing
3. Register in `merobox/commands/__init__.py`
4. Add to CLI in `merobox/cli.py`
5. Document in README.md

### Testing Your Changes Locally

```bash
# Start test nodes
merobox run --count 2

# Check they're running
merobox list
merobox health

# Run a test workflow
merobox bootstrap run workflow-examples/workflow-example.yml

# Stop nodes
merobox stop --all
```

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `MEROBOX_USERNAME` | Default auth username | - |
| `MEROBOX_PASSWORD` | Default auth password | - |
| `MEROBOX_API_KEY` | Default API key | - |
| `CALIMERO_WEBUI_FETCH` | Fresh WebUI (1) or cached (0) | 1 |
| `CALIMERO_AUTH_FRONTEND_FETCH` | Fresh auth UI (1) or cached (0) | 1 |
| `RUST_LOG` | Log level for nodes | debug |
| `RUST_BACKTRACE` | Enable backtraces | 0 |

## Debugging Tips

### Verbose Mode
```bash
merobox bootstrap run workflow.yml --verbose
```

### Check Node Logs
```bash
merobox logs calimero-node-1 --follow
```

### Debug in Python
```python
# In step implementations, use:
from merobox.commands.utils import console
console.print(f"[blue]Debug: {variable}[/blue]")
```

## Getting Help

- Check README.md for comprehensive documentation
- Look at workflow-examples/ for usage patterns
- Review existing step implementations for patterns
- Open GitHub issues for bugs or questions

## Tips for Effective AI Collaboration

1. **Provide context**: When asking about a bug, include the relevant file paths
2. **Be specific**: "Fix the race condition in binary_manager.py line 450" is better than "fix bugs"
3. **Test incrementally**: Make small changes, test, then continue
4. **Use @-mentions**: Reference files with `@merobox/commands/manager.py` in Cursor
5. **Share error messages**: Include full tracebacks when debugging
