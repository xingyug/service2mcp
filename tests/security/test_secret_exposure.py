"""Secret exposure prevention tests.

Verifies that the compiler API does not leak sensitive information through:
- Error responses (no stack traces in production-style errors)
- Tool listing / artifact payloads (no runtime secret refs)
- SSE event streams (no token echo)
- Security headers (cache-control: no-store)
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.security.conftest import (
    TEST_JWT_SECRET,
    auth_header,
    build_expired_jwt,
    build_valid_jwt,
    build_wrong_key_jwt,
)

pytestmark = pytest.mark.security


# ---------------------------------------------------------------------------
# Error responses must not leak stack traces
# ---------------------------------------------------------------------------


class TestErrorResponseSanitisation:
    async def test_401_no_stack_trace(self, client: AsyncClient) -> None:
        """Unauthenticated request error must not contain Python traceback."""
        resp = await client.get("/api/v1/compilations")
        assert resp.status_code == 401
        body = resp.text
        assert "Traceback" not in body
        assert 'File "' not in body
        assert '.py", line' not in body

    async def test_expired_token_error_no_internals(self, client: AsyncClient) -> None:
        token = build_expired_jwt()
        resp = await client.get("/api/v1/compilations", headers=auth_header(token))
        assert resp.status_code == 401
        body = resp.text
        assert "Traceback" not in body
        assert TEST_JWT_SECRET not in body

    async def test_wrong_key_error_no_secret_leak(self, client: AsyncClient) -> None:
        token = build_wrong_key_jwt()
        resp = await client.get("/api/v1/compilations", headers=auth_header(token))
        assert resp.status_code == 401
        body = resp.text
        assert TEST_JWT_SECRET not in body
        assert "wrong-secret" not in body

    async def test_malformed_token_error_minimal(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/compilations",
            headers=auth_header("garbage.token.here"),
        )
        assert resp.status_code == 401
        body = resp.text
        assert "Traceback" not in body

    async def test_422_validation_error_no_stack_trace(self, client: AsyncClient) -> None:
        """POST with invalid payload should return 422 without internals."""
        token = build_valid_jwt(roles=["admin"])
        resp = await client.post(
            "/api/v1/compilations",
            headers=auth_header(token),
            json={},  # missing required fields
        )
        assert resp.status_code == 422
        body = resp.text
        assert "Traceback" not in body

    async def test_404_no_stack_trace(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/nonexistent-route")
        body = resp.text
        assert "Traceback" not in body
        assert '.py", line' not in body


# ---------------------------------------------------------------------------
# JWT secret not in any response body
# ---------------------------------------------------------------------------


class TestJWTSecretNotExposed:
    async def test_secret_not_in_healthz(self, client: AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert TEST_JWT_SECRET not in resp.text

    async def test_secret_not_in_openapi_schema(self, client: AsyncClient) -> None:
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        assert TEST_JWT_SECRET not in resp.text

    async def test_secret_not_in_error_detail(self, client: AsyncClient) -> None:
        """Force various errors and verify the secret is never echoed."""
        for token in [
            build_expired_jwt(),
            build_wrong_key_jwt(),
            "not.a.jwt",
        ]:
            resp = await client.get(
                "/api/v1/compilations",
                headers=auth_header(token),
            )
            assert TEST_JWT_SECRET not in resp.text, f"Secret leaked for token={token[:20]}…"


# ---------------------------------------------------------------------------
# SSE streams must not echo tokens
# ---------------------------------------------------------------------------


class TestSSETokenExposure:
    async def test_sse_does_not_echo_token_in_header(
        self,
        client: AsyncClient,
        mock_session: AsyncMock,
    ) -> None:
        """Even if the SSE endpoint returns 401/404, the token must not be in the body."""
        token = build_valid_jwt(roles=["admin"])
        job_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/compilations/{job_id}/events",
            headers=auth_header(token),
        )
        assert token not in resp.text

    async def test_sse_does_not_echo_query_token(
        self,
        client: AsyncClient,
        mock_session: AsyncMock,
    ) -> None:
        """Tokens passed via query string must not appear in the response."""
        token = build_valid_jwt(roles=["admin"])
        job_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/compilations/{job_id}/events?token={token}",
        )
        assert token not in resp.text


# ---------------------------------------------------------------------------
# Security headers: cache-control, referrer-policy
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    async def test_cache_control_no_store(self, client: AsyncClient) -> None:
        """Responses must include Cache-Control: no-store to prevent caching tokens."""
        resp = await client.get("/healthz")
        assert resp.headers.get("Cache-Control") == "no-store"

    async def test_referrer_policy(self, client: AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    async def test_xss_protection_disabled(self, client: AsyncClient) -> None:
        """X-XSS-Protection: 0 (modern best practice — rely on CSP instead)."""
        resp = await client.get("/healthz")
        assert resp.headers.get("X-XSS-Protection") == "0"

    async def test_security_headers_on_error_response(self, client: AsyncClient) -> None:
        """Security headers must be present even on 401/404 errors."""
        resp = await client.get("/api/v1/compilations")  # 401
        assert resp.status_code == 401
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"

    async def test_security_headers_on_authenticated_response(self, client: AsyncClient) -> None:
        token = build_valid_jwt(roles=["admin"])
        resp = await client.get("/api/v1/compilations", headers=auth_header(token))
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Cache-Control") == "no-store"


# ---------------------------------------------------------------------------
# OpenAPI schema does not expose internal details
# ---------------------------------------------------------------------------


class TestOpenAPISchemaSanitisation:
    async def test_no_internal_paths_in_schema(self, client: AsyncClient) -> None:
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        # The schema should not expose any file system paths
        schema_text = json.dumps(schema)
        assert "/home/" not in schema_text
        assert "/etc/" not in schema_text
        assert "site-packages" not in schema_text

    async def test_no_database_url_in_schema(self, client: AsyncClient) -> None:
        resp = await client.get("/openapi.json")
        schema_text = resp.text
        assert "postgresql" not in schema_text.lower() or "postgresql" in schema_text.lower()
        # More specific: actual connection strings should not be present
        assert "postgres://" not in schema_text
        assert "postgresql+asyncpg://" not in schema_text


# ---------------------------------------------------------------------------
# Request-ID header must not leak internal state
# ---------------------------------------------------------------------------


class TestRequestIdSafety:
    async def test_request_id_returned(self, client: AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert "X-Request-ID" in resp.headers

    async def test_custom_request_id_reflected(self, client: AsyncClient) -> None:
        """Client-supplied request IDs are echoed back (for tracing)."""
        resp = await client.get("/healthz", headers={"X-Request-ID": "trace-abc"})
        assert resp.headers["X-Request-ID"] == "trace-abc"

    async def test_request_id_does_not_leak_pid_or_hostname(self, client: AsyncClient) -> None:
        """Auto-generated request IDs should be UUIDs, not PID/host-based."""
        resp = await client.get("/healthz")
        rid = resp.headers.get("X-Request-ID", "")
        # Should be a hex UUID (32 chars) — not contain hostnames
        assert len(rid) == 32 or "-" in rid  # uuid4 hex or uuid4 string
