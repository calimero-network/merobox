# Publishing Merobox to PyPI

This guide explains how to publish the Merobox package to PyPI (Python Package Index).

## Prerequisites

1. **PyPI Account**: Create an account at [pypi.org](https://pypi.org)
2. **API Token**: Generate an API token from your PyPI account settings
3. **TestPyPI Account**: Create an account at [test.pypi.org](https://test.pypi.org) for testing
4. **TestPyPI API Token**: Generate an API token from TestPyPI

## Setup

### 1. Install Development Dependencies

```bash
# Install the package in development mode with dev dependencies
pip install -e ".[dev]"

# Or install manually
pip install build twine
```

### 2. Configure PyPI Credentials

Create a `.pypirc` file in your home directory:

```bash
# Copy the template
cp .pypirc.template ~/.pypirc

# Edit with your actual credentials
nano ~/.pypirc
```

Fill in your actual API tokens:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-your_actual_token_here

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-your_testpypi_token_here
```

## Publishing Methods

### Method 1: Using Makefile (Recommended)

```bash
# Build and check the package
make check

# Test publish to TestPyPI
make test-publish

# Publish to PyPI (requires confirmation)
make publish

# Full release process
make release
```

### Method 2: Using Python Script

```bash
# Check package only
python scripts/publish.py --check-only

# Test publish to TestPyPI
python scripts/publish.py --test

# Publish to PyPI
python scripts/publish.py
```

### Method 3: Manual Commands

```bash
# Clean previous builds
make clean

# Build the package
python -m build

# Check the package
twine check dist/*

# Upload to TestPyPI
twine upload --repository testpypi dist/*

# Upload to PyPI
twine upload dist/*
```

## Package Structure

The package includes:

- **Core CLI**: `merobox_cli.py` with Click-based interface
- **Commands**: Modular command implementations in `commands/` directory
- **Workflows**: YAML-based workflow configuration support
- **Documentation**: README, bootstrap guide, and examples
- **Assets**: WASM files and workflow examples

## Version Management

### Updating Version

1. **Update version in multiple files**:
   - `pyproject.toml`
   - `setup.py`
   - `merobox_cli.py`
   - `CHANGELOG.md`

2. **Commit version changes**:
   ```bash
   git add .
   git commit -m "Bump version to X.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

### Release Process

1. **Create a GitHub release** with the new version tag
2. **GitHub Actions** will automatically:
   - Build the package
   - Run checks
   - Publish to both PyPI and TestPyPI

## Testing Before Publishing

### 1. Test Package Installation

```bash
# Build the package
make build

# Install from local build
pip install dist/merobox-*.whl

# Test the CLI
merobox --help
```

### 2. Test on TestPyPI

```bash
# Publish to TestPyPI
make test-publish

# Install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ merobox
```

### 3. Verify Package Contents

```bash
# Check what's included
tar -tzf dist/merobox-*.tar.gz
unzip -l dist/merobox-*.whl
```

## Troubleshooting

### Common Issues

1. **Authentication Errors**:
   - Verify your `.pypirc` file
   - Check API token permissions
   - Ensure tokens are valid and not expired

2. **Build Errors**:
   - Clean previous builds: `make clean`
   - Check Python version compatibility
   - Verify all dependencies are available

3. **Upload Errors**:
   - Check package size limits
   - Verify package name availability
   - Check for duplicate version numbers

### Debug Commands

```bash
# Check package metadata
python -c "import setuptools; print(setuptools.find_packages())"

# Validate package structure
python -m build --sdist
python -m build --wheel

# Check package contents
twine check dist/*
```

## Security Considerations

1. **Never commit API tokens** to version control
2. **Use environment variables** in CI/CD pipelines
3. **Rotate tokens regularly** for security
4. **Limit token permissions** to minimum required

## CI/CD Integration

The package includes GitHub Actions workflows for:

- **Automated testing** on pull requests
- **Automated publishing** on releases
- **Package validation** before publishing

### GitHub Secrets Required

- `PYPI_API_TOKEN`: Your PyPI API token
- `TEST_PYPI_API_TOKEN`: Your TestPyPI API token

## Support

For issues with publishing:

1. Check the [PyPI documentation](https://packaging.python.org/)
2. Review [TestPyPI documentation](https://test.pypi.org/help/)
3. Check package build logs and error messages
4. Verify package metadata and structure

## Package Information

- **Name**: merobox
- **Description**: A Python CLI tool for managing Calimero nodes in Docker containers
- **License**: MIT
- **Python Version**: >=3.8
- **Homepage**: https://github.com/merobox/merobox
