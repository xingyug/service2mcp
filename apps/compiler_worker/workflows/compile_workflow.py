"""Durable compilation workflow core with retries, rollback, and persistence hooks."""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter
from typing import Protocol
from uuid import UUID

from apps.compiler_worker.models import (
    CompilationContext,
    CompilationEventType,
    CompilationRequest,
    CompilationResult,
    CompilationStage,
    CompilationStatus,
    StageDefinition,
    StageExecutionResult,
    compilation_resume_checkpoint,
)
from apps.compiler_worker.observability import CompilationObservability

DEFAULT_STAGE_DEFINITIONS: tuple[StageDefinition, ...] = (
    StageDefinition(stage=CompilationStage.DETECT),
    StageDefinition(stage=CompilationStage.EXTRACT),
    StageDefinition(stage=CompilationStage.ENHANCE),
    StageDefinition(stage=CompilationStage.VALIDATE_IR),
    StageDefinition(stage=CompilationStage.GENERATE, rollback_enabled=True),
    StageDefinition(stage=CompilationStage.DEPLOY, rollback_enabled=True),
    StageDefinition(stage=CompilationStage.VALIDATE_RUNTIME),
    StageDefinition(stage=CompilationStage.ROUTE, rollback_enabled=True),
    StageDefinition(stage=CompilationStage.REGISTER),
)


class CompilationJobStore(Protocol):
    """Persistence interface required by the workflow."""

    async def create_job(
        self,
        request: CompilationRequest,
        *,
        job_id: UUID | None = None,
    ) -> UUID: ...

    async def mark_job_running(
        self,
        job_id: UUID,
        stage: CompilationStage,
        *,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None: ...

    async def mark_job_succeeded(
        self,
        job_id: UUID,
        stage: CompilationStage,
        *,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None: ...

    async def mark_job_failed(
        self,
        job_id: UUID,
        stage: CompilationStage,
        error_detail: str,
        *,
        rolled_back: bool,
        protocol: str | None = None,
        service_name: str | None = None,
    ) -> None: ...

    async def append_event(
        self,
        job_id: UUID,
        *,
        event_type: CompilationEventType,
        stage: CompilationStage | None = None,
        attempt: int | None = None,
        detail: dict[str, object] | None = None,
        error_detail: str | None = None,
    ) -> None: ...

    async def update_checkpoint(
        self,
        job_id: UUID,
        *,
        payload: dict[str, object],
        protocol: str | None,
        service_name: str | None,
        completed_stage: CompilationStage,
    ) -> None: ...


class CompilationActivities(Protocol):
    """Activity interface for workflow stages and compensating rollbacks."""

    async def run_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
    ) -> StageExecutionResult: ...

    async def rollback_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None: ...


class CompilationWorkflowError(RuntimeError):
    """Raised when workflow execution fails after retries."""

    def __init__(
        self,
        *,
        job_id: UUID,
        failed_stage: CompilationStage,
        final_status: CompilationStatus,
        message: str,
    ) -> None:
        super().__init__(message)
        self.job_id = job_id
        self.failed_stage = failed_stage
        self.final_status = final_status


class CompilationWorkflow:
    """Engine-agnostic workflow core suitable for Celery or future Temporal wrappers."""

    def __init__(
        self,
        *,
        store: CompilationJobStore,
        activities: CompilationActivities,
        stage_definitions: Sequence[StageDefinition] | None = None,
        observability: CompilationObservability | None = None,
    ) -> None:
        self._store = store
        self._activities = activities
        self._stage_definitions = tuple(stage_definitions or DEFAULT_STAGE_DEFINITIONS)
        self._observability = observability

    async def run(self, request: CompilationRequest) -> CompilationResult:
        """Execute the full compilation pipeline for the provided request."""

        job_id = await self._store.create_job(request, job_id=request.job_id)
        context = CompilationContext(
            job_id=job_id,
            request=request,
            payload={
                "source_url": request.source_url,
                "source_content": request.source_content,
                "source_hash": request.source_hash,
                "filename": request.filename,
                "options": dict(request.options),
            },
            service_name=request.service_id or request.service_name,
        )
        stage_definitions = self._resolve_stage_definitions(request, context)

        await self._store.append_event(job_id, event_type=CompilationEventType.JOB_CREATED)
        await self._store.append_event(job_id, event_type=CompilationEventType.JOB_STARTED)

        for stage_definition in stage_definitions:
            stage = stage_definition.stage
            await self._store.mark_job_running(
                job_id,
                stage,
                protocol=context.protocol,
                service_name=context.service_name,
            )

            for attempt in range(1, stage_definition.retry_policy.max_attempts + 1):
                stage_started_at = perf_counter()
                await self._store.append_event(
                    job_id,
                    event_type=CompilationEventType.STAGE_STARTED,
                    stage=stage,
                    attempt=attempt,
                )
                try:
                    result = await self._activities.run_stage(stage, context)
                except Exception as exc:  # broad-except: retry/rollback safety
                    error_detail = str(exc)
                    self._record_stage_metric(
                        stage,
                        outcome="error",
                        duration_seconds=perf_counter() - stage_started_at,
                    )
                    if stage is CompilationStage.EXTRACT:
                        self._record_extractor_metric(
                            context.protocol or "unknown",
                            outcome="error",
                        )
                    if attempt < stage_definition.retry_policy.max_attempts:
                        await self._store.append_event(
                            job_id,
                            event_type=CompilationEventType.STAGE_RETRYING,
                            stage=stage,
                            attempt=attempt,
                            detail={"next_attempt": attempt + 1},
                            error_detail=error_detail,
                        )
                        continue

                    await self._store.append_event(
                        job_id,
                        event_type=CompilationEventType.STAGE_FAILED,
                        stage=stage,
                        attempt=attempt,
                        error_detail=error_detail,
                    )
                    rolled_back, rollback_failures = await self._rollback(context)
                    await self._store.mark_job_failed(
                        job_id,
                        stage,
                        error_detail,
                        rolled_back=rolled_back,
                        protocol=context.protocol,
                        service_name=context.service_name,
                    )
                    await self._store.append_event(
                        job_id,
                        event_type=(
                            CompilationEventType.JOB_ROLLED_BACK
                            if rolled_back
                            else CompilationEventType.JOB_FAILED
                        ),
                        stage=stage,
                        detail=(
                            {"rollback_failures": rollback_failures} if rollback_failures else None
                        ),
                        error_detail=error_detail,
                    )
                    self._record_terminal_job_metric(
                        CompilationStatus.ROLLED_BACK if rolled_back else CompilationStatus.FAILED
                    )
                    raise CompilationWorkflowError(
                        job_id=job_id,
                        failed_stage=stage,
                        final_status=(
                            CompilationStatus.ROLLED_BACK
                            if rolled_back
                            else CompilationStatus.FAILED
                        ),
                        message=f"Compilation failed at stage {stage.value}: {error_detail}",
                    ) from exc

                context.stage_results[stage] = result
                context.payload.update(result.context_updates)
                if result.protocol is not None:
                    context.protocol = result.protocol
                if result.service_name is not None:
                    context.service_name = result.service_name
                await self._store.update_checkpoint(
                    job_id,
                    payload=context.payload,
                    protocol=context.protocol,
                    service_name=context.service_name,
                    completed_stage=stage,
                )
                self._record_stage_metric(
                    stage,
                    outcome="success",
                    duration_seconds=perf_counter() - stage_started_at,
                )
                if stage is CompilationStage.EXTRACT:
                    self._record_extractor_metric(
                        context.protocol or result.protocol or "unknown",
                        outcome="success",
                    )
                if stage is CompilationStage.ENHANCE:
                    self._record_llm_token_usage(result.context_updates.get("token_usage"))

                await self._store.mark_job_running(
                    job_id,
                    stage,
                    protocol=context.protocol,
                    service_name=context.service_name,
                )
                await self._store.append_event(
                    job_id,
                    event_type=CompilationEventType.STAGE_SUCCEEDED,
                    stage=stage,
                    attempt=attempt,
                    detail=result.event_detail,
                )
                break

        final_stage = stage_definitions[-1].stage
        await self._store.mark_job_succeeded(
            job_id,
            final_stage,
            protocol=context.protocol,
            service_name=context.service_name,
        )
        await self._store.append_event(
            job_id,
            event_type=CompilationEventType.JOB_SUCCEEDED,
            stage=final_stage,
            detail={
                "protocol": context.protocol,
                "service_name": context.service_name,
            },
        )
        self._record_terminal_job_metric(CompilationStatus.SUCCEEDED)
        return CompilationResult(
            job_id=job_id,
            status=CompilationStatus.SUCCEEDED,
            final_stage=final_stage,
            payload=context.payload,
        )

    def _resolve_stage_definitions(
        self,
        request: CompilationRequest,
        context: CompilationContext,
    ) -> tuple[StageDefinition, ...]:
        raw_from_stage = request.options.get("from_stage")
        if not isinstance(raw_from_stage, str) or not raw_from_stage.strip():
            return self._stage_definitions

        from_stage = CompilationStage(raw_from_stage.strip())
        stage_indices = {
            stage_definition.stage: index
            for index, stage_definition in enumerate(self._stage_definitions)
        }
        start_index = stage_indices[from_stage]
        if start_index <= 1:
            return self._stage_definitions[start_index:]

        checkpoint = compilation_resume_checkpoint(request.options)
        if checkpoint is None:
            raise RuntimeError(
                f"Retry from stage {from_stage.value} requires a persisted checkpoint."
            )

        expected_completed_stage = self._stage_definitions[start_index - 1].stage
        completed_stage = CompilationStage(checkpoint["completed_stage"])
        if completed_stage is not expected_completed_stage:
            raise RuntimeError(
                "Retry checkpoint does not match the requested stage boundary: "
                f"expected {expected_completed_stage.value}, found {completed_stage.value}."
            )

        context.payload = checkpoint["payload"]
        context.protocol = checkpoint["protocol"]
        context.service_name = checkpoint["service_name"]
        return self._stage_definitions[start_index:]

    async def _rollback(self, context: CompilationContext) -> tuple[bool, list[str]]:
        rollback_failures: list[str] = []
        rollback_executed = False

        for stage_definition in reversed(self._stage_definitions):
            if not stage_definition.rollback_enabled:
                continue
            stage = stage_definition.stage
            result = context.stage_results.get(stage)
            if result is None:
                continue

            rollback_executed = True
            await self._store.append_event(
                context.job_id,
                event_type=CompilationEventType.ROLLBACK_STARTED,
                stage=stage,
            )
            try:
                await self._activities.rollback_stage(stage, context, result)
            except Exception as exc:  # broad-except: best-effort rollback
                failure_message = f"{stage.value}: {exc}"
                rollback_failures.append(failure_message)
                await self._store.append_event(
                    context.job_id,
                    event_type=CompilationEventType.ROLLBACK_FAILED,
                    stage=stage,
                    error_detail=str(exc),
                )
            else:
                await self._store.append_event(
                    context.job_id,
                    event_type=CompilationEventType.ROLLBACK_SUCCEEDED,
                    stage=stage,
                )

        if rollback_failures:
            return False, rollback_failures
        return rollback_executed, []

    def _record_terminal_job_metric(self, status: CompilationStatus) -> None:
        if self._observability is None:
            return
        self._observability.record_job(status)

    def _record_stage_metric(
        self,
        stage: CompilationStage,
        *,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        if self._observability is None:
            return
        self._observability.record_stage(
            stage,
            outcome=outcome,
            duration_seconds=duration_seconds,
        )

    def _record_extractor_metric(self, protocol: str, *, outcome: str) -> None:
        if self._observability is None:
            return
        self._observability.record_extractor_run(protocol=protocol, outcome=outcome)

    def _record_llm_token_usage(self, token_usage: object | None) -> None:
        if self._observability is None or not isinstance(token_usage, dict):
            return

        model = token_usage.get("model")
        input_tokens = token_usage.get("input_tokens")
        output_tokens = token_usage.get("output_tokens")
        if not isinstance(model, str):
            return
        if not isinstance(input_tokens, int):
            return
        if not isinstance(output_tokens, int):
            return

        self._observability.record_llm_token_usage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
