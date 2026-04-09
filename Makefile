.PHONY: setup test contract-test test-integration test-cov lint typecheck format gitleaks dev-up dev-down dev-smoke gateway-smoke clean

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy

setup:
	./scripts/setup-dev.sh

test:
	$(PYTEST)

test-quick:
	$(PYTEST) -q --tb=short -x

contract-test:
	$(PYTEST) tests/contract

test-integration:
	$(PYTEST) tests/integration tests/e2e

test-cov:
	$(PYTEST) --cov=libs --cov=apps --cov-report=term-missing --cov-report=html --cov-report=xml:coverage.xml --cov-fail-under=55

lint:
	$(RUFF) check libs/ apps/ tests/
	$(RUFF) format --check libs/ apps/ tests/

typecheck:
	$(MYPY) libs/ apps/

# Required before git push: scan repo history and working tree for secrets (install: https://github.com/gitleaks/gitleaks)
gitleaks:
	@command -v gitleaks >/dev/null 2>&1 || { echo "gitleaks not found; install: https://github.com/gitleaks/gitleaks — or use a package manager (e.g. brew install gitleaks)"; exit 1; }
	gitleaks detect --source . --verbose

format:
	$(RUFF) format libs/ apps/ tests/
	$(RUFF) check --fix libs/ apps/ tests/

dev-up:
	docker compose -f deploy/docker-compose.yaml up -d

dev-down:
	docker compose -f deploy/docker-compose.yaml down

dev-smoke:
	./scripts/smoke-dev.sh

gateway-smoke:
	./scripts/smoke-gateway-routes.sh

clean:
	rm -rf $(VENV) .mypy_cache .pytest_cache .ruff_cache htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
