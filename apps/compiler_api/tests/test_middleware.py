"""Tests for compiler API middleware (request ID, security headers, logging)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from apps.compiler_api.middleware import (
    _SECURITY_HEADERS,
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)


@pytest.fixture
def middleware_app() -> FastAPI:
    """Minimal app with all three middleware layers wired."""
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/test")
    async def _test_endpoint() -> JSONResponse:
        return JSONResponse({"ok": True})

    return app


@pytest.fixture
async def client(middleware_app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=middleware_app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestRequestIdMiddleware:
    async def test_generates_request_id_when_missing(self, client: AsyncClient) -> None:
        resp = await client.get("/test")
        assert resp.status_code == 200
        request_id = resp.headers.get(REQUEST_ID_HEADER)
        assert request_id is not None
        assert len(request_id) == 32  # uuid4 hex

    async def test_preserves_incoming_request_id(self, client: AsyncClient) -> None:
        custom_id = "my-custom-request-id-123"
        resp = await client.get("/test", headers={REQUEST_ID_HEADER: custom_id})
        assert resp.status_code == 200
        assert resp.headers[REQUEST_ID_HEADER] == custom_id

    async def test_different_requests_get_different_ids(self, client: AsyncClient) -> None:
        r1 = await client.get("/test")
        r2 = await client.get("/test")
        assert r1.headers[REQUEST_ID_HEADER] != r2.headers[REQUEST_ID_HEADER]


class TestSecurityHeadersMiddleware:
    async def test_security_headers_present(self, client: AsyncClient) -> None:
        resp = await client.get("/test")
        assert resp.status_code == 200
        for header_name, header_value in _SECURITY_HEADERS.items():
            assert resp.headers.get(header_name) == header_value, (
                f"Missing or incorrect {header_name}"
            )

    async def test_does_not_override_existing_headers(self, middleware_app: FastAPI) -> None:
        """If the endpoint sets a security header, middleware should not override it."""

        @middleware_app.get("/custom-cache")
        async def _custom() -> JSONResponse:
            return JSONResponse(
                {"ok": True},
                headers={"Cache-Control": "max-age=3600"},
            )

        transport = ASGITransport(app=middleware_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/custom-cache")
            assert resp.headers["Cache-Control"] == "max-age=3600"


class TestRequestLoggingMiddleware:
    async def test_logs_request_completion(self, client: AsyncClient) -> None:
        with patch("apps.compiler_api.middleware._logger") as mock_logger:
            resp = await client.get("/test")
            assert resp.status_code == 200

            mock_logger.info.assert_called()
            call_args = mock_logger.info.call_args
            assert call_args[0][0] == "request completed"
            extra = call_args[1]["extra"]
            assert extra["method"] == "GET"
            assert extra["path"] == "/test"
            assert extra["status_code"] == 200
            assert "duration_ms" in extra

    async def test_logs_include_request_id(self, client: AsyncClient) -> None:
        with patch("apps.compiler_api.middleware._logger") as mock_logger:
            custom_id = "trace-abc-123"
            await client.get("/test", headers={REQUEST_ID_HEADER: custom_id})

            call_args = mock_logger.info.call_args
            extra = call_args[1]["extra"]
            assert extra["request_id"] == custom_id


class TestMiddlewareIntegration:
    async def test_all_middleware_applied_together(self, client: AsyncClient) -> None:
        resp = await client.get("/test")
        assert resp.status_code == 200

        # Request ID generated
        assert REQUEST_ID_HEADER in resp.headers

        # Security headers present
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"

    async def test_not_found_still_gets_middleware(self, client: AsyncClient) -> None:
        resp = await client.get("/nonexistent")
        assert resp.status_code in {404, 405}
        assert REQUEST_ID_HEADER in resp.headers
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
