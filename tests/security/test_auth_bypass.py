"""Authentication bypass tests.

Verifies that every protected compiler-API route returns 401 when the
request lacks a valid token, and that various invalid token forms
(expired, wrong key, malformed, empty) are correctly rejected.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.security.conftest import (
    auth_header,
    build_expired_jwt,
    build_jwt,
    build_valid_jwt,
    build_wrong_key_jwt,
)

pytestmark = pytest.mark.security

# ---------------------------------------------------------------------------
# All protected routes that require ``require_authenticated_caller``
# ---------------------------------------------------------------------------
_PROTECTED_ROUTES: list[tuple[str, str]] = [
    # compilations
    ("POST", "/api/v1/compilations"),
    ("GET", "/api/v1/compilations"),
    ("GET", f"/api/v1/compilations/{uuid.uuid4()}"),
    ("POST", f"/api/v1/compilations/{uuid.uuid4()}/retry"),
    ("POST", f"/api/v1/compilations/{uuid.uuid4()}/rollback"),
    # artifacts
    ("POST", "/api/v1/artifacts"),
    ("GET", "/api/v1/artifacts/svc-1/versions"),
    ("GET", "/api/v1/artifacts/svc-1/versions/1"),
    ("PUT", "/api/v1/artifacts/svc-1/versions/1"),
    ("DELETE", "/api/v1/artifacts/svc-1/versions/1"),
    ("POST", "/api/v1/artifacts/svc-1/versions/1/activate"),
    ("GET", "/api/v1/artifacts/svc-1/diff?from=1&to=2"),
    # services
    ("GET", "/api/v1/services"),
    ("GET", "/api/v1/services/dashboard/summary"),
    ("GET", "/api/v1/services/svc-1"),
    # workflows
    ("GET", "/api/v1/workflows/svc-1/v/1"),
    ("POST", "/api/v1/workflows/svc-1/v/1/transition"),
    ("PUT", "/api/v1/workflows/svc-1/v/1/notes"),
]

_SSE_ROUTES: list[tuple[str, str]] = [
    ("GET", f"/api/v1/compilations/{uuid.uuid4()}/events"),
]


# ---------------------------------------------------------------------------
# No-token tests
# ---------------------------------------------------------------------------


class TestNoToken:
    """Requests without any Authorization header must be rejected."""

    @pytest.mark.parametrize(
        ("method", "path"),
        _PROTECTED_ROUTES,
        ids=[p for _, p in _PROTECTED_ROUTES],
    )
    async def test_returns_401_without_token(
        self, client: AsyncClient, method: str, path: str
    ) -> None:
        resp = await client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} should return 401"

    @pytest.mark.parametrize(("method", "path"), _SSE_ROUTES, ids=[p for _, p in _SSE_ROUTES])
    async def test_sse_returns_401_without_token(
        self, client: AsyncClient, method: str, path: str
    ) -> None:
        resp = await client.request(method, path)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Expired token
# ---------------------------------------------------------------------------


class TestExpiredToken:
    @pytest.mark.parametrize(("method", "path"), _PROTECTED_ROUTES[:5])
    async def test_expired_jwt_rejected(self, client: AsyncClient, method: str, path: str) -> None:
        token = build_expired_jwt()
        resp = await client.request(method, path, headers=auth_header(token))
        assert resp.status_code == 401

    async def test_sse_expired_jwt_rejected(self, client: AsyncClient) -> None:
        token = build_expired_jwt()
        resp = await client.get(
            f"/api/v1/compilations/{uuid.uuid4()}/events",
            headers=auth_header(token),
        )
        assert resp.status_code == 401

    async def test_sse_expired_jwt_in_query_string_rejected(self, client: AsyncClient) -> None:
        """Regression: SSE query-string tokens must still be validated."""
        token = build_expired_jwt()
        resp = await client.get(
            f"/api/v1/compilations/{uuid.uuid4()}/events?token={token}",
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Wrong signing key
# ---------------------------------------------------------------------------


class TestWrongKeyToken:
    @pytest.mark.parametrize(("method", "path"), _PROTECTED_ROUTES[:5])
    async def test_wrong_key_jwt_rejected(
        self, client: AsyncClient, method: str, path: str
    ) -> None:
        token = build_wrong_key_jwt()
        resp = await client.request(method, path, headers=auth_header(token))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Malformed tokens
# ---------------------------------------------------------------------------


class TestMalformedToken:
    async def test_missing_bearer_prefix(self, client: AsyncClient) -> None:
        token = build_valid_jwt()
        resp = await client.get(
            "/api/v1/compilations",
            headers={"Authorization": token},  # no "Bearer " prefix
        )
        assert resp.status_code == 401

    async def test_empty_bearer_value(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/compilations",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

    async def test_random_garbage_token(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/compilations",
            headers=auth_header("not.a.valid.jwt.at.all"),
        )
        assert resp.status_code == 401

    async def test_two_segment_token(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/compilations",
            headers=auth_header("header.payload"),
        )
        assert resp.status_code == 401

    async def test_unsupported_algorithm(self, client: AsyncClient) -> None:
        """Token signed with RS256 header but HS256 body should be rejected."""
        token = build_jwt(
            {"sub": "user", "iat": 1, "exp": 99999999999, "roles": ["user"]},
            header={"alg": "RS256", "typ": "JWT"},
        )
        resp = await client.get("/api/v1/compilations", headers=auth_header(token))
        assert resp.status_code == 401

    async def test_jwt_missing_subject(self, client: AsyncClient) -> None:
        """A JWT without a 'sub' claim must be rejected."""
        import time

        now = int(time.time())
        token = build_jwt({"iat": now, "exp": now + 300, "roles": ["user"]})
        resp = await client.get("/api/v1/compilations", headers=auth_header(token))
        assert resp.status_code == 401

    async def test_jwt_empty_subject(self, client: AsyncClient) -> None:
        import time

        now = int(time.time())
        token = build_jwt({"sub": "", "iat": now, "exp": now + 300, "roles": ["user"]})
        resp = await client.get("/api/v1/compilations", headers=auth_header(token))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Health endpoints remain unauthenticated
# ---------------------------------------------------------------------------


class TestHealthEndpoints:
    async def test_healthz_no_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert resp.status_code == 200

    async def test_readyz_no_auth(self, client: AsyncClient) -> None:
        """readyz may return 200 or 503 but must not return 401."""
        resp = await client.get("/readyz")
        assert resp.status_code in {200, 503}
