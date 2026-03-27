"""Unit tests for apps/access_control/gateway_binding/client.py."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from apps.access_control.gateway_binding.client import (
    GatewayConsumer,
    GatewayPolicyBinding,
    GatewayRoute,
    HTTPGatewayAdminClient,
    InMemoryAPISIXAdminClient,
    _items_from_payload,
    load_gateway_admin_client_from_env,
)

# --- Dataclass tests ---


class TestGatewayConsumer:
    def test_frozen(self) -> None:
        consumer = GatewayConsumer(
            consumer_id="c1",
            username="alice",
            credential="key-abc",
            metadata={"role": "admin"},
        )
        assert consumer.consumer_id == "c1"
        with pytest.raises(AttributeError):
            consumer.consumer_id = "c2"  # type: ignore[misc]


class TestGatewayPolicyBinding:
    def test_frozen(self) -> None:
        binding = GatewayPolicyBinding(
            binding_id="b1",
            document={"rule": "allow"},
        )
        assert binding.binding_id == "b1"


class TestGatewayRoute:
    def test_frozen(self) -> None:
        route = GatewayRoute(route_id="r1", document={"uri": "/api/v1"})
        assert route.route_id == "r1"


# --- InMemoryAPISIXAdminClient tests ---


class TestInMemoryAPISIXAdminClient:
    @pytest.fixture
    def client(self) -> InMemoryAPISIXAdminClient:
        return InMemoryAPISIXAdminClient()

    @pytest.mark.asyncio
    async def test_upsert_and_list_consumers(self, client: InMemoryAPISIXAdminClient) -> None:
        await client.upsert_consumer(
            consumer_id="c1",
            username="alice",
            credential="key1",
            metadata={"env": "prod"},
        )
        consumers = await client.list_consumers()
        assert "c1" in consumers
        assert consumers["c1"].username == "alice"

    @pytest.mark.asyncio
    async def test_delete_consumer(self, client: InMemoryAPISIXAdminClient) -> None:
        await client.upsert_consumer(
            consumer_id="c1",
            username="alice",
            credential="key1",
            metadata={},
        )
        await client.delete_consumer("c1")
        consumers = await client.list_consumers()
        assert "c1" not in consumers

    @pytest.mark.asyncio
    async def test_delete_nonexistent_consumer_silent(
        self, client: InMemoryAPISIXAdminClient
    ) -> None:
        await client.delete_consumer("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_upsert_and_list_policy_bindings(self, client: InMemoryAPISIXAdminClient) -> None:
        await client.upsert_policy_binding(binding_id="b1", document={"rule": "allow"})
        bindings = await client.list_policy_bindings()
        assert "b1" in bindings
        assert bindings["b1"].document == {"rule": "allow"}

    @pytest.mark.asyncio
    async def test_delete_policy_binding(self, client: InMemoryAPISIXAdminClient) -> None:
        await client.upsert_policy_binding(binding_id="b1", document={})
        await client.delete_policy_binding("b1")
        bindings = await client.list_policy_bindings()
        assert "b1" not in bindings

    @pytest.mark.asyncio
    async def test_upsert_and_list_routes(self, client: InMemoryAPISIXAdminClient) -> None:
        await client.upsert_route(route_id="r1", document={"uri": "/api"})
        routes = await client.list_routes()
        assert "r1" in routes
        assert routes["r1"].document == {"uri": "/api"}

    @pytest.mark.asyncio
    async def test_delete_route(self, client: InMemoryAPISIXAdminClient) -> None:
        await client.upsert_route(route_id="r1", document={})
        await client.delete_route("r1")
        routes = await client.list_routes()
        assert "r1" not in routes

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, client: InMemoryAPISIXAdminClient) -> None:
        await client.upsert_consumer(
            consumer_id="c1", username="alice", credential="key1", metadata={}
        )
        await client.upsert_consumer(
            consumer_id="c1", username="bob", credential="key2", metadata={}
        )
        consumers = await client.list_consumers()
        assert consumers["c1"].username == "bob"


# --- HTTPGatewayAdminClient tests ---


class TestHTTPGatewayAdminClient:
    def test_init_default(self) -> None:
        client = HTTPGatewayAdminClient(base_url="http://localhost:9080")
        assert client._owns_client is True

    def test_init_with_external_client(self) -> None:
        import httpx
        ext_client = httpx.AsyncClient()
        client = HTTPGatewayAdminClient(
            base_url="http://localhost:9080",
            client=ext_client,
        )
        assert client._owns_client is False


# --- _items_from_payload tests ---


class TestItemsFromPayload:
    def test_valid_items(self) -> None:
        payload = {"items": [{"id": 1}, {"id": 2}]}
        result = _items_from_payload(payload)
        assert len(result) == 2

    def test_empty_items(self) -> None:
        payload: dict[str, Any] = {"items": []}
        result = _items_from_payload(payload)
        assert result == []

    def test_missing_items_key(self) -> None:
        payload = {"other": "data"}
        result = _items_from_payload(payload)
        assert result == []

    def test_non_list_items_raises(self) -> None:
        payload = {"items": "not-a-list"}
        with pytest.raises(RuntimeError, match="missing an items list"):
            _items_from_payload(payload)

    def test_non_dict_item_raises(self) -> None:
        payload = {"items": [{"id": 1}, "bad"]}
        with pytest.raises(RuntimeError, match="non-object item"):
            _items_from_payload(payload)


# --- load_gateway_admin_client_from_env tests ---


class TestLoadGatewayAdminClientFromEnv:
    def test_returns_in_memory_when_no_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            # Remove GATEWAY_ADMIN_URL if present
            os.environ.pop("GATEWAY_ADMIN_URL", None)
            client = load_gateway_admin_client_from_env()
            assert isinstance(client, InMemoryAPISIXAdminClient)

    def test_returns_http_client_when_env_set(self) -> None:
        with patch.dict(os.environ, {"GATEWAY_ADMIN_URL": "http://localhost:9080"}):
            client = load_gateway_admin_client_from_env()
            assert isinstance(client, HTTPGatewayAdminClient)

    def test_empty_url_returns_in_memory(self) -> None:
        with patch.dict(os.environ, {"GATEWAY_ADMIN_URL": "  "}):
            client = load_gateway_admin_client_from_env()
            assert isinstance(client, InMemoryAPISIXAdminClient)
