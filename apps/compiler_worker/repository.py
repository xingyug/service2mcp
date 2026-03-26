"""Persistence helpers for compilation workflow jobs and events."""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.compiler_worker.models import (
    CompilationEventRecord,
    CompilationEventType,
    CompilationJobRecord,
    CompilationRequest,
    CompilationStage,
    CompilationStatus,
)
from libs.db_models import CompilationEvent, CompilationJob


class SQLAlchemyCompilationJobStore:
    """Persist compilation jobs and their event stream in PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_job(
        self,
        request: CompilationRequest,
        *,
        job_id: UUID | None = None,
    ) -> UUID:
        resolved_job_id = job_id or request.job_id or uuid.uuid4()
        allow_existing = job_id is not None or request.job_id is not None
        async with self._session_factory() as session:
            if allow_existing:
                existing = await session.get(CompilationJob, resolved_job_id)
                if existing is not None:
                    return existing.id
            job = CompilationJob(
                id=resolved_job_id,
                source_url=request.source_url,
                source_hash=request.source_hash,
                status=CompilationStatus.PENDING.value,
                options=request.options or None,
                created_by=request.created_by,
                service_name=request.service_name,
            )
            session.add(job)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not allow_existing:
                    raise
                existing = await session.get(CompilationJob, resolved_job_id)
                if existing is None:
                    raise
                return existing.id
        return resolved_job_id

    async def get_job(self, job_id: UUID) -> CompilationJobRecord | None:
        async with self._session_factory() as session:
            job = await session.get(CompilationJob, job_id)
            if job is None:
                return None
            return self._to_job_record(job)

    async def list_events(self, job_id: UUID) -> list[CompilationEventRecord]:
        async with self._session_factory() as session:
            events = (
                await session.scalars(
                    select(CompilationEvent)
                    .where(CompilationEvent.job_id == job_id)
                    .order_by(CompilationEvent.sequence_number)
                )
            ).all()
            return [self._to_event_record(event) for event in events]

    async def mark_job_running(
        self,
        job_id: UUID,
        stage: CompilationStage,
        *,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            job = await self._require_job(session, job_id)
            job.status = CompilationStatus.RUNNING.value
            job.current_stage = stage.value
            job.error_detail = None
            if protocol is not None:
                job.protocol = protocol
            if service_name is not None:
                job.service_name = service_name
            await session.commit()

    async def mark_job_succeeded(
        self,
        job_id: UUID,
        stage: CompilationStage,
        *,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            job = await self._require_job(session, job_id)
            job.status = CompilationStatus.SUCCEEDED.value
            job.current_stage = stage.value
            job.error_detail = None
            if protocol is not None:
                job.protocol = protocol
            if service_name is not None:
                job.service_name = service_name
            await session.commit()

    async def mark_job_failed(
        self,
        job_id: UUID,
        stage: CompilationStage,
        error_detail: str,
        *,
        rolled_back: bool,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            job = await self._require_job(session, job_id)
            job.status = (
                CompilationStatus.ROLLED_BACK.value
                if rolled_back
                else CompilationStatus.FAILED.value
            )
            job.current_stage = stage.value
            job.error_detail = error_detail
            if protocol is not None:
                job.protocol = protocol
            if service_name is not None:
                job.service_name = service_name
            await session.commit()

    async def append_event(
        self,
        job_id: UUID,
        *,
        event_type: CompilationEventType,
        stage: CompilationStage | None = None,
        attempt: int | None = None,
        detail: dict[str, Any] | None = None,
        error_detail: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            await self._require_job(session, job_id)
            sequence_number = await self._next_sequence_number(session, job_id)
            event = CompilationEvent(
                job_id=job_id,
                sequence_number=sequence_number,
                stage=stage.value if stage is not None else None,
                event_type=event_type.value,
                attempt=attempt,
                detail=detail,
                error_detail=error_detail,
            )
            session.add(event)
            await session.commit()

    async def _next_sequence_number(self, session: AsyncSession, job_id: UUID) -> int:
        existing_max = await session.scalar(
            select(func.max(CompilationEvent.sequence_number)).where(
                CompilationEvent.job_id == job_id
            )
        )
        return int(existing_max or 0) + 1

    async def _require_job(self, session: AsyncSession, job_id: UUID) -> CompilationJob:
        job = await session.get(CompilationJob, job_id)
        if job is None:
            raise KeyError(f"Compilation job {job_id} does not exist.")
        return job

    def _to_job_record(self, job: CompilationJob) -> CompilationJobRecord:
        current_stage = (
            CompilationStage(job.current_stage) if job.current_stage is not None else None
        )
        return CompilationJobRecord(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=job.protocol,
            status=CompilationStatus(job.status),
            current_stage=current_stage,
            error_detail=job.error_detail,
            options=job.options,
            created_by=job.created_by,
            service_name=job.service_name,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    def _to_event_record(self, event: CompilationEvent) -> CompilationEventRecord:
        stage = CompilationStage(event.stage) if event.stage is not None else None
        return CompilationEventRecord(
            id=event.id,
            job_id=event.job_id,
            sequence_number=event.sequence_number,
            stage=stage,
            event_type=CompilationEventType(event.event_type),
            attempt=event.attempt,
            detail=event.detail,
            error_detail=event.error_detail,
            created_at=event.created_at,
        )
