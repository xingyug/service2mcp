"""Compilation execution adapters used by the queue-bound worker."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.compiler_worker.activities import (
    create_default_activity_registry,
    create_default_rollback_workflow,
)
from apps.compiler_worker.models import (
    CompilationEventType,
    CompilationRequest,
    CompilationStage,
    compilation_rollback_request,
)
from apps.compiler_worker.repository import SQLAlchemyCompilationJobStore
from apps.compiler_worker.workflows import CompilationWorkflow
from apps.compiler_worker.workflows.rollback_workflow import RollbackRequest


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
        try:
            session_factory = async_sessionmaker[AsyncSession](engine, expire_on_commit=False)
            store = SQLAlchemyCompilationJobStore(session_factory)
            rollback_metadata = compilation_rollback_request(request.options)
            if rollback_metadata is not None:
                await _execute_rollback_request(
                    session_factory=session_factory,
                    store=store,
                    request=request,
                    rollback_metadata=rollback_metadata,
                )
                return
            workflow = CompilationWorkflow(
                store=store,
                activities=create_default_activity_registry(session_factory=session_factory),
            )
            await workflow.run(request)
        finally:
            await engine.dispose()


async def _execute_rollback_request(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    store: SQLAlchemyCompilationJobStore,
    request: CompilationRequest,
    rollback_metadata: dict[str, Any],
) -> None:
    job_id = await store.create_job(request, job_id=request.job_id)
    rollback_stage = CompilationStage.DEPLOY
    final_stage = CompilationStage.REGISTER
    service_id = str(rollback_metadata["service_id"])
    target_version = int(rollback_metadata["target_version"])
    source_job_id = rollback_metadata["source_job_id"]

    await store.append_event(job_id, event_type=CompilationEventType.JOB_CREATED)
    await store.append_event(job_id, event_type=CompilationEventType.JOB_STARTED)
    await store.mark_job_running(job_id, rollback_stage, service_name=service_id)
    await store.append_event(
        job_id,
        event_type=CompilationEventType.ROLLBACK_STARTED,
        stage=rollback_stage,
        detail={
            "original_job_id": str(source_job_id),
            "service_id": service_id,
            "target_version": target_version,
        },
    )

    workflow = create_default_rollback_workflow(
        session_factory=session_factory,
        request_options=request.options,
    )
    try:
        result = await workflow.run(
            RollbackRequest(
                service_id=service_id,
                target_version=target_version,
                tenant=rollback_metadata.get("tenant"),
                environment=rollback_metadata.get("environment"),
            )
        )
    except Exception as exc:  # broad-except: workflow error boundary — record rollback failure
        error_detail = str(exc).strip() or exc.__class__.__name__
        await store.mark_job_failed(
            job_id,
            rollback_stage,
            error_detail,
            rolled_back=False,
            service_name=service_id,
        )
        await store.append_event(
            job_id,
            event_type=CompilationEventType.ROLLBACK_FAILED,
            stage=rollback_stage,
            detail={
                "original_job_id": str(source_job_id),
                "service_id": service_id,
                "target_version": target_version,
            },
            error_detail=error_detail,
        )
        await store.append_event(
            job_id,
            event_type=CompilationEventType.JOB_FAILED,
            stage=rollback_stage,
            detail={
                "mode": "rollback",
                "original_job_id": str(source_job_id),
                "service_id": service_id,
                "target_version": target_version,
            },
            error_detail=error_detail,
        )
        raise

    await store.mark_job_succeeded(
        job_id,
        final_stage,
        protocol=result.protocol,
        service_name=result.service_id,
    )
    await store.append_event(
        job_id,
        event_type=CompilationEventType.ROLLBACK_SUCCEEDED,
        stage=final_stage,
        detail={
            "original_job_id": str(source_job_id),
            "service_id": result.service_id,
            "target_version": result.target_version,
            "previous_active_version": result.previous_active_version,
            "deployment_revision": result.deployment_revision,
        },
    )
    await store.append_event(
        job_id,
        event_type=CompilationEventType.JOB_SUCCEEDED,
        stage=final_stage,
        detail={
            "mode": "rollback",
            "protocol": result.protocol,
            "service_name": result.service_id,
            "target_version": result.target_version,
            "previous_active_version": result.previous_active_version,
        },
    )


_configured_executor: CompilationExecutor | None = None
_cached_executor: DatabaseWorkflowCompilationExecutor | None = None


def configure_compilation_executor(executor: CompilationExecutor | None) -> None:
    """Override the active executor, primarily for tests."""

    global _configured_executor
    _configured_executor = executor


def reset_compilation_executor() -> None:
    """Reset any executor override and cached default runtime state."""

    global _configured_executor, _cached_executor
    _configured_executor = None
    _cached_executor = None


def resolve_compilation_executor() -> CompilationExecutor:
    """Resolve the active executor for queue task execution."""

    global _cached_executor
    if _configured_executor is not None:
        return _configured_executor
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured for compiler worker execution.")
    if _cached_executor is None:
        _cached_executor = DatabaseWorkflowCompilationExecutor(database_url=database_url)
    return _cached_executor
