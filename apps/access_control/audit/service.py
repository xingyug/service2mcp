"""Shared audit logging service."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.models import AuditLogEntryResponse
from libs.db_models import AuditLog


class AuditLogService:
    """Append-only audit logging helpers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_entry(
        self,
        *,
        actor: str,
        action: str,
        resource: str | None = None,
        detail: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> AuditLogEntryResponse:
        entry = AuditLog(
            actor=actor,
            action=action,
            resource=resource,
            detail=detail,
        )
        self._session.add(entry)
        await self._session.flush()
        if commit:
            await self._session.commit()
        await self._session.refresh(entry)
        return self._to_response(entry)

    async def list_entries(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        resource: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int | None = 1000,
    ) -> list[AuditLogEntryResponse]:
        query: Select[tuple[AuditLog]] = select(AuditLog)
        if actor is not None:
            query = query.where(AuditLog.actor == actor)
        if action is not None:
            query = query.where(AuditLog.action == action)
        if resource is not None:
            query = query.where(AuditLog.resource == resource)
        if start_at is not None:
            query = query.where(AuditLog.timestamp >= start_at)
        if end_at is not None:
            query = query.where(AuditLog.timestamp <= end_at)

        query = query.order_by(AuditLog.timestamp.desc())
        if limit is not None:
            query = query.limit(limit)

        result = await self._session.scalars(query)
        return [self._to_response(entry) for entry in result.all()]

    async def get_entry(self, entry_id: UUID) -> AuditLogEntryResponse | None:
        entry = await self._session.get(AuditLog, entry_id)
        if entry is None:
            return None
        return self._to_response(entry)

    @staticmethod
    def _to_response(entry: AuditLog) -> AuditLogEntryResponse:
        return AuditLogEntryResponse(
            id=entry.id,
            actor=entry.actor,
            action=entry.action,
            resource=entry.resource,
            detail=entry.detail,
            timestamp=entry.timestamp,
        )
