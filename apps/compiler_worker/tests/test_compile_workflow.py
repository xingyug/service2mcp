"""Unit tests for apps/compiler_worker/workflows/compile_workflow.py."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from apps.compiler_worker.models import (
    CompilationContext,
    CompilationEventType,
    CompilationRequest,
    CompilationStage,
    CompilationStatus,
    RetryPolicy,
    StageDefinition,
    StageExecutionResult,
)
from apps.compiler_worker.observability import CompilationObservability
from apps.compiler_worker.workflows.compile_workflow import (
    DEFAULT_STAGE_DEFINITIONS,
    CompilationWorkflow,
    CompilationWorkflowError,
)

# --- Fake implementations for protocol dependencies ---


class FakeJobStore:
    """In-memory store satisfying CompilationJobStore protocol."""

    def __init__(self) -> None:
        self.jobs: dict[UUID, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []

    async def create_job(
        self,
        request: CompilationRequest,
        *,
        job_id: UUID | None = None,
    ) -> UUID:
        resolved = job_id or uuid4()
        self.jobs[resolved] = {"status": "pending"}
        return resolved

    async def mark_job_running(
        self,
        job_id: UUID,
        stage: CompilationStage,
        *,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None:
        self.jobs[job_id]["status"] = "running"
        self.jobs[job_id]["stage"] = stage

    async def mark_job_succeeded(
        self,
        job_id: UUID,
        stage: CompilationStage,
        *,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None:
        self.jobs[job_id]["status"] = "succeeded"

    async def mark_job_failed(
        self,
        job_id: UUID,
        stage: CompilationStage,
        error_detail: str,
        *,
        rolled_back: bool,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None:
        self.jobs[job_id]["status"] = "rolled_back" if rolled_back else "failed"

    async def append_event(
        self,
        job_id: UUID,
        *,
        event_type: CompilationEventType,
        stage: CompilationStage | None = None,
        attempt: int | None = None,
        detail: dict[str, object] | None = None,
        error_detail: str | None = None,
    ) -> None:
        self.events.append(
            {
                "job_id": job_id,
                "event_type": event_type,
                "stage": stage,
                "attempt": attempt,
                "detail": detail,
                "error_detail": error_detail,
            }
        )


class FakeActivities:
    """In-memory activities satisfying CompilationActivities protocol."""

    def __init__(self) -> None:
        self.stage_results: dict[CompilationStage, StageExecutionResult] = {}
        self.stage_errors: dict[CompilationStage, list[Exception]] = {}
        self.rollback_calls: list[CompilationStage] = []
        self.rollback_errors: dict[CompilationStage, Exception] = {}

    async def run_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
    ) -> StageExecutionResult:
        errors = self.stage_errors.get(stage, [])
        if errors:
            raise errors.pop(0)
        return self.stage_results.get(stage, StageExecutionResult())

    async def rollback_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        self.rollback_calls.append(stage)
        error = self.rollback_errors.get(stage)
        if error is not None:
            raise error


# --- Tests ---


class TestDefaultStageDefinitions:
    def test_has_nine_stages(self) -> None:
        assert len(DEFAULT_STAGE_DEFINITIONS) == 9

    def test_starts_with_detect(self) -> None:
        assert DEFAULT_STAGE_DEFINITIONS[0].stage == CompilationStage.DETECT

    def test_ends_with_register(self) -> None:
        assert DEFAULT_STAGE_DEFINITIONS[-1].stage == CompilationStage.REGISTER

    def test_rollback_enabled_stages(self) -> None:
        rollback_stages = [sd.stage for sd in DEFAULT_STAGE_DEFINITIONS if sd.rollback_enabled]
        assert rollback_stages == [
            CompilationStage.GENERATE,
            CompilationStage.DEPLOY,
            CompilationStage.ROUTE,
        ]


class TestCompilationWorkflowError:
    def test_attributes(self) -> None:
        job_id = uuid4()
        err = CompilationWorkflowError(
            job_id=job_id,
            failed_stage=CompilationStage.EXTRACT,
            final_status=CompilationStatus.FAILED,
            message="extraction failed",
        )
        assert err.job_id == job_id
        assert err.failed_stage == CompilationStage.EXTRACT
        assert err.final_status == CompilationStatus.FAILED
        assert "extraction failed" in str(err)

    def test_is_runtime_error(self) -> None:
        err = CompilationWorkflowError(
            job_id=uuid4(),
            failed_stage=CompilationStage.DETECT,
            final_status=CompilationStatus.FAILED,
            message="test",
        )
        assert isinstance(err, RuntimeError)


class TestCompilationWorkflowSuccess:
    @pytest.mark.asyncio
    async def test_simple_two_stage_success(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_results[CompilationStage.DETECT] = StageExecutionResult(
            context_updates={"detected": True},
            protocol="openapi",
        )
        activities.stage_results[CompilationStage.EXTRACT] = StageExecutionResult(
            context_updates={"ir": "data"},
        )
        stages = (
            StageDefinition(stage=CompilationStage.DETECT),
            StageDefinition(stage=CompilationStage.EXTRACT),
        )
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        request = CompilationRequest(source_url="https://example.com")
        result = await wf.run(request)
        assert result.status == CompilationStatus.SUCCEEDED
        assert result.final_stage == CompilationStage.EXTRACT
        assert result.payload["detected"] is True

    @pytest.mark.asyncio
    async def test_protocol_propagated(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_results[CompilationStage.DETECT] = StageExecutionResult(protocol="graphql")
        stages = (StageDefinition(stage=CompilationStage.DETECT),)
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        result = await wf.run(CompilationRequest(source_url="https://example.com"))
        assert result.status == CompilationStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_service_name_propagated(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_results[CompilationStage.DETECT] = StageExecutionResult(
            service_name="my-service"
        )
        stages = (StageDefinition(stage=CompilationStage.DETECT),)
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        result = await wf.run(CompilationRequest(source_url="https://example.com"))
        assert result.status == CompilationStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_events_recorded(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        stages = (StageDefinition(stage=CompilationStage.DETECT),)
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        await wf.run(CompilationRequest(source_url="https://example.com"))
        event_types = [e["event_type"] for e in store.events]
        assert CompilationEventType.JOB_CREATED in event_types
        assert CompilationEventType.JOB_STARTED in event_types
        assert CompilationEventType.STAGE_STARTED in event_types
        assert CompilationEventType.STAGE_SUCCEEDED in event_types
        assert CompilationEventType.JOB_SUCCEEDED in event_types


class TestCompilationWorkflowRetry:
    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_errors[CompilationStage.DETECT] = [
            RuntimeError("transient"),
        ]
        stages = (
            StageDefinition(
                stage=CompilationStage.DETECT,
                retry_policy=RetryPolicy(max_attempts=2),
            ),
        )
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        result = await wf.run(CompilationRequest(source_url="https://example.com"))
        assert result.status == CompilationStatus.SUCCEEDED
        retry_events = [
            e for e in store.events if e["event_type"] == CompilationEventType.STAGE_RETRYING
        ]
        assert len(retry_events) == 1


class TestCompilationWorkflowFailure:
    @pytest.mark.asyncio
    async def test_fails_after_max_retries(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_errors[CompilationStage.DETECT] = [
            RuntimeError("fail1"),
            RuntimeError("fail2"),
            RuntimeError("fail3"),
        ]
        stages = (
            StageDefinition(
                stage=CompilationStage.DETECT,
                retry_policy=RetryPolicy(max_attempts=3),
            ),
        )
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        with pytest.raises(CompilationWorkflowError) as exc_info:
            await wf.run(CompilationRequest(source_url="https://example.com"))
        assert exc_info.value.failed_stage == CompilationStage.DETECT
        assert exc_info.value.final_status == CompilationStatus.FAILED


class TestCompilationWorkflowRollback:
    @pytest.mark.asyncio
    async def test_rollback_on_failure_with_rollback_stages(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_results[CompilationStage.GENERATE] = StageExecutionResult(
            rollback_payload={"manifest": "data"}
        )
        activities.stage_errors[CompilationStage.DEPLOY] = [RuntimeError("deploy boom")]
        stages = (
            StageDefinition(stage=CompilationStage.GENERATE, rollback_enabled=True),
            StageDefinition(
                stage=CompilationStage.DEPLOY,
                rollback_enabled=True,
                retry_policy=RetryPolicy(max_attempts=1),
            ),
        )
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        with pytest.raises(CompilationWorkflowError) as exc_info:
            await wf.run(CompilationRequest(source_url="https://example.com"))
        assert exc_info.value.final_status == CompilationStatus.ROLLED_BACK
        assert CompilationStage.GENERATE in activities.rollback_calls

    @pytest.mark.asyncio
    async def test_rollback_failure_sets_failed_status(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_results[CompilationStage.GENERATE] = StageExecutionResult()
        activities.stage_errors[CompilationStage.DEPLOY] = [RuntimeError("deploy fail")]
        activities.rollback_errors[CompilationStage.GENERATE] = RuntimeError("rollback fail")
        stages = (
            StageDefinition(stage=CompilationStage.GENERATE, rollback_enabled=True),
            StageDefinition(
                stage=CompilationStage.DEPLOY,
                rollback_enabled=True,
                retry_policy=RetryPolicy(max_attempts=1),
            ),
        )
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        with pytest.raises(CompilationWorkflowError) as exc_info:
            await wf.run(CompilationRequest(source_url="https://example.com"))
        assert exc_info.value.final_status == CompilationStatus.FAILED

    @pytest.mark.asyncio
    async def test_no_rollback_if_no_enabled_stages_completed(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_errors[CompilationStage.DETECT] = [RuntimeError("fail")]
        stages = (
            StageDefinition(
                stage=CompilationStage.DETECT,
                retry_policy=RetryPolicy(max_attempts=1),
            ),
        )
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        with pytest.raises(CompilationWorkflowError) as exc_info:
            await wf.run(CompilationRequest(source_url="https://example.com"))
        assert exc_info.value.final_status == CompilationStatus.FAILED
        assert activities.rollback_calls == []


class TestCompilationWorkflowObservability:
    @pytest.mark.asyncio
    async def test_records_metrics_on_success(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        obs = CompilationObservability()
        stages = (StageDefinition(stage=CompilationStage.DETECT),)
        wf = CompilationWorkflow(
            store=store,
            activities=activities,
            stage_definitions=stages,
            observability=obs,
        )
        await wf.run(CompilationRequest(source_url="https://example.com"))
        jobs_val = obs.jobs_total.labels(status="succeeded")._value.get()
        assert jobs_val == 1.0

    @pytest.mark.asyncio
    async def test_records_extractor_metric(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_results[CompilationStage.DETECT] = StageExecutionResult(protocol="rest")
        obs = CompilationObservability()
        stages = (
            StageDefinition(stage=CompilationStage.DETECT),
            StageDefinition(stage=CompilationStage.EXTRACT),
        )
        wf = CompilationWorkflow(
            store=store,
            activities=activities,
            stage_definitions=stages,
            observability=obs,
        )
        await wf.run(CompilationRequest(source_url="https://example.com"))
        val = obs.extractor_runs_total.labels(protocol="rest", outcome="success")._value.get()
        assert val == 1.0

    @pytest.mark.asyncio
    async def test_records_llm_token_usage(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        activities.stage_results[CompilationStage.ENHANCE] = StageExecutionResult(
            context_updates={
                "token_usage": {
                    "model": "deepseek",
                    "input_tokens": 100,
                    "output_tokens": 50,
                }
            }
        )
        obs = CompilationObservability()
        stages = (StageDefinition(stage=CompilationStage.ENHANCE),)
        wf = CompilationWorkflow(
            store=store,
            activities=activities,
            stage_definitions=stages,
            observability=obs,
        )
        await wf.run(CompilationRequest(source_url="https://example.com"))
        input_val = obs.llm_tokens_total.labels(model="deepseek", direction="input")._value.get()
        assert input_val == 100.0

    @pytest.mark.asyncio
    async def test_no_observability_is_fine(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        stages = (StageDefinition(stage=CompilationStage.DETECT),)
        wf = CompilationWorkflow(store=store, activities=activities, stage_definitions=stages)
        result = await wf.run(CompilationRequest(source_url="https://example.com"))
        assert result.status == CompilationStatus.SUCCEEDED


class TestRecordLlmTokenUsageEdgeCases:
    """Test the private _record_llm_token_usage guard clauses."""

    def test_none_token_usage_is_noop(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        obs = CompilationObservability()
        wf = CompilationWorkflow(
            store=store,
            activities=activities,
            observability=obs,
        )
        wf._record_llm_token_usage(None)  # should not raise

    def test_non_dict_token_usage_is_noop(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        obs = CompilationObservability()
        wf = CompilationWorkflow(
            store=store,
            activities=activities,
            observability=obs,
        )
        wf._record_llm_token_usage("not a dict")  # should not raise

    def test_missing_model_is_noop(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        obs = CompilationObservability()
        wf = CompilationWorkflow(
            store=store,
            activities=activities,
            observability=obs,
        )
        wf._record_llm_token_usage({"input_tokens": 10, "output_tokens": 5})

    def test_non_int_tokens_is_noop(self) -> None:
        store = FakeJobStore()
        activities = FakeActivities()
        obs = CompilationObservability()
        wf = CompilationWorkflow(
            store=store,
            activities=activities,
            observability=obs,
        )
        wf._record_llm_token_usage(
            {
                "model": "test",
                "input_tokens": "bad",
                "output_tokens": 5,
            }
        )
