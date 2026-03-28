"""Unit tests for apps/compiler_worker/models.py."""

from __future__ import annotations

from uuid import uuid4

import pytest

from apps.compiler_worker.models import (
    CompilationContext,
    CompilationEventType,
    CompilationRequest,
    CompilationResult,
    CompilationStage,
    CompilationStatus,
    RetryPolicy,
    StageDefinition,
    StageExecutionResult,
)


class TestCompilationStage:
    def test_all_stages_present(self) -> None:
        expected = {
            "detect",
            "extract",
            "enhance",
            "validate_ir",
            "generate",
            "deploy",
            "validate_runtime",
            "route",
            "register",
        }
        assert {s.value for s in CompilationStage} == expected

    def test_string_enum(self) -> None:
        assert str(CompilationStage.DETECT) == "detect"
        assert isinstance(CompilationStage.DETECT, str)


class TestCompilationStatus:
    def test_all_statuses_present(self) -> None:
        expected = {"pending", "running", "succeeded", "failed", "rolled_back"}
        assert {s.value for s in CompilationStatus} == expected


class TestCompilationEventType:
    def test_job_events(self) -> None:
        job_events = [e for e in CompilationEventType if e.value.startswith("job.")]
        assert len(job_events) == 5

    def test_stage_events(self) -> None:
        stage_events = [e for e in CompilationEventType if e.value.startswith("stage.")]
        assert len(stage_events) == 4

    def test_rollback_events(self) -> None:
        rb_events = [e for e in CompilationEventType if e.value.startswith("rollback.")]
        assert len(rb_events) == 3


class TestRetryPolicy:
    def test_default_max_attempts(self) -> None:
        rp = RetryPolicy()
        assert rp.max_attempts == 3

    def test_custom_max_attempts(self) -> None:
        rp = RetryPolicy(max_attempts=5)
        assert rp.max_attempts == 5

    def test_frozen(self) -> None:
        rp = RetryPolicy()
        with pytest.raises(AttributeError):
            rp.max_attempts = 10  # type: ignore[misc]


class TestStageDefinition:
    def test_defaults(self) -> None:
        sd = StageDefinition(stage=CompilationStage.DETECT)
        assert sd.retry_policy.max_attempts == 3
        assert sd.rollback_enabled is False

    def test_custom_rollback_enabled(self) -> None:
        sd = StageDefinition(
            stage=CompilationStage.DEPLOY,
            rollback_enabled=True,
            retry_policy=RetryPolicy(max_attempts=1),
        )
        assert sd.rollback_enabled is True
        assert sd.retry_policy.max_attempts == 1


class TestCompilationRequest:
    def test_to_payload_round_trip(self) -> None:
        job_id = uuid4()
        req = CompilationRequest(
            source_url="https://example.com/spec.yaml",
            source_content=None,
            source_hash="abc123",
            filename="spec.yaml",
            created_by="user1",
            service_name="my-service",
            options={"key": "value"},
            job_id=job_id,
        )
        payload = req.to_payload()
        restored = CompilationRequest.from_payload(payload)
        assert restored.source_url == req.source_url
        assert restored.source_hash == req.source_hash
        assert restored.filename == req.filename
        assert restored.created_by == req.created_by
        assert restored.service_name == req.service_name
        assert restored.options == req.options
        assert restored.job_id == req.job_id

    def test_to_payload_none_job_id(self) -> None:
        req = CompilationRequest(source_url="https://example.com")
        payload = req.to_payload()
        assert payload["job_id"] is None
        restored = CompilationRequest.from_payload(payload)
        assert restored.job_id is None

    def test_from_payload_missing_options_defaults_to_empty_dict(self) -> None:
        payload = {"source_url": "https://example.com"}
        req = CompilationRequest.from_payload(payload)
        assert req.options == {}

    def test_from_payload_non_dict_options_defaults_to_empty_dict(self) -> None:
        payload = {"source_url": "https://example.com", "options": "invalid"}
        req = CompilationRequest.from_payload(payload)
        assert req.options == {}

    def test_defaults(self) -> None:
        req = CompilationRequest()
        assert req.source_url is None
        assert req.source_content is None
        assert req.options == {}
        assert req.job_id is None


class TestStageExecutionResult:
    def test_defaults(self) -> None:
        result = StageExecutionResult()
        assert result.context_updates == {}
        assert result.event_detail is None
        assert result.rollback_payload is None
        assert result.protocol is None
        assert result.service_name is None

    def test_with_values(self) -> None:
        result = StageExecutionResult(
            context_updates={"ir": "data"},
            protocol="openapi",
            service_name="petstore",
        )
        assert result.protocol == "openapi"
        assert result.context_updates == {"ir": "data"}


class TestCompilationContext:
    def test_construction(self) -> None:
        job_id = uuid4()
        req = CompilationRequest(source_url="https://example.com")
        ctx = CompilationContext(job_id=job_id, request=req)
        assert ctx.job_id == job_id
        assert ctx.payload == {}
        assert ctx.stage_results == {}
        assert ctx.protocol is None

    def test_mutable_payload(self) -> None:
        ctx = CompilationContext(
            job_id=uuid4(),
            request=CompilationRequest(),
        )
        ctx.payload["ir"] = "data"
        ctx.protocol = "graphql"
        assert ctx.payload == {"ir": "data"}
        assert ctx.protocol == "graphql"


class TestCompilationResult:
    def test_frozen(self) -> None:
        result = CompilationResult(
            job_id=uuid4(),
            status=CompilationStatus.SUCCEEDED,
            final_stage=CompilationStage.REGISTER,
            payload={"service_id": "svc-1"},
        )
        assert result.status == CompilationStatus.SUCCEEDED
        with pytest.raises(AttributeError):
            result.status = CompilationStatus.FAILED  # type: ignore[misc]
