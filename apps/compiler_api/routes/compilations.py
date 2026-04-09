"""Compilation job routes served from the compiler API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Final, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.service import AuditLogService
from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.security import require_authenticated_caller, require_sse_caller
from apps.compiler_api.db import get_db_session, resolve_session_factory
from apps.compiler_api.dispatcher import CompilationDispatcher, get_compilation_dispatcher
from apps.compiler_api.models import CompilationCreateRequest, CompilationJobResponse
from apps.compiler_api.repository import ArtifactRegistryRepository, CompilationRepository
from apps.compiler_worker.models import (
    CompilationEventType,
    CompilationRequest,
    CompilationStage,
    CompilationStatus,
    compilation_request_replay,
    compilation_resume_checkpoint,
    store_compilation_rollback_request,
)

logger = logging.getLogger(__name__)

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


def _caller_actor(caller: TokenPrincipalResponse) -> str:
    return caller.username or caller.subject


def _job_resource(job: CompilationJobResponse) -> str:
    if isinstance(job.service_id, str) and job.service_id:
        return job.service_id
    if isinstance(job.service_name, str) and job.service_name:
        return job.service_name
    return str(job.id)


def _coerce_version_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _register_stage_detail(events: list[Any]) -> dict[str, Any] | None:
    for event in reversed(events):
        if (
            event.event_type == CompilationEventType.STAGE_SUCCEEDED.value
            and event.stage == CompilationStage.REGISTER.value
            and isinstance(event.detail, dict)
        ):
            return event.detail
    return None


def _resolve_execution_service_id(
    original: CompilationJobResponse,
    *,
    register_detail: dict[str, Any] | None = None,
) -> str | None:
    replay = compilation_request_replay(original.options)
    for candidate in (
        replay.get("service_id"),
        original.service_id,
        register_detail.get("service_id") if register_detail is not None else None,
        original.service_name,
    ):
        if isinstance(candidate, str):
            normalized = candidate.strip()
            if normalized:
                return normalized
    return None


def _resolve_registered_version(
    original: CompilationJobResponse,
    *,
    register_detail: dict[str, Any] | None = None,
) -> int | None:
    checkpoint = compilation_resume_checkpoint(original.options)
    if checkpoint is not None:
        registered_version = _coerce_version_number(checkpoint["payload"].get("registered_version"))
        if registered_version is not None:
            return registered_version
    if register_detail is None:
        return None
    return _coerce_version_number(register_detail.get("version_number"))


async def _resolve_rollback_metadata(
    compilation_repository: CompilationRepository,
    artifact_repository: ArtifactRegistryRepository,
    *,
    original_job_id: UUID,
    original: CompilationJobResponse,
) -> dict[str, Any]:
    events = await compilation_repository.list_events(original_job_id)
    register_detail = _register_stage_detail(events)
    service_id = _resolve_execution_service_id(original, register_detail=register_detail)
    if service_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Compilation {original.id} does not record the service_id needed "
                "to execute a rollback."
            ),
        )

    registered_version = _resolve_registered_version(original, register_detail=register_detail)
    if registered_version is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Compilation {original.id} does not record the version needed "
                "to determine a rollback target."
            ),
        )

    active_version = await artifact_repository.get_active_version(
        service_id,
        tenant=original.tenant,
        environment=original.environment,
    )
    if active_version is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Service {service_id} has no active version to roll back.",
        )
    if active_version.version_number != registered_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Compilation {original.id} is not the active deployment for service "
                f"{service_id}; only the currently active deployment can be rolled back safely."
            ),
        )

    versions = await artifact_repository.list_versions(
        service_id,
        tenant=original.tenant,
        environment=original.environment,
    )
    rollback_target = next(
        (version for version in versions.versions if version.version_number < registered_version),
        None,
    )
    if rollback_target is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Compilation {original.id} has no previous version available for rollback."),
        )

    return {
        "source_job_id": original_job_id,
        "service_id": service_id,
        "target_version": rollback_target.version_number,
        "tenant": original.tenant,
        "environment": original.environment,
    }


def _build_replay_request(
    original: CompilationJobResponse,
    *,
    actor: str,
    service_id: str | None = None,
    from_stage: str | None = None,
    rollback_metadata: dict[str, Any] | None = None,
) -> CompilationRequest:
    options = dict(original.options or {})
    if from_stage:
        options["from_stage"] = from_stage
    if rollback_metadata is not None:
        options = store_compilation_rollback_request(
            options,
            source_job_id=cast(UUID, rollback_metadata["source_job_id"]),
            service_id=cast(str, rollback_metadata["service_id"]),
            target_version=cast(int, rollback_metadata["target_version"]),
            tenant=cast(str | None, rollback_metadata.get("tenant")),
            environment=cast(str | None, rollback_metadata.get("environment")),
        )

    replay = compilation_request_replay(original.options)
    return CompilationRequest(
        source_url=original.source_url,
        source_content=cast(str | None, replay.get("source_content")),
        source_hash=original.source_hash,
        filename=cast(str | None, replay.get("filename")),
        created_by=actor,
        service_id=service_id
        or cast(str | None, replay.get("service_id"))
        or (original.service_id if isinstance(original.service_id, str) else None),
        service_name=original.service_name if isinstance(original.service_name, str) else None,
        options=options,
    )


def _validate_retry_stage_boundary(
    original: CompilationJobResponse,
    from_stage: str | None,
) -> None:
    if not from_stage:
        return
    try:
        target_stage = CompilationStage(from_stage)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown compilation stage: {from_stage}.",
        ) from exc
    ordered_stages = tuple(CompilationStage)
    start_index = ordered_stages.index(target_stage)
    if start_index <= 1:
        return

    checkpoint = compilation_resume_checkpoint(original.options)
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Compilation {original.id} does not have the persisted checkpoint "
                f"needed to resume from stage {target_stage.value}."
            ),
        )

    expected_completed_stage = ordered_stages[start_index - 1]
    completed_stage = CompilationStage(checkpoint["completed_stage"])
    if completed_stage is not expected_completed_stage:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Compilation {original.id} can only resume from the stage after "
                f"{completed_stage.value}, not from {target_stage.value}."
            ),
        )


@router.post("", response_model=CompilationJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_compilation(
    payload: CompilationCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    dispatcher: CompilationDispatcher = Depends(get_compilation_dispatcher),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> CompilationJobResponse:
    workflow_request = payload.to_workflow_request()
    workflow_request.created_by = _caller_actor(caller)
    repository = CompilationRepository(session)
    job = await repository.create_job(workflow_request)
    workflow_request.job_id = job.id
    audit_log = AuditLogService(session)

    try:
        await audit_log.append_entry(
            actor=workflow_request.created_by or "system",
            action="compilation.triggered",
            resource=_job_resource(job),
            detail={
                "job_id": str(job.id),
                "source_url": payload.source_url,
                "service_id": payload.service_id,
                "service_name": payload.service_name,
            },
            commit=False,
        )
    except Exception:  # broad-except: route error boundary
        logger.exception("Audit log failed during compilation creation")
        await session.rollback()
        await repository.delete_job(job.id)
        raise

    try:
        await dispatcher.enqueue(workflow_request)
        await session.commit()
    except Exception as exc:  # broad-except: route error boundary
        logger.exception("Compilation dispatch failed")
        await session.rollback()
        await repository.delete_job(job.id)
        error_message = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Compilation worker dispatch failed: {error_message}",
        ) from exc

    return job


@router.get(
    "",
    response_model=list[CompilationJobResponse],
)
async def list_compilations(
    session: AsyncSession = Depends(get_db_session),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
    service_id: str | None = None,
) -> list[CompilationJobResponse]:
    repository = CompilationRepository(session)
    return await repository.list_jobs(service_id=service_id)


@router.get(
    "/{job_id}",
    response_model=CompilationJobResponse,
)
async def get_compilation(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
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
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> CompilationJobResponse:
    """Create a new compilation job by cloning a previous one.

    Optionally accepts ``from_stage`` to hint the worker at which pipeline
    stage to resume from.
    """
    repository = CompilationRepository(session)
    original = await repository.get_job(job_id, include_internal_options=True)
    if original is None:
        raise _not_found(job_id)
    _validate_retry_stage_boundary(original, from_stage)

    retry_request = _build_replay_request(
        original,
        actor=_caller_actor(caller),
        from_stage=from_stage,
    )
    new_job = await repository.create_job(retry_request)
    retry_request.job_id = new_job.id
    audit_log = AuditLogService(session)

    try:
        await audit_log.append_entry(
            actor=retry_request.created_by or "system",
            action="compilation.retried",
            resource=_job_resource(original),
            detail={
                "original_job_id": str(job_id),
                "new_job_id": str(new_job.id),
                "from_stage": from_stage,
            },
            commit=False,
        )
    except Exception:  # broad-except: route error boundary
        logger.exception("Audit log failed during compilation retry")
        await session.rollback()
        await repository.delete_job(new_job.id)
        raise

    try:
        await dispatcher.enqueue(retry_request)
        await session.commit()
    except Exception as exc:  # broad-except: route error boundary
        logger.exception("Compilation dispatch failed during retry")
        await session.rollback()
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
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> CompilationJobResponse:
    """Create a rollback compilation job for a previously succeeded compilation."""
    repository = CompilationRepository(session)
    original = await repository.get_job(job_id, include_internal_options=True)
    if original is None:
        raise _not_found(job_id)

    if original.status != CompilationStatus.SUCCEEDED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only succeeded compilations can be rolled back (current: {original.status}).",
        )

    artifact_repository = ArtifactRegistryRepository(session)
    rollback_metadata = await _resolve_rollback_metadata(
        repository,
        artifact_repository,
        original_job_id=job_id,
        original=original,
    )
    rollback_request = _build_replay_request(
        original,
        actor=_caller_actor(caller),
        service_id=cast(str, rollback_metadata["service_id"]),
        rollback_metadata=rollback_metadata,
    )
    new_job = await repository.create_job(rollback_request)
    rollback_request.job_id = new_job.id
    audit_log = AuditLogService(session)

    try:
        await audit_log.append_entry(
            actor=rollback_request.created_by or "system",
            action="compilation.rollback_requested",
            resource=_job_resource(original),
            detail={
                "original_job_id": str(job_id),
                "rollback_job_id": str(new_job.id),
                "target_version": rollback_metadata["target_version"],
            },
            commit=False,
        )
    except Exception:  # broad-except: route error boundary
        logger.exception("Audit log failed during compilation rollback")
        await session.rollback()
        await repository.delete_job(new_job.id)
        raise

    try:
        await dispatcher.enqueue(rollback_request)
        await session.commit()
    except Exception as exc:  # broad-except: route error boundary
        logger.exception("Compilation dispatch failed during rollback")
        await session.rollback()
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
                try:
                    yield _format_sse_event(event.event_type, event.model_dump(mode="json"))
                except (TypeError, ValueError):
                    yield _format_sse_event(
                        "stream.error",
                        {
                            "message": (
                                f"Failed to serialize compilation event {event.event_type}"
                            )
                        },
                    )
                    return

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
