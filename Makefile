.PHONY: help clean build check test-publish publish install-dev

help: ## Show this help message
	@echo "Merobox Package Management"
	@echo "=========================="
	@echo ""
	@echo "Available commands:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

clean: ## Clean build artifacts
	rm -rf build/ dist/ *.egg-info/
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

build: clean ## Build the package
	python -m build

check: build ## Check the built package
	twine check dist/*

test-publish: build ## Publish to TestPyPI
	twine upload --repository testpypi dist/*

publish: build ## Publish to PyPI (requires confirmation)
	@echo "⚠️  Are you sure you want to publish to PyPI?"
	@read -p "Type 'yes' to confirm: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		twine upload dist/*; \
		echo "✅ Package published to PyPI!"; \
	else \
		echo "❌ Publishing cancelled"; \
	fi

install-dev: ## Install development dependencies
	pip install -e ".[dev]"

install: ## Install the package in development mode
	pip install -e .

uninstall: ## Uninstall the package
	pip uninstall merobox -y

test: ## Run tests (if available)
	python -m pytest tests/ -v

lint: ## Run linting checks
	black --check .
	flake8 .
	mypy .

format: ## Format code with black
	black .

docs: ## Build documentation (if available)
	@echo "Documentation building not yet implemented"

release: clean build check publish ## Full release process

test-release: clean build check test-publish ## Test release process
