# GitHub Release Setup

This document explains how to set up automated releases for merobox.

## Prerequisites

### 1. PyPI API Token

You need to add your PyPI API token to GitHub Secrets:

1. Go to [PyPI Account Settings](https://pypi.org/manage/account/)
2. Scroll to "API tokens" and click "Add API token"
3. Give it a name like "merobox-github-actions"
4. Set scope to "Project: merobox"
5. Copy the token (starts with `pypi-`)
6. Go to your GitHub repository settings
7. Navigate to **Settings → Secrets and variables → Actions**
8. Click "New repository secret"
9. Name: `PYPI_API_TOKEN`
10. Value: Paste your PyPI token
11. Click "Add secret"

### 2. GitHub Token

The `GITHUB_TOKEN` is automatically provided by GitHub Actions, no setup needed.

## How to Create a Release

Once the setup is complete, creating a release is simple:

### 1. Ensure version is correct
Make sure the version in these files matches:
- `pyproject.toml`
- `merobox/__init__.py`
- `CHANGELOG.md` (add entry if needed)

### 2. Commit your changes
```bash
git add .
git commit -m "Prepare release v0.1.27"
```

### 3. Create and push the tag
```bash
git tag v0.1.27
git push origin master --tags
```

### 4. Watch the magic happen!
The GitHub Actions workflow will automatically:
- Build binaries for macOS (x64, arm64) and Linux (x64, arm64)
- Publish the package to PyPI
- Create a GitHub release with all binaries attached
- Include changelog notes in the release

## What the Workflow Does

The release workflow (`release.yml`) performs these steps:

1. **Build Binaries**: 
   - macOS x64 (Intel Macs)
   - macOS arm64 (Apple Silicon)
   - Linux x64
   - Linux arm64

2. **Publish to PyPI**:
   - Builds Python wheel and source distribution
   - Validates the package
   - Uploads to PyPI

3. **Create GitHub Release**:
   - Creates a release with the version tag
   - Attaches all binaries
   - Includes changelog excerpt
   - Provides installation instructions

## Troubleshooting

### PyPI Upload Fails
- Check that `PYPI_API_TOKEN` is set correctly in GitHub Secrets
- Verify the token has permission for the merobox project
- Ensure the version number hasn't been published before

### Binary Build Fails
- Check the build logs in GitHub Actions
- Ensure `requirements.txt` includes all dependencies
- Verify `build.py` script works locally

### ARM64 Linux Builds
Note: GitHub doesn't provide free ARM64 runners by default. The workflow includes `ubuntu-latest-arm64`, but you may need to:
- Use GitHub's paid ARM runners
- Or remove this build from the matrix
- Or use a third-party CI service like CircleCI

## Manual Release (Fallback)

If you need to create a release manually:

```bash
# Build package
python -m build

# Build binary
python build.py

# Upload to PyPI
twine upload dist/*

# Create GitHub release manually and upload binaries
```

## Next Release

For future releases:
1. Update version in all files
2. Update CHANGELOG.md
3. Commit changes
4. Tag and push: `git tag vX.Y.Z && git push origin master --tags`
5. Workflow handles the rest!

