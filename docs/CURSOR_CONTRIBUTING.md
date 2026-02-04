# Cursor Contributor Guide for Merobox

This guide helps contributors get the most out of Cursor when working on merobox. It covers setup, best practices, and repo-specific workflows.

## Opening the Repository

### Clone and Setup

```bash
# Clone the repository
git clone https://github.com/calimero-network/merobox.git
cd merobox

# Open in Cursor
cursor .
```

### Ensure Python/Toolchain is Available

```bash
# Verify Python version (3.9-3.11 required, 3.12+ not supported)
python3 --version

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt
pip install -e .  # Install merobox in development mode

# Verify installation
merobox --version
```

### Install Development Dependencies

```bash
# Install dev dependencies for testing/formatting
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

## Cursor Best Practices for This Repo

### Using Cursor Rules

This repository would benefit from a `.cursorrules` file. If one exists, Cursor will automatically apply project conventions. Key conventions to follow:

- **Async/await patterns**: Most API operations are async. Use `async def` for network calls.
- **Error handling**: Return `{"success": True/False, "data": ..., "error": ...}` from operations.
- **Logging**: Use `rich.console.Console` for output, not `print()`.
- **Type hints**: Add type annotations to all public functions.

### Composer vs Agent

- **Use Composer** for:
  - Writing new step types in `merobox/commands/bootstrap/steps/`
  - Adding CLI commands to `merobox/commands/`
  - Creating workflow examples in `workflow-examples/`

- **Use Agent** for:
  - Multi-file refactoring (e.g., extracting common code from managers)
  - Investigating bugs that span multiple modules
  - Adding comprehensive tests across step types

### When to Use Terminal vs In-Editor

- **In-editor editing**: For Python code changes, step implementations, config updates
- **Terminal**: For running tests, workflows, and validating changes

```bash
# Terminal commands you'll use frequently
merobox bootstrap validate workflow-examples/workflow-example.yml
merobox bootstrap run workflow-examples/workflow-example.yml --no-docker --e2e-mode
make format
make format-check
pytest merobox/tests/unit/ -v
```

## Repo-Specific Workflow

### Running Tests

```bash
# Run unit tests
pytest merobox/tests/unit/ -v

# Run specific test file
pytest merobox/tests/unit/test_binary_manager.py -v

# Run with coverage
pytest merobox/tests/unit/ --cov=merobox --cov-report=html
```

### Formatting Code

```bash
# Format code with Black
make format

# Check formatting without modifying
make format-check

# Or use Black directly
black merobox/ --check  # Check only
black merobox/          # Apply formatting
```

### Running Lints

```bash
# Ruff linter
ruff check merobox/

# With auto-fix
ruff check merobox/ --fix
```

### Running Workflows Locally

```bash
# Validate workflow syntax
merobox bootstrap validate workflow-examples/workflow-example.yml

# Run workflow with Docker
merobox bootstrap run workflow-examples/workflow-example.yml

# Run workflow without Docker (binary mode)
merobox bootstrap run workflow-examples/workflow-example.yml --no-docker --binary-path /path/to/merod

# Run with NEAR Devnet
merobox bootstrap run workflow-examples/workflow-example.yml \
  --near-devnet \
  --contracts-dir ./contracts/near
```

### Key Environment Variables

| Variable | Description |
|----------|-------------|
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `RUST_BACKTRACE` | Backtrace for Rust components (0, 1, full) |
| `MEROBOX_USERNAME` | Default username for remote auth |
| `MEROBOX_PASSWORD` | Default password for remote auth |
| `CALIMERO_IMAGE` | Docker image for Calimero nodes |

### Where Main Crates/Entry Points Live

```
merobox/
├── cli.py                     # CLI entry point (main commands)
├── __init__.py               # Version and package metadata
└── commands/
    ├── manager.py             # DockerManager - Docker node management
    ├── binary_manager.py      # BinaryManager - Native binary management
    ├── call.py                # Function call execution
    ├── auth.py                # Authentication handling
    ├── node_resolver.py       # Node URL resolution
    ├── remote.py              # Remote node CLI commands
    ├── remote_nodes.py        # Remote node registry
    └── bootstrap/
        ├── run/
        │   └── executor.py    # Workflow executor (main orchestration)
        ├── steps/             # Step implementations
        │   ├── base.py        # Base step class
        │   ├── execute.py     # Contract call step
        │   ├── script.py      # Script execution step
        │   └── ...            # Other step types
        └── validate/
            └── validator.py   # Workflow validation
```

## Working on Bounties

### Picking a Bounty

1. Open `bounties.json` in the repository root
2. Find a bounty matching your skill level and interests
3. Note the `pathHint` - this points to the relevant file(s)
4. Read the `description` carefully for what needs to change

### Using pathHint and Description

The `pathHint` tells you where to start looking. For example:

```json
{
  "title": "Retry decorator does not distinguish transient vs permanent errors",
  "pathHint": "merobox/commands/retry.py",
  "description": "NETWORK_RETRY_CONFIG retries on ConnectionError but not on 401/403/404..."
}
```

Open `merobox/commands/retry.py` in Cursor, then use Composer to understand the code and propose changes.

### Making Minimal Changes

- **Focus on the specific issue** - don't refactor unrelated code
- **Add tests** for bug fixes when possible
- **Update docstrings** if changing function behavior
- **Keep commits focused** - one logical change per commit

### Running Tests and Formatting Before Committing

```bash
# Always run before committing:
make format              # Format code
make format-check        # Verify formatting
pytest merobox/tests/unit/ -v  # Run tests

# Validate if you modified workflow-related code:
merobox bootstrap validate workflow-examples/workflow-example.yml
```

## Conventional Commits and PRs

Use conventional commit format for all commits:

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

- `fix`: Bug fixes
- `feat`: New features
- `docs`: Documentation changes
- `refactor`: Code refactoring without behavior change
- `test`: Adding or updating tests
- `chore`: Maintenance tasks (deps, CI, etc.)
- `security`: Security fixes

### Examples

```bash
# Bug fix
git commit -m "fix(retry): distinguish transient vs permanent errors in retry logic"

# New feature
git commit -m "feat(steps): add timeout support for fuzzy test operations"

# Documentation
git commit -m "docs: add architecture section to README"

# Security fix
git commit -m "security(script): add path traversal validation for script paths"
```

### PR Title Format

Use the same conventional commit format for PR titles:

```
fix(auth): prevent rate-limiting bypass on authentication failures
```

## Quick Reference

### Common Commands

```bash
# Development
pip install -e .                    # Install in dev mode
merobox --help                      # CLI help
merobox bootstrap validate FILE     # Validate workflow

# Testing
pytest merobox/tests/unit/ -v       # Run tests
make format                         # Format code
make format-check                   # Check formatting

# Running workflows
merobox run --count 2               # Start 2 nodes
merobox list                        # List nodes
merobox stop --all                  # Stop all nodes
merobox nuke -f                     # Clean all data
```

### File Locations

| What | Where |
|------|-------|
| CLI entry point | `merobox/cli.py` |
| Step implementations | `merobox/commands/bootstrap/steps/` |
| Node managers | `merobox/commands/manager.py`, `binary_manager.py` |
| Tests | `merobox/tests/unit/` |
| Workflow examples | `workflow-examples/` |
| Bounties | `bounties.json` |

## Getting Help

- **README.md**: Comprehensive user documentation
- **GitHub Issues**: Bug reports and feature requests
- **Workflow Examples**: Reference implementations in `workflow-examples/`
- **Command Help**: `merobox <command> --help`
