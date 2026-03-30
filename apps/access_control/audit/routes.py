"""HTTP routes for querying audit logs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.models import AuditLogEntryResponse, AuditLogListResponse
from apps.access_control.audit.service import AuditLogService
from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.db import get_db_session
from apps.access_control.security import require_authenticated_caller

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def get_audit_log_service(session: AsyncSession = Depends(get_db_session)) -> AuditLogService:
    """Construct an audit log service for the current request."""

    return AuditLogService(session)


@router.get("/logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    actor: str | None = None,
    action: str | None = None,
    resource: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: Annotated[int, Query(ge=1)] = 1000,
    include_all: bool = False,
    service: AuditLogService = Depends(get_audit_log_service),
    _caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> AuditLogListResponse:
    return AuditLogListResponse(
        items=await service.list_entries(
            actor=actor,
            action=action,
            resource=resource,
            start_at=start_at,
            end_at=end_at,
            limit=None if include_all else limit,
        )
    )


@router.get("/logs/{entry_id}", response_model=AuditLogEntryResponse)
async def get_audit_log(
    entry_id: UUID,
    service: AuditLogService = Depends(get_audit_log_service),
    _caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> AuditLogEntryResponse:
    entry = await service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit entry {entry_id} not found.",
        )
    return entry
