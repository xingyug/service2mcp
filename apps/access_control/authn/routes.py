"""HTTP routes for the authentication module."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.audit.service import AuditLogService
from apps.access_control.authn.models import (
    PATCreateRequest,
    PATCreateResponse,
    PATListResponse,
    PATResponse,
    TokenPrincipalResponse,
    TokenValidationRequest,
)
from apps.access_control.authn.service import (
    AuthenticationError,
    AuthnService,
    JWTConfigurationError,
    JWTSettings,
    UserNotFoundError,
    resolve_jwt_settings,
)
from apps.access_control.db import get_db_session
from apps.access_control.gateway_binding.service import (
    GatewayBindingService,
    get_gateway_binding_service,
)
from apps.access_control.security import (
    require_authenticated_caller,
    require_self_or_admin,
)

router = APIRouter(prefix="/api/v1/authn", tags=["authn"])


def get_jwt_settings(request: Request) -> JWTSettings:
    """Resolve configured JWT settings from app state."""

    try:
        return resolve_jwt_settings(request.app.state)
    except JWTConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def get_authn_service(
    session: AsyncSession = Depends(get_db_session),
    jwt_settings: JWTSettings = Depends(get_jwt_settings),
) -> AuthnService:
    """Construct an authn service for the current request."""

    return AuthnService(session, jwt_settings=jwt_settings)


async def _rollback_and_reconcile_gateway(
    session: AsyncSession,
    gateway_binding: GatewayBindingService,
) -> None:
    await session.rollback()
    try:
        await gateway_binding.reconcile(session)
    except Exception as exc:  # pragma: no cover - exercised via route failure tests
        raise RuntimeError(
            f"Gateway compensation failed after transaction rollback: {exc}"
        ) from exc


@router.post("/validate", response_model=TokenPrincipalResponse)
async def validate_token(
    payload: TokenValidationRequest,
    service: AuthnService = Depends(get_authn_service),
) -> TokenPrincipalResponse:
    try:
        principal = await service.validate_token(payload.token)
        if principal.token_type == "jwt":
            await service.sync_jwt_user_roles(principal)
        return principal
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/pats", response_model=PATCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_pat(
    payload: PATCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    service: AuthnService = Depends(get_authn_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> PATCreateResponse:
    require_self_or_admin(caller, username=payload.username)
    caller_username = caller.username or caller.subject
    if caller.token_type == "jwt" and caller_username == payload.username:
        await service.sync_jwt_user_roles(caller, commit=False)
    try:
        created = await service.create_pat(
            username=payload.username,
            name=payload.name,
            commit=False,
        )
    except UserNotFoundError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        await gateway_binding.sync_pat_creation(created, created.token)
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gateway sync failed after PAT creation: {exc}",
        ) from exc
    try:
        audit_log = AuditLogService(session)
        await audit_log.append_entry(
            actor=caller.subject,
            action="pat.created",
            resource=str(created.id),
            detail={"username": payload.username, "name": payload.name},
            commit=False,
        )
        await session.commit()
    except Exception:
        await _rollback_and_reconcile_gateway(session, gateway_binding)
        raise
    return created


@router.get("/pats", response_model=PATListResponse)
async def list_pats(
    username: str,
    service: AuthnService = Depends(get_authn_service),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
) -> PATListResponse:
    require_self_or_admin(caller, username=username)
    return await service.list_pats(username=username, page=page, page_size=page_size)


@router.post("/pats/{pat_id}/revoke", response_model=PATResponse)
async def revoke_pat(
    pat_id: str,
    session: AsyncSession = Depends(get_db_session),
    service: AuthnService = Depends(get_authn_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> PATResponse:
    try:
        from uuid import UUID

        parsed_pat_id = UUID(pat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PAT ID.",
        ) from exc

    existing = await service.get_pat(parsed_pat_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PAT not found.")
    require_self_or_admin(caller, username=existing.username)

    revoked = await service.revoke_pat(parsed_pat_id, commit=False)
    if revoked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PAT not found.")
    try:
        await gateway_binding.sync_pat_revocation(revoked.id)
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gateway sync failed after PAT revocation: {exc}",
        ) from exc
    try:
        audit_log = AuditLogService(session)
        await audit_log.append_entry(
            actor=caller.subject,
            action="pat.revoked",
            resource=str(revoked.id),
            detail={"username": existing.username, "pat_id": pat_id},
            commit=False,
        )
        await session.commit()
    except Exception:
        await _rollback_and_reconcile_gateway(session, gateway_binding)
        raise
    return revoked
