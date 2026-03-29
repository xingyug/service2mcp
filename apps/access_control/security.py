"""Shared authentication and authorization helpers for access-control routes."""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.authn.service import AuthenticationError, AuthnService
from apps.access_control.db import get_db_session

_ADMIN_ROLES = frozenset({"admin", "administrator", "superuser"})


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
    return await _validate_token(request, session, token)


async def require_sse_caller(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> TokenPrincipalResponse:
    """Validate a token from the ``token`` query parameter.

    Browser ``EventSource`` cannot send custom headers, so SSE endpoints
    accept the bearer token as a query parameter instead.
    """

    token = request.query_params.get("token", "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Query parameter 'token' is required for SSE endpoints.",
        )
    return await _validate_token(request, session, token)


async def _validate_token(
    request: Request,
    session: AsyncSession,
    token: str,
) -> TokenPrincipalResponse:
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


def caller_roles(caller: TokenPrincipalResponse) -> set[str]:
    """Normalize the caller's role claims into a lowercased set."""

    raw_roles = caller.claims.get("roles")
    roles: Iterable[object]
    if isinstance(raw_roles, str):
        roles = [raw_roles]
    elif isinstance(raw_roles, list):
        roles = raw_roles
    else:
        roles = []
    return {
        role.strip().lower()
        for role in roles
        if isinstance(role, str) and role.strip()
    }


def caller_is_admin(caller: TokenPrincipalResponse) -> bool:
    """Return whether the caller carries one of the configured admin roles."""

    return bool(caller_roles(caller) & _ADMIN_ROLES)


def require_self_or_admin(
    caller: TokenPrincipalResponse,
    *,
    username: str,
) -> TokenPrincipalResponse:
    """Ensure the caller operates on their own principal or has admin privileges."""

    if caller.subject == username or caller_is_admin(caller):
        return caller
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Caller is not allowed to manage another user's PATs.",
    )


def require_admin_principal(caller: TokenPrincipalResponse) -> TokenPrincipalResponse:
    """Ensure the caller carries an admin role."""

    if caller_is_admin(caller):
        return caller
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin role required.",
    )


async def require_admin_caller(
    caller: TokenPrincipalResponse = Depends(require_authenticated_caller),
) -> TokenPrincipalResponse:
    """Dependency wrapper that only admits admin callers."""

    return require_admin_principal(caller)
