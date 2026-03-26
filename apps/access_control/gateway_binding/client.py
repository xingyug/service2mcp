"""Gateway admin client abstractions for APISIX-style gateway binding."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


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
        return {
            str(item["consumer_id"]): GatewayConsumer(
                consumer_id=str(item["consumer_id"]),
                username=str(item["username"]),
                credential=str(item["credential"]),
                metadata=dict(item.get("metadata", {})),
            )
            for item in _items_from_payload(payload)
        }

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
        return {
            str(item["binding_id"]): GatewayPolicyBinding(
                binding_id=str(item["binding_id"]),
                document=dict(item["document"]),
            )
            for item in _items_from_payload(payload)
        }

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
        return {
            str(item["route_id"]): GatewayRoute(
                route_id=str(item["route_id"]),
                document=dict(item["document"]),
            )
            for item in _items_from_payload(payload)
        }

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
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Gateway admin API returned a non-object response.")
        return payload


def load_gateway_admin_client_from_env() -> GatewayAdminClient:
    """Build the configured gateway admin client from process environment."""

    base_url = os.getenv("GATEWAY_ADMIN_URL", "").strip()
    if base_url:
        return HTTPGatewayAdminClient(
            base_url=base_url,
            timeout=float(os.getenv("GATEWAY_ADMIN_TIMEOUT_SECONDS", "10.0")),
            admin_token=os.getenv("GATEWAY_ADMIN_TOKEN"),
        )
    return InMemoryAPISIXAdminClient()


def _items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("Gateway admin API response is missing an items list.")
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Gateway admin API returned a non-object item.")
        normalized_items.append(item)
    return normalized_items


__all__ = [
    "GatewayAdminClient",
    "GatewayConsumer",
    "GatewayPolicyBinding",
    "GatewayRoute",
    "HTTPGatewayAdminClient",
    "InMemoryAPISIXAdminClient",
    "load_gateway_admin_client_from_env",
]
