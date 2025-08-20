.PHONY: help build clean install format format-check lint test release

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: clean ## Build the package
	python setup.py sdist bdist_wheel

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

release: build ## Build and release to PyPI
	twine check dist/*
	twine upload dist/*
