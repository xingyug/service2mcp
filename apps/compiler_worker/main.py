"""FastAPI app exposing compiler worker health and metrics endpoints."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST

from apps.compiler_worker.celery_app import COMPILATION_TASK_NAME, DEFAULT_COMPILATION_QUEUE
from apps.compiler_worker.observability import CompilationObservability


def create_app(
    *,
    observability: CompilationObservability | None = None,
) -> FastAPI:
    """Create the compiler worker application shell."""

    app = FastAPI(title="Tool Compiler Worker", version="0.1.0")
    app.state.observability = observability or CompilationObservability()
    app.state.workflow_engine = os.getenv("WORKFLOW_ENGINE", "celery")
    app.state.compilation_queue = os.getenv(
        "COMPILATION_TASK_QUEUE",
        DEFAULT_COMPILATION_QUEUE,
    )
    app.state.runtime_image = os.getenv("MCP_RUNTIME_IMAGE")
    app.state.target_namespace = os.getenv("COMPILER_TARGET_NAMESPACE")
    app.state.route_publish_mode = (os.getenv("ROUTE_PUBLISH_MODE") or "").strip() or None
    app.state.access_control_url = os.getenv("ACCESS_CONTROL_URL")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        checks: dict[str, Any] = {
            "workflow_engine": app.state.workflow_engine,
            "compilation_queue": app.state.compilation_queue,
            "task_name": COMPILATION_TASK_NAME,
            "runtime_image": app.state.runtime_image,
            "target_namespace": app.state.target_namespace,
            "route_publish_mode": app.state.route_publish_mode,
            "access_control_url": app.state.access_control_url,
        }
        required_keys = {
            "workflow_engine",
            "compilation_queue",
            "task_name",
            "runtime_image",
            "target_namespace",
            "route_publish_mode",
        }
        if app.state.route_publish_mode == "access-control":
            required_keys.add("access_control_url")
        missing = [k for k in required_keys if checks[k] is None]
        ready = not missing
        checks["status"] = "ok" if ready else "not_ready"
        if missing:
            checks["missing"] = missing
        return checks

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            content=app.state.observability.render_metrics(),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app


app = create_app()
