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
    service: AuthnService = Depends(get_authn_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
) -> PATCreateResponse:
    created = await service.create_pat(
        username=payload.username,
        name=payload.name,
        email=payload.email,
    )
    await gateway_binding.sync_pat_creation(created, created.token)
    return created


@router.get("/pats", response_model=PATListResponse)
async def list_pats(
    username: str,
    service: AuthnService = Depends(get_authn_service),
) -> PATListResponse:
    return PATListResponse(items=await service.list_pats(username=username))


@router.post("/pats/{pat_id}/revoke", response_model=PATResponse)
async def revoke_pat(
    pat_id: str,
    service: AuthnService = Depends(get_authn_service),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
) -> PATResponse:
    try:
        from uuid import UUID

        parsed_pat_id = UUID(pat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PAT ID.",
        ) from exc

    revoked = await service.revoke_pat(parsed_pat_id)
    if revoked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PAT not found.")
    await gateway_binding.sync_pat_revocation(revoked.id)
    return revoked
