"""Unit tests for apps/compiler_worker/workflows/rollback_workflow.py."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest

from apps.compiler_worker.workflows.rollback_workflow import (
    RollbackRequest,
    RollbackResult,
    RollbackWorkflow,
)
from libs.registry_client.models import (
    ArtifactVersionResponse,
    ArtifactVersionUpdate,
)


def _make_version(
    *,
    service_id: str = "svc-1",
    version_number: int = 1,
    is_active: bool = False,
) -> ArtifactVersionResponse:
    from libs.ir.models import ServiceIR

    ir = ServiceIR(
        service_id=service_id,
        service_name="Test",
        base_url="https://example.com",
        source_hash="sha256:test",
        protocol="openapi",
        operations=[],
    )
    return ArtifactVersionResponse(
        id=uuid4(),
        service_id=service_id,
        version_number=version_number,
        is_active=is_active,
        ir_json=ir.model_dump(),
        compiler_version="0.1.0",
        created_at=datetime.utcnow(),
    )


class FakeVersionStore:
    def __init__(self) -> None:
        self.versions: dict[tuple[str, int], ArtifactVersionResponse] = {}
        self.active: dict[str, ArtifactVersionResponse] = {}
        self.updates: list[tuple[str, int, ArtifactVersionUpdate]] = []
        self.activated: list[tuple[str, int]] = []
        self.scope_calls: list[tuple[str | None, str | None]] = []

    async def get_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        self.scope_calls.append((tenant, environment))
        return self.versions.get((service_id, version_number))

    async def get_active_version(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        self.scope_calls.append((tenant, environment))
        return self.active.get(service_id)

    async def update_version(
        self,
        service_id: str,
        version_number: int,
        payload: ArtifactVersionUpdate,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        self.scope_calls.append((tenant, environment))
        self.updates.append((service_id, version_number, payload))
        return self.versions.get((service_id, version_number))

    async def activate_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        self.scope_calls.append((tenant, environment))
        self.activated.append((service_id, version_number))
        return self.versions.get((service_id, version_number))


class FakeDeployer:
    def __init__(self, revision: str = "rev-abc") -> None:
        self.revision = revision
        self.applied: list[ArtifactVersionResponse] = []
        self.waited: list[str] = []

    async def apply_version(self, version: ArtifactVersionResponse) -> str:
        self.applied.append(version)
        return self.revision

    async def wait_for_rollout(self, deployment_revision: str) -> None:
        self.waited.append(deployment_revision)


class FakeValidator:
    def __init__(self, passed: bool = True) -> None:
        self._passed = passed

    async def validate(self, version: ArtifactVersionResponse) -> dict[str, Any]:
        return {"overall_passed": self._passed}


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, int]] = []

    async def publish(self, version: ArtifactVersionResponse) -> dict[str, Any]:
        self.published.append((version.service_id, version.version_number))
        return {"service_id": version.service_id, "version_number": version.version_number}


class TestRollbackRequest:
    def test_frozen(self) -> None:
        req = RollbackRequest(service_id="svc-1", target_version=2)
        assert req.service_id == "svc-1"
        with pytest.raises(AttributeError):
            req.service_id = "svc-2"


class TestRollbackResult:
    def test_construction(self) -> None:
        result = RollbackResult(
            service_id="svc-1",
            previous_active_version=1,
            target_version=2,
            deployment_revision="rev-abc",
            validation_report={"overall_passed": True},
        )
        assert result.target_version == 2


class TestRollbackWorkflowSuccess:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        v1 = _make_version(version_number=1, is_active=True)
        v2 = _make_version(version_number=2)
        store = FakeVersionStore()
        store.versions[("svc-1", 2)] = v2
        store.active["svc-1"] = v1
        deployer = FakeDeployer(revision="rev-xyz")
        validator = FakeValidator(passed=True)
        publisher = FakePublisher()
        wf = RollbackWorkflow(
            store=store,
            deployer=deployer,
            validator=validator,
            publisher=publisher,
        )
        result = await wf.run(RollbackRequest(service_id="svc-1", target_version=2))
        assert result.service_id == "svc-1"
        assert result.target_version == 2
        assert result.previous_active_version == 1
        assert result.deployment_revision == "rev-xyz"
        assert result.protocol == "openapi"
        assert deployer.applied == [v2]
        assert deployer.waited == ["rev-xyz"]
        assert len(store.updates) == 1
        assert store.activated == [("svc-1", 2)]
        assert publisher.published == [("svc-1", 2)]

    @pytest.mark.asyncio
    async def test_no_current_active(self) -> None:
        v2 = _make_version(version_number=2)
        store = FakeVersionStore()
        store.versions[("svc-1", 2)] = v2
        deployer = FakeDeployer()
        validator = FakeValidator(passed=True)
        wf = RollbackWorkflow(store=store, deployer=deployer, validator=validator)
        result = await wf.run(RollbackRequest(service_id="svc-1", target_version=2))
        assert result.previous_active_version is None


class TestRollbackWorkflowErrors:
    @pytest.mark.asyncio
    async def test_target_not_found_raises(self) -> None:
        store = FakeVersionStore()
        deployer = FakeDeployer()
        validator = FakeValidator()
        wf = RollbackWorkflow(store=store, deployer=deployer, validator=validator)
        with pytest.raises(ValueError, match="was not found"):
            await wf.run(RollbackRequest(service_id="svc-1", target_version=99))

    @pytest.mark.asyncio
    async def test_validation_failure_raises(self) -> None:
        v2 = _make_version(version_number=2)
        store = FakeVersionStore()
        store.versions[("svc-1", 2)] = v2
        deployer = FakeDeployer()
        validator = FakeValidator(passed=False)
        wf = RollbackWorkflow(store=store, deployer=deployer, validator=validator)
        with pytest.raises(RuntimeError, match="validation failed"):
            await wf.run(RollbackRequest(service_id="svc-1", target_version=2))

    @pytest.mark.asyncio
    async def test_validation_failure_restores_previous_active(self) -> None:
        v1 = _make_version(version_number=1, is_active=True)
        v2 = _make_version(version_number=2)
        store = FakeVersionStore()
        store.versions[("svc-1", 1)] = v1
        store.versions[("svc-1", 2)] = v2
        store.active["svc-1"] = v1
        deployer = FakeDeployer()
        validator = FakeValidator(passed=False)
        wf = RollbackWorkflow(store=store, deployer=deployer, validator=validator)
        with pytest.raises(RuntimeError, match="validation failed"):
            await wf.run(RollbackRequest(service_id="svc-1", target_version=2))
        assert ("svc-1", 1) in store.activated
        assert deployer.applied == [v2, v1]

    @pytest.mark.asyncio
    async def test_validation_failure_restore_error_preserves_validation_failure(self) -> None:
        v1 = _make_version(version_number=1, is_active=True)
        v2 = _make_version(version_number=2)
        store = FakeVersionStore()
        store.versions[("svc-1", 1)] = v1
        store.versions[("svc-1", 2)] = v2
        store.active["svc-1"] = v1
        deployer = FakeDeployer()
        validator = FakeValidator(passed=False)

        async def _raise_on_restore(
            service_id: str,
            version_number: int,
            *,
            tenant: str | None = None,
            environment: str | None = None,
        ) -> ArtifactVersionResponse | None:
            del service_id, version_number, tenant, environment
            raise RuntimeError("db down during restore")

        store.activate_version = _raise_on_restore
        wf = RollbackWorkflow(store=store, deployer=deployer, validator=validator)

        with pytest.raises(RuntimeError, match="Rollback validation failed") as exc_info:
            await wf.run(RollbackRequest(service_id="svc-1", target_version=2))

        assert "db down during restore" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_validation_failure_no_previous_active_skips_restore(self) -> None:
        v2 = _make_version(version_number=2)
        store = FakeVersionStore()
        store.versions[("svc-1", 2)] = v2
        deployer = FakeDeployer()
        validator = FakeValidator(passed=False)
        wf = RollbackWorkflow(store=store, deployer=deployer, validator=validator)
        with pytest.raises(RuntimeError, match="validation failed"):
            await wf.run(RollbackRequest(service_id="svc-1", target_version=2))
        assert store.activated == []

    @pytest.mark.asyncio
    async def test_activation_returns_none_raises(self) -> None:
        v2 = _make_version(version_number=2)
        store = FakeVersionStore()
        store.versions[("svc-1", 2)] = v2
        deployer = FakeDeployer()
        validator = FakeValidator(passed=True)
        wf = RollbackWorkflow(store=store, deployer=deployer, validator=validator)
        # Remove from versions so activate returns None
        store.versions.pop(("svc-1", 2))
        # Re-add for get_version to pass, but activate will fail
        store.versions[("svc-1", 2)] = v2

        # Monkey-patch activate to return None
        async def _activate_none(
            service_id: str,
            version_number: int,
            *,
            tenant: str | None = None,
            environment: str | None = None,
        ) -> None:
            del service_id, version_number, tenant, environment
            return None

        store.activate_version = _activate_none
        with pytest.raises(RuntimeError, match="activation failed"):
            await wf.run(RollbackRequest(service_id="svc-1", target_version=2))

    @pytest.mark.asyncio
    async def test_scope_is_forwarded_to_store(self) -> None:
        v1 = _make_version(version_number=1, is_active=True)
        store = FakeVersionStore()
        store.versions[("svc-1", 1)] = v1
        deployer = FakeDeployer()
        validator = FakeValidator(passed=True)
        wf = RollbackWorkflow(store=store, deployer=deployer, validator=validator)

        await wf.run(
            RollbackRequest(
                service_id="svc-1",
                target_version=1,
                tenant="team-a",
                environment="prod",
            )
        )
        assert store.scope_calls
        assert all(scope == ("team-a", "prod") for scope in store.scope_calls)
