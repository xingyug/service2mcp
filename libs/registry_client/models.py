"""Shared request/response models for the artifact registry API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from libs.ir.models import ServiceIR


class RegistryModel(BaseModel):
    """Base model for registry API payloads."""

    model_config = ConfigDict(extra="forbid")


class ArtifactRecordPayload(RegistryModel):
    """Artifact metadata attached to a service version."""

    artifact_type: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    storage_path: str | None = None
    metadata_json: dict[str, Any] | None = None


class ArtifactRecordResponse(ArtifactRecordPayload):
    """Stored artifact record returned by the registry API."""

    id: UUID
    created_at: datetime


class ArtifactVersionCreate(RegistryModel):
    """Payload for creating a new stored service version."""

    service_id: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    ir_json: dict[str, Any]
    raw_ir_json: dict[str, Any] | None = None
    compiler_version: str = "0.1.0"
    source_url: str | None = None
    source_hash: str | None = None
    protocol: str | None = None
    validation_report: dict[str, Any] | None = None
    deployment_revision: str | None = None
    route_config: dict[str, Any] | None = None
    tenant: str | None = None
    environment: str | None = None
    is_active: bool | None = None
    artifacts: list[ArtifactRecordPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ir_payloads(self) -> Self:
        ServiceIR.model_validate(self.ir_json)
        if self.raw_ir_json is not None:
            ServiceIR.model_validate(self.raw_ir_json)
        return self


class ArtifactVersionUpdate(RegistryModel):
    """Payload for updating mutable fields of a stored service version."""

    ir_json: dict[str, Any] | None = None
    raw_ir_json: dict[str, Any] | None = None
    compiler_version: str | None = None
    source_url: str | None = None
    source_hash: str | None = None
    protocol: str | None = None
    validation_report: dict[str, Any] | None = None
    deployment_revision: str | None = None
    route_config: dict[str, Any] | None = None
    tenant: str | None = None
    environment: str | None = None
    artifacts: list[ArtifactRecordPayload] | None = None

    @model_validator(mode="after")
    def validate_update_payload(self) -> Self:
        if not self.model_dump(exclude_none=True):
            raise ValueError("At least one field must be provided for an update.")
        if self.ir_json is not None:
            ServiceIR.model_validate(self.ir_json)
        if self.raw_ir_json is not None:
            ServiceIR.model_validate(self.raw_ir_json)
        return self


class ArtifactVersionResponse(RegistryModel):
    """Stored service version returned by the registry API."""

    id: UUID
    service_id: str
    version_number: int
    is_active: bool
    ir_json: dict[str, Any]
    raw_ir_json: dict[str, Any] | None = None
    compiler_version: str
    source_url: str | None = None
    source_hash: str | None = None
    protocol: str | None = None
    validation_report: dict[str, Any] | None = None
    deployment_revision: str | None = None
    route_config: dict[str, Any] | None = None
    tenant: str | None = None
    environment: str | None = None
    created_at: datetime
    artifacts: list[ArtifactRecordResponse] = Field(default_factory=list)


class ArtifactVersionListResponse(RegistryModel):
    """List response for service versions."""

    service_id: str
    versions: list[ArtifactVersionResponse]


class ArtifactDiffChange(RegistryModel):
    """A single field-level change in a diff."""

    field_name: str
    old_value: Any
    new_value: Any
    param_name: str | None = None


class ArtifactDiffOperation(RegistryModel):
    """Changes for a single operation across two versions."""

    operation_id: str
    operation_name: str
    changes: list[ArtifactDiffChange] = Field(default_factory=list)
    added_params: list[str] = Field(default_factory=list)
    removed_params: list[str] = Field(default_factory=list)


class ArtifactDiffResponse(RegistryModel):
    """Structured diff response between two stored versions."""

    service_id: str
    from_version: int
    to_version: int
    summary: str
    is_empty: bool
    added_operations: list[str] = Field(default_factory=list)
    removed_operations: list[str] = Field(default_factory=list)
    changed_operations: list[ArtifactDiffOperation] = Field(default_factory=list)
