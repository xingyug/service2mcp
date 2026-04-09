"""Contract tests for local development assets."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT_DIR / "deploy" / "docker-compose.yaml"
MAKEFILE_PATH = ROOT_DIR / "Makefile"
PYPROJECT_PATH = ROOT_DIR / "pyproject.toml"
MIGRATIONS_ENV_PATH = ROOT_DIR / "migrations" / "env.py"
DOCKERFILE_PATH = ROOT_DIR / "deploy" / "docker" / "Dockerfile.app"
SMOKE_DEV_PATH = ROOT_DIR / "scripts" / "smoke-dev.sh"
GATEWAY_SMOKE_PATH = ROOT_DIR / "scripts" / "smoke-gateway-routes.sh"
REAL_TARGET_SEED_PATH = ROOT_DIR / "deploy" / "k8s" / "real-targets" / "seed-all.sh"
QUICKSTART_PATH = ROOT_DIR / "docs" / "quickstart.md"


def test_docker_compose_defines_required_services_with_healthchecks() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]

    required_services = {
        "postgres",
        "redis",
        "temporal",
        "compiler-api",
        "access-control",
        "compiler-worker",
        "gateway-admin-mock",
        "mcp-runtime",
    }

    assert required_services.issubset(services)
    for service_name in required_services:
        assert "healthcheck" in services[service_name]

    compiler_api = services["compiler-api"]
    access_control = services["access-control"]
    compiler_worker = services["compiler-worker"]
    assert compiler_api["environment"]["WORKFLOW_ENGINE"] == "celery"
    assert compiler_api["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert access_control["environment"]["GATEWAY_ADMIN_URL"] == "http://gateway-admin-mock:8004"
    assert "apps.compiler_worker.entrypoint" in compiler_worker["command"][-1]
    assert compiler_worker["environment"]["CELERY_WORKER_CONCURRENCY"] == "1"
    assert compiler_worker["environment"]["CELERY_WORKER_POOL"] == "solo"
    assert compiler_worker["environment"]["MCP_RUNTIME_IMAGE"] == "tool-compiler/mcp-runtime:latest"
    assert compiler_worker["environment"]["ROUTE_PUBLISH_MODE"] == "access-control"
    assert compiler_worker["environment"]["ACCESS_CONTROL_URL"] == "http://access-control:8001"


def test_makefile_exposes_local_dev_and_integration_targets() -> None:
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")

    assert "setup:" in makefile
    assert "test:" in makefile
    assert "test-integration:" in makefile
    assert "dev-up:" in makefile
    assert "dev-down:" in makefile
    assert "dev-smoke:" in makefile
    assert "gateway-smoke:" in makefile


def test_smoke_scripts_and_quickstart_cover_gateway_route_smoke_flow() -> None:
    dev_smoke = SMOKE_DEV_PATH.read_text(encoding="utf-8")
    gateway_smoke = GATEWAY_SMOKE_PATH.read_text(encoding="utf-8")
    real_target_seed = REAL_TARGET_SEED_PATH.read_text(encoding="utf-8")
    quickstart = QUICKSTART_PATH.read_text(encoding="utf-8")

    assert "8004" in dev_smoke
    assert "http://127.0.0.1:8004/healthz" in dev_smoke
    assert "gateway-binding/reconcile" in gateway_smoke
    assert "/admin/routes" in gateway_smoke
    assert "COMPILER_API_URL" in gateway_smoke
    assert "ACCESS_CONTROL_URL" in gateway_smoke
    assert "GATEWAY_ADMIN_URL" in gateway_smoke
    assert (
        "Cluster-internal service DNS is not reachable from this shell; "
        "falling back to port-forward mode" in real_target_seed
    )
    assert "configure_base_urls" in real_target_seed
    assert "setup_port_forward_urls" in real_target_seed
    assert "make gateway-smoke" in quickstart or "gateway" in quickstart
    assert "Gateway Admin Mock" in quickstart
    assert "Helm" in quickstart or "helm" in quickstart


def test_migration_runtime_assets_include_sync_postgres_driver() -> None:
    pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")
    migrations_env = MIGRATIONS_ENV_PATH.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    migration_template = (
        ROOT_DIR / "deploy" / "helm" / "tool-compiler" / "templates" / "migration-job.yaml"
    ).read_text(encoding="utf-8")
    values = yaml.safe_load(
        (ROOT_DIR / "deploy" / "helm" / "tool-compiler" / "values.yaml").read_text(encoding="utf-8")
    )

    assert '"psycopg[binary]>=3.1,<4"' in pyproject
    assert 'replace("+asyncpg", "+psycopg")' in migrations_env
    assert "ARG INSTALL_EXTRAS=extractors,enhancer,observability" in dockerfile
    assert 'pip install ".[${INSTALL_EXTRAS}]"' in dockerfile
    assert "images.migrations.repository" in migration_template
    assert values["images"]["migrations"]["tag"] == ""
