# Cursor Contributor Guide for Merobox

This guide helps contributors get the best experience when using Cursor AI to work on the merobox codebase.

## Opening the Repository in Cursor

### Prerequisites

1. **Python 3.9-3.11** (Python 3.12+ is not supported due to `ed25519` dependency)
2. **Docker 20.10+** (for Docker mode)
3. **Git**

### Setup Steps

```bash
# Clone the repository
git clone https://github.com/calimero-network/merobox.git
cd merobox

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Verify installation
merobox --version
```

Open the cloned directory in Cursor.

## Project Architecture

### Key Directories

| Directory | Purpose |
|-----------|---------|
| `merobox/cli.py` | CLI entry point using Click |
| `merobox/commands/` | Command implementations |
| `merobox/commands/manager.py` | DockerManager for container orchestration |
| `merobox/commands/binary_manager.py` | BinaryManager for native process management |
| `merobox/commands/bootstrap/` | Workflow orchestration system |
| `merobox/commands/bootstrap/steps/` | Individual workflow step implementations |
| `merobox/tests/` | Unit tests |
| `workflow-examples/` | Example workflow YAML files |

### Core Classes

- **DockerManager** (`manager.py`): Manages Calimero nodes in Docker containers
- **BinaryManager** (`binary_manager.py`): Manages Calimero nodes as native processes
- **WorkflowExecutor** (`bootstrap/run/executor.py`): Orchestrates workflow execution
- **BaseStep** (`bootstrap/steps/base.py`): Base class for workflow steps
- **AuthManager** (`auth.py`): Handles authentication for remote nodes
- **NodeResolver** (`node_resolver.py`): Resolves node names to URLs

### Data Flow

1. CLI commands are defined in `cli.py` and dispatch to command modules
2. `merobox run` creates nodes via DockerManager or BinaryManager
3. `merobox bootstrap run` loads YAML configs and executes via WorkflowExecutor
4. WorkflowExecutor creates step instances that inherit from BaseStep
5. Steps resolve dynamic variables and make API calls to Calimero nodes

## Cursor Best Practices

### Using Cursor Rules

Create a `.cursorrules` file in the project root if one doesn't exist:

```
# Merobox Project Rules

## Code Style
- Use Black for formatting (line length 88)
- Use type hints for all public functions
- Follow existing patterns in the codebase
- Use Rich console for user output, not print()

## Testing
- Add tests for new functionality in merobox/tests/unit/
- Run tests before committing: pytest merobox/tests/

## Commands
- Format: black merobox/
- Lint: ruff check merobox/
- Test: pytest merobox/tests/
- Type check: mypy merobox/

## Architecture
- DockerManager/BinaryManager are for node lifecycle
- WorkflowExecutor orchestrates multi-step operations
- Step classes handle individual workflow operations
```

### Using Composer vs Agent

**Use Composer for:**
- Small, focused changes (fix a single bug, add a parameter)
- Code exploration and understanding
- Generating test cases
- Writing documentation

**Use Agent for:**
- Multi-file refactoring
- Implementing new features that span multiple modules
- Complex debugging requiring exploration
- Working on bounties from `bounties.json`

### Terminal vs In-Editor

**Use Terminal (Ctrl+`) for:**
- Running tests: `pytest merobox/tests/`
- Formatting: `black merobox/`
- Running merobox commands: `merobox list`
- Git operations

**Use In-Editor for:**
- Code changes and refactoring
- Exploring function definitions (Go to Definition)
- Viewing type information (hover)

## Repo-Specific Workflow

### Running Tests

```bash
# Run all tests
pytest merobox/tests/

# Run specific test file
pytest merobox/tests/unit/test_config_utils.py

# Run with verbose output
pytest -v merobox/tests/

# Run with coverage
pytest --cov=merobox merobox/tests/
```

### Code Formatting

```bash
# Format with Black
black merobox/

# Check formatting without changing
black --check merobox/

# Lint with ruff
ruff check merobox/

# Fix auto-fixable issues
ruff check --fix merobox/
```

### Local Testing with Merobox

```bash
# Start a single node
merobox run

# Start multiple nodes
merobox run --count 2

# List running nodes
merobox list

# Check health
merobox health

# View logs
merobox logs calimero-node-1

# Run a workflow
merobox bootstrap run workflow-examples/workflow-example.yml

# Validate workflow without running
merobox bootstrap validate workflow-examples/workflow-example.yml

# Clean up
merobox stop --all
merobox nuke --force
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `LOG_LEVEL` | Python logging level (DEBUG, INFO, etc.) |
| `CALIMERO_WEBUI_FETCH` | Set to `0` to use cached WebUI |
| `CALIMERO_AUTH_FRONTEND_FETCH` | Set to `0` to use cached auth frontend |
| `MEROBOX_USERNAME` | Default username for remote auth |
| `MEROBOX_PASSWORD` | Default password for remote auth |

## Working on Bounties

### Finding a Bounty

1. Open `bounties.json` in the repository root
2. Filter by severity (critical, high, medium, low) or category
3. Read the description and `pathHint` to understand the issue
4. Check if the bounty is already being worked on (search issues/PRs)

### Working a Bounty

1. **Understand the context**: Read the relevant code using `pathHint`
2. **Write tests first**: If fixing a bug, write a failing test
3. **Make minimal changes**: Focus on the specific issue
4. **Run tests**: Ensure existing tests pass
5. **Format code**: Run `black merobox/` before committing

### Example Bounty Workflow

```bash
# 1. Find the file mentioned in pathHint
# Use Cursor to navigate: Cmd+P -> filename

# 2. Understand the code (use Cursor's Go to Definition)

# 3. Make your changes

# 4. Test locally
pytest merobox/tests/
merobox run --count 2  # Verify functionality

# 5. Format and lint
black merobox/
ruff check merobox/

# 6. Commit with conventional format
git add .
git commit -m "fix(security): remove hardcoded secret key from constants"
```

## Conventional Commits

Use conventional commit format for all commits and PR titles:

| Type | Purpose | Example |
|------|---------|---------|
| `fix` | Bug fix | `fix(auth): handle expired token refresh` |
| `feat` | New feature | `feat(workflow): add dry-run mode` |
| `docs` | Documentation | `docs: add cursor contributing guide` |
| `refactor` | Code refactoring | `refactor(manager): extract auth service logic` |
| `test` | Adding tests | `test(config): add yaml parsing tests` |
| `chore` | Maintenance | `chore: update dependencies` |
| `security` | Security fix | `fix(security): validate script paths` |

### Commit Message Format

```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

Example:
```
fix(manager): remove privileged mode from containers

Docker containers were running with privileged=True which grants
full host access. This change removes the flag and adds only
the specific capabilities needed.

Fixes #123
```

## Common Pitfalls

1. **Python version**: Ensure you're using Python 3.9-3.11
2. **Docker running**: Many tests require Docker daemon
3. **Port conflicts**: Use `merobox nuke` to clean up before testing
4. **Virtual environment**: Always activate venv before running commands
5. **Formatting**: Always run `black` before committing

## Getting Help

- **README.md**: Comprehensive documentation
- **workflow-examples/**: Example configurations
- **GitHub Issues**: Report bugs or ask questions
- **`merobox --help`**: CLI command help

## Quick Reference

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Development
black merobox/              # Format
ruff check merobox/         # Lint
pytest merobox/tests/       # Test
merobox --help              # CLI help

# Testing workflows
merobox run --count 2
merobox bootstrap run workflow-examples/workflow-example.yml
merobox stop --all

# Clean slate
merobox nuke --force
```
