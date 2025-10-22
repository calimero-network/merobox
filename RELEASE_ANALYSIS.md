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

## How the Process Should Work

### Complete Release Flow:

```
1. Update version in:
   - pyproject.toml
   - merobox/__init__.py
   - setup.py (if exists)

2. Commit changes:
   git commit -m "chore: bump version to X.Y.Z"
   git push

3. Create and push tag:
   git tag vX.Y.Z
   git push origin vX.Y.Z

4. GitHub Actions automatically:
   a. Release workflow triggers
      - Builds binaries for all platforms
      - Creates GitHub release (as draft)
   
   b. Manually publish the release on GitHub
      - Go to Releases page
      - Edit the draft release
      - Click "Publish release"
   
   c. Publish workflow triggers
      - Builds Python package
      - Publishes to PyPI
      - Publishes to TestPyPI
```

---

## Current vs Expected Behavior

### Current Behavior:
1. ✅ Tag pushed → Release workflow runs → Binaries built
2. ✅ GitHub release created
3. 🔴 **Publish workflow fails** → `make publish` not found

### Expected Behavior:
1. ✅ Tag pushed → Release workflow runs → Binaries built
2. ✅ GitHub release created
3. ✅ Publish release → Publish workflow runs → Package on PyPI

---

## Immediate Action Required

1. **Add missing Makefile targets** (see Option 1 above)
2. **Ensure dependencies are installed** in workflow:
   ```bash
   pip install build twine
   ```
3. **Verify secrets are configured:**
   - `PYPI_API_TOKEN` - PyPI token
   - `TEST_PYPI_API_TOKEN` - TestPyPI token

4. **Test the flow:**
   ```bash
   # Locally test build
   make clean
   make build
   make check
   
   # Create test tag
   git tag v0.1.28-test
   git push origin v0.1.28-test
   
   # Watch GitHub Actions
   # If successful, publish the release manually
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

