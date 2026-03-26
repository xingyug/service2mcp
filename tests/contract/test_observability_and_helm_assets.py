"""Contract tests for Grafana dashboards and Helm chart assets."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
OBSERVABILITY_DIR = ROOT_DIR / "observability" / "grafana"
HELM_DIR = ROOT_DIR / "deploy" / "helm" / "tool-compiler"


def test_grafana_dashboards_reference_expected_metrics() -> None:
    compilation_dashboard = json.loads(
        (OBSERVABILITY_DIR / "compilation-dashboard.json").read_text(encoding="utf-8")
    )
    runtime_dashboard = json.loads(
        (OBSERVABILITY_DIR / "runtime-dashboard.json").read_text(encoding="utf-8")
    )

    compilation_queries = {
        target["expr"]
        for panel in compilation_dashboard["panels"]
        for target in panel["targets"]
    }
    runtime_queries = {
        target["expr"]
        for panel in runtime_dashboard["panels"]
        for target in panel["targets"]
    }

    assert any("compiler_workflow_jobs_total" in expr for expr in compilation_queries)
    assert any("compiler_workflow_stage_duration_seconds" in expr for expr in compilation_queries)
    assert any("compiler_extractor_runs_total" in expr for expr in compilation_queries)
    assert any("compiler_llm_tokens_total" in expr for expr in compilation_queries)
    assert any("mcp_runtime_tool_calls_total" in expr for expr in runtime_queries)
    assert any("mcp_runtime_tool_latency_seconds" in expr for expr in runtime_queries)
    assert any("mcp_runtime_upstream_errors_total" in expr for expr in runtime_queries)
    assert any("mcp_runtime_circuit_breaker_state" in expr for expr in runtime_queries)


def test_helm_chart_contains_core_metadata_and_hooked_migration_job() -> None:
    chart = yaml.safe_load((HELM_DIR / "Chart.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load((HELM_DIR / "values.yaml").read_text(encoding="utf-8"))
    migration_template = (HELM_DIR / "templates" / "migration-job.yaml").read_text(
        encoding="utf-8"
    )
    apps_template = (HELM_DIR / "templates" / "apps.yaml").read_text(encoding="utf-8")
    infra_template = (HELM_DIR / "templates" / "infra.yaml").read_text(encoding="utf-8")
    rbac_template = (HELM_DIR / "templates" / "rbac.yaml").read_text(encoding="utf-8")

    assert chart["name"] == "tool-compiler"
    assert chart["type"] == "application"
    assert values["images"]["compilerApi"]["repository"] == "tool-compiler/compiler-api"
    assert values["images"]["migrations"]["repository"] == ""
    assert values["compilerWorker"]["extraEnv"] == []
    assert values["compilerWorker"]["secretEnv"] == []
    assert values["gatewayAdminMock"]["enabled"] is False
    assert values["mcpRuntime"]["enabled"] is True
    assert '"helm.sh/hook": post-install,post-upgrade' in migration_template
    assert "alembic -c migrations/alembic.ini upgrade head" in migration_template
    assert "images.migrations.repository" in migration_template
    assert "images.migrations.tag" in migration_template
    assert '\\"tool-compiler.fullname\\"' not in migration_template
    assert '\\"tool-compiler.fullname\\"' not in apps_template
    assert 'include "tool-compiler.fullname" .' in migration_template
    assert 'include "tool-compiler.fullname" .' in apps_template
    assert "compiler-api" in apps_template
    assert "access-control" in apps_template
    assert "compiler-worker" in apps_template
    assert "mcp-runtime" in apps_template
    assert "gateway-admin-mock" in apps_template
    assert apps_template.count("startupProbe:") >= 4
    assert "failureThreshold: 30" in apps_template
    assert "postgres" in infra_template
    assert "redis" in infra_template
    assert "temporal" in infra_template
    assert infra_template.count("startupProbe:") >= 3
    assert "name: PGDATA" in infra_template
    assert "/var/lib/postgresql/data/pgdata" in infra_template
    assert 'name: WORKFLOW_ENGINE' in apps_template
    assert 'name: MCP_RUNTIME_IMAGE' in apps_template
    assert 'name: COMPILER_TARGET_NAMESPACE' in apps_template
    assert 'name: CELERY_WORKER_CONCURRENCY' in apps_template
    assert 'name: CELERY_WORKER_POOL' in apps_template
    assert 'name: ROUTE_PUBLISH_MODE' in apps_template
    assert 'name: ACCESS_CONTROL_URL' in apps_template
    assert ".Values.compilerWorker.extraEnv" in apps_template
    assert ".Values.compilerWorker.secretEnv" in apps_template
    assert 'name: GATEWAY_ADMIN_URL' in apps_template
    assert 'value: "1"' in apps_template
    assert "value: solo" in apps_template
    assert "value: access-control" in apps_template
    assert "secretKeyRef:" in apps_template
    assert "apps.compiler_worker.entrypoint" in apps_template
    assert "apps.gateway_admin_mock.main:app" in apps_template
    assert "serviceAccountName:" in apps_template
    assert "kind: ServiceAccount" in rbac_template
    assert "kind: Role" in rbac_template
    assert "kind: RoleBinding" in rbac_template
    assert "networkpolicies" in rbac_template
    assert "deployments" in rbac_template
