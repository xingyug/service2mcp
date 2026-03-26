"""Compilation execution adapters used by the queue-bound worker."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.compiler_worker.activities import create_default_activity_registry
from apps.compiler_worker.models import CompilationRequest
from apps.compiler_worker.repository import SQLAlchemyCompilationJobStore
from apps.compiler_worker.workflows import CompilationWorkflow


class CompilationExecutor(Protocol):
    """Async execution interface used by Celery task wrappers."""

    async def execute(self, request: CompilationRequest) -> None: ...


@dataclass
class CallbackCompilationExecutor:
    """Executor test double that forwards execution to an async callback."""

    callback: Callable[[CompilationRequest], Awaitable[None]]

    async def execute(self, request: CompilationRequest) -> None:
        await self.callback(request)


@dataclass
class WorkflowCompilationExecutor:
    """Executor that delegates to the workflow core."""

    workflow: CompilationWorkflow

    async def execute(self, request: CompilationRequest) -> None:
        await self.workflow.run(request)


@dataclass(frozen=True)
class DatabaseWorkflowCompilationExecutor:
    """Executor that builds a fresh workflow runtime for each task execution."""

    database_url: str

    async def execute(self, request: CompilationRequest) -> None:
        engine = create_async_engine(self.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker[AsyncSession](engine, expire_on_commit=False)
        try:
            store = SQLAlchemyCompilationJobStore(session_factory)
            workflow = CompilationWorkflow(
                store=store,
                activities=create_default_activity_registry(session_factory=session_factory),
            )
            await workflow.run(request)
        finally:
            await engine.dispose()


_configured_executor: CompilationExecutor | None = None


def configure_compilation_executor(executor: CompilationExecutor | None) -> None:
    """Override the active executor, primarily for tests."""

    global _configured_executor
    _configured_executor = executor


def reset_compilation_executor() -> None:
    """Reset any executor override and cached default runtime state."""

    global _configured_executor
    _configured_executor = None


def resolve_compilation_executor() -> CompilationExecutor:
    """Resolve the active executor for queue task execution."""

    if _configured_executor is not None:
        return _configured_executor
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured for compiler worker execution.")
    return DatabaseWorkflowCompilationExecutor(database_url=database_url)
