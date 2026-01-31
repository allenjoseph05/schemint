.PHONY: help install install-dev run test lint format typecheck check clean build

# Default target
help:
	@echo "Schemint - AI-powered database schema linter"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install      Install production dependencies"
	@echo "  install-dev  Install development dependencies"
	@echo "  run          Run the API server"
	@echo "  test         Run tests"
	@echo "  test-cov     Run tests with coverage"
	@echo "  lint         Run linter (ruff)"
	@echo "  format       Format code (ruff)"
	@echo "  typecheck    Run type checker (mypy)"
	@echo "  check        Run all checks (lint, typecheck, test)"
	@echo "  clean        Remove build artifacts"
	@echo "  build        Build package"

# Installation
install:
	pip install -e .

install-dev:
	pip install -e ".[all]"
	pre-commit install

# Run
run:
	uvicorn schemint.main:app --host 0.0.0.0 --port 8000 --reload

run-prod:
	uvicorn schemint.main:app --host 0.0.0.0 --port 8000 --workers 4

# Testing
test:
	pytest tests/ -v

test-unit:
	pytest tests/unit -v -m unit

test-integration:
	pytest tests/integration -v -m integration

test-cov:
	pytest tests/ --cov=src/schemint --cov-report=html --cov-report=term-missing

# Code Quality
lint:
	ruff check src/ tests/

lint-fix:
	ruff check src/ tests/ --fix

format:
	ruff format src/ tests/

format-check:
	ruff format src/ tests/ --check

typecheck:
	mypy src/schemint

# Run all checks
check: lint format-check typecheck test
	@echo "✅ All checks passed!"

# Cleanup
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Build
build: clean
	python -m build

# Docker
docker-build:
	docker build -t schemint:latest .

docker-run:
	docker run -p 8000:8000 schemint:latest
