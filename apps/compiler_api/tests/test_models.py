"""Unit tests for apps/compiler_api/models.py."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.compiler_api.models import (
    CompilationArtifacts,
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

    def test_rejects_both_source_url_and_source_content(self) -> None:
        with pytest.raises(ValidationError, match="exactly one of source_url or source_content"):
            CompilationCreateRequest(
                source_url="https://example.com/spec.yaml",
                source_content="openapi: 3.0.0",
            )

    def test_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            CompilationCreateRequest(
                source_url="https://example.com",
                unknown_field="bad",
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

    def test_whitespace_only_source_url_rejected(self) -> None:
        with pytest.raises(ValidationError, match="source_url or source_content"):
            CompilationCreateRequest(source_url="   ")

    def test_whitespace_only_source_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match="source_url or source_content"):
            CompilationCreateRequest(source_content="  \n  ")

    def test_source_url_stripped(self) -> None:
        req = CompilationCreateRequest(source_url="  https://example.com  ")
        assert req.source_url == "https://example.com"

    def test_source_url_rejects_crlf(self) -> None:
        with pytest.raises(ValidationError, match="control characters"):
            CompilationCreateRequest(source_url="http://example.com/\r\nHost: evil")

    def test_source_url_rejects_null_byte(self) -> None:
        with pytest.raises(ValidationError, match="control characters"):
            CompilationCreateRequest(source_url="http://example.com/\x00evil")

    def test_source_url_allows_non_http_schemes(self) -> None:
        req = CompilationCreateRequest(source_url="sqlite:///test.db")
        assert req.source_url == "sqlite:///test.db"


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
            service_id="petstore-id",
        )
        resp = CompilationJobResponse.from_record(record)
        assert resp.id == record.id
        assert resp.status == "succeeded"
        assert resp.current_stage == "register"
        assert resp.protocol == "openapi"
        assert resp.service_id == "petstore-id"
        assert resp.service_name == "petstore"
        assert resp.tenant == "team-a"
        assert resp.environment == "prod"
        assert resp.artifacts is not None
        assert resp.artifacts.ir_id == "petstore-id"

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
        assert resp.service_id is None
        assert resp.artifacts is None

    def test_from_record_service_id_none_falls_back_to_service_name(self) -> None:
        now = datetime.utcnow()
        record = CompilationJobRecord(
            id=uuid4(),
            source_url=None,
            source_hash=None,
            protocol=None,
            status=CompilationStatus.SUCCEEDED,
            current_stage=CompilationStage.REGISTER,
            error_detail=None,
            options=None,
            created_by=None,
            service_name="my-api",
            created_at=now,
            updated_at=now,
        )
        resp = CompilationJobResponse.from_record(record)
        assert resp.service_id is None
        assert resp.service_name == "my-api"
        assert resp.artifacts is not None
        assert resp.artifacts.ir_id == "my-api"

    def test_artifacts_none_is_backward_compatible(self) -> None:
        now = datetime.utcnow()
        resp = CompilationJobResponse(
            id=uuid4(),
            status="succeeded",
            created_at=now,
            updated_at=now,
        )
        assert resp.artifacts is None
        data = resp.model_dump(mode="json")
        assert data["artifacts"] is None

    def test_artifacts_serializes_correctly(self) -> None:
        artifacts = CompilationArtifacts(
            ir_id="petstore",
            image_digest="sha256:abc123",
            deployment_id="deploy-42",
        )
        data = artifacts.model_dump(mode="json")
        assert data == {
            "ir_id": "petstore",
            "image_digest": "sha256:abc123",
            "deployment_id": "deploy-42",
        }

    def test_response_includes_artifacts(self) -> None:
        now = datetime.utcnow()
        artifacts = CompilationArtifacts(ir_id="my-svc")
        resp = CompilationJobResponse(
            id=uuid4(),
            status="succeeded",
            created_at=now,
            updated_at=now,
            artifacts=artifacts,
        )
        data = resp.model_dump(mode="json")
        assert data["artifacts"]["ir_id"] == "my-svc"
        assert data["artifacts"]["image_digest"] is None
        assert data["artifacts"]["deployment_id"] is None


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
