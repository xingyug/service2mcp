"""HTTP routes for querying audit logs."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.models import AuditLogListResponse
from apps.access_control.audit.service import AuditLogService
from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.authn.service import AuthenticationError, AuthnService
from apps.access_control.db import get_db_session

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def get_audit_log_service(session: AsyncSession = Depends(get_db_session)) -> AuditLogService:
    """Construct an audit log service for the current request."""

    return AuditLogService(session)


async def require_authenticated_caller(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> TokenPrincipalResponse:
    """Extract and validate the Bearer/PAT token from the Authorization header."""

    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required.",
        )
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is required.",
        )
    jwt_settings = getattr(request.app.state, "jwt_settings", None)
    if jwt_settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT settings are not configured.",
        )
    authn = AuthnService(session, jwt_settings=jwt_settings)
    try:
        return await authn.validate_token(token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


@router.get("/logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    actor: str | None = None,
    action: str | None = None,
    resource: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
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
        )
    )
