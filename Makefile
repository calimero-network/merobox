.PHONY: help build clean install format format-check lint test check publish test-publish release

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: clean ## Build the package
	python -m build

clean: ## Clean build artifacts
	rm -rf build/ dist/ *.egg-info/
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

install: ## Install package in development mode
	pip install -e .

format: ## Format code with Black
	black merobox/ workflow-examples/ --exclude=venv

format-check: ## Check if code is formatted correctly with Black
	black --check merobox/ workflow-examples/ --exclude=venv

lint: format-check ## Run all linting checks

test: ## Run tests (placeholder)
	@echo "No tests configured yet"

check: build ## Check the built package
	twine check dist/*

test-publish: check ## Test publish to TestPyPI
	@if [ "$$CI" = "true" ] || [ "$$GITHUB_ACTIONS" = "true" ]; then \
		echo "🧪 Publishing to TestPyPI (CI mode)"; \
		twine upload --repository testpypi dist/*; \
		echo "✅ Package published to TestPyPI!"; \
	else \
		echo "🧪 Publishing to TestPyPI"; \
		twine upload --repository testpypi dist/*; \
		echo "✅ Package published to TestPyPI!"; \
	fi

publish: check ## Publish to PyPI (requires confirmation)
	@if [ "$$CI" = "true" ] || [ "$$GITHUB_ACTIONS" = "true" ]; then \
		echo "🚀 Publishing to PyPI (CI mode)"; \
		twine upload dist/*; \
		echo "✅ Package published to PyPI!"; \
	else \
		echo "⚠️  Are you sure you want to publish to PyPI?"; \
		read -p "Type 'yes' to confirm: " confirm; \
		if [ "$$confirm" = "yes" ]; then \
			twine upload dist/*; \
			echo "✅ Package published to PyPI!"; \
		else \
			echo "❌ Publishing cancelled"; \
		fi; \
	fi

release: publish ## Full release process (build, check, publish)
