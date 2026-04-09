"""Unit tests for apps.compiler_worker.main."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST

from apps.compiler_worker.celery_app import COMPILATION_TASK_NAME
from apps.compiler_worker.main import (
    app,
    create_app,
)


class _StubObservability:
    def __init__(self, metrics_payload: str = "compiler_worker_test_metric 1\n") -> None:
        self._metrics_payload = metrics_payload
        self.render_metrics = MagicMock(return_value=metrics_payload)


class TestCreateApp:
    def test_create_app_reads_environment_configuration(self) -> None:
        observability = _StubObservability()

        env = {
            "WORKFLOW_ENGINE": "celery",
            "COMPILATION_TASK_QUEUE": "priority-queue",
            "MCP_RUNTIME_IMAGE": "ghcr.io/example/runtime:test",
            "COMPILER_TARGET_NAMESPACE": "runtime-system",
            "ROUTE_PUBLISH_MODE": " access-control ",
            "ACCESS_CONTROL_URL": "https://access-control.example.test",
            "CELERY_BROKER_URL": "redis://broker:6379/0",
        }

        with patch.dict(os.environ, env, clear=True):
            worker_app = create_app(observability=observability)

        assert worker_app.state.observability is observability
        assert worker_app.state.workflow_engine == "celery"
        assert worker_app.state.compilation_queue == "priority-queue"
        assert worker_app.state.runtime_image == "ghcr.io/example/runtime:test"
        assert worker_app.state.target_namespace == "runtime-system"
        assert worker_app.state.route_publish_mode == "access-control"
        assert worker_app.state.access_control_url == "https://access-control.example.test"

    def test_module_level_app_exists(self) -> None:
        assert isinstance(app, FastAPI)
        assert app.title == "service2mcp Worker"


_VALID_ENV = {
    "WORKFLOW_ENGINE": "celery",
    "COMPILATION_TASK_QUEUE": "priority-queue",
    "MCP_RUNTIME_IMAGE": "ghcr.io/example/runtime:test",
    "COMPILER_TARGET_NAMESPACE": "runtime-system",
    "ROUTE_PUBLISH_MODE": "deferred",
    "CELERY_BROKER_URL": "redis://broker:6379/0",
}


class TestHealthEndpoints:
    def test_healthz_returns_ok(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    # /readyz must return 503 when not ready
    def test_readyz_returns_503_when_broker_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 503
        payload = response.json()
        assert payload["status"] == "not_ready"
        assert any("broker" in p for p in payload["problems"])

    # deferred mode with defaults should be ready
    def test_readyz_uses_effective_defaults_for_deferred_mode(self) -> None:
        env = {
            "ROUTE_PUBLISH_MODE": "deferred",
            "CELERY_BROKER_URL": "redis://broker:6379/0",
        }
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        # Effective defaults should be populated
        assert payload["runtime_image"] is not None
        assert payload["target_namespace"] is not None

    def test_readyz_requires_access_control_url_for_access_control_mode(self) -> None:
        env = {
            "MCP_RUNTIME_IMAGE": "ghcr.io/example/runtime:test",
            "COMPILER_TARGET_NAMESPACE": "runtime-system",
            "ROUTE_PUBLISH_MODE": "access-control",
            "CELERY_BROKER_URL": "redis://broker:6379/0",
        }
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert any("access_control_url" in p for p in response.json()["problems"])

    def test_readyz_returns_ok_when_required_settings_are_present(self) -> None:
        with patch.dict(os.environ, _VALID_ENV, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 200
        assert response.json() == {
            "workflow_engine": "celery",
            "compilation_queue": "priority-queue",
            "task_name": COMPILATION_TASK_NAME,
            "runtime_image": "ghcr.io/example/runtime:test",
            "target_namespace": "runtime-system",
            "route_publish_mode": "deferred",
            "access_control_url": None,
            "status": "ok",
        }

    # reject unsupported workflow engines
    def test_readyz_rejects_unsupported_workflow_engine(self) -> None:
        env = {**_VALID_ENV, "WORKFLOW_ENGINE": "temporal"}
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert any("workflow_engine" in p for p in response.json()["problems"])

    # reject unsupported ROUTE_PUBLISH_MODE
    def test_readyz_rejects_unsupported_route_publish_mode(self) -> None:
        env = {**_VALID_ENV, "ROUTE_PUBLISH_MODE": "bogus-mode"}
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert any("route_publish_mode" in p for p in response.json()["problems"])

    # reject ephemeral broker
    def test_readyz_rejects_ephemeral_broker(self) -> None:
        env = {**_VALID_ENV}
        del env["CELERY_BROKER_URL"]
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert any("broker" in p for p in response.json()["problems"])

    def test_readyz_accepts_redis_url_as_broker(self) -> None:
        env = {**_VALID_ENV}
        del env["CELERY_BROKER_URL"]
        env["REDIS_URL"] = "redis://redis:6379/0"
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_metrics_renders_observability_payload(self) -> None:
        observability = _StubObservability("compiler_worker_custom_metric 3\n")
        with patch.dict(os.environ, {}, clear=True):
            client = TestClient(create_app(observability=observability))

        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.text == "compiler_worker_custom_metric 3\n"
        assert response.headers["content-type"] == CONTENT_TYPE_LATEST
        observability.render_metrics.assert_called_once_with()
