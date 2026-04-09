"""Shared fixtures for the security test suite .

Provides JWT/PAT token helpers, a FastAPI test client wired to the compiler
API, and mock database session overrides so tests run without a real DB.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.access_control.authn.service import JWTSettings
from apps.compiler_api.main import create_app

# ---------------------------------------------------------------------------
# JWT secret shared by all security tests — matches root conftest env var.
# ---------------------------------------------------------------------------
TEST_JWT_SECRET = "test-jwt-secret-for-ci"
TEST_JWT_SETTINGS = JWTSettings(secret=TEST_JWT_SECRET)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _b64encode_json(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64encode_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def build_jwt(
    claims: dict[str, Any],
    *,
    secret: str = TEST_JWT_SECRET,
    header: dict[str, Any] | None = None,
) -> str:
    """Build an HS256 JWT with the given claims.

    The default ``header`` uses ``{"alg": "HS256", "typ": "JWT"}``.
    Override *header* to test unsupported algorithms or malformed headers.
    """
    hdr = header or {"alg": "HS256", "typ": "JWT"}
    header_segment = _b64encode_json(hdr)
    payload_segment = _b64encode_json(claims)
    signing_input = f"{header_segment}.{payload_segment}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    sig_segment = _b64encode_bytes(signature)
    return f"{header_segment}.{payload_segment}.{sig_segment}"


def build_valid_jwt(
    *,
    subject: str = "test-user",
    roles: list[str] | None = None,
    tenant: str | None = None,
    environment: str | None = None,
    extra_claims: dict[str, Any] | None = None,
    lifetime_seconds: int = 300,
    secret: str = TEST_JWT_SECRET,
) -> str:
    """Build a valid, non-expired HS256 JWT for test use."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + lifetime_seconds,
        "roles": roles or ["user"],
    }
    if tenant is not None:
        claims["tenant"] = tenant
    if environment is not None:
        claims["environment"] = environment
    if extra_claims:
        claims.update(extra_claims)
    return build_jwt(claims, secret=secret)


def build_expired_jwt(*, subject: str = "test-user", secret: str = TEST_JWT_SECRET) -> str:
    """Build an HS256 JWT that expired 60 s ago."""
    now = int(time.time())
    return build_jwt(
        {"sub": subject, "iat": now - 120, "exp": now - 60, "roles": ["user"]},
        secret=secret,
    )


def build_wrong_key_jwt(*, subject: str = "test-user") -> str:
    """Build a JWT signed with a different secret."""
    return build_valid_jwt(subject=subject, secret="wrong-secret-key-totally-different")


def auth_header(token: str) -> dict[str, str]:
    """Return an ``Authorization: Bearer <token>`` header dict."""
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Mock DB session factory
# ---------------------------------------------------------------------------


def _make_mock_session() -> AsyncMock:
    """Create an ``AsyncSession`` mock with sensible defaults.

    The mock is configured so that common ORM patterns (scalar, all,
    scalars, etc.) return ``None`` or empty lists rather than crashing
    with pydantic validation errors.
    """
    session = AsyncMock(spec=AsyncSession)

    # Default execute() result: behaves like an empty result set.
    _empty_result = MagicMock()
    _empty_result.scalar_one_or_none.return_value = None
    _empty_result.scalar.return_value = None
    _empty_result.scalars.return_value.all.return_value = []
    _empty_result.scalars.return_value.first.return_value = None
    _empty_result.all.return_value = []
    _empty_result.first.return_value = None
    _empty_result.one_or_none.return_value = None
    _empty_result.fetchall.return_value = []
    session.execute = AsyncMock(return_value=_empty_result)

    # scalars() shorthand on session — returns a sync result proxy.
    _scalars_proxy = MagicMock()
    _scalars_proxy.all.return_value = []
    _scalars_proxy.first.return_value = None
    session.scalars = AsyncMock(return_value=_scalars_proxy)

    # scalar() shorthand on session
    session.scalar = AsyncMock(return_value=None)

    # get() — used by repositories for PK lookups
    session.get = AsyncMock(return_value=None)

    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = MagicMock()

    return session


@pytest.fixture
def mock_session() -> AsyncMock:
    return _make_mock_session()


# ---------------------------------------------------------------------------
# FastAPI test app + async HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
def security_app(mock_session: AsyncMock):
    """Create a compiler-API app with mocked DB dependencies.

    Patches ``get_db_session`` to yield the mock session, and supplies
    no-op dispatcher / route publisher so routes can be exercised without
    a real database or Celery broker.
    """
    mock_factory = MagicMock(spec=async_sessionmaker)

    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    dispatcher = AsyncMock()
    dispatcher.enqueue = AsyncMock()

    publisher = AsyncMock()
    publisher.sync = AsyncMock(return_value=None)
    publisher.delete = AsyncMock(return_value=None)
    publisher.rollback = AsyncMock(return_value=None)

    app = create_app(
        session_factory=mock_factory,
        compilation_dispatcher=dispatcher,
        route_publisher=publisher,
        jwt_settings=TEST_JWT_SETTINGS,
    )

    # Build async-generator dependency overrides (matching the real signature).
    async def _mock_db_session():
        yield mock_session

    from apps.access_control.db import get_db_session as _ac_get_db_session
    from apps.compiler_api.db import get_db_session as _compiler_get_db_session

    app.dependency_overrides[_compiler_get_db_session] = _mock_db_session
    app.dependency_overrides[_ac_get_db_session] = _mock_db_session

    return app


@pytest.fixture
async def client(security_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=security_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
