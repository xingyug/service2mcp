"""FastAPI app exposing compiler worker health and metrics endpoints."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST

from apps.compiler_worker.celery_app import COMPILATION_TASK_NAME, DEFAULT_COMPILATION_QUEUE
from apps.compiler_worker.observability import CompilationObservability

_SUPPORTED_WORKFLOW_ENGINES = {"celery"}
_SUPPORTED_PUBLISH_MODES = {"deferred", "access-control"}
_DEFAULT_RUNTIME_IMAGE = "tool-compiler/mcp-runtime:latest"


def create_app(
    *,
    observability: CompilationObservability | None = None,
) -> FastAPI:
    """Create the compiler worker application shell."""

    app = FastAPI(title="service2mcp Worker", version="0.1.0")
    app.state.observability = observability or CompilationObservability()
    app.state.workflow_engine = os.getenv("WORKFLOW_ENGINE", "celery")
    app.state.compilation_queue = os.getenv(
        "COMPILATION_TASK_QUEUE",
        DEFAULT_COMPILATION_QUEUE,
    )
    app.state.route_publish_mode = (os.getenv("ROUTE_PUBLISH_MODE") or "deferred").strip()
    app.state.access_control_url = os.getenv("ACCESS_CONTROL_URL")

    # Resolve effective runtime image and namespace using the same fallback
    # chain as ProductionActivitySettings.from_env().
    app.state.runtime_image = (
        os.getenv("MCP_RUNTIME_IMAGE")
        or os.getenv("COMPILER_RUNTIME_IMAGE")
        or _DEFAULT_RUNTIME_IMAGE
    )
    app.state.target_namespace = os.getenv("COMPILER_TARGET_NAMESPACE") or "default"

    # Resolve effective broker URL for readiness check.
    app.state.broker_url = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> Response:
        checks: dict[str, Any] = {
            "workflow_engine": app.state.workflow_engine,
            "compilation_queue": app.state.compilation_queue,
            "task_name": COMPILATION_TASK_NAME,
            "runtime_image": app.state.runtime_image,
            "target_namespace": app.state.target_namespace,
            "route_publish_mode": app.state.route_publish_mode,
            "access_control_url": app.state.access_control_url,
        }
        problems: list[str] = []

        # Reject unsupported workflow engines
        if app.state.workflow_engine not in _SUPPORTED_WORKFLOW_ENGINES:
            problems.append(f"unsupported workflow_engine={app.state.workflow_engine!r}")

        # Validate ROUTE_PUBLISH_MODE
        if app.state.route_publish_mode not in _SUPPORTED_PUBLISH_MODES:
            problems.append(f"unsupported route_publish_mode={app.state.route_publish_mode!r}")

        # access_control_url only required for access-control mode
        if app.state.route_publish_mode == "access-control" and not app.state.access_control_url:
            problems.append("access_control_url required for access-control mode")

        # Warn if broker is ephemeral memory://
        if not app.state.broker_url:
            problems.append("no persistent broker configured (CELERY_BROKER_URL / REDIS_URL)")
            checks["broker_url"] = "memory:// (ephemeral)"

        ready = not problems
        checks["status"] = "ok" if ready else "not_ready"
        if problems:
            checks["problems"] = problems

        # Return HTTP 503 when not ready
        if not ready:
            return JSONResponse(content=checks, status_code=503)
        return JSONResponse(content=checks, status_code=200)

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            content=app.state.observability.render_metrics(),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app


app = create_app()
