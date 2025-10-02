# Building Merobox

This document describes how to build static executables for merobox.

## Prerequisites

- Python 3.9+
- PyInstaller

## Local Build

To build a static executable locally:

```bash
# Install dependencies
pip install -r requirements.txt
pip install pyinstaller

# Build executable
python build.py
```

The build script will:
1. Create a single-file executable using PyInstaller
2. Test the executable to ensure it works
3. Generate a SHA256 checksum file
4. Output files to the `dist/` directory

## Release Builds

Release builds are automatically triggered when pushing a git tag starting with `v` (e.g., `v1.0.0`).

The GitHub Actions workflow builds executables for:
- macOS (x64, arm64)
- Linux (x64, arm64)

Each build produces:
- `merobox-vX.Y.Z-{platform}-{arch}` - The executable
- `merobox-vX.Y.Z-{platform}-{arch}.sha256` - SHA256 checksum

## Manual Release

To create a release manually:

1. Update the version in `pyproject.toml` and `merobox/__init__.py`
2. Create and push a git tag:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
3. The GitHub Actions workflow will automatically build and create a release

## Testing

Test the built executable:

```bash
./dist/merobox --version
./dist/merobox --help
```

## Dependencies

The project uses `requirements.txt` with pinned versions for reproducible builds. All dependencies are locked to specific versions to ensure consistent builds across environments.
