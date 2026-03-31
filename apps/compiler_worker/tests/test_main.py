"""Unit tests for apps.compiler_worker.main."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST

from apps.compiler_worker.celery_app import COMPILATION_TASK_NAME, DEFAULT_COMPILATION_QUEUE
from apps.compiler_worker.main import app, create_app


class _StubObservability:
    def __init__(self, metrics_payload: str = "compiler_worker_test_metric 1\n") -> None:
        self._metrics_payload = metrics_payload
        self.render_metrics = MagicMock(return_value=metrics_payload)


class TestCreateApp:
    def test_create_app_reads_environment_configuration(self) -> None:
        observability = _StubObservability()

        env = {
            "WORKFLOW_ENGINE": "temporal",
            "COMPILATION_TASK_QUEUE": "priority-queue",
            "MCP_RUNTIME_IMAGE": "ghcr.io/example/runtime:test",
            "COMPILER_TARGET_NAMESPACE": "runtime-system",
            "ROUTE_PUBLISH_MODE": " access-control ",
            "ACCESS_CONTROL_URL": "https://access-control.example.test",
        }

        with patch.dict(os.environ, env, clear=True):
            worker_app = create_app(observability=observability)

        assert worker_app.state.observability is observability
        assert worker_app.state.workflow_engine == "temporal"
        assert worker_app.state.compilation_queue == "priority-queue"
        assert worker_app.state.runtime_image == "ghcr.io/example/runtime:test"
        assert worker_app.state.target_namespace == "runtime-system"
        assert worker_app.state.route_publish_mode == "access-control"
        assert worker_app.state.access_control_url == "https://access-control.example.test"

    def test_module_level_app_exists(self) -> None:
        assert isinstance(app, FastAPI)
        assert app.title == "Tool Compiler Worker"


class TestHealthEndpoints:
    def test_healthz_returns_ok(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_readyz_reports_missing_configuration_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 200
        payload = response.json()
        assert payload == {
            "workflow_engine": "celery",
            "compilation_queue": DEFAULT_COMPILATION_QUEUE,
            "task_name": COMPILATION_TASK_NAME,
            "runtime_image": None,
            "target_namespace": None,
            "route_publish_mode": None,
            "access_control_url": None,
            "status": "not_ready",
            "missing": payload["missing"],
        }
        assert set(payload["missing"]) == {
            "runtime_image",
            "target_namespace",
            "route_publish_mode",
        }

    def test_readyz_requires_access_control_url_for_access_control_mode(self) -> None:
        env = {
            "MCP_RUNTIME_IMAGE": "ghcr.io/example/runtime:test",
            "COMPILER_TARGET_NAMESPACE": "runtime-system",
            "ROUTE_PUBLISH_MODE": "access-control",
        }
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(create_app(observability=_StubObservability()))

        response = client.get("/readyz")

        assert response.status_code == 200
        assert response.json()["status"] == "not_ready"
        assert response.json()["missing"] == ["access_control_url"]

    def test_readyz_returns_ok_when_required_settings_are_present(self) -> None:
        env = {
            "WORKFLOW_ENGINE": "celery",
            "COMPILATION_TASK_QUEUE": "priority-queue",
            "MCP_RUNTIME_IMAGE": "ghcr.io/example/runtime:test",
            "COMPILER_TARGET_NAMESPACE": "runtime-system",
            "ROUTE_PUBLISH_MODE": "deferred",
        }
        with patch.dict(os.environ, env, clear=True):
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

    def test_metrics_renders_observability_payload(self) -> None:
        observability = _StubObservability("compiler_worker_custom_metric 3\n")
        with patch.dict(os.environ, {}, clear=True):
            client = TestClient(create_app(observability=observability))

        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.text == "compiler_worker_custom_metric 3\n"
        assert response.headers["content-type"] == CONTENT_TYPE_LATEST
        observability.render_metrics.assert_called_once_with()
