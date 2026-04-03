"""Compilation job routes served from the compiler API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.service import AuditLogService
from apps.compiler_api.db import get_db_session, resolve_session_factory
from apps.compiler_api.dispatcher import CompilationDispatcher, get_compilation_dispatcher
from apps.compiler_api.models import CompilationCreateRequest, CompilationJobResponse
from apps.compiler_api.repository import ArtifactRegistryRepository, CompilationRepository
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
        await dispatcher.enqueue(workflow_request)
    except Exception as exc:
        await repository.delete_job(job.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Compilation worker dispatch failed.",
        ) from exc

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

    return job


@router.get("", response_model=list[CompilationJobResponse])
async def list_compilations(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> list[CompilationJobResponse]:
    repository = CompilationRepository(session)
    return await repository.list_jobs(status=status_filter, limit=limit)


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


@router.get("/{job_id}/events")
async def stream_compilation_events(
    job_id: UUID,
    request: Request,
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


@router.post("/{job_id}/retry", response_model=CompilationJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_compilation(
    job_id: UUID,
    from_stage: str | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
    dispatcher: CompilationDispatcher = Depends(get_compilation_dispatcher),
) -> CompilationJobResponse:
    repo = CompilationRepository(session)
    original = await repo.get_job(job_id)
    if original is None:
        raise _not_found(job_id)
    if original.status != CompilationStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry job in '{original.status}' status.",
        )

    retry_options: dict[str, object] = {
        **(original.options or {}),
        "original_job_id": str(job_id),
    }
    if from_stage is not None:
        retry_options["retry_from_stage"] = from_stage

    retry_req = CompilationRequest(
        source_url=original.source_url,
        source_hash=original.source_hash,
        created_by=original.created_by,
        service_name=original.service_name,
        options=dict(retry_options),
    )
    new_job = await repo.create_job(retry_req)
    retry_req.job_id = new_job.id
    await dispatcher.enqueue(retry_req)
    return new_job


@router.post("/{job_id}/rollback", response_model=CompilationJobResponse)
async def rollback_compilation(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CompilationJobResponse:
    repo = CompilationRepository(session)
    job = await repo.get_job(job_id)
    if job is None:
        raise _not_found(job_id)
    if job.status not in (CompilationStatus.SUCCEEDED.value, CompilationStatus.FAILED.value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot rollback job in '{job.status}' status.",
        )
    if not job.service_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job has no associated service to rollback.",
        )

    artifact_repo = ArtifactRegistryRepository(session)
    versions_response = await artifact_repo.list_versions(job.service_name)
    versions = versions_response.versions

    active = next((v for v in versions if v.is_active), None)
    if active is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active artifact version found for service '{job.service_name}'.",
        )

    previous = next(
        (v for v in versions if v.version_number < active.version_number),
        None,
    )
    if previous is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No previous artifact version to rollback to for service '{job.service_name}'.",
        )

    await artifact_repo.activate_version(job.service_name, previous.version_number)
    updated_job = await repo.update_job_status(job_id, CompilationStatus.ROLLED_BACK.value)
    if updated_job is None:
        raise _not_found(job_id)
    return updated_job
