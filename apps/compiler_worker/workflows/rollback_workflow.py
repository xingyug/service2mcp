"""Rollback workflow for reverting a service to a previous active version."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from libs.registry_client.models import ArtifactVersionResponse, ArtifactVersionUpdate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RollbackRequest:
    """Input for a rollback execution."""

    service_id: str
    target_version: int
    tenant: str | None = None
    environment: str | None = None


@dataclass(frozen=True)
class RollbackResult:
    """Outcome of a completed rollback."""

    service_id: str
    previous_active_version: int | None
    target_version: int
    deployment_revision: str
    validation_report: dict[str, Any]
    protocol: str | None = None


class RollbackVersionStore(Protocol):
    """Registry interactions required by the rollback workflow."""

    async def get_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None: ...

    async def get_active_version(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None: ...

    async def update_version(
        self,
        service_id: str,
        version_number: int,
        payload: ArtifactVersionUpdate,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None: ...

    async def activate_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None: ...


class RollbackDeployer(Protocol):
    """Deployment interactions required by the rollback workflow."""

    async def apply_version(self, version: ArtifactVersionResponse) -> str: ...

    async def wait_for_rollout(self, deployment_revision: str) -> None: ...


class RollbackValidator(Protocol):
    """Post-deploy validation required by the rollback workflow."""

    async def validate(self, version: ArtifactVersionResponse) -> dict[str, Any]: ...


class RollbackPublisher(Protocol):
    """Gateway publication required by the rollback workflow."""

    async def publish(self, version: ArtifactVersionResponse) -> dict[str, Any] | None: ...


class RollbackWorkflow:
    """Orchestrate rollback to a previously compiled service version."""

    def __init__(
        self,
        *,
        store: RollbackVersionStore,
        deployer: RollbackDeployer,
        validator: RollbackValidator,
        publisher: RollbackPublisher | None = None,
    ) -> None:
        self._store = store
        self._deployer = deployer
        self._validator = validator
        self._publisher = publisher

    async def run(self, request: RollbackRequest) -> RollbackResult:
        scope = {"tenant": request.tenant, "environment": request.environment}
        current_active = await self._store.get_active_version(request.service_id, **scope)
        target_version = await self._store.get_version(
            request.service_id,
            request.target_version,
            **scope,
        )
        if target_version is None:
            raise ValueError(
                f"Rollback target {request.service_id} v{request.target_version} was not found."
            )

        deployment_revision = await self._deployer.apply_version(target_version)
        await self._deployer.wait_for_rollout(deployment_revision)
        validation_report = await self._validator.validate(target_version)
        if not bool(validation_report.get("overall_passed", False)):
            restore_error: Exception | None = None
            try:
                await self._restore_current_active(
                    current_active,
                    request=request,
                    restore_routes=False,
                )
            except Exception as exc:
                restore_error = exc
            message = (
                f"Rollback validation failed for {request.service_id} v{request.target_version}."
            )
            if restore_error is not None:
                raise RuntimeError(
                    f"{message} Restore attempt also failed: {restore_error}"
                ) from restore_error
            raise RuntimeError(message)

        await self._store.update_version(
            request.service_id,
            request.target_version,
            ArtifactVersionUpdate(
                deployment_revision=deployment_revision,
                validation_report=validation_report,
            ),
            **scope,
        )

        try:
            if self._publisher is not None:
                await self._publisher.publish(target_version)
            activated = await self._store.activate_version(
                request.service_id,
                request.target_version,
                **scope,
            )
        except Exception:
            await self._restore_current_active(
                current_active,
                request=request,
                restore_routes=True,
            )
            raise

        if activated is None:
            await self._restore_current_active(
                current_active,
                request=request,
                restore_routes=True,
            )
            raise RuntimeError(
                f"Rollback activation failed for {request.service_id} v{request.target_version}."
            )

        return RollbackResult(
            service_id=request.service_id,
            previous_active_version=current_active.version_number if current_active else None,
            target_version=activated.version_number,
            deployment_revision=deployment_revision,
            validation_report=validation_report,
            protocol=_version_protocol(activated),
        )

    async def _restore_current_active(
        self,
        current_active: ArtifactVersionResponse | None,
        *,
        request: RollbackRequest,
        restore_routes: bool,
    ) -> None:
        if current_active is None:
            return

        scope = {"tenant": request.tenant, "environment": request.environment}
        fresh_active = await self._store.get_active_version(request.service_id, **scope)
        if fresh_active is None or fresh_active.version_number != current_active.version_number:
            logger.warning(
                "Active version changed during rollback restoration "
                "(was v%s, now v%s); skipping restore",
                current_active.version_number,
                fresh_active.version_number if fresh_active else None,
            )
            return

        restore_revision = await self._deployer.apply_version(current_active)
        await self._deployer.wait_for_rollout(restore_revision)
        if restore_routes and self._publisher is not None:
            await self._publisher.publish(current_active)
        restored = await self._store.activate_version(
            request.service_id,
            current_active.version_number,
            **scope,
        )
        if restored is None:
            raise RuntimeError(
                "Rollback restore failed for "
                f"{request.service_id} v{current_active.version_number}."
            )


__all__ = ["RollbackPublisher", "RollbackRequest", "RollbackResult", "RollbackWorkflow"]


def _version_protocol(version: ArtifactVersionResponse) -> str | None:
    if isinstance(version.protocol, str) and version.protocol:
        return version.protocol
    raw_protocol = version.ir_json.get("protocol")
    if isinstance(raw_protocol, str) and raw_protocol:
        return raw_protocol
    return None
