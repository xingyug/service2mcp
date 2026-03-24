.PHONY: setup test test-cov lint typecheck dev-up dev-down clean

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy

setup: $(VENV)/bin/activate

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[all]"
	@echo "✓ Setup complete. Activate with: source $(VENV)/bin/activate"

test:
	$(PYTEST)

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

clean:
	rm -rf $(VENV) .mypy_cache .pytest_cache .ruff_cache htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
