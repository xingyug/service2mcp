"""Pydantic models for audit log queries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AuditLogEntryResponse(BaseModel):
    """Serialized audit log entry."""

    id: UUID
    actor: str
    action: str
    resource: str | None = None
    detail: dict[str, object] | None = None
    timestamp: datetime


class AuditLogListResponse(BaseModel):
    """Query response for audit logs."""

    items: list[AuditLogEntryResponse]
