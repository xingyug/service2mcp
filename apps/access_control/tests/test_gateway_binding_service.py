"""Unit tests for gateway_binding/service.py — route sync, rollback, helper functions."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from apps.access_control.authn.models import PATResponse
from apps.access_control.authz.models import PolicyResponse
from apps.access_control.gateway_binding.client import (
    InMemoryAPISIXAdminClient as InMemoryGatewayAdminClient,
)
from apps.access_control.gateway_binding.service import (
    GatewayBindingNotConfiguredError,
    GatewayBindingService,
    _consumer_id,
    _policy_binding_id,
    _service_route_documents,
    configure_gateway_binding_service,
    resolve_gateway_binding_service,
)
from libs.ir.models import RiskLevel


def _pat_response(
    pat_id: UUID | None = None,
    username: str = "alice",
    name: str = "my-pat",
) -> PATResponse:
    return PATResponse(
        id=pat_id or uuid4(),
        username=username,
        name=name,
        created_at=datetime.now(UTC),
        revoked_at=None,
    )


def _policy_response(policy_id: UUID | None = None, **overrides: object) -> PolicyResponse:
    defaults: dict[str, object] = {
        "id": policy_id or uuid4(),
        "subject_type": "user",
        "subject_id": "alice",
        "resource_id": "svc-1",
        "action_pattern": "*",
        "risk_threshold": RiskLevel.safe,
        "decision": "allow",
        "created_by": "admin",
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return PolicyResponse(**defaults)  # type: ignore[arg-type,unused-ignore]


def _route_config(
    service_id: str = "svc-1",
    service_name: str = "Test",
    namespace: str = "default",
    version_number: int = 1,
    tenant: str | None = None,
    environment: str | None = None,
) -> dict[str, object]:
    route_id_base = service_id
    if tenant:
        route_id_base = f"{route_id_base}-tenant-{tenant.lower().replace(' ', '-')}"
    if environment:
        route_id_base = f"{route_id_base}-env-{environment.lower().replace(' ', '-')}"
    route_config: dict[str, object] = {
        "service_id": service_id,
        "service_name": service_name,
        "namespace": namespace,
        "version_number": version_number,
        "default_route": {
            "route_id": f"{route_id_base}-active",
            "target_service": {"host": "10.0.0.1", "port": 8080},
        },
        "version_route": {
            "route_id": f"{route_id_base}-v{version_number}",
            "target_service": {"host": "10.0.0.1", "port": 8080},
            "match": {"headers": {"x-version": f"v{version_number}"}},
        },
    }
    if tenant is not None:
        route_config["tenant"] = tenant
    if environment is not None:
        route_config["environment"] = environment
    return route_config


class TestConsumerId:
    def test_format(self) -> None:
        pid = uuid4()
        assert _consumer_id(pid) == f"pat-{pid}"


class TestPolicyBindingId:
    def test_format(self) -> None:
        pid = uuid4()
        assert _policy_binding_id(pid) == f"policy-{pid}"


class TestServiceRouteDocuments:
    def test_both_routes(self) -> None:
        cfg = _route_config()
        docs = _service_route_documents(cfg)
        assert len(docs) == 2
        assert f"{cfg['service_id']}-active" in docs
        assert f"{cfg['service_id']}-v1" in docs

    def test_no_default_route(self) -> None:
        cfg = _route_config()
        docs = _service_route_documents(cfg, include_default=False)
        assert len(docs) == 1
        assert f"{cfg['service_id']}-active" not in docs

    def test_no_version_route(self) -> None:
        cfg = _route_config()
        docs = _service_route_documents(cfg, include_version=False)
        assert len(docs) == 1
        assert f"{cfg['service_id']}-v1" not in docs

    def test_route_document_has_required_fields(self) -> None:
        cfg = _route_config()
        docs = _service_route_documents(cfg)
        doc = docs[f"{cfg['service_id']}-active"]
        assert doc["route_id"] == f"{cfg['service_id']}-active"
        assert doc["route_type"] == "default"
        assert doc["service_id"] == "svc-1"
        assert doc["service_name"] == "Test"
        assert doc["namespace"] == "default"
        assert doc["target_service"]["host"] == "10.0.0.1"

    def test_route_document_preserves_scope(self) -> None:
        cfg = _route_config(tenant="Team A", environment="Prod")
        docs = _service_route_documents(cfg)

        assert "svc-1-tenant-team-a-env-prod-active" in docs
        assert docs["svc-1-tenant-team-a-env-prod-active"]["tenant"] == "Team A"
        assert docs["svc-1-tenant-team-a-env-prod-active"]["environment"] == "Prod"

    def test_invalid_top_level_route_config_raises(self) -> None:
        with pytest.raises(RuntimeError, match="Invalid gateway route configuration"):
            _service_route_documents({"service_id": "svc-1", "service_name": "Test"})


class TestSyncPatCreation:
    @pytest.mark.asyncio
    async def test_creates_consumer(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        pat = _pat_response()
        await svc.sync_pat_creation(pat, "pat_plaintext123")
        consumers = await client.list_consumers()
        assert _consumer_id(pat.id) in consumers


class TestSyncPatRevocation:
    @pytest.mark.asyncio
    async def test_deletes_consumer(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        pat = _pat_response()
        await svc.sync_pat_creation(pat, "pat_plaintext123")
        await svc.sync_pat_revocation(pat.id)
        consumers = await client.list_consumers()
        assert _consumer_id(pat.id) not in consumers


class TestSyncPolicy:
    @pytest.mark.asyncio
    async def test_upserts_policy_binding(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        policy = _policy_response()
        await svc.sync_policy(policy)
        bindings = await client.list_policy_bindings()
        assert _policy_binding_id(policy.id) in bindings


class TestDeletePolicy:
    @pytest.mark.asyncio
    async def test_removes_policy_binding(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        policy = _policy_response()
        await svc.sync_policy(policy)
        await svc.delete_policy(policy.id)
        bindings = await client.list_policy_bindings()
        assert _policy_binding_id(policy.id) not in bindings


class TestSyncServiceRoutes:
    @pytest.mark.asyncio
    async def test_creates_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        result = await svc.sync_service_routes(cfg)
        assert result["service_routes_synced"] == 2
        routes = await client.list_routes()
        assert len(routes) == 2

    @pytest.mark.asyncio
    async def test_returns_previous_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        await svc.sync_service_routes(cfg)
        result = await svc.sync_service_routes(cfg)
        assert len(result["previous_routes"]) == 2

    @pytest.mark.asyncio
    async def test_deletes_stale_version_routes_for_same_service(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        current_cfg = _route_config(version_number=7)
        target_cfg = _route_config(version_number=3)

        await svc.sync_service_routes(current_cfg)
        result = await svc.sync_service_routes(target_cfg)

        routes = await client.list_routes()
        assert "svc-1-v7" not in routes
        assert "svc-1-v3" in routes
        assert result["service_routes_deleted"] == 1
        assert "svc-1-v7" in result["previous_routes"]

    @pytest.mark.asyncio
    async def test_keeps_routes_for_other_scope_with_same_service_id(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        prod_cfg = _route_config(tenant="team-a", environment="prod")
        staging_cfg = _route_config(tenant="team-a", environment="staging")

        await svc.sync_service_routes(prod_cfg)
        result = await svc.sync_service_routes(staging_cfg)

        routes = await client.list_routes()
        assert "svc-1-tenant-team-a-env-prod-active" in routes
        assert "svc-1-tenant-team-a-env-prod-v1" in routes
        assert "svc-1-tenant-team-a-env-staging-active" in routes
        assert "svc-1-tenant-team-a-env-staging-v1" in routes
        assert result["service_routes_deleted"] == 0

    @pytest.mark.asyncio
    async def test_invalid_nested_route_definition_raises(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        default_route = cfg["default_route"]
        assert isinstance(default_route, dict)
        default_route.pop("target_service")

        with pytest.raises(RuntimeError, match="Invalid gateway route configuration"):
            await svc.sync_service_routes(cfg)

    @pytest.mark.asyncio
    async def test_missing_required_route_definition_field_raises(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        default_route = cfg["default_route"]
        assert isinstance(default_route, dict)
        default_route.pop("route_id")

        with pytest.raises(RuntimeError, match="Invalid gateway route configuration"):
            await svc.sync_service_routes(cfg)

    @pytest.mark.asyncio
    async def test_ignores_foreign_previous_routes_when_pruning_stale_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        foreign_route = {
            "route_id": "foreign-admin",
            "route_type": "default",
            "service_id": "admin-ui",
            "service_name": "Admin UI",
            "namespace": "default",
            "target_service": {"host": "10.0.0.9", "port": 8080},
        }
        await client.upsert_route(route_id="foreign-admin", document=foreign_route)

        result = await svc.sync_service_routes(
            cfg,
            previous_routes={"foreign-admin": foreign_route},
        )

        assert result["service_routes_deleted"] == 0
        routes = await client.list_routes()
        assert "foreign-admin" in routes

    @pytest.mark.asyncio
    async def test_uses_canonical_route_ids_instead_of_request_route_ids(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        default_route = cfg["default_route"]
        version_route = cfg["version_route"]
        assert isinstance(default_route, dict)
        assert isinstance(version_route, dict)
        default_route["route_id"] = "foreign-admin"
        version_route["route_id"] = "foreign-version"
        foreign_route = {
            "route_id": "foreign-admin",
            "route_type": "default",
            "service_id": "admin-ui",
            "service_name": "Admin UI",
            "namespace": "default",
            "target_service": {"host": "10.0.0.9", "port": 8080},
        }
        await client.upsert_route(route_id="foreign-admin", document=foreign_route)

        result = await svc.sync_service_routes(cfg)

        assert result["route_ids"] == ["svc-1-active", "svc-1-v1"]
        routes = await client.list_routes()
        assert "foreign-admin" in routes
        assert routes["foreign-admin"].document == foreign_route


class TestDeleteServiceRoutes:
    @pytest.mark.asyncio
    async def test_deletes_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        await svc.sync_service_routes(cfg)
        result = await svc.delete_service_routes(cfg)
        assert result["service_routes_deleted"] == 2
        assert len(result["previous_routes"]) == 2
        routes = await client.list_routes()
        assert len(routes) == 0

    @pytest.mark.asyncio
    async def test_delete_uses_canonical_route_ids(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        default_route = cfg["default_route"]
        version_route = cfg["version_route"]
        assert isinstance(default_route, dict)
        assert isinstance(version_route, dict)
        default_route["route_id"] = "foreign-admin"
        version_route["route_id"] = "foreign-version"
        foreign_route = {
            "route_id": "foreign-admin",
            "route_type": "default",
            "service_id": "admin-ui",
            "service_name": "Admin UI",
            "namespace": "default",
            "target_service": {"host": "10.0.0.9", "port": 8080},
        }
        await client.upsert_route(route_id="foreign-admin", document=foreign_route)

        result = await svc.delete_service_routes(cfg)

        assert result["route_ids"] == ["svc-1-active", "svc-1-v1"]
        routes = await client.list_routes()
        assert "foreign-admin" in routes


class TestRollbackServiceRoutes:
    @pytest.mark.asyncio
    async def test_restores_previous_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        original = await svc.sync_service_routes(cfg)
        previous_routes = original["previous_routes"]
        # Now change routes (simulated by deleting them)
        await svc.delete_service_routes(cfg)
        # Rollback
        result = await svc.rollback_service_routes(cfg, previous_routes)
        assert result["service_routes_deleted"] == 2  # new routes deleted
        assert result["service_routes_synced"] == 0  # no previous routes to restore

    @pytest.mark.asyncio
    async def test_rollback_with_real_previous(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        # First sync: creates routes
        await svc.sync_service_routes(cfg)
        # Second sync: returns the old docs as previous
        second = await svc.sync_service_routes(cfg)
        previous = second["previous_routes"]
        # Delete current routes
        await svc.delete_service_routes(cfg)
        # Rollback restores previous
        result = await svc.rollback_service_routes(cfg, previous)
        assert result["service_routes_synced"] == len(previous)
        routes = await client.list_routes()
        assert len(routes) == len(previous)

    @pytest.mark.asyncio
    async def test_rollback_ignores_foreign_previous_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        await svc.sync_service_routes(cfg)
        foreign_route = {
            "route_id": "foreign-admin",
            "route_type": "default",
            "service_id": "admin-ui",
            "service_name": "Admin UI",
            "namespace": "default",
            "target_service": {"host": "10.0.0.9", "port": 8080},
        }

        result = await svc.rollback_service_routes(
            cfg,
            {"foreign-admin": foreign_route},
        )

        assert result["service_routes_synced"] == 0
        routes = await client.list_routes()
        assert "foreign-admin" not in routes


class TestConfigureGatewayBindingService:
    def test_attaches_service(self) -> None:
        state = SimpleNamespace()
        client = InMemoryGatewayAdminClient()
        configure_gateway_binding_service(state, client=client)
        assert hasattr(state, "gateway_binding_service")
        assert isinstance(state.gateway_binding_service, GatewayBindingService)

    def test_records_configuration_error_when_env_missing(self) -> None:
        state = SimpleNamespace()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GATEWAY_ADMIN_URL", None)
            configure_gateway_binding_service(state)

        assert getattr(state, "gateway_binding_service", None) is None
        assert "GATEWAY_ADMIN_URL" in state.gateway_binding_error


class TestResolveGatewayBindingService:
    def test_raises_when_gateway_binding_is_unconfigured(self) -> None:
        state = SimpleNamespace()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GATEWAY_ADMIN_URL", None)
            with pytest.raises(
                GatewayBindingNotConfiguredError,
                match="GATEWAY_ADMIN_URL must be configured",
            ):
                resolve_gateway_binding_service(state)

    def test_reuses_existing(self) -> None:
        state = SimpleNamespace()
        client = InMemoryGatewayAdminClient()
        configure_gateway_binding_service(state, client=client)
        svc = resolve_gateway_binding_service(state)
        assert isinstance(svc, GatewayBindingService)


# Additional tests to cover uncovered lines in gateway_binding/service.py


class TestListServiceRoutes:
    @pytest.mark.asyncio
    async def test_empty(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        result = await svc.list_service_routes()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_sorted_documents(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        for route_id in ("z-route", "a-route", "m-route"):
            await client.upsert_route(
                route_id=route_id,
                document={
                    "route_id": route_id,
                    "route_type": "default",
                    "service_id": route_id,
                    "service_name": route_id.upper(),
                    "namespace": "default",
                    "target_service": {"host": "10.0.0.1", "port": 8080},
                },
            )
        result = await svc.list_service_routes()
        assert [document["route_id"] for document in result] == ["a-route", "m-route", "z-route"]

    @pytest.mark.asyncio
    async def test_filters_out_unmanaged_route_documents(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        managed_docs = _service_route_documents(cfg)
        await client.upsert_route(
            route_id="external-route",
            document={"uri": "/manual"},
        )
        for route_id, document in managed_docs.items():
            await client.upsert_route(route_id=route_id, document=document)

        result = await svc.list_service_routes()

        assert result == [managed_docs[route_id] for route_id in sorted(managed_docs)]


def _mock_session(
    pats_users: list | None = None,
    policies: list | None = None,
    service_versions: list | None = None,
) -> AsyncMock:
    """Build a mock AsyncSession for reconcile tests."""
    session = AsyncMock()

    pat_result = MagicMock()
    pat_result.all.return_value = pats_users or []
    session.execute.return_value = pat_result

    policy_scalars = MagicMock()
    policy_scalars.all.return_value = policies or []
    sv_scalars = MagicMock()
    sv_scalars.all.return_value = service_versions or []
    session.scalars = AsyncMock(side_effect=[policy_scalars, sv_scalars])

    return session


_ZERO_STATS = {
    "consumers_synced": 0,
    "consumers_deleted": 0,
    "policy_bindings_synced": 0,
    "policy_bindings_deleted": 0,
    "service_routes_synced": 0,
    "service_routes_deleted": 0,
}


class TestReconcile:
    @pytest.mark.asyncio
    async def test_empty_state(self) -> None:
        """No data in DB, no data in gateway → all zeros."""
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session())
        assert result == _ZERO_STATS

    @pytest.mark.asyncio
    async def test_syncs_missing_consumers(self) -> None:
        pat_id, user_id = uuid4(), uuid4()
        pat = SimpleNamespace(
            id=pat_id,
            user_id=user_id,
            token_hash="hash123",
            name="my-pat",
            created_at=datetime.now(UTC),
            revoked_at=None,
        )
        user = SimpleNamespace(id=user_id, username="alice")

        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(pats_users=[(pat, user)]))

        assert result["consumers_synced"] == 1
        assert result["consumers_deleted"] == 0
        consumers = await client.list_consumers()
        assert f"pat-{pat_id}" in consumers

    @pytest.mark.asyncio
    async def test_deletes_orphan_consumers(self) -> None:
        client = InMemoryGatewayAdminClient()
        await client.upsert_consumer(
            consumer_id="pat-orphan",
            username="ghost",
            credential="old_hash",
            metadata={},
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session())

        assert result["consumers_deleted"] == 1
        consumers = await client.list_consumers()
        assert "pat-orphan" not in consumers

    @pytest.mark.asyncio
    async def test_keeps_unmanaged_consumers(self) -> None:
        client = InMemoryGatewayAdminClient()
        await client.upsert_consumer(
            consumer_id="external-consumer",
            username="ghost",
            credential="old_hash",
            metadata={},
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session())

        assert result["consumers_deleted"] == 0
        consumers = await client.list_consumers()
        assert "external-consumer" in consumers

    @pytest.mark.asyncio
    async def test_resyncs_credential_mismatch(self) -> None:
        pat_id, user_id = uuid4(), uuid4()
        pat = SimpleNamespace(
            id=pat_id,
            user_id=user_id,
            token_hash="new_hash",
            name="my-pat",
            created_at=datetime.now(UTC),
            revoked_at=None,
        )
        user = SimpleNamespace(id=user_id, username="alice")

        client = InMemoryGatewayAdminClient()
        await client.upsert_consumer(
            consumer_id=f"pat-{pat_id}",
            username="alice",
            credential="old_hash",
            metadata={},
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(pats_users=[(pat, user)]))

        assert result["consumers_synced"] == 1
        assert result["consumers_deleted"] == 0
        consumer = (await client.list_consumers())[f"pat-{pat_id}"]
        assert consumer.credential == "new_hash"

    @pytest.mark.asyncio
    async def test_consumer_already_in_sync(self) -> None:
        pat_id, user_id = uuid4(), uuid4()
        now = datetime.now(UTC)
        pat = SimpleNamespace(
            id=pat_id,
            user_id=user_id,
            token_hash="same_hash",
            name="my-pat",
            created_at=now,
            revoked_at=None,
        )
        user = SimpleNamespace(id=user_id, username="alice")

        client = InMemoryGatewayAdminClient()
        await client.upsert_consumer(
            consumer_id=f"pat-{pat_id}",
            username="alice",
            credential="same_hash",
            metadata={"username": "alice", "pat_name": "my-pat", "created_at": now.isoformat()},
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(pats_users=[(pat, user)]))

        assert result["consumers_synced"] == 0
        assert result["consumers_deleted"] == 0

    @pytest.mark.asyncio
    async def test_resyncs_consumer_username_or_metadata_drift(self) -> None:
        pat_id, user_id = uuid4(), uuid4()
        now = datetime.now(UTC)
        pat = SimpleNamespace(
            id=pat_id,
            user_id=user_id,
            token_hash="same_hash",
            name="my-pat",
            created_at=now,
            revoked_at=None,
        )
        user = SimpleNamespace(id=user_id, username="alice")

        client = InMemoryGatewayAdminClient()
        await client.upsert_consumer(
            consumer_id=f"pat-{pat_id}",
            username="tampered",
            credential="same_hash",
            metadata={"username": "tampered", "pat_name": "wrong", "created_at": now.isoformat()},
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(pats_users=[(pat, user)]))

        assert result["consumers_synced"] == 1
        consumer = (await client.list_consumers())[f"pat-{pat_id}"]
        assert consumer.username == "alice"
        assert consumer.metadata["username"] == "alice"
        assert consumer.metadata["pat_name"] == "my-pat"

    @pytest.mark.asyncio
    async def test_syncs_missing_policy_bindings(self) -> None:
        policy_id = uuid4()
        policy = SimpleNamespace(
            id=policy_id,
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="*",
            risk_threshold="safe",
            decision="allow",
            created_by="admin",
            created_at=datetime.now(UTC),
        )

        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(policies=[policy]))

        assert result["policy_bindings_synced"] == 1
        assert result["policy_bindings_deleted"] == 0
        bindings = await client.list_policy_bindings()
        assert f"policy-{policy_id}" in bindings

    @pytest.mark.asyncio
    async def test_deletes_orphan_policy_bindings(self) -> None:
        client = InMemoryGatewayAdminClient()
        await client.upsert_policy_binding(
            binding_id="policy-orphan",
            document={"old": True},
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session())

        assert result["policy_bindings_deleted"] == 1
        bindings = await client.list_policy_bindings()
        assert "policy-orphan" not in bindings

    @pytest.mark.asyncio
    async def test_keeps_unmanaged_policy_bindings(self) -> None:
        client = InMemoryGatewayAdminClient()
        await client.upsert_policy_binding(
            binding_id="external-binding",
            document={"old": True},
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session())

        assert result["policy_bindings_deleted"] == 0
        bindings = await client.list_policy_bindings()
        assert "external-binding" in bindings

    @pytest.mark.asyncio
    async def test_resyncs_policy_document_mismatch(self) -> None:
        policy_id = uuid4()
        policy = SimpleNamespace(
            id=policy_id,
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="*",
            risk_threshold="safe",
            decision="allow",
            created_by="admin",
            created_at=datetime.now(UTC),
        )

        client = InMemoryGatewayAdminClient()
        await client.upsert_policy_binding(
            binding_id=f"policy-{policy_id}",
            document={"stale": True},
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(policies=[policy]))

        assert result["policy_bindings_synced"] == 1
        assert result["policy_bindings_deleted"] == 0

    @pytest.mark.asyncio
    async def test_syncs_missing_routes(self) -> None:
        sv = SimpleNamespace(route_config=_route_config(), is_active=True)

        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(service_versions=[sv]))

        assert result["service_routes_synced"] == 2
        assert result["service_routes_deleted"] == 0
        routes = await client.list_routes()
        assert len(routes) == 2

    @pytest.mark.asyncio
    async def test_scopes_reconcile_route_ids_from_service_version_scope(self) -> None:
        scoped_route_config = _route_config()
        scoped_version = SimpleNamespace(
            route_config=scoped_route_config,
            is_active=True,
            tenant="Team A",
            environment="Prod",
        )

        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(service_versions=[scoped_version]))

        assert result["service_routes_synced"] == 2
        routes = await client.list_routes()
        assert "svc-1-tenant-team-a-env-prod-active" in routes
        assert "svc-1-tenant-team-a-env-prod-v1" in routes
        assert routes["svc-1-tenant-team-a-env-prod-active"].document["tenant"] == "Team A"
        assert routes["svc-1-tenant-team-a-env-prod-active"].document["environment"] == "Prod"

    @pytest.mark.asyncio
    async def test_deletes_orphan_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        await client.upsert_route(
            route_id="orphan-route",
            document={
                "route_id": "orphan-route",
                "route_type": "default",
                "service_id": "orphan-svc",
                "service_name": "Orphan Service",
                "namespace": "default",
                "target_service": {"host": "10.0.0.2", "port": 8080},
            },
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session())

        assert result["service_routes_deleted"] == 1
        routes = await client.list_routes()
        assert "orphan-route" not in routes

    @pytest.mark.asyncio
    async def test_keeps_unmanaged_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        await client.upsert_route(route_id="external-route", document={"stale": True})
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session())

        assert result["service_routes_deleted"] == 0
        routes = await client.list_routes()
        assert "external-route" in routes

    @pytest.mark.asyncio
    async def test_resyncs_route_document_mismatch(self) -> None:
        sv = SimpleNamespace(route_config=_route_config(), is_active=True)
        expected_docs = _service_route_documents(
            sv.route_config,
            include_default=True,
            include_version=True,
        )
        route_id = next(iter(expected_docs))

        client = InMemoryGatewayAdminClient()
        await client.upsert_route(
            route_id=route_id,
            document={
                **expected_docs[route_id],
                "target_service": {"host": "10.0.0.9", "port": 9000},
            },
        )
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(service_versions=[sv]))

        assert result["service_routes_synced"] == 2
        updated = (await client.list_routes())[route_id]
        assert updated.document == expected_docs[route_id]

    @pytest.mark.asyncio
    async def test_skips_non_dict_route_config(self) -> None:
        sv = SimpleNamespace(route_config="not-a-dict", is_active=True)

        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(service_versions=[sv]))

        assert result["service_routes_synced"] == 0
        assert result["service_routes_deleted"] == 0

    @pytest.mark.asyncio
    async def test_inactive_version_omits_default_route(self) -> None:
        sv = SimpleNamespace(route_config=_route_config(), is_active=False)

        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        result = await svc.reconcile(_mock_session(service_versions=[sv]))

        assert result["service_routes_synced"] == 1
        routes = await client.list_routes()
        assert list(routes.keys()) == ["svc-1-v1"]

    @pytest.mark.asyncio
    async def test_full_reconcile_mixed(self) -> None:
        """Mix of create, update, and delete across all resource types."""
        pat_id, user_id = uuid4(), uuid4()
        pat = SimpleNamespace(
            id=pat_id,
            user_id=user_id,
            token_hash="hash123",
            name="my-pat",
            created_at=datetime.now(UTC),
            revoked_at=None,
        )
        user = SimpleNamespace(id=user_id, username="alice")

        policy_id = uuid4()
        policy = SimpleNamespace(
            id=policy_id,
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="*",
            risk_threshold="safe",
            decision="allow",
            created_by="admin",
            created_at=datetime.now(UTC),
        )

        sv = SimpleNamespace(route_config=_route_config(), is_active=True)

        client = InMemoryGatewayAdminClient()
        await client.upsert_consumer(
            consumer_id="pat-orphan",
            username="ghost",
            credential="old",
            metadata={},
        )
        await client.upsert_policy_binding(
            binding_id="policy-orphan",
            document={"old": True},
        )
        await client.upsert_route(
            route_id="orphan-route",
            document={
                "route_id": "orphan-route",
                "route_type": "default",
                "service_id": "orphan-svc",
                "service_name": "Orphan Service",
                "namespace": "default",
                "target_service": {"host": "10.0.0.2", "port": 8080},
            },
        )

        svc = GatewayBindingService(client)
        result = await svc.reconcile(
            _mock_session(
                pats_users=[(pat, user)],
                policies=[policy],
                service_versions=[sv],
            )
        )

        assert result["consumers_synced"] == 1
        assert result["consumers_deleted"] == 1
        assert result["policy_bindings_synced"] == 1
        assert result["policy_bindings_deleted"] == 1
        assert result["service_routes_synced"] == 2
        assert result["service_routes_deleted"] == 1

    @pytest.mark.asyncio
    async def test_sync_service_routes_refuses_unmanaged_route_collision(self) -> None:
        client = InMemoryGatewayAdminClient()
        cfg = _route_config()
        await client.upsert_route(
            route_id="svc-1-active",
            document={"uri": "/manual"},
        )
        svc = GatewayBindingService(client)

        with pytest.raises(
            RuntimeError,
            match="Refusing to overwrite unmanaged gateway route svc-1-active",
        ):
            await svc.sync_service_routes(cfg)


class TestDisposeGatewayBindingService:
    async def test_no_service_to_dispose(self):
        """Test lines 249-251: dispose when no service exists."""
        from apps.access_control.gateway_binding.service import dispose_gateway_binding_service

        state = SimpleNamespace()  # No gateway_binding_service attribute

        # Should not raise any exception
        await dispose_gateway_binding_service(state)

    async def test_disposes_service_with_aclose(self):
        """Test lines 252-254: dispose service with aclose method."""
        from unittest.mock import AsyncMock

        from apps.access_control.gateway_binding.service import dispose_gateway_binding_service

        state = SimpleNamespace()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        state.gateway_binding_service = SimpleNamespace(_client=mock_client)

        await dispose_gateway_binding_service(state)

        mock_client.aclose.assert_called_once()

    async def test_disposes_service_without_aclose(self):
        """Test lines 252-254: dispose service without aclose method."""
        from apps.access_control.gateway_binding.service import dispose_gateway_binding_service

        state = SimpleNamespace()
        mock_client = SimpleNamespace()  # No aclose method
        state.gateway_binding_service = SimpleNamespace(_client=mock_client)

        # Should not raise any exception
        await dispose_gateway_binding_service(state)


class TestPolicyDocument:
    def test_with_policy_response(self):
        """Test lines 267-269: _policy_document with PolicyResponse."""
        from apps.access_control.gateway_binding.service import _policy_document

        policy = _policy_response()
        doc = _policy_document(policy)

        assert doc["id"] == str(policy.id)
        assert doc["risk_threshold"] == policy.risk_threshold.value
        assert doc["created_at"] == policy.created_at.isoformat()
        assert doc["created_by"] == policy.created_by

    def test_with_policy_model(self):
        """Test lines 270-273: _policy_document with Policy model."""
        from types import SimpleNamespace

        from apps.access_control.gateway_binding.service import _policy_document

        # Mock Policy object
        policy = SimpleNamespace(
            id=uuid4(),
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="*",
            risk_threshold="safe",
            decision="allow",
            created_by="admin",
            created_at=datetime.now(UTC),
        )

        doc = _policy_document(policy)

        assert doc["id"] == str(policy.id)
        assert doc["risk_threshold"] == "safe"  # RiskLevel enum value
        assert doc["created_at"] == policy.created_at.isoformat()
        assert doc["created_by"] == "admin"
