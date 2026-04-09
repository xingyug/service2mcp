"""Unit tests for HTTPGatewayAdminClient, focusing on error-handling edge cases."""

from __future__ import annotations

import httpx
import pytest

from apps.access_control.gateway_binding.client import HTTPGatewayAdminClient


def _make_transport(status_code: int, json_body: dict | None = None) -> httpx.MockTransport:
    """Build a MockTransport that always returns the given status and body."""

    def handler(request: httpx.Request) -> httpx.Response:
        content = b""
        headers: dict[str, str] = {}
        if json_body is not None:
            import json as _json

            content = _json.dumps(json_body).encode()
            headers["content-type"] = "application/json"
        return httpx.Response(status_code=status_code, content=content, headers=headers)

    return httpx.MockTransport(handler)


def _make_client(status_code: int, json_body: dict | None = None) -> HTTPGatewayAdminClient:
    transport = _make_transport(status_code, json_body)
    inner = httpx.AsyncClient(base_url="http://gateway:9180", transport=transport)
    return HTTPGatewayAdminClient(base_url="http://gateway:9180", client=inner)


class TestDeleteIdempotency:
    """DELETE operations should treat 404 as success (idempotent delete)."""

    @pytest.mark.asyncio
    async def test_delete_route_404_is_idempotent(self) -> None:
        """Deleting a route that no longer exists should not raise."""
        client = _make_client(404, {"message": "not found"})
        await client.delete_route("nonexistent-route")

    @pytest.mark.asyncio
    async def test_delete_consumer_404_is_idempotent(self) -> None:
        """Deleting a consumer that no longer exists should not raise."""
        client = _make_client(404, {"message": "not found"})
        await client.delete_consumer("nonexistent-consumer")

    @pytest.mark.asyncio
    async def test_delete_policy_binding_404_is_idempotent(self) -> None:
        """Deleting a policy binding that no longer exists should not raise."""
        client = _make_client(404, {"message": "not found"})
        await client.delete_policy_binding("nonexistent-binding")

    @pytest.mark.asyncio
    async def test_delete_route_500_still_raises(self) -> None:
        """Server errors on DELETE should still raise."""
        client = _make_client(500, {"message": "server error"})
        with pytest.raises(httpx.HTTPStatusError):
            await client.delete_route("some-route")

    @pytest.mark.asyncio
    async def test_get_request_404_still_raises(self) -> None:
        """Non-DELETE methods should still raise on 404."""
        client = _make_client(404, {"message": "not found"})
        with pytest.raises(httpx.HTTPStatusError):
            await client.list_routes()
