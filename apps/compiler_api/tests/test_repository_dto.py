"""Unit tests for apps/compiler_api/repository.py — static DTO transformer methods."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.compiler_api.repository import (
    ArtifactRegistryRepository,
    CompilationRepository,
    ServiceCatalogRepository,
)
from libs.ir.diff import ParamChange
from libs.ir.models import ServiceIR


def _utcnow() -> datetime:
    return datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC)


def _fake_job(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source_url": "https://example.com/api.yaml",
        "source_hash": "sha256:abc",
        "protocol": "openapi",
        "status": "completed",
        "current_stage": "deploy",
        "error_detail": None,
        "options": {"key": "value"},
        "created_by": "user@example.com",
        "service_name": "Test API",
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_event(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "sequence_number": 1,
        "stage": "extract",
        "event_type": "stage.succeeded",
        "attempt": 1,
        "detail": {"operations": 5},
        "error_detail": None,
        "created_at": _utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _minimal_ir_json() -> dict[str, Any]:
    return ServiceIR(
        service_id="test-svc",
        service_name="Test Service",
        base_url="https://example.com",
        source_hash="sha256:abc",
        protocol="openapi",
        operations=[],
    ).model_dump(mode="json")


def _fake_service_version(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "service_id": "test-svc",
        "version_number": 1,
        "is_active": True,
        "ir_json": _minimal_ir_json(),
        "raw_ir_json": None,
        "compiler_version": "0.1.0",
        "source_url": "https://example.com/api.yaml",
        "source_hash": "sha256:abc",
        "protocol": "openapi",
        "validation_report": None,
        "deployment_revision": "rev-1",
        "route_config": None,
        "tenant": None,
        "environment": None,
        "created_at": _utcnow(),
        "artifacts": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_artifact(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "artifact_type": "image",
        "content_hash": "sha256:img",
        "storage_path": "/artifacts/image.tar",
        "metadata_json": {"tag": "latest"},
        "created_at": _utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- CompilationRepository._to_job_response ---


class TestToJobResponse:
    def test_basic_fields(self) -> None:
        job = _fake_job()
        result = CompilationRepository._to_job_response(job)  # type: ignore[arg-type]
        assert result.id == job.id
        assert result.source_url == "https://example.com/api.yaml"
        assert result.status == "completed"
        assert result.current_stage == "deploy"
        assert result.service_name == "Test API"
        assert result.created_at == _utcnow()

    def test_null_fields(self) -> None:
        job = _fake_job(
            source_url=None,
            source_hash=None,
            protocol=None,
            current_stage=None,
            options=None,
            created_by=None,
            service_name=None,
        )
        result = CompilationRepository._to_job_response(job)  # type: ignore[arg-type]
        assert result.source_url is None
        assert result.protocol is None
        assert result.current_stage is None


# --- CompilationRepository._to_event_response ---


class TestToEventResponse:
    def test_basic_fields(self) -> None:
        event = _fake_event()
        result = CompilationRepository._to_event_response(event)  # type: ignore[arg-type]
        assert result.id == event.id
        assert result.job_id == event.job_id
        assert result.sequence_number == 1
        assert result.stage == "extract"
        assert result.event_type == "stage.succeeded"
        assert result.attempt == 1
        assert result.detail == {"operations": 5}

    def test_null_optional_fields(self) -> None:
        event = _fake_event(stage=None, attempt=None, detail=None, error_detail=None)
        result = CompilationRepository._to_event_response(event)  # type: ignore[arg-type]
        assert result.stage is None
        assert result.attempt is None


# --- ServiceCatalogRepository._to_service_summary ---


class TestToServiceSummary:
    def test_basic_fields(self) -> None:
        version = _fake_service_version()
        result = ServiceCatalogRepository._to_service_summary(version)  # type: ignore[arg-type]
        assert result.service_id == "test-svc"
        assert result.active_version == 1
        assert result.service_name == "Test Service"
        assert result.tool_count == 0
        assert result.protocol == "openapi"

    def test_protocol_fallback_to_ir(self) -> None:
        version = _fake_service_version(protocol=None)
        result = ServiceCatalogRepository._to_service_summary(version)  # type: ignore[arg-type]
        assert result.protocol == "openapi"


# --- ArtifactRegistryRepository._normalize_ir_json ---


class TestNormalizeIrJson:
    def test_valid_ir(self) -> None:
        ir_json = _minimal_ir_json()
        result = ArtifactRegistryRepository._normalize_ir_json(ir_json)
        assert result["service_name"] == "Test Service"
        assert result["protocol"] == "openapi"

    def test_invalid_ir_raises(self) -> None:
        with pytest.raises(Exception):
            ArtifactRegistryRepository._normalize_ir_json({"bad": "data"})


class TestNormalizeOptionalIrJson:
    def test_none_returns_none(self) -> None:
        assert ArtifactRegistryRepository._normalize_optional_ir_json(None) is None

    def test_valid_returns_normalized(self) -> None:
        result = ArtifactRegistryRepository._normalize_optional_ir_json(_minimal_ir_json())
        assert result is not None
        assert result["service_name"] == "Test Service"


# --- ArtifactRegistryRepository._to_response ---


class TestToResponse:
    def test_basic_fields(self) -> None:
        version = _fake_service_version()
        result = ArtifactRegistryRepository._to_response(version)  # type: ignore[arg-type]
        assert result.service_id == "test-svc"
        assert result.version_number == 1
        assert result.is_active is True
        assert result.compiler_version == "0.1.0"
        assert result.artifacts == []

    def test_with_artifacts(self) -> None:
        art = _fake_artifact()
        version = _fake_service_version(artifacts=[art])
        result = ArtifactRegistryRepository._to_response(version)  # type: ignore[arg-type]
        assert len(result.artifacts) == 1
        assert result.artifacts[0].artifact_type == "image"
        assert result.artifacts[0].content_hash == "sha256:img"


# --- ArtifactRegistryRepository._to_diff_change ---


class TestToDiffChange:
    def test_param_change(self) -> None:
        change = ParamChange(
            field_name="type",
            old_value="string",
            new_value="integer",
            param_name="user_id",
        )
        result = ArtifactRegistryRepository._to_diff_change(change)
        assert result.field_name == "type"
        assert result.old_value == "string"
        assert result.new_value == "integer"
        assert result.param_name == "user_id"

    def test_tuple_change(self) -> None:
        change = ("description", "old desc", "new desc")
        result = ArtifactRegistryRepository._to_diff_change(change)
        assert result.field_name == "description"
        assert result.old_value == "old desc"
        assert result.new_value == "new desc"
        assert result.param_name is None
