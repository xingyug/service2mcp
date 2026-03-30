"""Tests for apps/gateway_admin_mock/main.py — gateway admin mock endpoints."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from apps.gateway_admin_mock.main import (
    _forward_headers,
    _response_headers,
    _select_route_id,
    _service_key,
    _upstream_base_url,
    create_app,
)


@pytest.fixture
def gateway_app():
    return create_app()


@pytest.fixture
async def client(gateway_app):
    transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class TestHealthz:
    async def test_healthz(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestConsumers:
    async def test_list_consumers_empty(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/admin/consumers")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_upsert_consumer(self, client: httpx.AsyncClient) -> None:
        resp = await client.put(
            "/admin/consumers/c1",
            json={
                "username": "alice",
                "credential": "key-123",
                "metadata": {"role": "admin"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["consumer_id"] == "c1"
        assert data["username"] == "alice"
        assert data["credential"] == "key-123"
        assert data["metadata"] == {"role": "admin"}

    async def test_list_consumers_after_upsert(self, client: httpx.AsyncClient) -> None:
        await client.put(
            "/admin/consumers/c1",
            json={"username": "alice", "credential": "key-123", "metadata": {}},
        )
        resp = await client.get("/admin/consumers")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["consumer_id"] == "c1"

    async def test_delete_consumer(self, client: httpx.AsyncClient) -> None:
        await client.put(
            "/admin/consumers/c1",
            json={"username": "alice", "credential": "key-123", "metadata": {}},
        )
        resp = await client.delete("/admin/consumers/c1")
        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted"}

        resp = await client.get("/admin/consumers")
        assert resp.json()["items"] == []

    async def test_delete_nonexistent_consumer(self, client: httpx.AsyncClient) -> None:
        resp = await client.delete("/admin/consumers/nonexistent")
        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted"}


class TestPolicyBindings:
    async def test_list_policy_bindings_empty(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/admin/policy-bindings")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_upsert_policy_binding(self, client: httpx.AsyncClient) -> None:
        resp = await client.put(
            "/admin/policy-bindings/pb1",
            json={"document": {"policy": "rate-limit", "config": {"rpm": 100}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["binding_id"] == "pb1"
        assert data["document"]["policy"] == "rate-limit"

    async def test_list_policy_bindings_after_upsert(self, client: httpx.AsyncClient) -> None:
        await client.put(
            "/admin/policy-bindings/pb1",
            json={"document": {"policy": "rate-limit"}},
        )
        resp = await client.get("/admin/policy-bindings")
        items = resp.json()["items"]
        assert len(items) == 1

    async def test_delete_policy_binding(self, client: httpx.AsyncClient) -> None:
        await client.put(
            "/admin/policy-bindings/pb1",
            json={"document": {"policy": "rate-limit"}},
        )
        resp = await client.delete("/admin/policy-bindings/pb1")
        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted"}

        resp = await client.get("/admin/policy-bindings")
        assert resp.json()["items"] == []

    async def test_delete_nonexistent_policy_binding(self, client: httpx.AsyncClient) -> None:
        resp = await client.delete("/admin/policy-bindings/nonexistent")
        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted"}


class TestRoutes:
    async def test_list_routes_empty(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/admin/routes")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_upsert_route(self, client: httpx.AsyncClient) -> None:
        resp = await client.put(
            "/admin/routes/r1",
            json={"document": {"target_service": {"name": "svc", "port": 8080}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["route_id"] == "r1"
        assert data["document"]["target_service"]["name"] == "svc"

    async def test_upsert_route_rejects_non_numeric_target_port(
        self, client: httpx.AsyncClient
    ) -> None:
        resp = await client.put(
            "/admin/routes/r1",
            json={"document": {"target_service": {"name": "svc", "port": "oops"}}},
        )

        assert resp.status_code == 422

    async def test_upsert_route_rejects_missing_target_service_name(
        self, client: httpx.AsyncClient
    ) -> None:
        resp = await client.put(
            "/admin/routes/r1",
            json={"document": {"target_service": {"port": 8080}}},
        )

        assert resp.status_code == 422

    async def test_delete_route(self, client: httpx.AsyncClient) -> None:
        await client.put(
            "/admin/routes/r1",
            json={"document": {"target_service": {"name": "svc", "port": 8080}}},
        )
        resp = await client.delete("/admin/routes/r1")
        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted"}


class TestProxyGateway:
    async def test_route_not_found(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/gateway/my-service")
        assert resp.status_code == 404
        data = resp.json()
        assert "not configured" in data["detail"]

    async def test_route_not_found_with_version_header(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(
            "/gateway/my-service",
            headers={"x-tool-compiler-version": "2"},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert "my-service-v2" in data["detail"]

    async def test_proxy_with_upstream_override(
        self, gateway_app, client: httpx.AsyncClient
    ) -> None:
        # First create a route
        await client.put(
            "/admin/routes/catalog-active",
            json={
                "document": {
                    "target_service": {"name": "catalog", "port": 8080},
                }
            },
        )

        # Set up upstream override with mock transport
        mock_transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"proxied": True})
        )
        gateway_app.state.upstream_overrides["catalog:8080"] = {
            "base_url": "http://mock-catalog:8080",
            "transport": mock_transport,
        }

        resp = await client.get("/gateway/catalog/items/123")
        assert resp.status_code == 200
        assert resp.json() == {"proxied": True}

    async def test_proxy_with_upstream_override_namespace(
        self, gateway_app, client: httpx.AsyncClient
    ) -> None:
        await client.put(
            "/admin/routes/catalog-active",
            json={
                "document": {
                    "target_service": {"name": "catalog", "namespace": "prod", "port": 8080},
                }
            },
        )

        mock_transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"ns_proxied": True})
        )
        gateway_app.state.upstream_overrides["catalog.prod:8080"] = {
            "base_url": "http://mock-catalog:8080",
            "transport": mock_transport,
        }

        resp = await client.get("/gateway/catalog")
        assert resp.status_code == 200
        assert resp.json() == {"ns_proxied": True}

    async def test_proxy_http_error_returns_502(
        self, gateway_app, client: httpx.AsyncClient
    ) -> None:
        await client.put(
            "/admin/routes/catalog-active",
            json={
                "document": {
                    "target_service": {"name": "catalog", "port": 8080},
                }
            },
        )

        def raise_error(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        mock_transport = httpx.MockTransport(raise_error)
        gateway_app.state.upstream_overrides["catalog:8080"] = {
            "base_url": "http://mock-catalog:8080",
            "transport": mock_transport,
        }

        resp = await client.get("/gateway/catalog/test")
        assert resp.status_code == 502
        data = resp.json()
        assert "Failed to reach" in data["detail"]

    async def test_proxy_forwards_query_params(
        self, gateway_app, client: httpx.AsyncClient
    ) -> None:
        await client.put(
            "/admin/routes/catalog-active",
            json={
                "document": {
                    "target_service": {"name": "catalog", "port": 8080},
                }
            },
        )

        captured_requests: list[httpx.Request] = []

        def capture_request(req: httpx.Request) -> httpx.Response:
            captured_requests.append(req)
            return httpx.Response(200, json={"ok": True})

        mock_transport = httpx.MockTransport(capture_request)
        gateway_app.state.upstream_overrides["catalog:8080"] = {
            "base_url": "http://mock-catalog:8080",
            "transport": mock_transport,
        }

        resp = await client.get("/gateway/catalog/items?view=detail&limit=10")
        assert resp.status_code == 200
        assert len(captured_requests) == 1
        assert "view=detail" in str(captured_requests[0].url)

    async def test_proxy_post_method(self, gateway_app, client: httpx.AsyncClient) -> None:
        await client.put(
            "/admin/routes/catalog-active",
            json={
                "document": {
                    "target_service": {"name": "catalog", "port": 8080},
                }
            },
        )

        mock_transport = httpx.MockTransport(
            lambda req: httpx.Response(201, json={"created": True})
        )
        gateway_app.state.upstream_overrides["catalog:8080"] = {
            "base_url": "http://mock-catalog:8080",
            "transport": mock_transport,
        }

        resp = await client.post("/gateway/catalog/items", json={"name": "test"})
        assert resp.status_code == 201

    async def test_proxy_respects_prefix_match(
        self, gateway_app, client: httpx.AsyncClient
    ) -> None:
        await client.put(
            "/admin/routes/catalog-active",
            json={
                "document": {
                    "match": {"prefix": "/catalog/versions/v2"},
                    "target_service": {"name": "catalog", "port": 8080},
                }
            },
        )

        mock_transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"proxied": True})
        )
        gateway_app.state.upstream_overrides["catalog:8080"] = {
            "base_url": "http://mock-catalog:8080",
            "transport": mock_transport,
        }

        resp = await client.get("/gateway/catalog/items/123")
        assert resp.status_code == 404
        assert "did not match" in resp.json()["detail"]

    async def test_proxy_respects_header_match(
        self, gateway_app, client: httpx.AsyncClient
    ) -> None:
        await client.put(
            "/admin/routes/catalog-v2",
            json={
                "document": {
                    "match": {"headers": {"x-tool-compiler-version": "9"}},
                    "target_service": {"name": "catalog", "port": 8080},
                }
            },
        )

        mock_transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"proxied": True})
        )
        gateway_app.state.upstream_overrides["catalog:8080"] = {
            "base_url": "http://mock-catalog:8080",
            "transport": mock_transport,
        }

        resp = await client.get(
            "/gateway/catalog/items/123",
            headers={"x-tool-compiler-version": "2"},
        )
        assert resp.status_code == 404
        assert "did not match" in resp.json()["detail"]

    async def test_proxy_invalid_stored_route_document_returns_500(
        self, gateway_app, client: httpx.AsyncClient
    ) -> None:
        gateway_app.state.routes["catalog-active"] = {
            "route_id": "catalog-active",
            "document": {"target_service": {"name": "catalog", "port": "oops"}},
        }

        resp = await client.get("/gateway/catalog")

        assert resp.status_code == 500
        data = resp.json()
        assert "Stored route document is invalid" in data["detail"]

    async def test_proxy_missing_target_service_returns_500(
        self, gateway_app, client: httpx.AsyncClient
    ) -> None:
        gateway_app.state.routes["catalog-active"] = {
            "route_id": "catalog-active",
            "document": {},
        }

        resp = await client.get("/gateway/catalog")

        assert resp.status_code == 500
        data = resp.json()
        assert "Stored route document is invalid" in data["detail"]


class TestSelectRouteId:
    def test_without_version_header(self) -> None:
        mock_request = type("R", (), {"headers": {}})()
        result = _select_route_id("my-service", mock_request)
        assert result == "my-service-active"

    def test_with_version_header(self) -> None:
        mock_request = type("R", (), {"headers": {"x-tool-compiler-version": "3"}})()
        result = _select_route_id("my-service", mock_request)
        assert result == "my-service-v3"

    def test_with_empty_version_header(self) -> None:
        mock_request = type("R", (), {"headers": {"x-tool-compiler-version": "  "}})()
        result = _select_route_id("my-service", mock_request)
        assert result == "my-service-active"


class TestServiceKey:
    def test_without_namespace(self) -> None:
        target = {"name": "catalog", "port": 8080}
        assert _service_key(target) == "catalog:8080"

    def test_with_namespace(self) -> None:
        target = {"name": "catalog", "namespace": "prod", "port": 8080}
        assert _service_key(target) == "catalog.prod:8080"

    def test_with_empty_namespace(self) -> None:
        target = {"name": "catalog", "namespace": "  ", "port": 8080}
        assert _service_key(target) == "catalog:8080"


class TestUpstreamBaseUrl:
    def test_without_namespace(self) -> None:
        target: dict[str, Any] = {"name": "catalog", "port": 8080}
        assert _upstream_base_url(target) == "http://catalog:8080"

    def test_with_namespace(self) -> None:
        target: dict[str, Any] = {"name": "catalog", "namespace": "prod", "port": 8080}
        assert _upstream_base_url(target) == "http://catalog.prod.svc.cluster.local:8080"

    def test_with_empty_namespace(self) -> None:
        target: dict[str, Any] = {"name": "catalog", "namespace": "", "port": 8080}
        assert _upstream_base_url(target) == "http://catalog:8080"


class TestForwardHeaders:
    def test_excludes_hop_by_hop(self) -> None:

        # Use a simple mock request
        class MockHeaders:
            def items(self):
                return [
                    ("host", "example.com"),
                    ("connection", "keep-alive"),
                    ("content-length", "100"),
                    ("transfer-encoding", "chunked"),
                    ("x-custom", "value"),
                    ("authorization", "Bearer token"),
                ]

        mock_request = type("R", (), {"headers": MockHeaders()})()
        result = _forward_headers(mock_request)
        assert "x-custom" in result
        assert "authorization" in result
        assert "host" not in result
        assert "connection" not in result
        assert "content-length" not in result
        assert "transfer-encoding" not in result


class TestResponseHeaders:
    def test_excludes_hop_by_hop(self) -> None:
        resp = httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "connection": "keep-alive",
                "content-length": "42",
                "transfer-encoding": "chunked",
                "x-custom": "value",
            },
            request=httpx.Request("GET", "http://test"),
        )
        result = _response_headers(resp)
        assert "content-type" in result
        assert "x-custom" in result
        assert "connection" not in result
        assert "content-length" not in result
        assert "transfer-encoding" not in result
