"""Tests for SSE query-string token mitigation in require_sse_caller."""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.authn.service import JWTSettings
from apps.access_control.security import _SSE_QS_MAX_LIFETIME, require_sse_caller

_AUTHN_PATCH = "apps.access_control.security.AuthnService"
_JWT_SETTINGS_PATCH = "apps.access_control.security.resolve_jwt_settings"

_TEST_SECRET = "test-secret-key"


def _make_request(
    *,
    query_token: str | None = None,
    bearer_token: str | None = None,
) -> MagicMock:
    """Return a minimal mock FastAPI Request."""
    req = MagicMock()
    req.query_params = {"token": query_token} if query_token else {}
    req.headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    return req


def _principal(subject: str = "alice") -> TokenPrincipalResponse:
    return TokenPrincipalResponse(
        subject=subject,
        token_type="jwt",
        claims={"sub": subject},
    )


def _make_jwt(exp_offset: int) -> str:
    """Create an HS256 JWT that expires *exp_offset* seconds from now."""
    return pyjwt.encode(
        {"sub": "alice", "exp": int(time.time()) + exp_offset},
        _TEST_SECRET,
        algorithm="HS256",
    )


# -- Helpers to patch the authn layer so require_sse_caller can resolve ------


def _patch_authn(expected: TokenPrincipalResponse):  # noqa: ANN201
    """Return a combined context manager that stubs both resolve_jwt_settings
    and AuthnService so ``require_sse_caller`` can run end-to-end."""
    jwt_settings = JWTSettings(secret=_TEST_SECRET)
    mock_service = AsyncMock()
    mock_service.validate_token.return_value = expected

    class _Ctx:
        def __init__(self) -> None:
            self._stack: list[object] = []
            self.mock_service = mock_service

        def __enter__(self) -> _Ctx:
            p1 = patch(_JWT_SETTINGS_PATCH, return_value=jwt_settings)
            p2 = patch(_AUTHN_PATCH, return_value=mock_service)
            self._stack = [p1.__enter__(), p2.__enter__()]
            return self

        def __exit__(self, *args: object) -> None:
            for p in reversed(self._stack):
                # each is the mock returned; but we need the patcher
                pass
            # Use a cleaner approach: just stop via patch objects
            patch.stopall()

    return _Ctx()


# -- Functional tests --------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_auth_works_with_query_string_token() -> None:
    """SSE auth succeeds when the token is supplied via query string."""
    expected = _principal()
    req = _make_request(query_token="qs-token-123")
    session = AsyncMock()

    with _patch_authn(expected) as ctx:
        result = await require_sse_caller(req, session)

    assert result == expected
    ctx.mock_service.validate_token.assert_called_once_with("qs-token-123")


@pytest.mark.asyncio
async def test_sse_auth_works_with_bearer_header() -> None:
    """SSE auth falls back to Authorization header when no query param."""
    expected = _principal()
    req = _make_request(bearer_token="hdr-token-456")
    session = AsyncMock()

    with _patch_authn(expected) as ctx:
        result = await require_sse_caller(req, session)

    assert result == expected
    ctx.mock_service.validate_token.assert_called_once_with("hdr-token-456")


@pytest.mark.asyncio
async def test_sse_auth_prefers_query_string_over_header() -> None:
    """Query-string token takes precedence over Authorization header."""
    expected = _principal()
    req = _make_request(query_token="qs-tok", bearer_token="hdr-tok")
    session = AsyncMock()

    with _patch_authn(expected) as ctx:
        result = await require_sse_caller(req, session)

    assert result == expected
    ctx.mock_service.validate_token.assert_called_once_with("qs-tok")


@pytest.mark.asyncio
async def test_sse_auth_rejects_missing_token() -> None:
    """SSE auth returns 401 when neither query string nor header present."""
    from fastapi import HTTPException

    req = _make_request()
    session = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await require_sse_caller(req, session)

    assert exc_info.value.status_code == 401


# -- Logging tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_query_token_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A warning is logged when a token is supplied via the query string."""
    expected = _principal()
    req = _make_request(query_token="some-opaque-token")
    session = AsyncMock()

    with _patch_authn(expected), caplog.at_level(logging.WARNING):
        await require_sse_caller(req, session)

    messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("query string" in m for m in messages), (
        f"Expected a query-string warning; got: {messages}"
    )


@pytest.mark.asyncio
async def test_sse_long_lived_query_token_logs_lifetime_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A warning is logged when the query-string JWT expires far in the future."""
    long_lived_jwt = _make_jwt(exp_offset=3600)  # 1 hour — well above 300 s
    expected = _principal()
    req = _make_request(query_token=long_lived_jwt)
    session = AsyncMock()

    with _patch_authn(expected), caplog.at_level(logging.WARNING):
        await require_sse_caller(req, session)

    messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("remaining lifetime" in m for m in messages), (
        f"Expected a long-lived-token warning; got: {messages}"
    )


@pytest.mark.asyncio
async def test_sse_short_lived_query_token_does_not_log_lifetime_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No lifetime warning when the JWT expires within the threshold."""
    short_lived_jwt = _make_jwt(exp_offset=60)  # 60 s — well within 300 s
    expected = _principal()
    req = _make_request(query_token=short_lived_jwt)
    session = AsyncMock()

    with _patch_authn(expected), caplog.at_level(logging.WARNING):
        await require_sse_caller(req, session)

    lifetime_warnings = [
        r.message
        for r in caplog.records
        if r.levelno >= logging.WARNING and "remaining lifetime" in r.message
    ]
    assert lifetime_warnings == [], (
        f"Unexpected lifetime warning for short-lived token: {lifetime_warnings}"
    )


@pytest.mark.asyncio
async def test_sse_bearer_header_does_not_log_query_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No query-string warning when token comes from Authorization header."""
    expected = _principal()
    req = _make_request(bearer_token="hdr-tok")
    session = AsyncMock()

    with _patch_authn(expected), caplog.at_level(logging.WARNING):
        await require_sse_caller(req, session)

    qs_warnings = [
        r.message
        for r in caplog.records
        if r.levelno >= logging.WARNING and "query string" in r.message
    ]
    assert qs_warnings == [], f"Unexpected query-string warning: {qs_warnings}"


def test_sse_qs_max_lifetime_constant() -> None:
    """Sanity-check that the threshold constant matches the documented 5 min."""
    assert _SSE_QS_MAX_LIFETIME == 300
