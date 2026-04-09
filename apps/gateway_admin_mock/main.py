"""Lightweight HTTP gateway admin mock used for live reconciliation tests."""

from __future__ import annotations

from typing import Any, cast

import httpx
from fastapi import FastAPI, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import JSONResponse, Response


class ConsumerUpsertRequest(BaseModel):
    """Consumer payload stored by the mock gateway admin API."""

    username: str
    credential: str
    metadata: dict[str, Any]


class PolicyBindingUpsertRequest(BaseModel):
    """Policy binding payload stored by the mock gateway admin API."""

    document: dict[str, Any]


class RouteUpsertRequest(BaseModel):
    """Route payload stored by the mock gateway admin API."""

    document: RouteDocument


class RouteTargetService(BaseModel):
    """Validated upstream target embedded inside a stored route document."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(validation_alias=AliasChoices("name", "host"))
    port: int
    namespace: str | None = None


class RouteDocument(BaseModel):
    """Validated route document stored by the mock gateway admin API."""

    model_config = ConfigDict(extra="allow")

    target_service: RouteTargetService


def create_app() -> FastAPI:
    """Create the gateway admin mock application."""

    app = FastAPI(title="Gateway Admin Mock", version="0.1.0")
    app.state.consumers = {}
    app.state.policy_bindings = {}
    app.state.routes = {}
    app.state.upstream_overrides = {}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/admin/consumers")
    async def list_consumers() -> dict[str, list[dict[str, Any]]]:
        return {"items": list(app.state.consumers.values())}

    @app.put("/admin/consumers/{consumer_id}")
    async def upsert_consumer(
        consumer_id: str,
        request: ConsumerUpsertRequest,
    ) -> dict[str, Any]:
        consumer = {
            "consumer_id": consumer_id,
            "username": request.username,
            "credential": request.credential,
            "metadata": request.metadata,
        }
        app.state.consumers[consumer_id] = consumer
        return consumer

    @app.delete("/admin/consumers/{consumer_id}")
    async def delete_consumer(consumer_id: str) -> dict[str, str]:
        app.state.consumers.pop(consumer_id, None)
        return {"status": "deleted"}

    @app.get("/admin/policy-bindings")
    async def list_policy_bindings() -> dict[str, list[dict[str, Any]]]:
        return {"items": list(app.state.policy_bindings.values())}

    @app.put("/admin/policy-bindings/{binding_id}")
    async def upsert_policy_binding(
        binding_id: str,
        request: PolicyBindingUpsertRequest,
    ) -> dict[str, Any]:
        binding = {
            "binding_id": binding_id,
            "document": request.document,
        }
        app.state.policy_bindings[binding_id] = binding
        return binding

    @app.delete("/admin/policy-bindings/{binding_id}")
    async def delete_policy_binding(binding_id: str) -> dict[str, str]:
        app.state.policy_bindings.pop(binding_id, None)
        return {"status": "deleted"}

    @app.get("/admin/routes")
    async def list_routes() -> dict[str, list[dict[str, Any]]]:
        return {"items": list(app.state.routes.values())}

    @app.put("/admin/routes/{route_id}")
    async def upsert_route(
        route_id: str,
        request: RouteUpsertRequest,
    ) -> dict[str, Any]:
        route = {
            "route_id": route_id,
            "document": request.document.model_dump(mode="python", exclude_none=True),
        }
        app.state.routes[route_id] = route
        return route

    @app.delete("/admin/routes/{route_id}")
    async def delete_route(route_id: str) -> dict[str, str]:
        app.state.routes.pop(route_id, None)
        return {"status": "deleted"}

    @app.api_route("/gateway/{service_id}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.api_route(
        "/gateway/{service_id}/{upstream_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_gateway_request(
        request: Request,
        service_id: str,
        upstream_path: str = "",
    ) -> Response:
        route_id = _select_route_id(service_id, request)
        route = cast(dict[str, Any] | None, app.state.routes.get(route_id))
        if route is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"Route {route_id!r} is not configured."},
            )
        route_document = cast(dict[str, Any], route.get("document", {}))
        try:
            if not _route_matches_request(
                route_document=route_document,
                request=request,
                service_id=service_id,
                upstream_path=upstream_path,
            ):
                return JSONResponse(
                    status_code=404,
                    content={"detail": f"Route {route_id!r} did not match the request."},
                )
            return await _forward_request(
                request=request,
                route_document=route_document,
                upstream_overrides=cast(dict[str, dict[str, Any]], app.state.upstream_overrides),
                upstream_path=upstream_path,
            )
        except RuntimeError as exc:
            return JSONResponse(
                status_code=500,
                content={"detail": str(exc), "route_id": route_id},
            )
        except httpx.HTTPError as exc:
            return JSONResponse(
                status_code=502,
                content={
                    "detail": "Failed to reach configured upstream.",
                    "error": str(exc),
                    "route_id": route_id,
                },
            )

    return app


def _select_route_id(service_id: str, request: Request) -> str:
    version = request.headers.get("x-tool-compiler-version", "").strip()
    if version:
        return f"{service_id}-v{version}"
    return f"{service_id}-active"


def _route_matches_request(
    *,
    route_document: dict[str, Any],
    request: Request,
    service_id: str,
    upstream_path: str,
) -> bool:
    match = route_document.get("match")
    if match is None:
        return True
    if not isinstance(match, dict):
        raise RuntimeError("Stored route match is invalid: expected an object.")

    prefix = match.get("prefix")
    if prefix is not None:
        if not isinstance(prefix, str):
            raise RuntimeError("Stored route match prefix is invalid: expected a string.")
        request_path = f"/{service_id}"
        if upstream_path:
            request_path = f"{request_path}/{upstream_path.lstrip('/')}"
        if not request_path.startswith(prefix):
            return False

    headers = match.get("headers")
    if headers is not None:
        if not isinstance(headers, dict):
            raise RuntimeError("Stored route match headers are invalid: expected an object.")
        for name, expected in headers.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                raise RuntimeError(
                    "Stored route match headers are invalid: expected string keys and values."
                )
            if request.headers.get(name) != expected:
                return False

    return True


async def _forward_request(
    *,
    request: Request,
    route_document: dict[str, Any],
    upstream_overrides: dict[str, dict[str, Any]],
    upstream_path: str,
) -> Response:
    target_service = _validated_target_service(route_document)
    service_key = _service_key(target_service)
    override = upstream_overrides.get(service_key)
    request_body = await request.body()
    request_headers = _forward_headers(request)
    query_params = dict(request.query_params)
    relative_path = "/" + upstream_path.lstrip("/") if upstream_path else "/"

    if override is None:
        upstream_client = httpx.AsyncClient(
            base_url=_upstream_base_url(target_service),
            timeout=10.0,
        )
    else:
        upstream_client = httpx.AsyncClient(
            base_url=str(override["base_url"]).rstrip("/"),
            transport=cast(httpx.AsyncBaseTransport, override["transport"]),
            timeout=10.0,
        )

    async with upstream_client:
        upstream_response = await upstream_client.request(
            request.method,
            relative_path,
            content=request_body,
            headers=request_headers,
            params=query_params,
        )

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=_response_headers(upstream_response),
    )


def _service_key(target_service: dict[str, Any]) -> str:
    name = str(target_service["name"])
    namespace = str(target_service.get("namespace", "")).strip()
    port = int(target_service["port"])
    if namespace:
        return f"{name}.{namespace}:{port}"
    return f"{name}:{port}"


def _upstream_base_url(target_service: dict[str, Any]) -> str:
    name = str(target_service["name"])
    namespace = str(target_service.get("namespace", "")).strip()
    port = int(target_service["port"])
    if namespace:
        host = f"{name}.{namespace}.svc.cluster.local"
    else:
        host = name
    return f"http://{host}:{port}"


def _validated_target_service(route_document: dict[str, Any]) -> dict[str, Any]:
    try:
        document = RouteDocument.model_validate(route_document)
    except ValidationError as exc:
        raise RuntimeError(f"Stored route document is invalid: {exc}") from exc
    return document.target_service.model_dump(mode="python", exclude_none=True)


def _forward_headers(request: Request) -> dict[str, str]:
    excluded = {"host", "connection", "content-length", "transfer-encoding"}
    return {key: value for key, value in request.headers.items() if key.lower() not in excluded}


def _response_headers(response: httpx.Response) -> dict[str, str]:
    excluded = {"connection", "content-length", "transfer-encoding"}
    return {key: value for key, value in response.headers.items() if key.lower() not in excluded}


app = create_app()
