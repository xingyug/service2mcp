"""HTTP routes for gateway binding operations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.db import get_db_session
from apps.access_control.gateway_binding.service import (
    GatewayBindingService,
    get_gateway_binding_service,
)
from apps.access_control.security import require_admin_caller, require_admin_principal
from libs.route_config import GatewayRouteConfig

router = APIRouter(prefix="/api/v1/gateway-binding", tags=["gateway_binding"])


class ReconcileResponse(BaseModel):
    """Gateway reconciliation summary."""

    consumers_synced: int
    consumers_deleted: int
    policy_bindings_synced: int
    policy_bindings_deleted: int
    service_routes_synced: int
    service_routes_deleted: int


class ServiceRouteRequest(BaseModel):
    """Service route publication payload."""

    route_config: GatewayRouteConfig
    previous_routes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ServiceRouteResponse(BaseModel):
    """Route publication or deletion summary."""

    route_ids: list[str]
    service_routes_synced: int
    service_routes_deleted: int
    previous_routes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class GatewayRouteDocumentResponse(BaseModel):
    """Serialized gateway route document."""

    route_id: str
    route_type: str
    service_id: str
    service_name: str
    tenant: str | None = None
    environment: str | None = None
    namespace: str
    target_service: dict[str, Any]
    version_number: int | None = None
    switch_strategy: str | None = None
    match: dict[str, Any] | None = None


class ServiceRoutesListResponse(BaseModel):
    """Current gateway routes mirrored for services."""

    items: list[GatewayRouteDocumentResponse]


@router.post("/reconcile", response_model=ReconcileResponse)
async def reconcile_gateway_state(
    session: AsyncSession = Depends(get_db_session),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    _caller: TokenPrincipalResponse = Depends(require_admin_caller),
) -> ReconcileResponse:
    require_admin_principal(_caller)
    return ReconcileResponse(**(await gateway_binding.reconcile(session)))


@router.post("/service-routes/sync", response_model=ServiceRouteResponse)
async def sync_service_routes(
    request: ServiceRouteRequest,
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    _caller: TokenPrincipalResponse = Depends(require_admin_caller),
) -> ServiceRouteResponse:
    require_admin_principal(_caller)
    route_config = request.route_config.model_dump(mode="python", exclude_none=True)
    return ServiceRouteResponse(
        **(
            await gateway_binding.sync_service_routes(
                route_config,
                request.previous_routes,
            )
        )
    )


@router.post("/service-routes/delete", response_model=ServiceRouteResponse)
async def delete_service_routes(
    request: ServiceRouteRequest,
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    _caller: TokenPrincipalResponse = Depends(require_admin_caller),
) -> ServiceRouteResponse:
    require_admin_principal(_caller)
    route_config = request.route_config.model_dump(mode="python", exclude_none=True)
    return ServiceRouteResponse(
        **(await gateway_binding.delete_service_routes(route_config))
    )


@router.post("/service-routes/rollback", response_model=ServiceRouteResponse)
async def rollback_service_routes(
    request: ServiceRouteRequest,
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    _caller: TokenPrincipalResponse = Depends(require_admin_caller),
) -> ServiceRouteResponse:
    require_admin_principal(_caller)
    route_config = request.route_config.model_dump(mode="python", exclude_none=True)
    return ServiceRouteResponse(
        **(
            await gateway_binding.rollback_service_routes(
                route_config,
                request.previous_routes,
            )
        )
    )


@router.get("/service-routes", response_model=ServiceRoutesListResponse)
async def list_service_routes(
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
    _caller: TokenPrincipalResponse = Depends(require_admin_caller),
) -> ServiceRoutesListResponse:
    require_admin_principal(_caller)
    return ServiceRoutesListResponse(
        items=[
            GatewayRouteDocumentResponse(**document)
            for document in await gateway_binding.list_service_routes()
        ]
    )
