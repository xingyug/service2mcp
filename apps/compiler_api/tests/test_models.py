"""Unit tests for apps/compiler_api/models.py."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.compiler_api.models import (
    CompilationCreateRequest,
    CompilationEventResponse,
    CompilationJobResponse,
    ServiceListResponse,
    ServiceSummaryResponse,
)
from apps.compiler_worker.models import (
    CompilationEventRecord,
    CompilationEventType,
    CompilationJobRecord,
    CompilationStage,
    CompilationStatus,
)


class TestCompilationCreateRequest:
    def test_valid_with_source_url(self) -> None:
        req = CompilationCreateRequest(source_url="https://example.com/spec.yaml")
        assert req.source_url == "https://example.com/spec.yaml"
        assert req.source_content is None

    def test_valid_with_source_content(self) -> None:
        req = CompilationCreateRequest(source_content="openapi: 3.0.0")
        assert req.source_content == "openapi: 3.0.0"

    def test_requires_source_url_or_content(self) -> None:
        with pytest.raises(ValidationError, match="source_url or source_content"):
            CompilationCreateRequest()

    def test_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            CompilationCreateRequest(
                source_url="https://example.com",
                unknown_field="bad",  # type: ignore[call-arg]
            )

    def test_to_workflow_request(self) -> None:
        req = CompilationCreateRequest(
            source_url="https://example.com/spec.yaml",
            service_id="billing-api",
            service_name="my-svc",
            options={"key": "val"},
        )
        wf = req.to_workflow_request()
        assert wf.source_url == "https://example.com/spec.yaml"
        assert wf.service_id == "billing-api"
        assert wf.service_name == "my-svc"
        assert wf.options == {"key": "val"}

    def test_default_options_empty(self) -> None:
        req = CompilationCreateRequest(source_url="https://example.com")
        assert req.options == {}


class TestCompilationJobResponse:
    def test_from_record(self) -> None:
        now = datetime.utcnow()
        record = CompilationJobRecord(
            id=uuid4(),
            source_url="https://example.com",
            source_hash="abc",
            protocol="openapi",
            status=CompilationStatus.SUCCEEDED,
            current_stage=CompilationStage.REGISTER,
            error_detail=None,
            options={"k": "v"},
            created_by="user1",
            service_name="petstore",
            created_at=now,
            updated_at=now,
            tenant="team-a",
            environment="prod",
        )
        resp = CompilationJobResponse.from_record(record)
        assert resp.id == record.id
        assert resp.status == "succeeded"
        assert resp.current_stage == "register"
        assert resp.protocol == "openapi"
        assert resp.service_id == "petstore"
        assert resp.tenant == "team-a"
        assert resp.environment == "prod"

    def test_from_record_none_stage(self) -> None:
        now = datetime.utcnow()
        record = CompilationJobRecord(
            id=uuid4(),
            source_url=None,
            source_hash=None,
            protocol=None,
            status=CompilationStatus.PENDING,
            current_stage=None,
            error_detail=None,
            options=None,
            created_by=None,
            service_name=None,
            created_at=now,
            updated_at=now,
        )
        resp = CompilationJobResponse.from_record(record)
        assert resp.current_stage is None
        assert resp.status == "pending"


class TestCompilationEventResponse:
    def test_from_record(self) -> None:
        now = datetime.utcnow()
        record = CompilationEventRecord(
            id=uuid4(),
            job_id=uuid4(),
            sequence_number=1,
            stage=CompilationStage.EXTRACT,
            event_type=CompilationEventType.STAGE_STARTED,
            attempt=1,
            detail={"protocol": "graphql"},
            error_detail=None,
            created_at=now,
        )
        resp = CompilationEventResponse.from_record(record)
        assert resp.stage == "extract"
        assert resp.event_type == "stage.started"
        assert resp.attempt == 1

    def test_from_record_none_stage(self) -> None:
        now = datetime.utcnow()
        record = CompilationEventRecord(
            id=uuid4(),
            job_id=uuid4(),
            sequence_number=0,
            stage=None,
            event_type=CompilationEventType.JOB_CREATED,
            attempt=None,
            detail=None,
            error_detail=None,
            created_at=now,
        )
        resp = CompilationEventResponse.from_record(record)
        assert resp.stage is None


class TestServiceSummaryResponse:
    def test_construction(self) -> None:
        now = datetime.utcnow()
        resp = ServiceSummaryResponse(
            service_id="svc-1",
            active_version=1,
            version_count=2,
            service_name="petstore",
            tool_count=10,
            created_at=now,
        )
        assert resp.service_id == "svc-1"
        assert resp.protocol is None
        assert resp.version_count == 2


class TestServiceListResponse:
    def test_empty_list(self) -> None:
        resp = ServiceListResponse(services=[])
        assert resp.services == []
