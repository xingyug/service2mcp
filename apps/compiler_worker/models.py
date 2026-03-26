"""Shared models for compilation workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class CompilationStage(StrEnum):
    """Ordered stages in the compilation pipeline."""

    DETECT = "detect"
    EXTRACT = "extract"
    ENHANCE = "enhance"
    VALIDATE_IR = "validate_ir"
    GENERATE = "generate"
    DEPLOY = "deploy"
    VALIDATE_RUNTIME = "validate_runtime"
    ROUTE = "route"
    REGISTER = "register"


class CompilationStatus(StrEnum):
    """Persisted lifecycle states for a compilation job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class CompilationEventType(StrEnum):
    """Persisted workflow and rollback event types."""

    JOB_CREATED = "job.created"
    JOB_STARTED = "job.started"
    JOB_SUCCEEDED = "job.succeeded"
    JOB_FAILED = "job.failed"
    JOB_ROLLED_BACK = "job.rolled_back"
    STAGE_STARTED = "stage.started"
    STAGE_SUCCEEDED = "stage.succeeded"
    STAGE_RETRYING = "stage.retrying"
    STAGE_FAILED = "stage.failed"
    ROLLBACK_STARTED = "rollback.started"
    ROLLBACK_SUCCEEDED = "rollback.succeeded"
    ROLLBACK_FAILED = "rollback.failed"


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for a single workflow stage."""

    max_attempts: int = 3


@dataclass(frozen=True)
class StageDefinition:
    """Configuration for an ordered workflow stage."""

    stage: CompilationStage
    retry_policy: RetryPolicy = RetryPolicy()
    rollback_enabled: bool = False


@dataclass
class CompilationRequest:
    """Input used to create and execute a compilation job."""

    source_url: str | None = None
    source_content: str | None = None
    source_hash: str | None = None
    filename: str | None = None
    created_by: str | None = None
    service_name: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    job_id: UUID | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialize the request into a JSON-safe task payload."""

        return {
            "source_url": self.source_url,
            "source_content": self.source_content,
            "source_hash": self.source_hash,
            "filename": self.filename,
            "created_by": self.created_by,
            "service_name": self.service_name,
            "options": dict(self.options),
            "job_id": str(self.job_id) if self.job_id is not None else None,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CompilationRequest:
        """Rebuild a request from a JSON-safe task payload."""

        job_id_value = payload.get("job_id")
        options = payload.get("options")
        return cls(
            source_url=payload.get("source_url"),
            source_content=payload.get("source_content"),
            source_hash=payload.get("source_hash"),
            filename=payload.get("filename"),
            created_by=payload.get("created_by"),
            service_name=payload.get("service_name"),
            options=dict(options) if isinstance(options, dict) else {},
            job_id=UUID(str(job_id_value)) if job_id_value is not None else None,
        )


@dataclass
class StageExecutionResult:
    """Outcome of a successful stage execution."""

    context_updates: dict[str, Any] = field(default_factory=dict)
    event_detail: dict[str, Any] | None = None
    rollback_payload: dict[str, Any] | None = None
    protocol: str | None = None
    service_name: str | None = None


@dataclass
class CompilationContext:
    """Mutable workflow context shared across stages."""

    job_id: UUID
    request: CompilationRequest
    payload: dict[str, Any] = field(default_factory=dict)
    protocol: str | None = None
    service_name: str | None = None
    stage_results: dict[CompilationStage, StageExecutionResult] = field(default_factory=dict)


@dataclass(frozen=True)
class CompilationJobRecord:
    """Persisted view of a compilation job."""

    id: UUID
    source_url: str | None
    source_hash: str | None
    protocol: str | None
    status: CompilationStatus
    current_stage: CompilationStage | None
    error_detail: str | None
    options: dict[str, Any] | None
    created_by: str | None
    service_name: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CompilationEventRecord:
    """Persisted view of a compilation job event."""

    id: UUID
    job_id: UUID
    sequence_number: int
    stage: CompilationStage | None
    event_type: CompilationEventType
    attempt: int | None
    detail: dict[str, Any] | None
    error_detail: str | None
    created_at: datetime


@dataclass(frozen=True)
class CompilationResult:
    """Final workflow result."""

    job_id: UUID
    status: CompilationStatus
    final_stage: CompilationStage
    payload: dict[str, Any]
