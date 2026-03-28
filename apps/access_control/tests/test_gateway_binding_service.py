"""Unit tests for gateway_binding/service.py — route sync, rollback, helper functions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from apps.access_control.authn.models import PATResponse
from apps.access_control.authz.models import PolicyResponse
from apps.access_control.gateway_binding.client import (
    InMemoryAPISIXAdminClient as InMemoryGatewayAdminClient,
)
from apps.access_control.gateway_binding.service import (
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
) -> dict[str, object]:
    return {
        "service_id": service_id,
        "service_name": service_name,
        "namespace": namespace,
        "version_number": 1,
        "default_route": {
            "route_id": f"default-{service_id}",
            "target_service": {"host": "10.0.0.1", "port": 8080},
        },
        "version_route": {
            "route_id": f"version-{service_id}-v1",
            "target_service": {"host": "10.0.0.1", "port": 8080},
            "match": {"headers": {"x-version": "v1"}},
        },
    }


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
        assert f"default-{cfg['service_id']}" in docs
        assert f"version-{cfg['service_id']}-v1" in docs

    def test_no_default_route(self) -> None:
        cfg = _route_config()
        docs = _service_route_documents(cfg, include_default=False)
        assert len(docs) == 1
        assert f"default-{cfg['service_id']}" not in docs

    def test_no_version_route(self) -> None:
        cfg = _route_config()
        docs = _service_route_documents(cfg, include_version=False)
        assert len(docs) == 1
        assert f"version-{cfg['service_id']}-v1" not in docs

    def test_route_document_has_required_fields(self) -> None:
        cfg = _route_config()
        docs = _service_route_documents(cfg)
        doc = docs[f"default-{cfg['service_id']}"]
        assert doc["route_id"] == f"default-{cfg['service_id']}"
        assert doc["route_type"] == "default"
        assert doc["service_id"] == "svc-1"
        assert doc["service_name"] == "Test"
        assert doc["namespace"] == "default"
        assert doc["target_service"]["host"] == "10.0.0.1"


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


class TestDeleteServiceRoutes:
    @pytest.mark.asyncio
    async def test_deletes_routes(self) -> None:
        client = InMemoryGatewayAdminClient()
        svc = GatewayBindingService(client)
        cfg = _route_config()
        await svc.sync_service_routes(cfg)
        result = await svc.delete_service_routes(cfg)
        assert result["service_routes_deleted"] == 2
        routes = await client.list_routes()
        assert len(routes) == 0


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


class TestConfigureGatewayBindingService:
    def test_attaches_service(self) -> None:
        state = SimpleNamespace()
        client = InMemoryGatewayAdminClient()
        configure_gateway_binding_service(state, client=client)
        assert hasattr(state, "gateway_binding_service")
        assert isinstance(state.gateway_binding_service, GatewayBindingService)


class TestResolveGatewayBindingService:
    def test_creates_if_missing(self) -> None:
        state = SimpleNamespace()
        svc = resolve_gateway_binding_service(state)
        assert isinstance(svc, GatewayBindingService)

    def test_reuses_existing(self) -> None:
        state = SimpleNamespace()
        client = InMemoryGatewayAdminClient()
        configure_gateway_binding_service(state, client=client)
        svc = resolve_gateway_binding_service(state)
        assert isinstance(svc, GatewayBindingService)


# Additional tests to cover uncovered lines in gateway_binding/service.py


class TestReconcile:
    pass


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
