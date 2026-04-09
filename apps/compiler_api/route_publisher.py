"""Route publication abstractions for compiler API artifact mutations."""

from __future__ import annotations

import os
from typing import Any, Protocol, cast

import httpx
from fastapi import FastAPI, Request

from apps.access_control.authn.service import build_service_jwt

_ROUTE_PUBLISHER_STATE_KEY = "artifact_route_publisher"
_DEFAULT_TIMEOUT_SECONDS = 10.0


class ArtifactRoutePublisher(Protocol):
    """Minimal interface for syncing service routes after artifact changes."""

    async def sync(self, route_config: dict[str, Any]) -> dict[str, Any] | None: ...

    async def delete(self, route_config: dict[str, Any]) -> dict[str, Any] | None: ...

    async def rollback(
        self,
        route_config: dict[str, Any],
        previous_routes: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None: ...


class NoopArtifactRoutePublisher:
    """Default publisher used when no access-control URL is configured."""

    async def sync(self, route_config: dict[str, Any]) -> dict[str, Any] | None:
        del route_config
        return None

    async def delete(self, route_config: dict[str, Any]) -> dict[str, Any] | None:
        del route_config
        return None

    async def rollback(
        self,
        route_config: dict[str, Any],
        previous_routes: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        del route_config, previous_routes
        return None


class UnconfiguredArtifactRoutePublisher:
    """Fail-closed publisher used when route publication is required but unconfigured."""

    @staticmethod
    def _error() -> RuntimeError:
        return RuntimeError(
            "ACCESS_CONTROL_URL is not configured; artifact route publication is unavailable."
        )

    async def sync(self, route_config: dict[str, Any]) -> dict[str, Any] | None:
        del route_config
        raise self._error()

    async def delete(self, route_config: dict[str, Any]) -> dict[str, Any] | None:
        del route_config
        raise self._error()

    async def rollback(
        self,
        route_config: dict[str, Any],
        previous_routes: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        del route_config, previous_routes
        raise self._error()


class AccessControlArtifactRoutePublisher:
    """Compiler API publisher that delegates route changes to access-control."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
        auth_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._auth_token = auth_token

    async def sync(self, route_config: dict[str, Any]) -> dict[str, Any] | None:
        return await self._post("/api/v1/gateway-binding/service-routes/sync", route_config)

    async def delete(self, route_config: dict[str, Any]) -> dict[str, Any] | None:
        return await self._post("/api/v1/gateway-binding/service-routes/delete", route_config)

    async def rollback(
        self,
        route_config: dict[str, Any],
        previous_routes: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        return await self._post(
            "/api/v1/gateway-binding/service-routes/rollback",
            route_config,
            previous_routes=previous_routes,
        )

    async def _post(
        self,
        path: str,
        route_config: dict[str, Any],
        *,
        previous_routes: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
        )
        try:
            response = await client.post(
                path,
                json={
                    "route_config": route_config,
                    **({"previous_routes": previous_routes} if previous_routes is not None else {}),
                },
                headers=self._headers,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "Access-control route publisher returned a non-JSON response."
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeError("Access-control route publisher returned a non-object response.")
            return cast(dict[str, Any], payload)
        finally:
            if owns_client:
                await client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        token = self._auth_token or build_service_jwt()
        return {"Authorization": f"Bearer {token}"}


def configure_route_publisher(
    app: FastAPI,
    *,
    route_publisher: ArtifactRoutePublisher | None = None,
) -> None:
    """Attach the configured route publisher to compiler API app state."""

    setattr(
        app.state,
        _ROUTE_PUBLISHER_STATE_KEY,
        route_publisher or _resolve_default_route_publisher(),
    )


def get_route_publisher(request: Request) -> ArtifactRoutePublisher:
    """Resolve the configured route publisher from FastAPI app state."""

    publisher = getattr(request.app.state, _ROUTE_PUBLISHER_STATE_KEY, None)
    if publisher is None:
        publisher = _resolve_default_route_publisher()
        setattr(request.app.state, _ROUTE_PUBLISHER_STATE_KEY, publisher)
    return cast(ArtifactRoutePublisher, publisher)


async def dispose_route_publisher(app: FastAPI) -> None:
    """Close any owned route publisher resources on shutdown."""

    publisher: Any = getattr(app.state, _ROUTE_PUBLISHER_STATE_KEY, None)
    if publisher is None:
        return
    close = getattr(publisher, "aclose", None)
    if close is None:
        return
    await close()


def _resolve_default_route_publisher() -> ArtifactRoutePublisher:
    base_url = os.getenv("ACCESS_CONTROL_URL", "").strip()
    if not base_url:
        return UnconfiguredArtifactRoutePublisher()
    return AccessControlArtifactRoutePublisher(base_url=base_url)


__all__ = [
    "AccessControlArtifactRoutePublisher",
    "ArtifactRoutePublisher",
    "NoopArtifactRoutePublisher",
    "UnconfiguredArtifactRoutePublisher",
    "configure_route_publisher",
    "dispose_route_publisher",
    "get_route_publisher",
]
