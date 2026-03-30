"""Gateway admin client abstractions for APISIX-style gateway binding."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class GatewayAdminConfigurationError(RuntimeError):
    """Raised when the gateway admin client cannot be configured from environment."""


@dataclass(frozen=True)
class GatewayConsumer:
    """Consumer representation mirrored to the gateway."""

    consumer_id: str
    username: str
    credential: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class GatewayPolicyBinding:
    """Policy binding representation mirrored to the gateway."""

    binding_id: str
    document: dict[str, Any]


@dataclass(frozen=True)
class GatewayRoute:
    """Route representation mirrored to the gateway."""

    route_id: str
    document: dict[str, Any]


class GatewayAdminClient(Protocol):
    """Subset of APISIX Admin API needed by the binding layer."""

    async def upsert_consumer(
        self,
        *,
        consumer_id: str,
        username: str,
        credential: str,
        metadata: dict[str, Any],
    ) -> None: ...

    async def delete_consumer(self, consumer_id: str) -> None: ...

    async def list_consumers(self) -> dict[str, GatewayConsumer]: ...

    async def upsert_policy_binding(self, *, binding_id: str, document: dict[str, Any]) -> None: ...

    async def delete_policy_binding(self, binding_id: str) -> None: ...

    async def list_policy_bindings(self) -> dict[str, GatewayPolicyBinding]: ...

    async def upsert_route(self, *, route_id: str, document: dict[str, Any]) -> None: ...

    async def delete_route(self, route_id: str) -> None: ...

    async def list_routes(self) -> dict[str, GatewayRoute]: ...


class InMemoryAPISIXAdminClient:
    """In-memory stand-in for the APISIX Admin API used by tests."""

    def __init__(self) -> None:
        self.consumers: dict[str, GatewayConsumer] = {}
        self.policy_bindings: dict[str, GatewayPolicyBinding] = {}
        self.routes: dict[str, GatewayRoute] = {}

    async def upsert_consumer(
        self,
        *,
        consumer_id: str,
        username: str,
        credential: str,
        metadata: dict[str, Any],
    ) -> None:
        self.consumers[consumer_id] = GatewayConsumer(
            consumer_id=consumer_id,
            username=username,
            credential=credential,
            metadata=dict(metadata),
        )

    async def delete_consumer(self, consumer_id: str) -> None:
        self.consumers.pop(consumer_id, None)

    async def list_consumers(self) -> dict[str, GatewayConsumer]:
        return dict(self.consumers)

    async def upsert_policy_binding(self, *, binding_id: str, document: dict[str, Any]) -> None:
        self.policy_bindings[binding_id] = GatewayPolicyBinding(
            binding_id=binding_id,
            document=dict(document),
        )

    async def delete_policy_binding(self, binding_id: str) -> None:
        self.policy_bindings.pop(binding_id, None)

    async def list_policy_bindings(self) -> dict[str, GatewayPolicyBinding]:
        return dict(self.policy_bindings)

    async def upsert_route(self, *, route_id: str, document: dict[str, Any]) -> None:
        self.routes[route_id] = GatewayRoute(route_id=route_id, document=dict(document))

    async def delete_route(self, route_id: str) -> None:
        self.routes.pop(route_id, None)

    async def list_routes(self) -> dict[str, GatewayRoute]:
        return dict(self.routes)


class HTTPGatewayAdminClient:
    """HTTP-backed gateway admin client used for live reconciliation tests."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 10.0,
        admin_token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if admin_token:
            headers["Authorization"] = f"Bearer {admin_token}"
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def upsert_consumer(
        self,
        *,
        consumer_id: str,
        username: str,
        credential: str,
        metadata: dict[str, Any],
    ) -> None:
        await self._request(
            "PUT",
            f"/admin/consumers/{consumer_id}",
            json={
                "username": username,
                "credential": credential,
                "metadata": metadata,
            },
        )

    async def delete_consumer(self, consumer_id: str) -> None:
        await self._request("DELETE", f"/admin/consumers/{consumer_id}")

    async def list_consumers(self) -> dict[str, GatewayConsumer]:
        payload = await self._request("GET", "/admin/consumers")
        consumers: dict[str, GatewayConsumer] = {}
        for item in _items_from_payload(payload):
            consumer = _consumer_from_item(item)
            consumers[consumer.consumer_id] = consumer
        return consumers

    async def upsert_policy_binding(self, *, binding_id: str, document: dict[str, Any]) -> None:
        await self._request(
            "PUT",
            f"/admin/policy-bindings/{binding_id}",
            json={"document": document},
        )

    async def delete_policy_binding(self, binding_id: str) -> None:
        await self._request("DELETE", f"/admin/policy-bindings/{binding_id}")

    async def list_policy_bindings(self) -> dict[str, GatewayPolicyBinding]:
        payload = await self._request("GET", "/admin/policy-bindings")
        bindings: dict[str, GatewayPolicyBinding] = {}
        for item in _items_from_payload(payload):
            binding = _policy_binding_from_item(item)
            bindings[binding.binding_id] = binding
        return bindings

    async def upsert_route(self, *, route_id: str, document: dict[str, Any]) -> None:
        await self._request(
            "PUT",
            f"/admin/routes/{route_id}",
            json={"document": document},
        )

    async def delete_route(self, route_id: str) -> None:
        await self._request("DELETE", f"/admin/routes/{route_id}")

    async def list_routes(self) -> dict[str, GatewayRoute]:
        payload = await self._request("GET", "/admin/routes")
        routes: dict[str, GatewayRoute] = {}
        for item in _items_from_payload(payload):
            route = _route_from_item(item)
            routes[route.route_id] = route
        return routes

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        if response.status_code == 204:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Gateway admin API returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Gateway admin API returned a non-object response.")
        return payload


def load_gateway_admin_client_from_env() -> GatewayAdminClient:
    """Build the configured gateway admin client from process environment."""

    base_url = os.getenv("GATEWAY_ADMIN_URL", "").strip()
    if not base_url:
        raise GatewayAdminConfigurationError(
            "GATEWAY_ADMIN_URL must be configured for gateway binding operations."
        )
    try:
        timeout = float(os.getenv("GATEWAY_ADMIN_TIMEOUT_SECONDS", "10.0"))
    except ValueError:
        timeout = 10.0
    return HTTPGatewayAdminClient(
        base_url=base_url,
        timeout=timeout,
        admin_token=os.getenv("GATEWAY_ADMIN_TOKEN"),
    )


def _items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "items" not in payload:
        raise RuntimeError("Gateway admin API response is missing an items list.")
    items = payload["items"]
    if not isinstance(items, list):
        raise RuntimeError("Gateway admin API response is missing an items list.")
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Gateway admin API returned a non-object item.")
        normalized_items.append(item)
    return normalized_items


def _consumer_from_item(item: dict[str, Any]) -> GatewayConsumer:
    return GatewayConsumer(
        consumer_id=str(_required_item_field(item, "consumer_id")),
        username=str(_required_item_field(item, "username")),
        credential=str(_required_item_field(item, "credential")),
        metadata=dict(_optional_object_field(item, "metadata")),
    )


def _policy_binding_from_item(item: dict[str, Any]) -> GatewayPolicyBinding:
    return GatewayPolicyBinding(
        binding_id=str(_required_item_field(item, "binding_id")),
        document=dict(_required_object_field(item, "document")),
    )


def _route_from_item(item: dict[str, Any]) -> GatewayRoute:
    return GatewayRoute(
        route_id=str(_required_item_field(item, "route_id")),
        document=dict(_required_object_field(item, "document")),
    )


def _required_item_field(item: dict[str, Any], field_name: str) -> Any:
    if field_name not in item:
        raise RuntimeError(f"Gateway admin item is missing required field '{field_name}'.")
    return item[field_name]


def _required_object_field(item: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = _required_item_field(item, field_name)
    if not isinstance(value, dict):
        raise RuntimeError(f"Gateway admin field '{field_name}' must be an object.")
    return value


def _optional_object_field(item: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = item.get(field_name, {})
    if not isinstance(value, dict):
        raise RuntimeError(f"Gateway admin field '{field_name}' must be an object.")
    return value


__all__ = [
    "GatewayAdminClient",
    "GatewayAdminConfigurationError",
    "GatewayConsumer",
    "GatewayPolicyBinding",
    "GatewayRoute",
    "HTTPGatewayAdminClient",
    "InMemoryAPISIXAdminClient",
    "load_gateway_admin_client_from_env",
]
