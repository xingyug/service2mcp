"""HTTP routes for the authentication module."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import (
    PATCreateRequest,
    PATCreateResponse,
    PATListResponse,
    PATResponse,
    TokenPrincipalResponse,
    TokenValidationRequest,
)
from apps.access_control.authn.service import AuthenticationError, AuthnService, JWTSettings
from apps.access_control.db import get_db_session
from apps.access_control.gateway_binding.service import (
    GatewayBindingService,
    get_gateway_binding_service,
)
from apps.access_control.security import require_authenticated_caller, require_self_or_admin

router = APIRouter(prefix="/api/v1/authn", tags=["authn"])


def get_jwt_settings(request: Request) -> JWTSettings:
    """Resolve configured JWT settings from app state."""

    settings = getattr(request.app.state, "jwt_settings", None)
    if settings is None:
        raise RuntimeError("JWT settings are not configured.")
    return cast(JWTSettings, settings)


def get_authn_service(
    session: AsyncSession = Depends(get_db_session),
    jwt_settings: JWTSettings = Depends(get_jwt_settings),
) -> AuthnService:
    """Construct an authn service for the current request."""

    return AuthnService(session, jwt_settings=jwt_settings)


@router.post("/validate", response_model=TokenPrincipalResponse)
async def validate_token(
    payload: TokenValidationRequest,
    service: AuthnService = Depends(get_authn_service),
) -> TokenPrincipalResponse:
    try:
        return await service.validate_token(payload.token)
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
    try:
        created = await service.create_pat(
            username=payload.username,
            name=payload.name,
            email=payload.email,
            commit=False,
        )
        await gateway_binding.sync_pat_creation(created, created.token)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gateway sync failed after PAT creation: {exc}",
        ) from exc
    return created


@router.get("/pats", response_model=PATListResponse)
async def list_pats(
    username: str,
    service: AuthnService = Depends(get_authn_service),
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> PATListResponse:
    require_self_or_admin(caller, username=username)
    return PATListResponse(items=await service.list_pats(username=username))


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
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gateway sync failed after PAT revocation: {exc}",
        ) from exc
    return revoked
