.PHONY: setup test contract-test test-integration test-cov lint typecheck format dev-up dev-down dev-smoke gateway-smoke gke-gateway-smoke gke-grpc-stream-smoke gke-llm-e2e-smoke deepseek-validate e2e-real-deepseek-smoke clean

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

contract-test:
	$(PYTEST) tests/contract

test-integration:
	$(PYTEST) tests/integration tests/e2e

test-cov:
	$(PYTEST) --cov=libs --cov=apps --cov-report=term-missing --cov-report=html

lint:
	$(RUFF) check libs/ apps/ tests/
	$(RUFF) format --check libs/ apps/ tests/

typecheck:
	$(MYPY) libs/ apps/

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

gke-gateway-smoke:
	./scripts/smoke-gke-gateway-routes.sh

gke-grpc-stream-smoke:
	./scripts/smoke-gke-grpc-stream.sh

gke-llm-e2e-smoke:
	./scripts/smoke-gke-llm-e2e.sh

deepseek-validate:
	$(VENV)/bin/python ./scripts/validate_deepseek_enhancer.py

e2e-real-deepseek-smoke:
	bash ./scripts/e2e-real-deepseek-smoke.sh

clean:
	rm -rf $(VENV) .mypy_cache .pytest_cache .ruff_cache htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
