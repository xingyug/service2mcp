"""Gateway binding coordination and drift reconciliation."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, cast
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import PATResponse
from apps.access_control.authn.service import hash_token_value
from apps.access_control.authz.models import PolicyResponse
from apps.access_control.gateway_binding.client import (
    GatewayAdminClient,
    load_gateway_admin_client_from_env,
)
from libs.db_models import PersonalAccessToken, Policy, ServiceVersion, User
from libs.ir.models import RiskLevel


class GatewayBindingService:
    """Synchronize PATs and policies to the gateway admin client."""

    def __init__(self, client: GatewayAdminClient) -> None:
        self._client = client

    async def sync_pat_creation(self, pat: PATResponse, plaintext_token: str) -> None:
        await self._client.upsert_consumer(
            consumer_id=_consumer_id(pat.id),
            username=pat.username,
            credential=hash_token_value(plaintext_token),
            metadata={
                "username": pat.username,
                "pat_name": pat.name,
                "created_at": pat.created_at.isoformat(),
            },
        )

    async def sync_pat_revocation(self, pat_id: UUID) -> None:
        await self._client.delete_consumer(_consumer_id(pat_id))

    async def sync_policy(self, policy: PolicyResponse) -> None:
        await self._client.upsert_policy_binding(
            binding_id=_policy_binding_id(policy.id),
            document={
                "id": str(policy.id),
                "subject_type": policy.subject_type,
                "subject_id": policy.subject_id,
                "resource_id": policy.resource_id,
                "action_pattern": policy.action_pattern,
                "risk_threshold": policy.risk_threshold.value,
                "decision": policy.decision,
                "created_by": policy.created_by,
                "created_at": policy.created_at.isoformat(),
            },
        )

    async def delete_policy(self, policy_id: UUID) -> None:
        await self._client.delete_policy_binding(_policy_binding_id(policy_id))

    async def sync_service_routes(self, route_config: dict[str, Any]) -> dict[str, Any]:
        route_documents = _service_route_documents(route_config)
        existing_routes = await self._client.list_routes()
        previous_routes = {
            route_id: existing_routes[route_id].document
            for route_id in route_documents
            if route_id in existing_routes
        }
        for route_id, document in route_documents.items():
            await self._client.upsert_route(route_id=route_id, document=document)
        return {
            "route_ids": list(route_documents),
            "service_routes_synced": len(route_documents),
            "service_routes_deleted": 0,
            "previous_routes": previous_routes,
        }

    async def delete_service_routes(self, route_config: dict[str, Any]) -> dict[str, Any]:
        route_documents = _service_route_documents(route_config)
        for route_id in route_documents:
            await self._client.delete_route(route_id)
        return {
            "route_ids": list(route_documents),
            "service_routes_synced": 0,
            "service_routes_deleted": len(route_documents),
            "previous_routes": {},
        }

    async def rollback_service_routes(
        self,
        route_config: dict[str, Any],
        previous_routes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        route_documents = _service_route_documents(route_config)
        deleted_routes = 0
        restored_routes = 0

        for route_id in route_documents:
            if route_id in previous_routes:
                continue
            await self._client.delete_route(route_id)
            deleted_routes += 1

        for route_id, document in previous_routes.items():
            await self._client.upsert_route(route_id=route_id, document=document)
            restored_routes += 1

        return {
            "route_ids": list(route_documents),
            "service_routes_synced": restored_routes,
            "service_routes_deleted": deleted_routes,
            "previous_routes": {},
        }

    async def reconcile(self, session: AsyncSession) -> dict[str, int]:
        active_pat_rows = await session.execute(
            select(PersonalAccessToken, User)
            .join(User, PersonalAccessToken.user_id == User.id)
            .where(PersonalAccessToken.revoked_at.is_(None))
        )
        policies = (await session.scalars(select(Policy))).all()
        service_versions = (
            await session.scalars(
                select(ServiceVersion).where(ServiceVersion.route_config.is_not(None))
            )
        ).all()

        expected_consumers = {
            _consumer_id(pat.id): {
                "username": user.username,
                "credential": pat.token_hash,
                "metadata": {
                    "username": user.username,
                    "pat_name": pat.name,
                    "created_at": pat.created_at.isoformat(),
                },
            }
            for pat, user in active_pat_rows.all()
        }
        expected_policy_bindings = {
            _policy_binding_id(policy.id): _policy_document(policy) for policy in policies
        }
        expected_routes: dict[str, dict[str, Any]] = {}
        for version in service_versions:
            if not isinstance(version.route_config, dict):
                continue
            expected_routes.update(
                _service_route_documents(
                    version.route_config,
                    include_default=version.is_active,
                    include_version=True,
                )
            )

        existing_consumers = await self._client.list_consumers()
        existing_policy_bindings = await self._client.list_policy_bindings()
        existing_routes = await self._client.list_routes()

        consumers_synced = 0
        consumers_deleted = 0
        policy_bindings_synced = 0
        policy_bindings_deleted = 0
        service_routes_synced = 0
        service_routes_deleted = 0

        for consumer_id, expected in expected_consumers.items():
            existing_consumer = existing_consumers.get(consumer_id)
            if existing_consumer is None or existing_consumer.credential != expected["credential"]:
                await self._client.upsert_consumer(
                    consumer_id=consumer_id,
                    username=str(expected["username"]),
                    credential=str(expected["credential"]),
                    metadata=dict(expected["metadata"]),
                )
                consumers_synced += 1

        for consumer_id in set(existing_consumers) - set(expected_consumers):
            await self._client.delete_consumer(consumer_id)
            consumers_deleted += 1

        for binding_id, expected_document in expected_policy_bindings.items():
            existing_binding = existing_policy_bindings.get(binding_id)
            if existing_binding is None or existing_binding.document != expected_document:
                await self._client.upsert_policy_binding(
                    binding_id=binding_id,
                    document=expected_document,
                )
                policy_bindings_synced += 1

        for binding_id in set(existing_policy_bindings) - set(expected_policy_bindings):
            await self._client.delete_policy_binding(binding_id)
            policy_bindings_deleted += 1

        for route_id, expected_document in expected_routes.items():
            existing_route = existing_routes.get(route_id)
            if existing_route is None or existing_route.document != expected_document:
                await self._client.upsert_route(route_id=route_id, document=expected_document)
                service_routes_synced += 1

        for route_id in set(existing_routes) - set(expected_routes):
            await self._client.delete_route(route_id)
            service_routes_deleted += 1

        return {
            "consumers_synced": consumers_synced,
            "consumers_deleted": consumers_deleted,
            "policy_bindings_synced": policy_bindings_synced,
            "policy_bindings_deleted": policy_bindings_deleted,
            "service_routes_synced": service_routes_synced,
            "service_routes_deleted": service_routes_deleted,
        }


def configure_gateway_binding_service(
    app_state: Any,
    *,
    client: GatewayAdminClient | None = None,
) -> None:
    """Attach the gateway binding service to application state."""

    app_state.gateway_binding_service = GatewayBindingService(
        client or load_gateway_admin_client_from_env()
    )


def resolve_gateway_binding_service(app_state: Any) -> GatewayBindingService:
    """Resolve the configured gateway binding service from application state."""

    service = getattr(app_state, "gateway_binding_service", None)
    if service is None:
        configure_gateway_binding_service(app_state)
        service = getattr(app_state, "gateway_binding_service")
    return cast(GatewayBindingService, service)


def get_gateway_binding_service(request: Request) -> GatewayBindingService:
    """Resolve the configured gateway binding service from FastAPI request state."""

    return resolve_gateway_binding_service(request.app.state)


async def dispose_gateway_binding_service(app_state: Any) -> None:
    """Close the configured gateway client when it owns transport resources."""

    service = getattr(app_state, "gateway_binding_service", None)
    if service is None:
        return
    close = getattr(service._client, "aclose", None)
    if callable(close):
        await cast(Awaitable[None], close())


def _consumer_id(pat_id: UUID) -> str:
    return f"pat-{pat_id}"


def _policy_binding_id(policy_id: UUID) -> str:
    return f"policy-{policy_id}"


def _policy_document(policy: Policy | PolicyResponse) -> dict[str, Any]:
    if isinstance(policy, PolicyResponse):
        risk_threshold = policy.risk_threshold.value
        created_at = policy.created_at.isoformat()
        created_by = policy.created_by
    else:
        risk_threshold = RiskLevel(policy.risk_threshold).value
        created_at = policy.created_at.isoformat()
        created_by = policy.created_by
    return {
        "id": str(policy.id),
        "subject_type": policy.subject_type,
        "subject_id": policy.subject_id,
        "resource_id": policy.resource_id,
        "action_pattern": policy.action_pattern,
        "risk_threshold": risk_threshold,
        "decision": policy.decision,
        "created_by": created_by,
        "created_at": created_at,
    }


def _service_route_documents(
    route_config: dict[str, Any],
    *,
    include_default: bool = True,
    include_version: bool = True,
) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    service_id = str(route_config["service_id"])
    service_name = str(route_config["service_name"])
    namespace = str(route_config["namespace"])
    version_number = route_config.get("version_number")

    if include_default and isinstance(route_config.get("default_route"), dict):
        default_route = cast(dict[str, Any], route_config["default_route"])
        route_id = str(default_route["route_id"])
        documents[route_id] = _route_document(
            route_id=route_id,
            route_type="default",
            service_id=service_id,
            service_name=service_name,
            namespace=namespace,
            version_number=version_number,
            route_definition=default_route,
        )

    if include_version and isinstance(route_config.get("version_route"), dict):
        version_route = cast(dict[str, Any], route_config["version_route"])
        route_id = str(version_route["route_id"])
        documents[route_id] = _route_document(
            route_id=route_id,
            route_type="version",
            service_id=service_id,
            service_name=service_name,
            namespace=namespace,
            version_number=version_number,
            route_definition=version_route,
        )

    return documents


def _route_document(
    *,
    route_id: str,
    route_type: str,
    service_id: str,
    service_name: str,
    namespace: str,
    version_number: Any,
    route_definition: dict[str, Any],
) -> dict[str, Any]:
    document = {
        "route_id": route_id,
        "route_type": route_type,
        "service_id": service_id,
        "service_name": service_name,
        "namespace": namespace,
        "target_service": dict(cast(dict[str, Any], route_definition["target_service"])),
    }
    if "switch_strategy" in route_definition:
        document["switch_strategy"] = route_definition["switch_strategy"]
    if "match" in route_definition:
        document["match"] = dict(cast(dict[str, Any], route_definition["match"]))
    if version_number is not None:
        document["version_number"] = version_number
    return document


__all__ = [
    "GatewayBindingService",
    "configure_gateway_binding_service",
    "dispose_gateway_binding_service",
    "get_gateway_binding_service",
    "resolve_gateway_binding_service",
]
