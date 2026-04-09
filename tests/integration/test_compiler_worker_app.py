"""Integration tests for the compiler worker app shell."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from apps.compiler_worker.celery_app import COMPILATION_TASK_NAME, DEFAULT_COMPILATION_QUEUE
from apps.compiler_worker.main import create_app
from apps.compiler_worker.models import CompilationStage, CompilationStatus
from apps.compiler_worker.observability import CompilationObservability


@pytest.fixture
def observability() -> CompilationObservability:
    metrics = CompilationObservability()
    metrics.record_job(CompilationStatus.SUCCEEDED)
    metrics.record_stage(
        CompilationStage.EXTRACT,
        outcome="success",
        duration_seconds=0.42,
    )
    metrics.record_extractor_run(protocol="openapi", outcome="success")
    metrics.record_llm_token_usage(model="stub-enhancer", input_tokens=12, output_tokens=8)
    return metrics


@pytest.fixture
def app(
    observability: CompilationObservability,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    monkeypatch.setenv("MCP_RUNTIME_IMAGE", "tool-compiler/mcp-runtime:test")
    monkeypatch.setenv("COMPILER_TARGET_NAMESPACE", "tool-compiler-test")
    monkeypatch.setenv("ROUTE_PUBLISH_MODE", "access-control")
    monkeypatch.setenv("ACCESS_CONTROL_URL", "http://access-control.test:8001")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    return create_app(observability=observability)


@pytest.fixture
async def http_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_compiler_worker_health_ready_and_metrics(http_client: httpx.AsyncClient) -> None:
    health = await http_client.get("/healthz")
    ready = await http_client.get("/readyz")
    metrics = await http_client.get("/metrics")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json()["workflow_engine"] == "celery"
    assert ready.json()["compilation_queue"] == DEFAULT_COMPILATION_QUEUE
    assert ready.json()["task_name"] == COMPILATION_TASK_NAME
    assert ready.json()["runtime_image"] == "tool-compiler/mcp-runtime:test"
    assert ready.json()["target_namespace"] == "tool-compiler-test"
    assert ready.json()["route_publish_mode"] == "access-control"
    assert ready.json()["access_control_url"] == "http://access-control.test:8001"
    assert metrics.status_code == 200
    assert "compiler_workflow_jobs_total" in metrics.text
    assert "compiler_workflow_stage_duration_seconds" in metrics.text
    assert "compiler_extractor_runs_total" in metrics.text
    assert "compiler_llm_tokens_total" in metrics.text


@pytest.mark.asyncio
async def test_readyz_rejects_unsupported_route_publish_mode(
    observability: CompilationObservability,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_RUNTIME_IMAGE", "tool-compiler/mcp-runtime:test")
    monkeypatch.setenv("COMPILER_TARGET_NAMESPACE", "tool-compiler-test")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("ROUTE_PUBLISH_MODE", "unknown-mode")
    monkeypatch.delenv("ACCESS_CONTROL_URL", raising=False)
    app = create_app(observability=observability)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        ready = await client.get("/readyz")

    assert ready.status_code == 503
    payload = ready.json()
    assert payload["status"] == "not_ready"
    assert any("route_publish_mode" in p for p in payload["problems"])


@pytest.mark.asyncio
async def test_readyz_allows_explicit_deferred_without_access_control_url(
    observability: CompilationObservability,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_RUNTIME_IMAGE", "tool-compiler/mcp-runtime:test")
    monkeypatch.setenv("COMPILER_TARGET_NAMESPACE", "tool-compiler-test")
    monkeypatch.setenv("ROUTE_PUBLISH_MODE", "deferred")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("ACCESS_CONTROL_URL", raising=False)
    app = create_app(observability=observability)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        ready = await client.get("/readyz")

    assert ready.status_code == 200
    payload = ready.json()
    assert payload["status"] == "ok"
    assert payload["route_publish_mode"] == "deferred"
    assert "missing" not in payload
