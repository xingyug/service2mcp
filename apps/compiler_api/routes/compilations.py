"""Compilation job routes served from the compiler API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.service import AuditLogService
from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.security import require_sse_caller
from apps.compiler_api.db import get_db_session, resolve_session_factory
from apps.compiler_api.dispatcher import CompilationDispatcher, get_compilation_dispatcher
from apps.compiler_api.models import CompilationCreateRequest, CompilationJobResponse
from apps.compiler_api.repository import CompilationRepository
from apps.compiler_worker.models import CompilationRequest, CompilationStatus

router = APIRouter(prefix="/api/v1/compilations", tags=["compilations"])

_TERMINAL_STATUSES: Final = {
    CompilationStatus.SUCCEEDED.value,
    CompilationStatus.FAILED.value,
    CompilationStatus.ROLLED_BACK.value,
}


def _not_found(job_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Compilation job {job_id} was not found.",
    )


@router.post("", response_model=CompilationJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_compilation(
    payload: CompilationCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    dispatcher: CompilationDispatcher = Depends(get_compilation_dispatcher),
) -> CompilationJobResponse:
    workflow_request = payload.to_workflow_request()
    repository = CompilationRepository(session)
    job = await repository.create_job(workflow_request)
    workflow_request.job_id = job.id
    audit_log = AuditLogService(session)

    try:
        await audit_log.append_entry(
            actor=payload.created_by or "system",
            action="compilation.triggered",
            resource=job.service_name or str(job.id),
            detail={
                "job_id": str(job.id),
                "source_url": payload.source_url,
                "service_name": payload.service_name,
            },
        )
        await dispatcher.enqueue(workflow_request)
    except Exception as exc:
        await repository.delete_job(job.id)
        error_message = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Compilation worker dispatch failed: {error_message}",
        ) from exc

    return job


@router.get("", response_model=list[CompilationJobResponse])
async def list_compilations(
    session: AsyncSession = Depends(get_db_session),
) -> list[CompilationJobResponse]:
    repository = CompilationRepository(session)
    return await repository.list_jobs()


@router.get("/{job_id}", response_model=CompilationJobResponse)
async def get_compilation(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CompilationJobResponse:
    repository = CompilationRepository(session)
    job = await repository.get_job(job_id)
    if job is None:
        raise _not_found(job_id)
    return job


@router.post(
    "/{job_id}/retry",
    response_model=CompilationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_compilation(
    job_id: UUID,
    from_stage: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    dispatcher: CompilationDispatcher = Depends(get_compilation_dispatcher),
) -> CompilationJobResponse:
    """Create a new compilation job by cloning a previous one.

    Optionally accepts ``from_stage`` to hint the worker at which pipeline
    stage to resume from.
    """
    repository = CompilationRepository(session)
    original = await repository.get_job(job_id)
    if original is None:
        raise _not_found(job_id)

    options = dict(original.options or {})
    if from_stage:
        options["from_stage"] = from_stage

    retry_request = CompilationRequest(
        source_url=original.source_url,
        source_hash=original.source_hash,
        created_by=original.created_by,
        service_name=original.service_name,
        options=options,
    )
    new_job = await repository.create_job(retry_request)
    retry_request.job_id = new_job.id
    audit_log = AuditLogService(session)

    try:
        await audit_log.append_entry(
            actor=original.created_by or "system",
            action="compilation.retried",
            resource=original.service_name or str(original.id),
            detail={
                "original_job_id": str(job_id),
                "new_job_id": str(new_job.id),
                "from_stage": from_stage,
            },
        )
        await dispatcher.enqueue(retry_request)
    except Exception as exc:
        await repository.delete_job(new_job.id)
        error_message = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Compilation worker dispatch failed: {error_message}",
        ) from exc

    return new_job


@router.post(
    "/{job_id}/rollback",
    response_model=CompilationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rollback_compilation(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    dispatcher: CompilationDispatcher = Depends(get_compilation_dispatcher),
) -> CompilationJobResponse:
    """Create a rollback compilation job for a previously succeeded compilation."""
    repository = CompilationRepository(session)
    original = await repository.get_job(job_id)
    if original is None:
        raise _not_found(job_id)

    if original.status != CompilationStatus.SUCCEEDED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only succeeded compilations can be rolled back (current: {original.status}).",
        )

    options = dict(original.options or {})
    options["rollback_from_job_id"] = str(job_id)

    rollback_request = CompilationRequest(
        source_url=original.source_url,
        source_hash=original.source_hash,
        created_by=original.created_by,
        service_name=original.service_name,
        options=options,
    )
    new_job = await repository.create_job(rollback_request)
    rollback_request.job_id = new_job.id
    audit_log = AuditLogService(session)

    try:
        await audit_log.append_entry(
            actor=original.created_by or "system",
            action="compilation.rollback_requested",
            resource=original.service_name or str(original.id),
            detail={
                "original_job_id": str(job_id),
                "rollback_job_id": str(new_job.id),
            },
        )
        await dispatcher.enqueue(rollback_request)
    except Exception as exc:
        await repository.delete_job(new_job.id)
        error_message = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Compilation worker dispatch failed: {error_message}",
        ) from exc

    return new_job


@router.get("/{job_id}/events")
async def stream_compilation_events(
    job_id: UUID,
    request: Request,
    _caller: TokenPrincipalResponse = Depends(require_sse_caller),
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    repository = CompilationRepository(session)
    if await repository.get_job(job_id) is None:
        raise _not_found(job_id)

    session_factory = resolve_session_factory(request.app)

    async def event_stream() -> AsyncIterator[str]:
        last_sequence = 0

        while True:
            async with session_factory() as poll_session:
                poll_repository = CompilationRepository(poll_session)
                events = await poll_repository.list_events(job_id, after_sequence=last_sequence)
                job = await poll_repository.get_job(job_id)

            for event in events:
                last_sequence = event.sequence_number
                yield _format_sse_event(event.event_type, event.model_dump(mode="json"))

            if job is None:
                break
            if job.status in _TERMINAL_STATUSES:
                break
            if await request.is_disconnected():
                break

            await asyncio.sleep(0.1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _format_sse_event(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
