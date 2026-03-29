"""Contract tests for local development assets."""

from __future__ import annotations

import re
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
GKE_GATEWAY_SMOKE_PATH = ROOT_DIR / "scripts" / "smoke-gke-gateway-routes.sh"
GKE_GRPC_STREAM_SMOKE_PATH = ROOT_DIR / "scripts" / "smoke-gke-grpc-stream.sh"
GKE_LLM_E2E_SMOKE_PATH = ROOT_DIR / "scripts" / "smoke-gke-llm-e2e.sh"
LOCAL_REAL_DEEPSEEK_SMOKE_PATH = ROOT_DIR / "scripts" / "e2e-real-deepseek-smoke.sh"
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
    assert "gke-gateway-smoke:" in makefile
    assert "gke-grpc-stream-smoke:" in makefile
    assert "gke-llm-e2e-smoke:" in makefile
    assert "e2e-real-deepseek-smoke:" in makefile


def test_smoke_scripts_and_quickstart_cover_gateway_route_smoke_flow() -> None:
    dev_smoke = SMOKE_DEV_PATH.read_text(encoding="utf-8")
    gateway_smoke = GATEWAY_SMOKE_PATH.read_text(encoding="utf-8")
    gke_gateway_smoke = GKE_GATEWAY_SMOKE_PATH.read_text(encoding="utf-8")
    gke_grpc_stream_smoke = GKE_GRPC_STREAM_SMOKE_PATH.read_text(encoding="utf-8")
    gke_llm_e2e_smoke = GKE_LLM_E2E_SMOKE_PATH.read_text(encoding="utf-8")
    local_real_deepseek_smoke = LOCAL_REAL_DEEPSEEK_SMOKE_PATH.read_text(encoding="utf-8")
    quickstart = QUICKSTART_PATH.read_text(encoding="utf-8")

    assert "8004" in dev_smoke
    assert "http://127.0.0.1:8004/healthz" in dev_smoke
    assert "gateway-binding/reconcile" in gateway_smoke
    assert "/admin/routes" in gateway_smoke
    assert "COMPILER_API_URL" in gateway_smoke
    assert "ACCESS_CONTROL_URL" in gateway_smoke
    assert "GATEWAY_ADMIN_URL" in gateway_smoke
    assert "ACCESS_CONTROL_IMAGE" in gke_gateway_smoke
    assert "COMPILER_API_IMAGE" in gke_gateway_smoke
    assert "SMOKE_MODE" in gke_gateway_smoke
    assert (
        'wait -n "${NAMESPACE}" --for=condition=complete job/gateway-smoke-migrate'
        in gke_gateway_smoke
    )
    assert "gateway-binding/service-routes/sync" in gke_gateway_smoke
    assert "gateway-binding/reconcile" in gke_gateway_smoke
    assert "/gateway/" in gke_gateway_smoke
    assert "registry.service_versions" in gke_gateway_smoke
    assert "gateway-smoke-runtime-v1" in gke_gateway_smoke
    assert "gateway-smoke-runtime-v2" in gke_gateway_smoke
    assert "generate_generic_manifests" in gke_grpc_stream_smoke
    assert "ENABLE_NATIVE_GRPC_STREAM" in gke_grpc_stream_smoke
    assert "grpc_stream" in gke_grpc_stream_smoke
    assert "grpc-stream-upstream" in gke_grpc_stream_smoke
    assert "PostDeployValidator" in gke_grpc_stream_smoke
    assert "build_streamable_http_tool_invoker" in gke_grpc_stream_smoke
    assert "LLM_SKIP_IF_DESCRIPTION_EXISTS" in gke_llm_e2e_smoke
    assert "apps.proof_runner.http_mock:app" in gke_llm_e2e_smoke
    assert "apps.proof_runner.grpc_mock" in gke_llm_e2e_smoke
    assert "apps.proof_runner.live_llm_e2e" in gke_llm_e2e_smoke
    assert 'PROTOCOL="${PROTOCOL:-all}"' in gke_llm_e2e_smoke
    protocol_usage = re.search(r"all\|[A-Za-z0-9_|-]+", gke_llm_e2e_smoke)
    assert protocol_usage is not None
    assert protocol_usage.group(0) == (
        "all|graphql|rest|openapi|grpc|jsonrpc|odata|scim|soap|sql"
    )
    assert '--protocol"' in gke_llm_e2e_smoke
    assert "llm-proof-sql" in gke_llm_e2e_smoke
    assert "startupProbe:" in gke_llm_e2e_smoke
    assert "failureThreshold: 60" in gke_llm_e2e_smoke
    assert "llm-e2e-secrets" in gke_llm_e2e_smoke
    assert "ENABLE_REAL_DEEPSEEK_E2E=1" in local_real_deepseek_smoke
    assert "LLM_API_KEY_FILE" in local_real_deepseek_smoke
    assert "graphql_introspection_compiles_to_running_runtime_and_tool_invocation" in (
        local_real_deepseek_smoke
    )
    assert "sql_schema_compiles_to_running_runtime_and_tool_invocation" in (
        local_real_deepseek_smoke
    )
    assert "make gke-grpc-stream-smoke" in quickstart
    assert "RUNTIME_IMAGE=" in quickstart
    assert "grpc-stream" in quickstart
    assert "make gke-llm-e2e-smoke" in quickstart
    assert "PROTOCOL=graphql" in quickstart
    assert "make e2e-real-deepseek-smoke" in quickstart
    assert "DeepSeek" in quickstart
    assert "GraphQL, REST, gRPC, SOAP/WSDL, and SQL" in quickstart
    assert "GraphQL + SQL" in quickstart
    assert "make gke-gateway-smoke" in quickstart
    assert "SMOKE_MODE=rollout" in quickstart
    assert "make gateway-smoke" in quickstart
    assert "Gateway Admin Mock" in quickstart
    assert "data-plane request" in quickstart


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
