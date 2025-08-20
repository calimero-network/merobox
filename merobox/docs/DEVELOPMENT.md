# Merobox Development Guide

This guide covers how to set up a development environment, build the project, and contribute to Merobox.

## Table of Contents

- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Building the Project](#building-the-project)
- [Testing](#testing)
- [Code Quality](#code-quality)
- [Contributing](#contributing)
- [Release Process](#release-process)

## Development Setup

### Prerequisites

- **Python**: 3.8 or higher (3.11+ recommended)
- **Docker**: 20.10+ for running Calimero nodes
- **Git**: For version control
- **Make**: For build automation (optional but recommended)

### Environment Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/calimero-network/merobox.git
   cd merobox
   ```

2. **Create virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install development dependencies:**
   ```bash
   pip install -e .
   ```

### Docker Setup

Ensure Docker is running and accessible:

```bash
# Test Docker access
docker --version
docker ps

# If you get permission errors on Linux:
sudo usermod -aG docker $USER
# Log out and back in
```

## Project Structure

```
merobox/
├── merobox/                    # Main package
│   ├── __init__.py            # Package initialization
│   ├── cli.py                 # CLI entry point
│   └── commands/              # Command implementations
│       ├── __init__.py        # Commands package
│       ├── bootstrap/         # Workflow orchestration
│       │   ├── __init__.py
│       │   ├── bootstrap.py   # Main bootstrap command
│       │   ├── config.py      # Configuration loading
│       │   ├── run/           # Execution logic
│       │   │   ├── __init__.py
│       │   │   ├── executor.py # Workflow executor
│       │   │   └── run.py     # Run functions
│       │   ├── steps/         # Step implementations
│       │   │   ├── __init__.py
│       │   │   ├── base.py    # Base step class
│       │   │   ├── context.py # Context step
│       │   │   ├── execute.py # Execute step
│       │   │   ├── identity.py # Identity steps
│       │   │   ├── install.py # Install step
│       │   │   ├── join.py    # Join step
│       │   │   ├── repeat.py  # Repeat step
│       │   │   ├── script.py  # Script step
│       │   │   └── wait.py    # Wait step
│       │   └── validate/      # Validation logic
│       │       ├── __init__.py
│       │       └── validator.py
│       ├── call.py            # Function execution
│       ├── context.py         # Context management
│       ├── health.py          # Health checks
│       ├── identity.py        # Identity management
│       ├── install.py         # Application installation
│       ├── join.py            # Context joining
│       ├── list.py            # Node listing
│       ├── logs.py            # Log viewing
│       ├── manager.py         # Docker node management
│       ├── nuke.py            # Node cleanup
│       ├── run.py             # Node management
│       ├── stop.py            # Node stopping
│       └── utils.py           # Utility functions
├── workflow-examples/          # Example workflows
├── docs/                       # Documentation
├── requirements.txt            # Python dependencies
├── setup.py                   # Package configuration
├── Makefile                   # Build automation
└── README.md                  # Project overview
```

## Building the Project

### Using Makefile (Recommended)

The project includes a comprehensive Makefile for all build tasks:

```bash
# Show all available commands
make help

# Build the package
make build

# Check package with twine
make check

# Install in development mode
make install

# Clean build artifacts
make clean
```

### Manual Build

If you prefer to build manually:

```bash
# Clean previous builds
rm -rf build/ dist/ *.egg-info/

# Build source distribution and wheel
python setup.py sdist bdist_wheel

# Check the built package
twine check dist/*
```

### Development Installation

For development, install the package in editable mode:

```bash
pip install -e .
```

This allows you to modify the code and see changes immediately without reinstalling.

## Testing

### Running Tests

Currently, the project has a placeholder test setup:

```bash
# Run tests (placeholder)
make test
```

### Manual Testing

Test the CLI commands manually:

```bash
# Test basic commands
merobox --help
merobox --version

# Test workflow commands
merobox bootstrap --help
merobox bootstrap create-sample

# Test node management (requires Docker)
merobox run --count 1
merobox list
merobox health
merobox stop --all
```

### Workflow Testing

Test workflow execution with example files:

```bash
# Create sample workflow
merobox bootstrap create-sample

# Validate workflow
merobox bootstrap validate workflow-sample.yml

# Execute workflow (if nodes are running)
merobox bootstrap run workflow-sample.yml
```

## Code Quality

### Code Formatting

The project uses Black for code formatting:

```bash
# Format code
make format

# Check formatting
make format-check

# Run all linting checks
make lint
```

### Code Style

Follow these guidelines:

- **Python**: Follow PEP 8 with Black formatting
- **Imports**: Use absolute imports throughout the codebase
- **Docstrings**: Use Google-style docstrings
- **Type Hints**: Include type hints for function parameters and returns

### Pre-commit Hooks

Consider setting up pre-commit hooks:

```bash
# Install pre-commit
pip install pre-commit

# Install hooks
pre-commit install

# Run hooks manually
pre-commit run --all-files
```

## Contributing

### Development Workflow

1. **Fork the repository** on GitHub
2. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** following the code style guidelines
4. **Test your changes** thoroughly
5. **Commit your changes:**
   ```bash
   git add .
   git commit -m "feat: add your feature description"
   ```
6. **Push to your fork:**
   ```bash
   git push origin feature/your-feature-name
   ```
7. **Create a pull request** on GitHub

### Commit Message Format

Use conventional commit format:

- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation changes
- `style:` Code style changes
- `refactor:` Code refactoring
- `test:` Test additions or changes
- `chore:` Maintenance tasks

### Adding New Commands

To add a new command:

1. **Create the command file** in `merobox/commands/`
2. **Implement the command** using Click decorators
3. **Add to `merobox/commands/__init__.py`**
4. **Update documentation**

Example command structure:

```python
import click
from merobox.commands.utils import console

@click.command()
@click.option('--option', help='Option description')
def my_command(option):
    """Command description."""
    try:
        # Command implementation
        console.print("Command executed successfully!")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()
```

### Adding New Workflow Steps

To add a new workflow step:

1. **Create the step file** in `merobox/commands/bootstrap/steps/`
2. **Inherit from `BaseStep`**
3. **Implement required methods:**
   - `_get_required_fields()`
   - `_validate_field_types()`
   - `execute()`
4. **Add to the step registry** in `_create_step_executor()`
5. **Update validation** in `validator.py`
6. **Add documentation**

Example step structure:

```python
from merobox.commands.bootstrap.steps.base import BaseStep

class MyStep(BaseStep):
    def _get_required_fields(self):
        return ['required_field']
    
    def _validate_field_types(self):
        # Validate field types
        pass
    
    def execute(self):
        # Step execution logic
        pass
```

## Release Process

### Version Management

1. **Update version** in `merobox/__init__.py` and `setup.py`
2. **Update CHANGELOG.md** with new changes
3. **Commit version bump:**
   ```bash
   git add .
   git commit -m "chore: bump version to X.Y.Z"
   git tag vX.Y.Z
   ```

### Building for Release

```bash
# Build and check package
make check

# Test publish to TestPyPI
make test-publish

# Publish to PyPI (requires confirmation)
make publish
```

### GitHub Release

1. **Push tags:**
   ```bash
   git push origin --tags
   ```
2. **Create GitHub release** with release notes
3. **GitHub Actions** will automatically publish to PyPI

### Release Checklist

- [ ] All tests pass
- [ ] Code is formatted with Black
- [ ] Documentation is updated
- [ ] Version is bumped
- [ ] CHANGELOG is updated
- [ ] Package builds successfully
- [ ] Package passes twine check
- [ ] GitHub release is created

## Troubleshooting

### Common Issues

#### Build Failures

```bash
# Clean and rebuild
make clean
make build

# Check dependencies
pip install -r requirements.txt
```

#### Import Errors

```bash
# Reinstall in development mode
pip uninstall merobox
pip install -e .

# Check import paths
python -c "import merobox; print(merobox.__file__)"
```

#### Docker Issues

```bash
# Check Docker status
docker info

# Restart Docker service
sudo systemctl restart docker  # Linux
# Or restart Docker Desktop on macOS/Windows
```

#### Permission Issues

```bash
# Fix Docker permissions (Linux)
sudo usermod -aG docker $USER
# Log out and back in
```

### Getting Help

- **GitHub Issues**: Report bugs and request features
- **Documentation**: Check this guide and other docs
- **Examples**: See `workflow-examples/` directory
- **Code**: Review source code for implementation details

## Next Steps

- **Explore the codebase** to understand the architecture
- **Run examples** to see how everything works together
- **Contribute** by fixing bugs or adding features
- **Improve documentation** based on your experience

For more information, see the [API Reference](API_REFERENCE.md) and [Workflow Guide](WORKFLOW_GUIDE.md).
