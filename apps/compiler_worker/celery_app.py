"""Celery application and task bindings for compilation jobs."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from celery import Celery

from apps.compiler_worker.executor import resolve_compilation_executor
from apps.compiler_worker.models import CompilationRequest

COMPILATION_TASK_NAME = "compiler_worker.execute_compilation"
DEFAULT_COMPILATION_QUEUE = "compiler.jobs"
_logger = logging.getLogger(__name__)


def create_celery_app(
    *,
    broker_url: str | None = None,
    result_backend: str | None = None,
    queue_name: str | None = None,
) -> Celery:
    """Create a Celery app with the compilation task registered."""

    resolved_broker_url = (
        broker_url or os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL") or "memory://"
    )
    resolved_result_backend = (
        result_backend
        or os.getenv("CELERY_RESULT_BACKEND")
        or os.getenv("REDIS_URL")
        or "cache+memory://"
    )
    if resolved_broker_url == "memory://":
        _logger.warning(
            "Celery broker falling back to memory:// — tasks will be lost on restart. "
            "Set CELERY_BROKER_URL or REDIS_URL for production."
        )
    if resolved_result_backend == "cache+memory://":
        _logger.warning(
            "Celery result backend falling back to cache+memory:// — results are ephemeral. "
            "Set CELERY_RESULT_BACKEND or REDIS_URL for production."
        )
    resolved_queue_name = (
        queue_name or os.getenv("COMPILATION_TASK_QUEUE") or DEFAULT_COMPILATION_QUEUE
    )

    app = Celery(
        "compiler_worker",
        broker=resolved_broker_url,
        backend=resolved_result_backend,
    )
    app.conf.update(
        accept_content=["json"],
        broker_connection_retry_on_startup=True,
        result_serializer="json",
        task_default_queue=resolved_queue_name,
        task_serializer="json",
        task_track_started=True,
    )

    def execute_compilation(payload: dict[str, Any]) -> dict[str, str | None]:
        request = CompilationRequest.from_payload(payload)
        try:
            _run_coro(_execute_compilation(request))
        except Exception as exc:
            _logger.error("Compilation task failed: %s", exc, exc_info=True)
            return {
                "job_id": str(request.job_id) if request.job_id is not None else None,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        return {"job_id": str(request.job_id) if request.job_id is not None else None}

    app.task(name=COMPILATION_TASK_NAME)(execute_compilation)
    return app


async def _execute_compilation(request: CompilationRequest) -> None:
    executor = resolve_compilation_executor()
    await executor.execute(request)


def _run_coro[T](coro: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


celery_app = create_celery_app()
