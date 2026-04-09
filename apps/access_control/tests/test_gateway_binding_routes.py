"""Unit tests for gateway_binding/routes.py auth guards and pass-through behavior."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.gateway_binding.routes import (
    GatewayRouteDocumentResponse,
    ServiceRouteRequest,
    ServiceRoutesListResponse,
    delete_service_routes,
    list_service_routes,
    reconcile_gateway_state,
    rollback_service_routes,
    sync_service_routes,
)
from apps.access_control.security import require_admin_principal


def _admin_caller() -> TokenPrincipalResponse:
    return TokenPrincipalResponse(
        subject="admin",
        token_type="jwt",
        claims={"sub": "admin", "roles": ["admin"]},
    )


def _user_caller() -> TokenPrincipalResponse:
    return TokenPrincipalResponse(
        subject="alice",
        token_type="jwt",
        claims={"sub": "alice"},
    )


def _route_config() -> dict[str, Any]:
    return {
        "service_id": "svc-1",
        "service_name": "Billing API",
        "namespace": "default",
        "version_number": 1,
        "default_route": {
            "route_id": "svc-1-active",
            "target_service": {"host": "svc-1-v1", "port": 8000},
        },
        "version_route": {
            "route_id": "svc-1-v1",
            "target_service": {"host": "svc-1-v1", "port": 8000},
        },
    }


def test_require_admin_principal_rejects_non_admin_gateway_caller() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_admin_principal(_user_caller())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_reconcile_gateway_state_calls_service_for_admin() -> None:
    session = object()
    gateway_binding = AsyncMock()
    gateway_binding.reconcile.return_value = {
        "consumers_synced": 1,
        "consumers_deleted": 0,
        "policy_bindings_synced": 1,
        "policy_bindings_deleted": 0,
        "service_routes_synced": 2,
        "service_routes_deleted": 0,
    }

    response = await reconcile_gateway_state(session, gateway_binding, _admin_caller())

    assert response.service_routes_synced == 2
    gateway_binding.reconcile.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_sync_delete_and_rollback_forward_route_payloads() -> None:
    gateway_binding = AsyncMock()
    route_config = _route_config()
    gateway_binding.sync_service_routes.return_value = {
        "route_ids": ["active"],
        "service_routes_synced": 1,
        "service_routes_deleted": 0,
        "previous_routes": {},
    }
    gateway_binding.delete_service_routes.return_value = {
        "route_ids": ["active"],
        "service_routes_synced": 0,
        "service_routes_deleted": 1,
        "previous_routes": {},
    }
    gateway_binding.rollback_service_routes.return_value = {
        "route_ids": ["active"],
        "service_routes_synced": 1,
        "service_routes_deleted": 1,
        "previous_routes": {"active": {"route_id": "old"}},
    }
    request = ServiceRouteRequest(
        route_config=route_config,
        previous_routes={"active": {"route_id": "old"}},
    )
    caller = _admin_caller()

    sync_response = await sync_service_routes(request, gateway_binding, caller)
    delete_response = await delete_service_routes(request, gateway_binding, caller)
    rollback_response = await rollback_service_routes(request, gateway_binding, caller)

    assert sync_response.service_routes_synced == 1
    assert delete_response.service_routes_deleted == 1
    assert rollback_response.previous_routes == {"active": {"route_id": "old"}}
    gateway_binding.sync_service_routes.assert_awaited_once_with(
        route_config,
        {"active": {"route_id": "old"}},
    )
    gateway_binding.delete_service_routes.assert_awaited_once_with(route_config)
    gateway_binding.rollback_service_routes.assert_awaited_once_with(
        route_config,
        {"active": {"route_id": "old"}},
    )


def test_service_route_request_rejects_invalid_route_config() -> None:
    with pytest.raises(ValidationError, match="namespace"):
        ServiceRouteRequest(
            route_config={"service_id": "svc-1", "service_name": "Billing API"},
        )


def test_service_route_request_rejects_non_string_service_id() -> None:
    with pytest.raises(ValidationError, match="service_id"):
        ServiceRouteRequest(
            route_config={
                "service_id": None,
                "service_name": "Billing API",
                "namespace": "default",
            },
        )


@pytest.mark.asyncio
async def test_list_service_routes_returns_current_gateway_documents() -> None:
    gateway_binding = AsyncMock()
    gateway_binding.list_service_routes.return_value = [
        {
            "route_id": "svc-1-active",
            "route_type": "default",
            "service_id": "svc-1",
            "service_name": "Billing API",
            "tenant": "team-a",
            "environment": "prod",
            "namespace": "runtime-system",
            "target_service": {
                "name": "billing-runtime-v2",
                "namespace": "runtime-system",
                "port": 8003,
            },
            "version_number": 2,
        }
    ]

    response = await list_service_routes(gateway_binding, _admin_caller())

    assert response == ServiceRoutesListResponse(
        items=[
            GatewayRouteDocumentResponse(
                route_id="svc-1-active",
                route_type="default",
                service_id="svc-1",
                service_name="Billing API",
                tenant="team-a",
                environment="prod",
                namespace="runtime-system",
                target_service={
                    "name": "billing-runtime-v2",
                    "namespace": "runtime-system",
                    "port": 8003,
                },
                version_number=2,
            )
        ]
    )
    gateway_binding.list_service_routes.assert_awaited_once_with()
