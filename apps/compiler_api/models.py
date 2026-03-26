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
)


class CompilerApiModel(BaseModel):
    """Base model for compiler API payloads."""

    model_config = ConfigDict(extra="forbid")


class CompilationCreateRequest(CompilerApiModel):
    """HTTP payload for a compilation submission."""

    source_url: str | None = None
    source_content: str | None = None
    source_hash: str | None = None
    filename: str | None = None
    created_by: str | None = None
    service_name: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> CompilationCreateRequest:
        if not self.source_url and not self.source_content:
            raise ValueError("Either source_url or source_content must be provided.")
        return self

    def to_workflow_request(self) -> CompilationRequest:
        """Convert the API payload into a workflow request model."""

        return CompilationRequest(
            source_url=self.source_url,
            source_content=self.source_content,
            source_hash=self.source_hash,
            filename=self.filename,
            created_by=self.created_by,
            service_name=self.service_name,
            options=dict(self.options),
        )


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
    service_name: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: CompilationJobRecord) -> CompilationJobResponse:
        return cls(
            id=record.id,
            source_url=record.source_url,
            source_hash=record.source_hash,
            protocol=record.protocol,
            status=record.status.value,
            current_stage=record.current_stage.value if record.current_stage is not None else None,
            error_detail=record.error_detail,
            options=record.options,
            created_by=record.created_by,
            service_name=record.service_name,
            created_at=record.created_at,
            updated_at=record.updated_at,
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
