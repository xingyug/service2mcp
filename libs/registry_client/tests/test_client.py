"""Unit tests for RegistryClient — error paths, activate_version, and edge cases."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from libs.registry_client.client import RegistryClient, RegistryClientError


def _version_response_json(
    *,
    service_id: str = "test-svc",
    version_number: int = 1,
    is_active: bool = False,
) -> dict[str, Any]:
    """Minimal valid ArtifactVersionResponse payload."""
    return {
        "id": str(uuid4()),
        "service_id": service_id,
        "version_number": version_number,
        "is_active": is_active,
        "ir_json": {
            "source_hash": "a" * 64,
            "protocol": "rest",
            "service_name": service_id,
            "base_url": "https://example.test",
        },
        "compiler_version": "0.1.0",
        "created_at": datetime.now(tz=UTC).isoformat(),
    }


def _mock_transport(
    status: int,
    body: Any = None,
    *,
    capture: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """Build an httpx.MockTransport that returns a fixed status and body."""
    body_bytes = json.dumps(body).encode() if body is not None else b""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        return httpx.Response(
            status,
            content=body_bytes,
            headers={"content-type": "application/json"} if body is not None else {},
        )

    return httpx.MockTransport(handler)


class TestRegistryClientError:
    @pytest.mark.asyncio
    async def test_ensure_success_raises_on_4xx(self) -> None:
        transport = _mock_transport(404, {"detail": "Not found"})
        http = httpx.AsyncClient(transport=transport, base_url="http://test")
        async with RegistryClient("http://test", client=http) as client:
            with pytest.raises(RegistryClientError, match="404"):
                await client.get_version("test-svc", 1)

    @pytest.mark.asyncio
    async def test_ensure_success_raises_on_5xx(self) -> None:
        transport = _mock_transport(500, {"detail": "Internal error"})
        http = httpx.AsyncClient(transport=transport, base_url="http://test")
        async with RegistryClient("http://test", client=http) as client:
            with pytest.raises(RegistryClientError, match="500"):
                await client.list_versions("test-svc")

    @pytest.mark.asyncio
    async def test_error_wraps_original_http_status_error(self) -> None:
        transport = _mock_transport(422, {"detail": "Unprocessable"})
        http = httpx.AsyncClient(transport=transport, base_url="http://test")
        async with RegistryClient("http://test", client=http) as client:
            with pytest.raises(RegistryClientError) as exc_info:
                await client.delete_version("test-svc", 1)
            assert exc_info.value.__cause__ is not None
            assert isinstance(exc_info.value.__cause__, httpx.HTTPStatusError)


class TestActivateVersion:
    @pytest.mark.asyncio
    async def test_activate_version_sends_post(self) -> None:
        captured: list[httpx.Request] = []
        payload = _version_response_json(is_active=True)
        transport = _mock_transport(200, payload, capture=captured)
        http = httpx.AsyncClient(transport=transport, base_url="http://test")
        async with RegistryClient("http://test", client=http) as client:
            resp = await client.activate_version("test-svc", 1)
        assert resp.is_active is True
        assert len(captured) == 1
        assert captured[0].method == "POST"
        assert "/test-svc/versions/1/activate" in str(captured[0].url)

    @pytest.mark.asyncio
    async def test_activate_version_raises_on_error(self) -> None:
        transport = _mock_transport(404, {"detail": "Not found"})
        http = httpx.AsyncClient(transport=transport, base_url="http://test")
        async with RegistryClient("http://test", client=http) as client:
            with pytest.raises(RegistryClientError):
                await client.activate_version("test-svc", 99)


class TestFilterParams:
    def test_empty_when_no_filters(self) -> None:
        assert RegistryClient._filter_params() == {}

    def test_tenant_only(self) -> None:
        assert RegistryClient._filter_params(tenant="acme") == {"tenant": "acme"}

    def test_environment_only(self) -> None:
        assert RegistryClient._filter_params(environment="prod") == {
            "environment": "prod",
        }

    def test_both_filters(self) -> None:
        result = RegistryClient._filter_params(tenant="acme", environment="staging")
        assert result == {"tenant": "acme", "environment": "staging"}


class TestClientOwnership:
    @pytest.mark.asyncio
    async def test_external_client_not_closed_on_aexit(self) -> None:
        transport = _mock_transport(200, _version_response_json())
        http = httpx.AsyncClient(transport=transport, base_url="http://test")
        async with RegistryClient("http://test", client=http) as client:
            assert client._owns_client is False
        # External client should still be usable after RegistryClient exits.
        resp = await http.get("/healthz")
        assert resp.status_code == 200
        await http.aclose()

    @pytest.mark.asyncio
    async def test_owned_client_closed_on_aexit(self) -> None:
        # When no external client is passed, RegistryClient creates its own.
        client = RegistryClient("http://test")
        assert client._owns_client is True
        await client.aclose()
        # After close, the internal client should be shut down.
        assert client._client.is_closed
