"""Pydantic models for the compiler API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.compiler_worker.models import (
    CompilationEventRecord,
    CompilationJobRecord,
    CompilationRequest,
    public_compilation_options,
)


class CompilerApiModel(BaseModel):
    """Base model for compiler API payloads."""

    model_config = ConfigDict(extra="forbid")


class CompilationCreateRequest(CompilerApiModel):
    """HTTP payload for a compilation submission."""

    source_url: str | None = Field(default=None, max_length=4096)
    source_content: str | None = Field(default=None, max_length=10_000_000)  # 10 MB cap
    source_hash: str | None = None
    filename: str | None = None
    created_by: str | None = None
    service_id: str | None = None
    service_name: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> CompilationCreateRequest:
        url = self.source_url.strip() if self.source_url else ""
        content = self.source_content.strip() if self.source_content else ""
        if not url and not content:
            raise ValueError("Either source_url or source_content must be provided.")
        if url and content:
            raise ValueError("Provide exactly one of source_url or source_content, not both")
        if url:
            # Reject control characters (CRLF injection, null bytes)
            if any(c in url for c in "\r\n\x00"):
                raise ValueError("source_url contains invalid control characters.")
            self.source_url = url
        if content:
            self.source_content = content
        return self

    def to_workflow_request(self) -> CompilationRequest:
        """Convert the API payload into a workflow request model."""

        return CompilationRequest(
            source_url=self.source_url,
            source_content=self.source_content,
            source_hash=self.source_hash,
            filename=self.filename,
            created_by=self.created_by,
            service_id=self.service_id,
            service_name=self.service_name,
            options=dict(self.options),
        )


class CompilationArtifacts(CompilerApiModel):
    """Artifact references produced by a successful compilation."""

    ir_id: str | None = None
    image_digest: str | None = None
    deployment_id: str | None = None


class CompilationJobResponse(CompilerApiModel):
    """Serialized compilation job returned by the API."""

    id: UUID
    source_url: str | None = None
    source_hash: str | None = None
    protocol: str | None = None
    status: str
    current_stage: str | None = None
    error_detail: str | None = None
    options: dict[str, Any] | None = None
    created_by: str | None = None
    service_id: str | None = None
    service_name: str | None = None
    created_at: datetime
    updated_at: datetime
    tenant: str | None = None
    environment: str | None = None
    artifacts: CompilationArtifacts | None = None

    @classmethod
    def from_record(cls, record: CompilationJobRecord) -> CompilationJobResponse:
        service_id = record.service_id or record.service_name
        artifacts: CompilationArtifacts | None = None
        if service_id:
            artifacts = CompilationArtifacts(ir_id=service_id)
        return cls(
            id=record.id,
            source_url=record.source_url,
            source_hash=record.source_hash,
            protocol=record.protocol,
            status=record.status.value,
            current_stage=record.current_stage.value if record.current_stage is not None else None,
            error_detail=record.error_detail,
            options=public_compilation_options(record.options),
            created_by=record.created_by,
            service_id=record.service_id,
            service_name=record.service_name,
            created_at=record.created_at,
            updated_at=record.updated_at,
            tenant=record.tenant,
            environment=record.environment,
            artifacts=artifacts,
        )


class CompilationEventResponse(CompilerApiModel):
    """Serialized workflow event returned via API or SSE."""

    id: UUID
    job_id: UUID
    sequence_number: int
    stage: str | None = None
    event_type: str
    attempt: int | None = None
    detail: dict[str, Any] | None = None
    error_detail: str | None = None
    created_at: datetime

    @classmethod
    def from_record(cls, record: CompilationEventRecord) -> CompilationEventResponse:
        return cls(
            id=record.id,
            job_id=record.job_id,
            sequence_number=record.sequence_number,
            stage=record.stage.value if record.stage is not None else None,
            event_type=record.event_type.value,
            attempt=record.attempt,
            detail=record.detail,
            error_detail=record.error_detail,
            created_at=record.created_at,
        )


class ServiceSummaryResponse(CompilerApiModel):
    """Summary of a compiled service exposed by the API."""

    service_id: str
    active_version: int
    version_count: int
    service_name: str
    service_description: str | None = None
    tool_count: int
    protocol: str | None = None
    tenant: str | None = None
    environment: str | None = None
    deployment_revision: str | None = None
    created_at: datetime


class ServiceListResponse(CompilerApiModel):
    """List response for compiled services."""

    services: list[ServiceSummaryResponse]


class DashboardSummaryResponse(CompilerApiModel):
    """Aggregate dashboard summary for UI presentation."""

    total_services: int
    total_tools: int
    protocol_distribution: dict[str, int]
    recent_compilations: list[CompilationJobResponse]
    services_by_status: dict[str, int] = Field(
        default_factory=dict,
        description="Service counts grouped by workflow status",
    )
