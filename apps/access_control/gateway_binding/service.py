"""Gateway binding coordination and drift reconciliation."""

from __future__ import annotations

import re
from collections.abc import Awaitable
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.authn.models import PATResponse
from apps.access_control.authn.service import hash_token_value
from apps.access_control.authz.models import PolicyResponse
from apps.access_control.gateway_binding.client import (
    GatewayAdminClient,
    GatewayAdminConfigurationError,
    load_gateway_admin_client_from_env,
)
from libs.db_models import PersonalAccessToken, Policy, ServiceVersion, User
from libs.ir.models import RiskLevel
from libs.route_config import validate_route_config

_MAX_ROUTE_SCOPE_COMPONENT_LENGTH = 63


class GatewayBindingNotConfiguredError(RuntimeError):
    """Raised when gateway binding cannot be used because configuration is missing."""


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

    async def sync_service_routes(
        self,
        route_config: dict[str, Any],
        previous_routes: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        route_documents = _service_route_documents(route_config)
        existing_routes = await self._client.list_routes()
        service_identity = _route_service_identity(route_config)
        matching_previous_routes = _matching_route_documents(previous_routes, service_identity)
        for route_id in route_documents:
            existing_route = existing_routes.get(route_id)
            if existing_route is not None and not _is_managed_route_document(
                route_id, existing_route.document
            ):
                raise RuntimeError(f"Refusing to overwrite unmanaged gateway route {route_id}.")
        previous_route_documents = {
            route_id: existing_routes[route_id].document
            for route_id in route_documents
            if route_id in existing_routes
            and _is_managed_route_document(route_id, existing_routes[route_id].document)
        }
        stale_route_ids = {
            route_id
            for route_id, route in existing_routes.items()
            if route_id not in route_documents
            and _is_managed_route_document(route_id, route.document)
            and _route_belongs_to_service(route.document, service_identity)
        }
        if matching_previous_routes:
            stale_route_ids.update(
                route_id
                for route_id in matching_previous_routes
                if route_id not in route_documents
                and route_id in existing_routes
                and _is_managed_route_document(route_id, existing_routes[route_id].document)
                and _route_belongs_to_service(existing_routes[route_id].document, service_identity)
            )
        previous_route_documents.update(
            {
                route_id: existing_routes[route_id].document
                for route_id in stale_route_ids
                if route_id in existing_routes
            }
        )
        for route_id in stale_route_ids:
            await self._client.delete_route(route_id)
        for route_id, document in route_documents.items():
            await self._client.upsert_route(route_id=route_id, document=document)
        return {
            "route_ids": list(route_documents),
            "service_routes_synced": len(route_documents),
            "service_routes_deleted": len(stale_route_ids),
            "previous_routes": previous_route_documents,
        }

    async def delete_service_routes(self, route_config: dict[str, Any]) -> dict[str, Any]:
        route_documents = _service_route_documents(route_config)
        existing_routes = await self._client.list_routes()
        previous_route_documents: dict[str, dict[str, Any]] = {}
        for route_id in route_documents:
            existing_route = existing_routes.get(route_id)
            if existing_route is not None and not _is_managed_route_document(
                route_id, existing_route.document
            ):
                raise RuntimeError(f"Refusing to delete unmanaged gateway route {route_id}.")
            if existing_route is not None:
                previous_route_documents[route_id] = existing_route.document
            await self._client.delete_route(route_id)
        return {
            "route_ids": list(route_documents),
            "service_routes_synced": 0,
            "service_routes_deleted": len(route_documents),
            "previous_routes": previous_route_documents,
        }

    async def rollback_service_routes(
        self,
        route_config: dict[str, Any],
        previous_routes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        route_documents = _service_route_documents(route_config)
        existing_routes = await self._client.list_routes()
        matching_previous_routes = _matching_route_documents(
            previous_routes,
            _route_service_identity(route_config),
        )
        deleted_routes = 0
        restored_routes = 0

        for route_id in route_documents:
            if route_id in matching_previous_routes:
                continue
            existing_route = existing_routes.get(route_id)
            if existing_route is not None and not _is_managed_route_document(
                route_id, existing_route.document
            ):
                raise RuntimeError(f"Refusing to delete unmanaged gateway route {route_id}.")
            await self._client.delete_route(route_id)
            deleted_routes += 1

        for route_id, document in matching_previous_routes.items():
            await self._client.upsert_route(route_id=route_id, document=document)
            restored_routes += 1

        return {
            "route_ids": list(route_documents),
            "service_routes_synced": restored_routes,
            "service_routes_deleted": deleted_routes,
            "previous_routes": {},
        }

    async def list_service_routes(self) -> list[dict[str, Any]]:
        routes = await self._client.list_routes()
        return [
            routes[route_id].document
            for route_id in sorted(routes)
            if _is_managed_route_document(route_id, routes[route_id].document)
        ]

    async def reconcile(self, session: AsyncSession) -> dict[str, int]:
        active_pat_rows = await session.execute(
            select(PersonalAccessToken, User)
            .join(User, PersonalAccessToken.user_id == User.id)
            .where(PersonalAccessToken.revoked_at.is_(None), User.is_active.is_(True))
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
                    _route_config_with_scope(
                        version.route_config,
                        tenant=getattr(version, "tenant", None),
                        environment=getattr(version, "environment", None),
                    ),
                    include_default=version.is_active,
                    include_version=True,
                )
            )

        existing_consumers = await self._client.list_consumers()
        existing_policy_bindings = await self._client.list_policy_bindings()
        existing_routes = await self._client.list_routes()
        managed_existing_consumers = {
            consumer_id: consumer
            for consumer_id, consumer in existing_consumers.items()
            if _is_managed_consumer_id(consumer_id)
        }
        managed_existing_policy_bindings = {
            binding_id: binding
            for binding_id, binding in existing_policy_bindings.items()
            if _is_managed_policy_binding_id(binding_id)
        }
        managed_existing_routes = {
            route_id: route
            for route_id, route in existing_routes.items()
            if _is_managed_route_document(route_id, route.document)
        }

        consumers_synced = 0
        consumers_deleted = 0
        policy_bindings_synced = 0
        policy_bindings_deleted = 0
        service_routes_synced = 0
        service_routes_deleted = 0

        for consumer_id, expected in expected_consumers.items():
            existing_consumer = managed_existing_consumers.get(consumer_id)
            if _consumer_requires_sync(existing_consumer, expected):
                await self._client.upsert_consumer(
                    consumer_id=consumer_id,
                    username=str(expected["username"]),
                    credential=str(expected["credential"]),
                    metadata=dict(expected["metadata"]),
                )
                consumers_synced += 1

        for consumer_id in set(managed_existing_consumers) - set(expected_consumers):
            await self._client.delete_consumer(consumer_id)
            consumers_deleted += 1

        for binding_id, expected_document in expected_policy_bindings.items():
            existing_binding = managed_existing_policy_bindings.get(binding_id)
            if existing_binding is None or existing_binding.document != expected_document:
                await self._client.upsert_policy_binding(
                    binding_id=binding_id,
                    document=expected_document,
                )
                policy_bindings_synced += 1

        for binding_id in set(managed_existing_policy_bindings) - set(expected_policy_bindings):
            await self._client.delete_policy_binding(binding_id)
            policy_bindings_deleted += 1

        for route_id, expected_document in expected_routes.items():
            if route_id in existing_routes and route_id not in managed_existing_routes:
                raise RuntimeError(f"Refusing to overwrite unmanaged gateway route {route_id}.")
            existing_route = managed_existing_routes.get(route_id)
            if existing_route is None or existing_route.document != expected_document:
                await self._client.upsert_route(route_id=route_id, document=expected_document)
                service_routes_synced += 1

        for route_id in set(managed_existing_routes) - set(expected_routes):
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
    app_state.gateway_binding_error = None
    if client is not None:
        app_state.gateway_binding_service = GatewayBindingService(client)
        return
    try:
        configured_client = load_gateway_admin_client_from_env()
    except GatewayAdminConfigurationError as exc:
        app_state.gateway_binding_service = None
        app_state.gateway_binding_error = str(exc)
        return
    app_state.gateway_binding_service = GatewayBindingService(configured_client)


def resolve_gateway_binding_service(app_state: Any) -> GatewayBindingService:
    """Resolve the configured gateway binding service from application state."""

    service = getattr(app_state, "gateway_binding_service", None)
    if service is None:
        configure_gateway_binding_service(app_state)
        service = getattr(app_state, "gateway_binding_service")
    if service is None:
        detail = getattr(app_state, "gateway_binding_error", None)
        if not isinstance(detail, str) or not detail:
            detail = "Gateway binding service is not configured."
        raise GatewayBindingNotConfiguredError(detail)
    return cast(GatewayBindingService, service)


def get_gateway_binding_service(request: Request) -> GatewayBindingService:
    """Resolve the configured gateway binding service from FastAPI request state."""
    try:
        return resolve_gateway_binding_service(request.app.state)
    except GatewayBindingNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


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


def _is_managed_consumer_id(consumer_id: str) -> bool:
    return consumer_id.startswith("pat-")


def _is_managed_policy_binding_id(binding_id: str) -> bool:
    return binding_id.startswith("policy-")


def _consumer_requires_sync(existing_consumer: Any, expected: dict[str, Any]) -> bool:
    if existing_consumer is None:
        return True
    return (
        existing_consumer.username != expected["username"]
        or existing_consumer.credential != expected["credential"]
        or existing_consumer.metadata != expected["metadata"]
    )


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
    route_config = _validated_route_config(route_config)
    documents: dict[str, dict[str, Any]] = {}
    service_id = str(route_config["service_id"])
    service_name = str(route_config["service_name"])
    namespace = str(route_config["namespace"])
    version_number = route_config.get("version_number")
    tenant = _normalize_scope_value(route_config.get("tenant"))
    environment = _normalize_scope_value(route_config.get("environment"))

    if include_default and isinstance(route_config.get("default_route"), dict):
        default_route = cast(dict[str, Any], route_config["default_route"])
        route_id = _default_route_id(
            service_id,
            tenant=tenant,
            environment=environment,
        )
        documents[route_id] = _route_document(
            route_id=route_id,
            route_type="default",
            service_id=service_id,
            service_name=service_name,
            namespace=namespace,
            version_number=version_number,
            tenant=tenant,
            environment=environment,
            route_definition=default_route,
        )

    if include_version and isinstance(route_config.get("version_route"), dict):
        version_route = cast(dict[str, Any], route_config["version_route"])
        route_id = _version_route_id(
            service_id,
            version_number,
            tenant=tenant,
            environment=environment,
        )
        documents[route_id] = _route_document(
            route_id=route_id,
            route_type="version",
            service_id=service_id,
            service_name=service_name,
            namespace=namespace,
            version_number=version_number,
            tenant=tenant,
            environment=environment,
            route_definition=version_route,
        )

    return documents


def _route_belongs_to_service(
    route_document: dict[str, Any],
    service_identity: tuple[str, str | None, str | None],
) -> bool:
    service_id, tenant, environment = service_identity
    return (
        route_document.get("service_id") == service_id
        and _normalize_scope_value(route_document.get("tenant")) == tenant
        and _normalize_scope_value(route_document.get("environment")) == environment
    )


def _is_managed_route_document(route_id: str, route_document: Any) -> bool:
    if not isinstance(route_document, dict):
        return False
    if route_document.get("route_id") != route_id:
        return False
    if route_document.get("route_type") not in {"default", "version"}:
        return False
    if not all(
        isinstance(route_document.get(field), str) and route_document.get(field)
        for field in ("service_id", "service_name", "namespace")
    ):
        return False
    for field in ("tenant", "environment"):
        value = route_document.get(field)
        if value is not None and not _normalize_scope_value(value):
            return False
    return isinstance(route_document.get("target_service"), dict)


def _route_document(
    *,
    route_id: str,
    route_type: str,
    service_id: str,
    service_name: str,
    namespace: str,
    version_number: Any,
    tenant: str | None,
    environment: str | None,
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
    if tenant is not None:
        document["tenant"] = tenant
    if environment is not None:
        document["environment"] = environment
    if "switch_strategy" in route_definition:
        document["switch_strategy"] = route_definition["switch_strategy"]
    if "match" in route_definition:
        document["match"] = dict(cast(dict[str, Any], route_definition["match"]))
    if version_number is not None:
        document["version_number"] = version_number
    return document


def _normalize_scope_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _sanitize_route_component(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        return "service"
    return normalized[:_MAX_ROUTE_SCOPE_COMPONENT_LENGTH].rstrip("-")


def _route_identity_base(
    service_id: str,
    *,
    tenant: str | None,
    environment: str | None,
) -> str:
    segments = [_sanitize_route_component(service_id)]
    if tenant is not None:
        segments.extend(("tenant", _sanitize_route_component(tenant)))
    if environment is not None:
        segments.extend(("env", _sanitize_route_component(environment)))
    return "-".join(segments)


def _default_route_id(
    service_id: str,
    *,
    tenant: str | None,
    environment: str | None,
) -> str:
    return f"{_route_identity_base(service_id, tenant=tenant, environment=environment)}-active"


def _version_route_id(
    service_id: str,
    version_number: Any,
    *,
    tenant: str | None,
    environment: str | None,
) -> str:
    if not isinstance(version_number, int):
        raise RuntimeError("Version route configuration is missing a valid version_number.")
    return (
        f"{_route_identity_base(service_id, tenant=tenant, environment=environment)}"
        f"-v{version_number}"
    )


def _route_service_identity(route_config: dict[str, Any]) -> tuple[str, str | None, str | None]:
    return (
        str(route_config["service_id"]),
        _normalize_scope_value(route_config.get("tenant")),
        _normalize_scope_value(route_config.get("environment")),
    )


def _matching_route_documents(
    previous_routes: dict[str, dict[str, Any]] | None,
    service_identity: tuple[str, str | None, str | None],
) -> dict[str, dict[str, Any]]:
    if not previous_routes:
        return {}
    return {
        route_id: document
        for route_id, document in previous_routes.items()
        if _is_managed_route_document(route_id, document)
        and _route_belongs_to_service(document, service_identity)
    }


def _route_config_with_scope(
    route_config: dict[str, Any],
    *,
    tenant: Any,
    environment: Any,
) -> dict[str, Any]:
    normalized_tenant = _normalize_scope_value(tenant)
    normalized_environment = _normalize_scope_value(environment)
    if normalized_tenant is None and normalized_environment is None:
        return route_config
    normalized_route_config = dict(route_config)
    if normalized_tenant is not None:
        normalized_route_config["tenant"] = normalized_tenant
    if normalized_environment is not None:
        normalized_route_config["environment"] = normalized_environment
    return normalized_route_config


def _validated_route_config(route_config: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_route_config(route_config)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid gateway route configuration: {exc}") from exc


__all__ = [
    "GatewayBindingService",
    "configure_gateway_binding_service",
    "dispose_gateway_binding_service",
    "get_gateway_binding_service",
    "resolve_gateway_binding_service",
]
