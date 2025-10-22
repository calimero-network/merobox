# Release & Publish Automation Analysis

## Current State

The merobox repository has automated release and publish workflows, but they're currently **NOT triggering** due to missing Makefile targets.

## Workflow Triggers

### 1. Release Workflow (`.github/workflows/release.yml`)

**Triggers:**
- ✅ Push tags matching `v*` pattern (e.g., `v0.1.27`)
- ✅ Manual workflow dispatch with version input

**What it does:**
- Builds binaries for multiple platforms (macOS x64/arm64, Linux x64/arm64)
- Creates GitHub release with binaries
- Generates release notes automatically

**Status:** ✅ **WORKING** - Creates GitHub releases when tags are pushed

**Last successful release:** `v0.1.24` (based on existing tags)

---

### 2. Publish Workflow (`.github/workflows/publish.yml`)

**Triggers:**
- 🔴 When a GitHub release is **published** (not just created)
- Depends on the release workflow completing

**What it does:**
- Runs `make check` (lint + test)
- Runs `make publish` to publish to PyPI
- Runs `make test-publish` to publish to TestPyPI

**Status:** 🔴 **FAILING** - Missing Makefile targets

**Problem:**
```bash
# These targets are missing from Makefile:
make build         # Build Python package
make check         # Exists but calls 'make test' which may not exist
make publish       # Publish to PyPI
make test-publish  # Publish to TestPyPI
```

---

### 3. CI Workflow (`.github/workflows/ci.yml`)

**Triggers:**
- Push to `master` branch
- Pull requests to `master` branch

**What it does:**
- Format checking with black
- Integration tests
- Merobox CLI testing

**Status:** ✅ **WORKING**

---

## Root Cause

The Makefile **used to have** publish targets (commit `8d2321a`), but they were **removed** in a later commit. The workflow still references these targets.

### Missing Makefile Targets:

```makefile
build: clean ## Build the package
	python setup.py sdist bdist_wheel

test-publish: check ## Test publish to TestPyPI
	twine upload --repository testpypi dist/*

publish: check ## Publish to PyPI (requires confirmation)
	@echo "⚠️  Are you sure you want to publish to PyPI?"
	@read -p "Type 'yes' to confirm: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		twine upload dist/*; \
		echo "✅ Package published to PyPI!"; \
	else \
		echo "❌ Publish cancelled."; \
		exit 1; \
	fi
```

---

## How to Fix

### Option 1: Restore Makefile Targets (Recommended)

Add the missing targets back to `Makefile`:

```makefile
build: clean ## Build the package
	python -m build

check: ## Build and validate package
	python -m build
	twine check dist/*

test-publish: ## Publish to TestPyPI
	twine upload --repository testpypi dist/*

publish: ## Publish to PyPI
	twine upload dist/*
```

**Note:** Updated to use `python -m build` (modern standard) instead of `setup.py` (deprecated)

### Option 2: Update Workflow to Not Use Make

Update `.github/workflows/publish.yml` to run commands directly:

```yaml
- name: Build package
  run: python -m build

- name: Check package
  run: twine check dist/*

- name: Publish to PyPI
  run: twine upload dist/*
```

---

## How the Process Works Now

### Complete Release Flow (100% AUTOMATED):

```
1. Update version in:
   - pyproject.toml
   - merobox/__init__.py
   
   Example:
   version = "0.1.28"  (in pyproject.toml)
   __version__ = "0.1.28"  (in merobox/__init__.py)

2. Commit and push to master:
   git add pyproject.toml merobox/__init__.py
   git commit -m "chore: bump version to 0.1.28"
   git push origin master

3. 🎉 GitHub Actions automatically does EVERYTHING:
   
   a. Auto-tag workflow triggers (on push to master)
      ✓ Detects version bump in pyproject.toml
      ✓ Verifies versions match in __init__.py
      ✓ Creates tag vX.Y.Z automatically
      ✓ Pushes tag to GitHub
      ✓ Comments on commit with status
   
   b. Release workflow triggers (on tag creation)
      ✓ Builds binaries for all platforms (macOS x64/arm64, Linux x64/arm64)
      ✓ Generates checksums (SHA256)
      ✓ Creates GitHub release with binaries
      ✓ Generates release notes automatically
      ✓ PUBLISHES the release immediately (not draft)
   
   c. Publish workflow triggers (on release published)
      ✓ Builds Python package (sdist + wheel)
      ✓ Validates package with twine
      ✓ Publishes to PyPI automatically
      ✓ Publishes to TestPyPI (if token exists)
   
   d. Done! 🎊
      ✓ GitHub release is live with binaries
      ✓ Package is on PyPI
      ✓ Users can install via: pip install merobox
      ✓ Users can download binaries from GitHub releases
```

### Zero Manual Steps Required!

Just bump the version and push to master. Everything else is automatic. You can monitor progress in the Actions tab.

### Workflow Chain:

```
Version Bump Commit → Auto-Tag → Release Build → Publish to PyPI
       (you)         (automated)   (automated)      (automated)
```

---

## Current vs Expected Behavior

### Before This Fix:
1. ✅ Tag pushed → Release workflow runs → Binaries built
2. ✅ GitHub release created as **DRAFT**
3. ❌ Manual step: Publish the draft release
4. 🔴 **Publish workflow fails** → `make publish` not found
5. ❌ Manual step: Build and publish to PyPI locally

### After This Fix (FULLY AUTOMATED):
1. ✅ Tag pushed → Release workflow runs → Binaries built
2. ✅ GitHub release **PUBLISHED** automatically
3. ✅ Publish workflow triggers → Package published to PyPI
4. 🎉 **Done!** No manual steps required!

---

## Unified Workflow (Recommended)

We've created a **single unified workflow** (`release-unified.yml`) that handles everything:

### Benefits:
- ✅ Single workflow to maintain instead of 3 separate ones
- ✅ Better job dependencies and error handling
- ✅ Clearer execution flow
- ✅ Automatic retry and rollback capabilities
- ✅ Single source of truth for the release process

### Workflow Jobs:

1. **auto-tag** - Detects version bumps and creates tags
2. **build-binaries** - Builds executables for all platforms
3. **create-release** - Creates and publishes GitHub release
4. **publish-pypi** - Publishes package to PyPI
5. **notify-completion** - Reports final status

### Migration Plan:

**Option A: Use Unified Workflow (Recommended)**
1. Enable `release-unified.yml`
2. Disable old workflows:
   - Rename `.github/workflows/release.yml` to `release.yml.disabled`
   - Rename `.github/workflows/publish.yml` to `publish.yml.disabled`
   - Rename `.github/workflows/auto-tag.yml` to `auto-tag.yml.disabled`

**Option B: Keep Separate Workflows**
1. Use the fixes in `release.yml`, `publish.yml`, and `auto-tag.yml`
2. Keep workflows separate for granular control

### Required Secrets:

Ensure these are configured in GitHub repository settings:
- `PYPI_API_TOKEN` - PyPI token (required)
- `TEST_PYPI_API_TOKEN` - TestPyPI token (optional)

### Test the Flow:

```bash
# 1. Update version
# Edit pyproject.toml and merobox/__init__.py

# 2. Commit and push
git add pyproject.toml merobox/__init__.py
git commit -m "chore: bump version to 0.1.28"
git push origin master

# 3. Watch the magic happen
# Monitor: https://github.com/calimero-network/merobox/actions
```

---

## Why Manual Releases Are Currently Required

You've been manually publishing to PyPI because:
1. The automated publish workflow is **broken** (missing Makefile targets)
2. You have to run the build/publish steps locally instead
3. This is why you had to:
   - `mv build.py build.py.bak`
   - `python -m build`
   - `python -m twine upload dist/*`

---

## Recommendations

1. **Fix the Makefile** - Add back the missing targets
2. **Update workflows** to use modern `python -m build` instead of `setup.py`
3. **Document the process** in CONTRIBUTING.md
4. **Automate version bumping** - Consider using tools like `bump2version`
5. **Add pre-publish checks** - Version validation, changelog updates, etc.

