"""Rollback workflow for reverting a service to a previous active version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from libs.registry_client.models import ArtifactVersionResponse, ArtifactVersionUpdate


@dataclass(frozen=True)
class RollbackRequest:
    """Input for a rollback execution."""

    service_id: str
    target_version: int


@dataclass(frozen=True)
class RollbackResult:
    """Outcome of a completed rollback."""

    service_id: str
    previous_active_version: int | None
    target_version: int
    deployment_revision: str
    validation_report: dict[str, Any]


class RollbackVersionStore(Protocol):
    """Registry interactions required by the rollback workflow."""

    async def get_version(
        self,
        service_id: str,
        version_number: int,
    ) -> ArtifactVersionResponse | None: ...

    async def get_active_version(self, service_id: str) -> ArtifactVersionResponse | None: ...

    async def update_version(
        self,
        service_id: str,
        version_number: int,
        payload: ArtifactVersionUpdate,
    ) -> ArtifactVersionResponse | None: ...

    async def activate_version(
        self,
        service_id: str,
        version_number: int,
    ) -> ArtifactVersionResponse | None: ...


class RollbackDeployer(Protocol):
    """Deployment interactions required by the rollback workflow."""

    async def apply_version(self, version: ArtifactVersionResponse) -> str: ...

    async def wait_for_rollout(self, deployment_revision: str) -> None: ...


class RollbackValidator(Protocol):
    """Post-deploy validation required by the rollback workflow."""

    async def validate(self, version: ArtifactVersionResponse) -> dict[str, Any]: ...


class RollbackWorkflow:
    """Orchestrate rollback to a previously compiled service version."""

    def __init__(
        self,
        *,
        store: RollbackVersionStore,
        deployer: RollbackDeployer,
        validator: RollbackValidator,
    ) -> None:
        self._store = store
        self._deployer = deployer
        self._validator = validator

    async def run(self, request: RollbackRequest) -> RollbackResult:
        current_active = await self._store.get_active_version(request.service_id)
        target_version = await self._store.get_version(request.service_id, request.target_version)
        if target_version is None:
            raise ValueError(
                f"Rollback target {request.service_id} v{request.target_version} was not found."
            )

        deployment_revision = await self._deployer.apply_version(target_version)
        await self._deployer.wait_for_rollout(deployment_revision)
        validation_report = await self._validator.validate(target_version)
        if not bool(validation_report.get("overall_passed", False)):
            raise RuntimeError(
                f"Rollback validation failed for {request.service_id} v{request.target_version}."
            )

        await self._store.update_version(
            request.service_id,
            request.target_version,
            ArtifactVersionUpdate(
                deployment_revision=deployment_revision,
                validation_report=validation_report,
            ),
        )
        activated = await self._store.activate_version(request.service_id, request.target_version)
        if activated is None:
            raise RuntimeError(
                f"Rollback activation failed for {request.service_id} v{request.target_version}."
            )

        return RollbackResult(
            service_id=request.service_id,
            previous_active_version=current_active.version_number if current_active else None,
            target_version=activated.version_number,
            deployment_revision=deployment_revision,
            validation_report=validation_report,
        )


__all__ = ["RollbackRequest", "RollbackResult", "RollbackWorkflow"]
