"""HTTP routes for gateway binding operations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.db import get_db_session
from apps.access_control.gateway_binding.service import (
    GatewayBindingService,
    get_gateway_binding_service,
)

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

    route_config: dict[str, Any]
    previous_routes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ServiceRouteResponse(BaseModel):
    """Route publication or deletion summary."""

    route_ids: list[str]
    service_routes_synced: int
    service_routes_deleted: int
    previous_routes: dict[str, dict[str, Any]] = Field(default_factory=dict)


@router.post("/reconcile", response_model=ReconcileResponse)
async def reconcile_gateway_state(
    session: AsyncSession = Depends(get_db_session),
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
) -> ReconcileResponse:
    return ReconcileResponse(**(await gateway_binding.reconcile(session)))


@router.post("/service-routes/sync", response_model=ServiceRouteResponse)
async def sync_service_routes(
    request: ServiceRouteRequest,
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
) -> ServiceRouteResponse:
    return ServiceRouteResponse(**(await gateway_binding.sync_service_routes(request.route_config)))


@router.post("/service-routes/delete", response_model=ServiceRouteResponse)
async def delete_service_routes(
    request: ServiceRouteRequest,
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
) -> ServiceRouteResponse:
    return ServiceRouteResponse(
        **(await gateway_binding.delete_service_routes(request.route_config))
    )


@router.post("/service-routes/rollback", response_model=ServiceRouteResponse)
async def rollback_service_routes(
    request: ServiceRouteRequest,
    gateway_binding: GatewayBindingService = Depends(get_gateway_binding_service),
) -> ServiceRouteResponse:
    return ServiceRouteResponse(
        **(
            await gateway_binding.rollback_service_routes(
                request.route_config,
                request.previous_routes,
            )
        )
    )
