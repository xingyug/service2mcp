"""Shared models for compilation workflow orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

_INTERNAL_OPTION_PREFIX = "__compiler_"
_REQUEST_REPLAY_OPTION_KEY = f"{_INTERNAL_OPTION_PREFIX}request_replay"
_RESUME_CHECKPOINT_OPTION_KEY = f"{_INTERNAL_OPTION_PREFIX}resume_checkpoint"
_ROLLBACK_REQUEST_OPTION_KEY = f"{_INTERNAL_OPTION_PREFIX}rollback_request"


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
    service_id: str | None = None
    service_name: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    job_id: UUID | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialize the request into a JSON-safe task payload."""
        if not self.source_url and not self.source_content:
            raise ValueError("Either source_url or source_content must be provided.")

        return {
            "source_url": self.source_url,
            "source_content": self.source_content,
            "source_hash": self.source_hash,
            "filename": self.filename,
            "created_by": self.created_by,
            "service_id": self.service_id,
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
            service_id=payload.get("service_id"),
            service_name=payload.get("service_name"),
            options=dict(options) if isinstance(options, dict) else {},
            job_id=UUID(str(job_id_value)) if job_id_value is not None else None,
        )


def store_compilation_request_options(
    request: CompilationRequest,
) -> dict[str, Any] | None:
    """Persist public options plus internal replay metadata needed for retries."""

    public_options, internal_options = split_compilation_options(request.options)
    request_replay: dict[str, Any] = {}
    if request.source_content is not None:
        request_replay["source_content"] = request.source_content
    if request.filename is not None:
        request_replay["filename"] = request.filename
    if request.service_id is not None:
        request_replay["service_id"] = request.service_id
    if request_replay:
        internal_options[_REQUEST_REPLAY_OPTION_KEY] = request_replay
    stored_options = {**public_options, **internal_options}
    return stored_options or None


def public_compilation_options(
    options: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return only caller-controlled options, excluding internal metadata."""

    public_options, _ = split_compilation_options(options)
    return public_options or None


def compilation_request_replay(
    options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return persisted request fields needed to replay a job."""

    _, internal_options = split_compilation_options(options)
    replay = internal_options.get(_REQUEST_REPLAY_OPTION_KEY)
    if not isinstance(replay, Mapping):
        return {}
    return {key: value for key, value in replay.items()}


def store_compilation_checkpoint(
    options: Mapping[str, Any] | None,
    *,
    payload: Mapping[str, Any],
    protocol: str | None,
    service_name: str | None,
    completed_stage: str,
) -> dict[str, Any]:
    """Persist the latest resumable workflow checkpoint alongside options."""

    public_options, internal_options = split_compilation_options(options)
    internal_options[_RESUME_CHECKPOINT_OPTION_KEY] = {
        "payload": deepcopy(dict(payload)),
        "protocol": protocol,
        "service_name": service_name,
        "completed_stage": completed_stage,
    }
    return {**public_options, **internal_options}


def compilation_resume_checkpoint(
    options: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the latest persisted workflow checkpoint for resume-from-stage retries."""

    _, internal_options = split_compilation_options(options)
    checkpoint = internal_options.get(_RESUME_CHECKPOINT_OPTION_KEY)
    if not isinstance(checkpoint, Mapping):
        return None
    payload = checkpoint.get("payload")
    completed_stage = checkpoint.get("completed_stage")
    if not isinstance(payload, Mapping) or not isinstance(completed_stage, str):
        return None
    protocol = checkpoint.get("protocol")
    service_name = checkpoint.get("service_name")
    return {
        "payload": dict(payload),
        "protocol": protocol if isinstance(protocol, str) else None,
        "service_name": service_name if isinstance(service_name, str) else None,
        "completed_stage": completed_stage,
    }


def store_compilation_rollback_request(
    options: Mapping[str, Any] | None,
    *,
    source_job_id: UUID,
    service_id: str,
    target_version: int,
    tenant: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """Persist internal metadata that routes a job through rollback execution."""

    public_options, internal_options = split_compilation_options(options)
    rollback_request: dict[str, Any] = {
        "source_job_id": str(source_job_id),
        "service_id": service_id,
        "target_version": target_version,
    }
    if tenant is not None:
        rollback_request["tenant"] = tenant
    if environment is not None:
        rollback_request["environment"] = environment
    internal_options[_ROLLBACK_REQUEST_OPTION_KEY] = rollback_request
    return {**public_options, **internal_options}


def compilation_rollback_request(
    options: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the internal rollback execution metadata for a queued job."""

    _, internal_options = split_compilation_options(options)
    rollback_request = internal_options.get(_ROLLBACK_REQUEST_OPTION_KEY)
    if not isinstance(rollback_request, Mapping):
        return None
    service_id = rollback_request.get("service_id")
    if not isinstance(service_id, str):
        return None
    source_job_id = rollback_request.get("source_job_id")
    if not isinstance(source_job_id, str):
        return None
    try:
        resolved_source_job_id = UUID(source_job_id)
    except (TypeError, ValueError):
        return None
    target_version = rollback_request.get("target_version")
    if not isinstance(target_version, int):
        return None
    return {
        "source_job_id": resolved_source_job_id,
        "service_id": service_id,
        "target_version": target_version,
        "tenant": _normalize_scope_value(rollback_request.get("tenant")),
        "environment": _normalize_scope_value(rollback_request.get("environment")),
    }


def split_compilation_options(
    options: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split stored job options into public and internal namespaces."""

    public_options: dict[str, Any] = {}
    internal_options: dict[str, Any] = {}
    if not isinstance(options, Mapping):
        return public_options, internal_options
    for key, value in options.items():
        target = (
            internal_options
            if isinstance(key, str) and key.startswith(_INTERNAL_OPTION_PREFIX)
            else public_options
        )
        target[str(key)] = value
    return public_options, internal_options


def request_scope_from_options(
    options: Mapping[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Extract normalized tenant/environment scope values from request options."""

    if not isinstance(options, Mapping):
        return None, None
    return (
        _normalize_scope_value(options.get("tenant")),
        _normalize_scope_value(options.get("environment")),
    )


def _normalize_scope_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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
    tenant: str | None = None
    environment: str | None = None
    service_id: str | None = None


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
