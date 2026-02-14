# Cursor Contributor Guide for Merobox

This guide helps contributors get the most out of Cursor when working on merobox bounties and contributions.

## Opening the Repository

### Clone and Setup

```bash
# Clone the repository
git clone https://github.com/calimero-network/merobox.git
cd merobox

# Open in Cursor
cursor .
```

### Python Environment Setup

Merobox requires Python 3.9-3.11 (Python 3.12+ is not supported due to dependency constraints).

```bash
# Check Python version
python3 --version

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -e ".[dev]"
```

### Verify Installation

```bash
# Check merobox is installed
merobox --version

# Run tests to verify environment
pytest merobox/tests/unit/ -v
```

## Cursor Best Practices

### Using Cursor Rules

This repository benefits from cursor rules that help the AI understand project conventions. Create a `.cursorrules` file or use the existing project context:

**Key project conventions to be aware of:**
- Python 3.9+ style with type hints
- Black for formatting (line-length 88)
- Rich for console output
- Async/await for I/O operations
- Click for CLI commands
- YAML for workflow configurations

### Composer vs Agent Mode

| Use Case | Recommended Mode |
|----------|------------------|
| Understanding code structure | Composer (Chat) |
| Fixing a specific bounty | Agent Mode |
| Adding new features | Agent Mode |
| Refactoring large files | Agent Mode with review |
| Writing tests | Agent Mode |
| Documentation updates | Composer |

### When to Use Terminal vs In-Editor

**Use Terminal for:**
- Running tests: `pytest merobox/tests/`
- Formatting: `black merobox/`
- Linting: `ruff check merobox/`
- Running merobox commands: `merobox run --count 2`

**Use In-Editor for:**
- Code navigation and search
- Applying bounty fixes
- Writing new code with AI assistance

## Repository Structure

```
merobox/
├── merobox/                    # Main package
│   ├── cli.py                  # CLI entry point (Click commands)
│   ├── commands/               # Command implementations
│   │   ├── manager.py          # DockerManager for container nodes
│   │   ├── binary_manager.py   # BinaryManager for native nodes
│   │   ├── auth.py             # Authentication (JWT, tokens)
│   │   ├── node_resolver.py    # Node resolution (remote/local)
│   │   ├── bootstrap/          # Workflow orchestration
│   │   │   ├── run/executor.py # Workflow executor
│   │   │   ├── steps/          # Step implementations
│   │   │   └── validate/       # Workflow validation
│   │   └── near/               # NEAR sandbox integration
│   └── tests/                  # Unit tests
├── workflow-examples/          # Example workflow YAMLs
├── pyproject.toml              # Project configuration
└── bounties.json               # Available bounties
```

### Key Entry Points

- **CLI**: `merobox/cli.py` - Start here to understand commands
- **Workflows**: `merobox/commands/bootstrap/run/executor.py` - Main workflow logic
- **Step Base**: `merobox/commands/bootstrap/steps/base.py` - Common step functionality
- **Node Management**: `merobox/commands/manager.py` and `binary_manager.py`

## Working on Bounties

### Finding a Bounty

1. Open `bounties.json` in the repository root
2. Browse by severity (critical > high > medium > low)
3. Use `pathHint` to locate the relevant code

### Using Bounty Information in Cursor

When working on a bounty, provide Cursor with:

```
I'm working on this bounty:
Title: [bounty title]
Description: [bounty description]
Path: [pathHint]

[paste the relevant code section]
```

### Bounty Workflow

1. **Understand the issue**: Read the description and locate the code
2. **Create a branch**: `git checkout -b fix/bounty-title`
3. **Make minimal changes**: Focus on the specific issue
4. **Run tests**: `pytest merobox/tests/ -v`
5. **Format code**: `black merobox/`
6. **Lint**: `ruff check merobox/ --fix`
7. **Commit**: Use conventional commit format

## Running Tests

```bash
# Run all unit tests
pytest merobox/tests/unit/ -v

# Run specific test file
pytest merobox/tests/unit/test_binary_manager.py -v

# Run with coverage
pytest merobox/tests/ --cov=merobox --cov-report=html

# Run a specific test
pytest merobox/tests/unit/test_binary_manager.py::test_binary_manager_path_fix -v
```

## Formatting and Linting

```bash
# Format code with Black
black merobox/

# Check formatting without modifying
black merobox/ --check

# Run ruff linter
ruff check merobox/

# Auto-fix ruff issues
ruff check merobox/ --fix

# Type checking (optional, not all code is typed)
mypy merobox/ --ignore-missing-imports
```

## Testing Workflows

### Start Test Nodes

```bash
# Start Docker nodes (requires Docker)
merobox run --count 2

# Check node status
merobox list
merobox health

# Stop nodes
merobox stop --all
```

### Run Example Workflow

```bash
# Run a basic workflow
merobox bootstrap run workflow-examples/workflow-example.yml

# Validate workflow without running
merobox bootstrap validate workflow-examples/workflow-example.yml
```

### Binary Mode (No Docker)

```bash
# Requires merod binary installed
merobox run --no-docker --binary-path /path/to/merod --count 2
```

## Conventional Commits

Use conventional commit format for all commits:

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

- `fix`: Bug fix
- `feat`: New feature
- `docs`: Documentation only
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `test`: Adding or modifying tests
- `chore`: Maintenance tasks

### Examples

```bash
git commit -m "fix(auth): handle expired token refresh correctly"
git commit -m "feat(workflow): add step timeout configuration"
git commit -m "docs: update contributing guide for Cursor users"
git commit -m "refactor(manager): extract container config builder"
git commit -m "test(auth): add unit tests for credential resolution"
```

## Pull Request Guidelines

1. **Title**: Use conventional commit format
2. **Description**: Reference bounty if applicable
3. **Tests**: Include tests for bug fixes
4. **Docs**: Update relevant documentation
5. **Size**: Prefer small, focused PRs

### PR Checklist

- [ ] Code passes `black merobox/` formatting
- [ ] Code passes `ruff check merobox/` linting
- [ ] All tests pass: `pytest merobox/tests/ -v`
- [ ] New code has appropriate tests
- [ ] Commit messages follow conventional format
- [ ] PR description explains the change

## Environment Variables

Key environment variables for development:

| Variable | Purpose |
|----------|---------|
| `LOG_LEVEL` | Set logging verbosity (DEBUG, INFO, WARNING) |
| `MEROBOX_USERNAME` | Default username for auth |
| `MEROBOX_PASSWORD` | Default password for auth |
| `MEROBOX_API_KEY` | Default API key for auth |
| `CALIMERO_CONTRACTS_VERSION` | Override NEAR contracts version |

## Common Issues

### Docker Not Running

```
Failed to connect to Docker
```

Ensure Docker daemon is running: `docker ps`

### Port Conflicts

```
Port 2528 already in use
```

Stop existing nodes: `merobox stop --all` or use different ports.

### Python Version

```
Python 3.12+ is not supported
```

Use Python 3.9-3.11 due to ed25519 dependency constraints.

## Getting Help

- **Documentation**: Check `README.md` and `LLM.md`
- **Examples**: Browse `workflow-examples/` directory
- **Issues**: Open GitHub issue for questions
- **Code**: Use Cursor's AI to explain complex code sections

## Quick Reference

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Development cycle
black merobox/                    # Format
ruff check merobox/ --fix         # Lint
pytest merobox/tests/ -v          # Test

# Running merobox
merobox run --count 2             # Start nodes
merobox list                      # List nodes
merobox health                    # Check health
merobox bootstrap run file.yml    # Run workflow
merobox stop --all                # Stop nodes

# Git workflow
git checkout -b fix/issue-name
git add -A
git commit -m "fix(scope): description"
git push -u origin fix/issue-name
```
