"""Dispatch abstractions for handing compilation jobs to the worker layer."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from celery import Celery
from fastapi import FastAPI, Request

from apps.compiler_worker.celery_app import (
    COMPILATION_TASK_NAME,
    DEFAULT_COMPILATION_QUEUE,
)
from apps.compiler_worker.celery_app import (
    celery_app as default_celery_app,
)
from apps.compiler_worker.models import CompilationRequest

_DISPATCHER_STATE_KEY = "compilation_dispatcher"


class CompilationDispatcher(Protocol):
    """Queue-like interface used by the API to submit workflow requests."""

    async def enqueue(self, request: CompilationRequest) -> None: ...


@dataclass
class InMemoryCompilationDispatcher:
    """Minimal dispatcher that records submissions in memory."""

    submitted_requests: list[CompilationRequest] = field(default_factory=list)

    async def enqueue(self, request: CompilationRequest) -> None:
        self.submitted_requests.append(request)


@dataclass
class CallbackCompilationDispatcher:
    """Dispatcher that forwards submissions to an async callback."""

    callback: Callable[[CompilationRequest], Awaitable[None]]

    async def enqueue(self, request: CompilationRequest) -> None:
        await self.callback(request)


@dataclass
class CeleryCompilationDispatcher:
    """Dispatcher that submits compilation requests to a Celery queue."""

    celery_app: Celery = field(default_factory=lambda: default_celery_app)
    task_name: str = COMPILATION_TASK_NAME
    queue_name: str = field(
        default_factory=lambda: os.getenv("COMPILATION_TASK_QUEUE", DEFAULT_COMPILATION_QUEUE)
    )

    async def enqueue(self, request: CompilationRequest) -> None:
        task = self.celery_app.tasks.get(self.task_name)
        if task is None:
            raise RuntimeError(f"Celery task {self.task_name} is not registered.")
        task.apply_async(args=[request.to_payload()], queue=self.queue_name)


def configure_compilation_dispatcher(
    app: FastAPI,
    *,
    dispatcher: CompilationDispatcher | None = None,
) -> None:
    """Attach the compilation dispatcher to app state."""

    setattr(
        app.state,
        _DISPATCHER_STATE_KEY,
        dispatcher or _resolve_default_dispatcher(),
    )


def get_compilation_dispatcher(request: Request) -> CompilationDispatcher:
    """Resolve the configured dispatcher from FastAPI app state."""

    dispatcher = getattr(request.app.state, _DISPATCHER_STATE_KEY, None)
    if dispatcher is None:
        dispatcher = _resolve_default_dispatcher()
        setattr(request.app.state, _DISPATCHER_STATE_KEY, dispatcher)
    return dispatcher


def _resolve_default_dispatcher() -> CompilationDispatcher:
    workflow_engine = os.getenv("WORKFLOW_ENGINE", "").strip().lower()
    if workflow_engine == "celery":
        return CeleryCompilationDispatcher()
    return InMemoryCompilationDispatcher()
