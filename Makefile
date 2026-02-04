.PHONY: help install test test-unit test-integration lint format check pre-commit clean

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	pip install -r requirements.txt

install-dev: ## Install development dependencies
	pip install -r requirements.txt
	pre-commit install

test: ## Run all tests
	pytest

test-unit: ## Run unit tests only
	pytest merobox/tests/unit -v

test-integration: ## Run integration tests only
	pytest example-project/tests -v

test-coverage: ## Run tests with coverage
	pytest --cov=merobox --cov-report=html --cov-report=term

lint: ## Run linting checks
	ruff check merobox/
	black --check merobox/

format: ## Format code with black and ruff
	black merobox/
	ruff check --fix merobox/

format-check: ## Check code formatting without making changes
	black --check merobox/
	ruff check merobox/

check: lint test ## Run all checks (lint + test)

pre-commit: ## Run pre-commit on all files
	pre-commit run --all-files

clean: ## Clean up generated files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build/
	rm -rf dist/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .pytest_cache/
	rm -rf .ruff_cache/

ci: ## Run CI checks (format, lint, test)
	$(MAKE) format
	$(MAKE) lint
	$(MAKE) test

build: clean ## Build the package
	python -m build

check-build: build ## Build and validate package
	twine check dist/*

test-publish: check-build ## Publish to TestPyPI
	twine upload --repository testpypi dist/*

publish: check-build ## Publish to PyPI
	twine upload dist/*