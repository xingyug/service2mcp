"""Repository layer for artifact registry, compilation job, and service queries."""

from __future__ import annotations

import uuid
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.compiler_api.models import (
    CompilationEventResponse,
    CompilationJobResponse,
    ServiceListResponse,
    ServiceSummaryResponse,
)
from apps.compiler_worker.models import CompilationRequest
from libs.db_models import ArtifactRecord, CompilationEvent, CompilationJob, ServiceVersion
from libs.ir.diff import ParamChange, compute_diff
from libs.ir.models import ServiceIR
from libs.registry_client.models import (
    ArtifactDiffChange,
    ArtifactDiffOperation,
    ArtifactDiffResponse,
    ArtifactRecordPayload,
    ArtifactRecordResponse,
    ArtifactVersionCreate,
    ArtifactVersionListResponse,
    ArtifactVersionResponse,
    ArtifactVersionUpdate,
)


class CompilationRepository:
    """Persistence helpers for compilation jobs and workflow events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_job(
        self,
        request: CompilationRequest,
        *,
        job_id: UUID | None = None,
    ) -> CompilationJobResponse:
        resolved_job_id = job_id or request.job_id or uuid.uuid4()
        job = CompilationJob(
            id=resolved_job_id,
            source_url=request.source_url,
            source_hash=request.source_hash,
            status="pending",
            options=request.options or None,
            created_by=request.created_by,
            service_name=request.service_name,
        )
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        return self._to_job_response(job)

    async def delete_job(self, job_id: UUID) -> None:
        job = await self._session.get(CompilationJob, job_id)
        if job is None:
            return
        await self._session.delete(job)
        await self._session.commit()

    async def get_job(self, job_id: UUID) -> CompilationJobResponse | None:
        job = await self._session.get(CompilationJob, job_id)
        if job is None:
            return None
        return self._to_job_response(job)

    async def list_events(
        self,
        job_id: UUID,
        *,
        after_sequence: int = 0,
    ) -> list[CompilationEventResponse]:
        result = await self._session.scalars(
            select(CompilationEvent)
            .where(CompilationEvent.job_id == job_id)
            .where(CompilationEvent.sequence_number > after_sequence)
            .order_by(CompilationEvent.sequence_number)
        )
        return [self._to_event_response(event) for event in result.all()]

    @staticmethod
    def _to_job_response(job: CompilationJob) -> CompilationJobResponse:
        return CompilationJobResponse(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=job.protocol,
            status=job.status,
            current_stage=job.current_stage,
            error_detail=job.error_detail,
            options=job.options,
            created_by=job.created_by,
            service_name=job.service_name,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @staticmethod
    def _to_event_response(event: CompilationEvent) -> CompilationEventResponse:
        return CompilationEventResponse(
            id=event.id,
            job_id=event.job_id,
            sequence_number=event.sequence_number,
            stage=event.stage,
            event_type=event.event_type,
            attempt=event.attempt,
            detail=event.detail,
            error_detail=event.error_detail,
            created_at=event.created_at,
        )


class ServiceCatalogRepository:
    """Read-model queries for compiled service discovery."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_services(
        self,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ServiceListResponse:
        query = select(ServiceVersion).where(ServiceVersion.is_active.is_(True))
        if tenant is not None:
            query = query.where(ServiceVersion.tenant == tenant)
        if environment is not None:
            query = query.where(ServiceVersion.environment == environment)

        result = await self._session.scalars(query.order_by(ServiceVersion.service_id))
        versions = result.all()
        return ServiceListResponse(
            services=[self._to_service_summary(version) for version in versions]
        )

    @staticmethod
    def _to_service_summary(version: ServiceVersion) -> ServiceSummaryResponse:
        service_ir = ServiceIR.model_validate(version.ir_json)
        return ServiceSummaryResponse(
            service_id=version.service_id,
            active_version=version.version_number,
            service_name=service_ir.service_name,
            service_description=service_ir.service_description,
            tool_count=len(service_ir.operations),
            protocol=version.protocol or service_ir.protocol,
            tenant=version.tenant,
            environment=version.environment,
            deployment_revision=version.deployment_revision,
            created_at=version.created_at,
        )


class ArtifactRegistryRepository:
    """Persistence helpers for versioned service artifacts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_version(self, payload: ArtifactVersionCreate) -> ArtifactVersionResponse:
        existing_versions = await self.list_versions(payload.service_id)
        if payload.is_active is not None:
            is_active = payload.is_active
        else:
            is_active = not existing_versions.versions

        if is_active:
            await self._deactivate_service_versions(payload.service_id)

        version = ServiceVersion(
            service_id=payload.service_id,
            version_number=payload.version_number,
            is_active=is_active,
            ir_json=self._normalize_ir_json(payload.ir_json),
            raw_ir_json=self._normalize_optional_ir_json(payload.raw_ir_json),
            compiler_version=payload.compiler_version,
            source_url=payload.source_url,
            source_hash=payload.source_hash,
            protocol=payload.protocol,
            validation_report=payload.validation_report,
            deployment_revision=payload.deployment_revision,
            route_config=payload.route_config,
            tenant=payload.tenant,
            environment=payload.environment,
        )
        self._session.add(version)
        await self._session.flush()
        await self._replace_artifacts(version.id, payload.artifacts)
        await self._session.commit()

        return await self._require_version(payload.service_id, payload.version_number)

    async def get_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        record = await self._get_version_record(
            service_id,
            version_number,
            tenant=tenant,
            environment=environment,
        )
        if record is None:
            return None
        return self._to_response(record)

    async def list_versions(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionListResponse:
        query = self._version_query(service_id, tenant=tenant, environment=environment).order_by(
            desc(ServiceVersion.version_number)
        )
        result = await self._session.scalars(query)
        versions = [self._to_response(record) for record in result.all()]
        return ArtifactVersionListResponse(service_id=service_id, versions=versions)

    async def get_active_version(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse | None:
        query = self._version_query(service_id, tenant=tenant, environment=environment).where(
            ServiceVersion.is_active.is_(True)
        )
        record = cast(ServiceVersion | None, await self._session.scalar(query))
        if record is None:
            return None
        return self._to_response(record)

    async def update_version(
        self,
        service_id: str,
        version_number: int,
        payload: ArtifactVersionUpdate,
    ) -> ArtifactVersionResponse | None:
        record = await self._get_version_record(service_id, version_number)
        if record is None:
            return None

        if payload.ir_json is not None:
            record.ir_json = self._normalize_ir_json(payload.ir_json)
        if payload.raw_ir_json is not None:
            record.raw_ir_json = self._normalize_ir_json(payload.raw_ir_json)
        if payload.compiler_version is not None:
            record.compiler_version = payload.compiler_version
        if payload.source_url is not None:
            record.source_url = payload.source_url
        if payload.source_hash is not None:
            record.source_hash = payload.source_hash
        if payload.protocol is not None:
            record.protocol = payload.protocol
        if payload.validation_report is not None:
            record.validation_report = payload.validation_report
        if payload.deployment_revision is not None:
            record.deployment_revision = payload.deployment_revision
        if payload.route_config is not None:
            record.route_config = payload.route_config
        if payload.tenant is not None:
            record.tenant = payload.tenant
        if payload.environment is not None:
            record.environment = payload.environment
        if payload.artifacts is not None:
            await self._replace_artifacts(record.id, payload.artifacts)

        await self._session.commit()
        return await self.get_version(service_id, version_number)

    async def activate_version(
        self,
        service_id: str,
        version_number: int,
    ) -> ArtifactVersionResponse | None:
        record = await self._get_version_record(service_id, version_number)
        if record is None:
            return None

        await self._deactivate_service_versions(service_id)
        record.is_active = True
        await self._session.commit()
        return await self.get_version(service_id, version_number)

    async def delete_version(self, service_id: str, version_number: int) -> bool:
        record = await self._get_version_record(service_id, version_number)
        if record is None:
            return False

        was_active = record.is_active
        await self._session.delete(record)
        await self._session.flush()

        if was_active:
            replacement = await self._session.scalar(
                select(ServiceVersion)
                .where(ServiceVersion.service_id == service_id)
                .order_by(desc(ServiceVersion.version_number))
                .limit(1)
            )
            if replacement is not None:
                replacement.is_active = True

        await self._session.commit()
        return True

    async def diff_versions(
        self,
        service_id: str,
        *,
        from_version: int,
        to_version: int,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactDiffResponse | None:
        from_record = await self._get_version_record(
            service_id,
            from_version,
            tenant=tenant,
            environment=environment,
        )
        to_record = await self._get_version_record(
            service_id,
            to_version,
            tenant=tenant,
            environment=environment,
        )
        if from_record is None or to_record is None:
            return None

        diff = compute_diff(
            ServiceIR.model_validate(from_record.ir_json),
            ServiceIR.model_validate(to_record.ir_json),
        )
        return ArtifactDiffResponse(
            service_id=service_id,
            from_version=from_version,
            to_version=to_version,
            summary=diff.summary,
            is_empty=diff.is_empty,
            added_operations=diff.added_operations,
            removed_operations=diff.removed_operations,
            changed_operations=[
                ArtifactDiffOperation(
                    operation_id=operation.operation_id,
                    operation_name=operation.operation_name,
                    added_params=operation.added_params,
                    removed_params=operation.removed_params,
                    changes=[self._to_diff_change(change) for change in operation.changes],
                )
                for operation in diff.changed_operations
            ],
        )

    async def _deactivate_service_versions(self, service_id: str) -> None:
        # Lock active versions first to prevent concurrent activation race
        await self._session.scalars(
            select(ServiceVersion.id)
            .where(ServiceVersion.service_id == service_id)
            .where(ServiceVersion.is_active.is_(True))
            .with_for_update()
        )
        await self._session.execute(
            update(ServiceVersion)
            .where(ServiceVersion.service_id == service_id)
            .values(is_active=False)
        )

    async def _get_version_record(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ServiceVersion | None:
        query = self._version_query(service_id, tenant=tenant, environment=environment).where(
            ServiceVersion.version_number == version_number
        )
        return cast(ServiceVersion | None, await self._session.scalar(query))

    def _version_query(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> Select[tuple[ServiceVersion]]:
        query = (
            select(ServiceVersion)
            .execution_options(populate_existing=True)
            .options(selectinload(ServiceVersion.artifacts))
            .where(ServiceVersion.service_id == service_id)
        )
        if tenant is not None:
            query = query.where(ServiceVersion.tenant == tenant)
        if environment is not None:
            query = query.where(ServiceVersion.environment == environment)
        return query

    async def _replace_artifacts(
        self,
        service_version_id: object,
        artifacts: list[ArtifactRecordPayload],
    ) -> None:
        await self._session.execute(
            delete(ArtifactRecord).where(ArtifactRecord.service_version_id == service_version_id)
        )
        for artifact in artifacts:
            self._session.add(
                ArtifactRecord(
                    service_version_id=service_version_id,
                    artifact_type=artifact.artifact_type,
                    content_hash=artifact.content_hash,
                    storage_path=artifact.storage_path,
                    metadata_json=artifact.metadata_json,
                )
            )

    async def _require_version(
        self,
        service_id: str,
        version_number: int,
    ) -> ArtifactVersionResponse:
        record = await self.get_version(service_id, version_number)
        if record is None:
            raise RuntimeError("Artifact version disappeared after commit.")
        return record

    @staticmethod
    def _normalize_ir_json(payload: dict[str, Any]) -> dict[str, Any]:
        return ServiceIR.model_validate(payload).model_dump(mode="json")

    @classmethod
    def _normalize_optional_ir_json(cls, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        return cls._normalize_ir_json(payload)

    @staticmethod
    def _to_response(record: ServiceVersion) -> ArtifactVersionResponse:
        return ArtifactVersionResponse(
            id=record.id,
            service_id=record.service_id,
            version_number=record.version_number,
            is_active=record.is_active,
            ir_json=record.ir_json,
            raw_ir_json=record.raw_ir_json,
            compiler_version=record.compiler_version,
            source_url=record.source_url,
            source_hash=record.source_hash,
            protocol=record.protocol,
            validation_report=record.validation_report,
            deployment_revision=record.deployment_revision,
            route_config=record.route_config,
            tenant=record.tenant,
            environment=record.environment,
            created_at=record.created_at,
            artifacts=[
                ArtifactRecordResponse(
                    id=artifact.id,
                    artifact_type=artifact.artifact_type,
                    content_hash=artifact.content_hash,
                    storage_path=artifact.storage_path,
                    metadata_json=artifact.metadata_json,
                    created_at=artifact.created_at,
                )
                for artifact in record.artifacts
            ],
        )

    @staticmethod
    def _to_diff_change(change: ParamChange | tuple[str, Any, Any]) -> ArtifactDiffChange:
        if isinstance(change, ParamChange):
            return ArtifactDiffChange(
                field_name=change.field_name,
                old_value=change.old_value,
                new_value=change.new_value,
                param_name=change.param_name,
            )
        field_name, old_value, new_value = change
        return ArtifactDiffChange(field_name=field_name, old_value=old_value, new_value=new_value)
