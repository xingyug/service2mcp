"""Repository layer for artifact registry, compilation job, and service queries."""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Select, Subquery, and_, delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from apps.compiler_api.models import (
    CompilationEventResponse,
    CompilationJobResponse,
    ServiceListResponse,
    ServiceSummaryResponse,
)
from apps.compiler_worker.models import (
    CompilationRequest,
    public_compilation_options,
    request_scope_from_options,
    store_compilation_request_options,
)
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

logger = logging.getLogger(__name__)


class MalformedServiceVersionError(RuntimeError):
    """Raised when an active service version cannot be materialized into ServiceIR."""

    def __init__(self, *, service_id: str, version_number: int) -> None:
        super().__init__(
            f"Active service record for {service_id} v{version_number}"
            " is malformed and cannot be served."
        )
        self.service_id = service_id
        self.version_number = version_number


class MalformedArtifactDiffError(RuntimeError):
    """Raised when a stored version cannot be materialized during artifact diffing."""

    def __init__(self, *, service_id: str, version_number: int) -> None:
        super().__init__(
            f"Stored artifact version {service_id}:{version_number}"
            " is malformed and cannot be diffed."
        )
        self.service_id = service_id
        self.version_number = version_number


class AmbiguousServiceVersionError(RuntimeError):
    """Raised when a registry lookup that expects one row matches multiple rows."""

    def __init__(
        self,
        *,
        service_id: str,
        version_number: int | None = None,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> None:
        identifier = f"{service_id}:{version_number}" if version_number is not None else service_id
        super().__init__(
            "Registry lookup for "
            f"{identifier} matched multiple service versions "
            f"(tenant={tenant!r}, environment={environment!r})."
        )
        self.service_id = service_id
        self.version_number = version_number
        self.tenant = tenant
        self.environment = environment


class CompilationRepository:
    """Persistence helpers for compilation jobs and workflow events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_job(
        self,
        request: CompilationRequest,
        *,
        job_id: UUID | None = None,
        commit: bool = True,
    ) -> CompilationJobResponse:
        resolved_job_id = job_id or request.job_id or uuid.uuid4()
        tenant, environment = request_scope_from_options(request.options)
        job = CompilationJob(
            id=resolved_job_id,
            source_url=request.source_url,
            source_hash=request.source_hash,
            status="pending",
            options=store_compilation_request_options(request),
            created_by=request.created_by,
            service_name=request.service_name,
            tenant=tenant,
            environment=environment,
        )
        self._session.add(job)
        await self._session.flush()
        if commit:
            await self._session.commit()
        await self._session.refresh(job)
        return self._to_job_response(job)

    async def delete_job(self, job_id: UUID) -> None:
        job = await self._session.get(CompilationJob, job_id)
        if job is None:
            return
        await self._session.delete(job)
        await self._session.commit()

    async def get_job(
        self,
        job_id: UUID,
        *,
        include_internal_options: bool = False,
    ) -> CompilationJobResponse | None:
        job = await self._session.get(CompilationJob, job_id)
        if job is None:
            return None
        return self._to_job_response(job, include_internal_options=include_internal_options)

    async def list_jobs(
        self,
        *,
        limit: int | None = None,
    ) -> list[CompilationJobResponse]:
        query = select(CompilationJob).order_by(desc(CompilationJob.created_at))
        if limit is not None:
            query = query.limit(limit)
        result = await self._session.scalars(query)
        return [self._to_job_response(job) for job in result.all()]

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
    def _to_job_response(
        job: CompilationJob,
        *,
        include_internal_options: bool = False,
    ) -> CompilationJobResponse:
        return CompilationJobResponse(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=job.protocol,
            status=job.status,
            current_stage=job.current_stage,
            error_detail=job.error_detail,
            options=job.options
            if include_internal_options
            else public_compilation_options(job.options),
            created_by=job.created_by,
            service_id=job.service_name,
            service_name=job.service_name,
            created_at=job.created_at,
            updated_at=job.updated_at,
            tenant=job.tenant,
            environment=job.environment,
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
        stats = self._service_stats_subquery()
        query = (
            select(
                ServiceVersion,
                stats.c.version_count,
                stats.c.last_compiled_at,
            )
            .join(stats, self._service_stats_join_condition(stats))
            .where(ServiceVersion.is_active.is_(True))
        )
        if tenant is not None:
            query = query.where(ServiceVersion.tenant == tenant)
        if environment is not None:
            query = query.where(ServiceVersion.environment == environment)

        result = await self._session.execute(query.order_by(ServiceVersion.service_id))
        services: list[ServiceSummaryResponse] = []
        for version, version_count, last_compiled_at in result.all():
            try:
                services.append(self._to_service_summary(version, version_count, last_compiled_at))
            except MalformedServiceVersionError:
                logger.warning(
                    "Skipping malformed active service version %s v%s from service catalog list.",
                    version.service_id,
                    version.version_number,
                    exc_info=True,
                )
        return ServiceListResponse(services=services)

    async def get_service(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ServiceSummaryResponse | None:
        stats = self._service_stats_subquery()
        query = (
            select(
                ServiceVersion,
                stats.c.version_count,
                stats.c.last_compiled_at,
            )
            .join(stats, self._service_stats_join_condition(stats))
            .where(ServiceVersion.is_active.is_(True))
            .where(ServiceVersion.service_id == service_id)
        )
        if tenant is not None:
            query = query.where(ServiceVersion.tenant == tenant)
        if environment is not None:
            query = query.where(ServiceVersion.environment == environment)

        rows = (await self._session.execute(query.limit(2))).all()
        if not rows:
            return None
        if len(rows) > 1:
            raise AmbiguousServiceVersionError(
                service_id=service_id,
                tenant=tenant,
                environment=environment,
            )
        version, version_count, last_compiled_at = rows[0]
        return self._to_service_summary(version, version_count, last_compiled_at)

    @staticmethod
    def _to_service_summary(
        version: ServiceVersion,
        version_count: int,
        last_compiled_at: Any,
    ) -> ServiceSummaryResponse:
        try:
            service_ir = ServiceIR.model_validate(version.ir_json)
        except ValidationError as exc:
            raise MalformedServiceVersionError(
                service_id=version.service_id,
                version_number=version.version_number,
            ) from exc
        return ServiceSummaryResponse(
            service_id=version.service_id,
            active_version=version.version_number,
            version_count=int(version_count),
            service_name=service_ir.service_name,
            service_description=service_ir.service_description,
            tool_count=sum(1 for operation in service_ir.operations if operation.enabled),
            protocol=version.protocol or service_ir.protocol,
            tenant=version.tenant,
            environment=version.environment,
            deployment_revision=version.deployment_revision,
            created_at=cast(Any, last_compiled_at) or version.created_at,
        )

    @staticmethod
    def _service_stats_subquery() -> Subquery:
        return (
            select(
                ServiceVersion.service_id.label("service_id"),
                ServiceVersion.tenant.label("tenant"),
                ServiceVersion.environment.label("environment"),
                func.count(ServiceVersion.id).label("version_count"),
                func.max(ServiceVersion.created_at).label("last_compiled_at"),
            )
            .group_by(
                ServiceVersion.service_id,
                ServiceVersion.tenant,
                ServiceVersion.environment,
            )
            .subquery()
        )

    @staticmethod
    def _service_stats_join_condition(stats: Any) -> ColumnElement[bool]:
        return and_(
            ServiceVersion.service_id == stats.c.service_id,
            ServiceVersion.tenant.is_not_distinct_from(stats.c.tenant),
            ServiceVersion.environment.is_not_distinct_from(stats.c.environment),
        )


class ArtifactRegistryRepository:
    """Persistence helpers for versioned service artifacts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_version(
        self,
        payload: ArtifactVersionCreate,
        *,
        commit: bool = True,
    ) -> ArtifactVersionResponse:
        existing_versions = await self.list_versions(
            payload.service_id,
            tenant=payload.tenant,
            environment=payload.environment,
        )
        if payload.is_active is not None:
            is_active = payload.is_active
        else:
            is_active = not existing_versions.versions

        if is_active:
            await self._deactivate_service_versions(
                payload.service_id,
                tenant=payload.tenant,
                environment=payload.environment,
            )

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
        if commit:
            await self._session.commit()

        return await self._require_version(
            payload.service_id,
            payload.version_number,
            tenant=payload.tenant,
            environment=payload.environment,
        )

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
        record = await self._require_unique_version_record(
            query,
            service_id=service_id,
            tenant=tenant,
            environment=environment,
        )
        if record is None:
            return None
        return self._to_response(record)

    async def update_version(
        self,
        service_id: str,
        version_number: int,
        payload: ArtifactVersionUpdate,
        *,
        tenant: str | None = None,
        environment: str | None = None,
        commit: bool = True,
    ) -> ArtifactVersionResponse | None:
        record = await self._get_version_record(
            service_id,
            version_number,
            tenant=tenant,
            environment=environment,
        )
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

        await self._session.flush()
        if commit:
            await self._session.commit()
        await self._session.refresh(record)
        return self._to_response(record)

    async def activate_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
        commit: bool = True,
    ) -> ArtifactVersionResponse | None:
        record = await self._get_version_record(
            service_id,
            version_number,
            tenant=tenant,
            environment=environment,
        )
        if record is None:
            return None

        await self._deactivate_service_versions(
            service_id,
            tenant=record.tenant,
            environment=record.environment,
        )
        record.is_active = True
        await self._session.flush()
        if commit:
            await self._session.commit()
        await self._session.refresh(record)
        return self._to_response(record)

    async def delete_version(
        self,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
        commit: bool = True,
    ) -> bool:
        record = await self._get_version_record(
            service_id,
            version_number,
            tenant=tenant,
            environment=environment,
        )
        if record is None:
            return False

        was_active = record.is_active
        record_tenant = record.tenant
        record_environment = record.environment
        await self._session.delete(record)
        await self._session.flush()

        if was_active:
            replacement_query = select(ServiceVersion).where(
                ServiceVersion.service_id == service_id
            )
            if record_tenant is not None:
                replacement_query = replacement_query.where(
                    ServiceVersion.tenant == record_tenant,
                )
            else:
                replacement_query = replacement_query.where(
                    ServiceVersion.tenant.is_(None),
                )
            if record_environment is not None:
                replacement_query = replacement_query.where(
                    ServiceVersion.environment == record_environment,
                )
            else:
                replacement_query = replacement_query.where(
                    ServiceVersion.environment.is_(None),
                )
            replacement = await self._session.scalar(
                replacement_query.order_by(desc(ServiceVersion.version_number)).limit(1)
            )
            if replacement is not None:
                replacement.is_active = True

        if commit:
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

        try:
            from_ir = ServiceIR.model_validate(from_record.ir_json)
        except ValidationError as exc:
            raise MalformedArtifactDiffError(
                service_id=service_id,
                version_number=from_version,
            ) from exc
        try:
            to_ir = ServiceIR.model_validate(to_record.ir_json)
        except ValidationError as exc:
            raise MalformedArtifactDiffError(
                service_id=service_id,
                version_number=to_version,
            ) from exc

        diff = compute_diff(from_ir, to_ir)
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

    async def _deactivate_service_versions(
        self,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> None:
        # Lock active versions first to prevent concurrent activation race
        lock_query = (
            select(ServiceVersion.id)
            .where(ServiceVersion.service_id == service_id)
            .where(ServiceVersion.is_active.is_(True))
        )
        deactivate_query = update(ServiceVersion).where(ServiceVersion.service_id == service_id)
        if tenant is not None:
            lock_query = lock_query.where(ServiceVersion.tenant == tenant)
            deactivate_query = deactivate_query.where(ServiceVersion.tenant == tenant)
        else:
            lock_query = lock_query.where(ServiceVersion.tenant.is_(None))
            deactivate_query = deactivate_query.where(ServiceVersion.tenant.is_(None))
        if environment is not None:
            lock_query = lock_query.where(ServiceVersion.environment == environment)
            deactivate_query = deactivate_query.where(
                ServiceVersion.environment == environment,
            )
        else:
            lock_query = lock_query.where(ServiceVersion.environment.is_(None))
            deactivate_query = deactivate_query.where(
                ServiceVersion.environment.is_(None),
            )
        await self._session.scalars(lock_query.with_for_update())
        await self._session.execute(deactivate_query.values(is_active=False))

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
        return await self._require_unique_version_record(
            query,
            service_id=service_id,
            version_number=version_number,
            tenant=tenant,
            environment=environment,
        )

    async def _require_unique_version_record(
        self,
        query: Select[tuple[ServiceVersion]],
        *,
        service_id: str,
        version_number: int | None = None,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ServiceVersion | None:
        records = (await self._session.scalars(query.limit(2))).all()
        if not records:
            return None
        if len(records) > 1:
            raise AmbiguousServiceVersionError(
                service_id=service_id,
                version_number=version_number,
                tenant=tenant,
                environment=environment,
            )
        return records[0]

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
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> ArtifactVersionResponse:
        record = await self.get_version(
            service_id,
            version_number,
            tenant=tenant,
            environment=environment,
        )
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
