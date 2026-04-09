"""Shared authentication and authorization helpers for access-control routes."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.authn.service import (
    AuthenticationError,
    AuthnService,
    JWTConfigurationError,
    resolve_jwt_settings,
)
from apps.access_control.db import get_db_session

logger = logging.getLogger(__name__)

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
    token = _extract_bearer_token(auth_header)
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
    """Validate a token from the query string or Authorization header.

    Mitigation: The browser ``EventSource`` API does not support
    custom headers, so SSE endpoints must accept tokens via the query
    string.  This unavoidably exposes the token in browser history, proxy
    logs, and devtools.  To reduce risk we:
      1. Log a warning whenever a query-string token is used.
      2. Warn operators when the token has an ``exp`` claim more than
         300 s in the future (long-lived tokens amplify exposure).
    Prefer issuing short-lived tokens for SSE connections.
    """

    from_query_string = False
    token: str | None = request.query_params.get("token", "").strip() or None
    if token:
        from_query_string = True
    else:
        token = _extract_bearer_token(request.headers.get("Authorization", ""))
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token or query parameter 'token' is required for SSE endpoints.",
        )

    if from_query_string:
        logger.warning("SSE auth: token supplied via query string — consider short-lived tokens")
        _warn_long_lived_query_token(token)

    return await _validate_token(request, session, token)


# Maximum acceptable remaining lifetime (seconds) for query-string tokens.
_SSE_QS_MAX_LIFETIME = 300


def _warn_long_lived_query_token(token: str) -> None:
    """Log a warning if *token* has an ``exp`` claim far in the future.

    Uses unverified decode — the real signature check happens later in
    ``_validate_token``.  We intentionally only warn (never reject) to
    avoid breaking existing integrations.
    """
    try:
        payload = pyjwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256", "RS256"],
        )
    except Exception:  # noqa: BLE001 — best-effort; PATs won't decode
        return

    exp = payload.get("exp")
    if exp is None:
        return

    remaining = float(exp) - time.time()
    if remaining > _SSE_QS_MAX_LIFETIME:
        logger.warning(
            "SSE auth: query-string token has %.0f s remaining lifetime "
            "(> %d s) — long-lived tokens in URLs increase exposure risk",
            remaining,
            _SSE_QS_MAX_LIFETIME,
        )


async def _validate_token(
    request: Request,
    session: AsyncSession,
    token: str,
) -> TokenPrincipalResponse:
    try:
        jwt_settings = resolve_jwt_settings(request.app.state)
    except JWTConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
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
    return {role.strip().lower() for role in roles if isinstance(role, str) and role.strip()}


def _extract_bearer_token(auth_header: str) -> str | None:
    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def caller_is_admin(caller: TokenPrincipalResponse) -> bool:
    """Return whether the caller carries one of the configured admin roles."""

    return bool(caller_roles(caller) & _ADMIN_ROLES)


def require_scope_access(
    caller: TokenPrincipalResponse,
    *,
    tenant: str | None = None,
    environment: str | None = None,
) -> None:
    """Raise HTTPException(403) if caller cannot access the requested scope.

    Admin roles bypass scope checks. Non-admin callers must have matching
    tenant/environment in their token claims.
    """
    if caller_is_admin(caller):
        return

    caller_tenant = caller.claims.get("tenant")
    caller_environment = caller.claims.get("environment")
    caller_tenants: list[str] | None = caller.claims.get("tenants")  # type: ignore[assignment]

    if tenant is not None:
        if caller_tenant and caller_tenant != tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not authorized for tenant {tenant!r}",
            )
        if caller_tenants is not None and tenant not in caller_tenants:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not authorized for tenant {tenant!r}",
            )

    if environment is not None:
        caller_environments: list[str] | None = caller.claims.get("environments")  # type: ignore[assignment]
        if caller_environment and caller_environment != environment:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not authorized for environment {environment!r}",
            )
        if caller_environments is not None and environment not in caller_environments:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not authorized for environment {environment!r}",
            )


def require_self_or_admin(
    caller: TokenPrincipalResponse,
    *,
    username: str,
) -> TokenPrincipalResponse:
    """Ensure the caller operates on their own principal or has admin privileges."""

    principal_username = caller.username or caller.subject
    if principal_username == username or caller_is_admin(caller):
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
