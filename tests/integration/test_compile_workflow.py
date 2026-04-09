"""Integration-style tests for the compilation workflow state machine."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.compiler_worker.models import (
    CompilationContext,
    CompilationEventRecord,
    CompilationEventType,
    CompilationJobRecord,
    CompilationRequest,
    CompilationStage,
    CompilationStatus,
    StageExecutionResult,
    store_compilation_checkpoint,
)
from apps.compiler_worker.repository import SQLAlchemyCompilationJobStore
from apps.compiler_worker.workflows.compile_workflow import (
    DEFAULT_STAGE_DEFINITIONS,
    CompilationWorkflow,
    CompilationWorkflowError,
)
from libs.db_models import Base


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


class InMemoryCompilationJobStore:
    """Simple in-memory job store for workflow behavior tests."""

    def __init__(self) -> None:
        self.jobs: dict[UUID, CompilationJobRecord] = {}
        self.events: dict[UUID, list[CompilationEventRecord]] = defaultdict(list)

    async def create_job(
        self,
        request: CompilationRequest,
        *,
        job_id: UUID | None = None,
    ) -> UUID:
        resolved_job_id = job_id or uuid4()
        now = datetime.now(UTC)
        self.jobs[resolved_job_id] = CompilationJobRecord(
            id=resolved_job_id,
            source_url=request.source_url,
            source_hash=request.source_hash,
            protocol=None,
            status=CompilationStatus.PENDING,
            current_stage=None,
            error_detail=None,
            options=request.options or None,
            created_by=request.created_by,
            service_name=request.service_name,
            created_at=now,
            updated_at=now,
        )
        return resolved_job_id

    async def mark_job_running(
        self,
        job_id: UUID,
        stage: CompilationStage,
        *,
        protocol: str | None = None,
        service_name: str | None = None,
        service_id: str | None = None,
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = CompilationJobRecord(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=protocol if protocol is not None else job.protocol,
            status=CompilationStatus.RUNNING,
            current_stage=stage,
            error_detail=None,
            options=job.options,
            created_by=job.created_by,
            service_name=service_name if service_name is not None else job.service_name,
            created_at=job.created_at,
            updated_at=datetime.now(UTC),
            service_id=service_id if service_id is not None else job.service_id,
        )

    async def mark_job_succeeded(
        self,
        job_id: UUID,
        stage: CompilationStage,
        *,
        protocol: str | None = None,
        service_name: str | None = None,
        service_id: str | None = None,
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = CompilationJobRecord(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=protocol if protocol is not None else job.protocol,
            status=CompilationStatus.SUCCEEDED,
            current_stage=stage,
            error_detail=None,
            options=job.options,
            created_by=job.created_by,
            service_name=service_name if service_name is not None else job.service_name,
            created_at=job.created_at,
            updated_at=datetime.now(UTC),
            service_id=service_id if service_id is not None else job.service_id,
        )

    async def mark_job_failed(
        self,
        job_id: UUID,
        stage: CompilationStage,
        error_detail: str,
        *,
        rolled_back: bool,
        protocol: str | None = None,
        service_name: str | None = None,
        service_id: str | None = None,
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = CompilationJobRecord(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=protocol if protocol is not None else job.protocol,
            status=CompilationStatus.ROLLED_BACK if rolled_back else CompilationStatus.FAILED,
            current_stage=stage,
            error_detail=error_detail,
            options=job.options,
            created_by=job.created_by,
            service_name=service_name if service_name is not None else job.service_name,
            created_at=job.created_at,
            updated_at=datetime.now(UTC),
            service_id=service_id if service_id is not None else job.service_id,
        )

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
        self.events[job_id].append(
            CompilationEventRecord(
                id=uuid4(),
                job_id=job_id,
                sequence_number=len(self.events[job_id]) + 1,
                stage=stage,
                event_type=event_type,
                attempt=attempt,
                detail=detail,
                error_detail=error_detail,
                created_at=datetime.now(UTC),
            )
        )

    async def update_checkpoint(
        self,
        job_id: UUID,
        *,
        payload: dict[str, object],
        protocol: str | None,
        service_name: str | None,
        completed_stage: CompilationStage,
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = replace(
            job,
            protocol=protocol if protocol is not None else job.protocol,
            service_name=service_name if service_name is not None else job.service_name,
            options=store_compilation_checkpoint(
                job.options,
                payload=payload,
                protocol=protocol,
                service_name=service_name,
                completed_stage=completed_stage.value,
            ),
            updated_at=datetime.now(UTC),
        )

    async def get_job_status(self, job_id: UUID) -> CompilationStatus | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return job.status

    async def get_job(self, job_id: UUID) -> CompilationJobRecord | None:
        return self.jobs.get(job_id)

    async def list_events(self, job_id: UUID) -> list[CompilationEventRecord]:
        return list(self.events[job_id])


class StubActivities:
    """Deterministic stage runner used to validate workflow control flow."""

    def __init__(
        self,
        *,
        fail_stage: CompilationStage | None = None,
        transient_failures: dict[CompilationStage, int] | None = None,
    ) -> None:
        self.fail_stage = fail_stage
        self.transient_failures = transient_failures or {}
        self.attempts: dict[CompilationStage, int] = defaultdict(int)
        self.stage_calls: list[CompilationStage] = []
        self.rollback_calls: list[CompilationStage] = []

    async def run_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
    ) -> StageExecutionResult:
        self.stage_calls.append(stage)
        self.attempts[stage] += 1

        if self.fail_stage is stage:
            raise RuntimeError(f"{stage.value} failed")

        allowed_failures = self.transient_failures.get(stage, 0)
        if self.attempts[stage] <= allowed_failures:
            raise RuntimeError(f"{stage.value} transient failure {self.attempts[stage]}")

        return StageExecutionResult(
            context_updates={f"{stage.value}_attempt": self.attempts[stage]},
            event_detail={"attempt": self.attempts[stage]},
            rollback_payload={"stage": stage.value},
            protocol="openapi" if stage is CompilationStage.DETECT else None,
            service_name="billing-api" if stage is CompilationStage.EXTRACT else None,
        )

    async def rollback_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        del context, result
        self.rollback_calls.append(stage)


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture
async def session_factory(
    postgres_container: PostgresContainer,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_to_asyncpg_url(postgres_container.get_connection_url()))

    async with engine.begin() as connection:
        for schema_name in ("compiler", "registry", "auth"):
            await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_compile_workflow_happy_path_records_all_stages() -> None:
    store = InMemoryCompilationJobStore()
    activities = StubActivities()
    workflow = CompilationWorkflow(store=store, activities=activities)

    result = await workflow.run(
        CompilationRequest(
            source_url="https://example.com/openapi.json",
            created_by="tester",
        )
    )

    assert result.status is CompilationStatus.SUCCEEDED
    assert activities.stage_calls == [definition.stage for definition in DEFAULT_STAGE_DEFINITIONS]

    job = await store.get_job(result.job_id)
    assert job is not None
    assert job.status is CompilationStatus.SUCCEEDED
    assert job.current_stage is CompilationStage.REGISTER
    assert job.protocol == "openapi"
    assert job.service_name == "billing-api"

    events = await store.list_events(result.job_id)
    stage_successes = [
        event.stage for event in events if event.event_type is CompilationEventType.STAGE_SUCCEEDED
    ]
    assert stage_successes == [definition.stage for definition in DEFAULT_STAGE_DEFINITIONS]
    assert events[0].event_type is CompilationEventType.JOB_CREATED
    assert events[1].event_type is CompilationEventType.JOB_STARTED
    assert events[-1].event_type is CompilationEventType.JOB_SUCCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_stage", "expected_rollbacks", "expected_status"),
    [
        (CompilationStage.DETECT, [], CompilationStatus.FAILED),
        (CompilationStage.EXTRACT, [], CompilationStatus.FAILED),
        (CompilationStage.ENHANCE, [], CompilationStatus.FAILED),
        (CompilationStage.VALIDATE_IR, [], CompilationStatus.FAILED),
        (CompilationStage.GENERATE, [], CompilationStatus.FAILED),
        (CompilationStage.DEPLOY, [CompilationStage.GENERATE], CompilationStatus.ROLLED_BACK),
        (
            CompilationStage.VALIDATE_RUNTIME,
            [CompilationStage.DEPLOY, CompilationStage.GENERATE],
            CompilationStatus.ROLLED_BACK,
        ),
        (
            CompilationStage.ROUTE,
            [CompilationStage.DEPLOY, CompilationStage.GENERATE],
            CompilationStatus.ROLLED_BACK,
        ),
        (
            CompilationStage.REGISTER,
            [CompilationStage.ROUTE, CompilationStage.DEPLOY, CompilationStage.GENERATE],
            CompilationStatus.ROLLED_BACK,
        ),
    ],
)
async def test_compile_workflow_rolls_back_completed_side_effect_stages(
    failed_stage: CompilationStage,
    expected_rollbacks: list[CompilationStage],
    expected_status: CompilationStatus,
) -> None:
    store = InMemoryCompilationJobStore()
    activities = StubActivities(fail_stage=failed_stage)
    workflow = CompilationWorkflow(store=store, activities=activities)

    with pytest.raises(CompilationWorkflowError) as exc_info:
        await workflow.run(CompilationRequest(source_url="https://example.com/spec.yaml"))

    error = exc_info.value
    assert error.failed_stage is failed_stage
    assert error.final_status is expected_status
    assert activities.rollback_calls == expected_rollbacks

    job = await store.get_job(error.job_id)
    assert job is not None
    assert job.status is expected_status
    assert job.current_stage is failed_stage
    assert job.error_detail == f"{failed_stage.value} failed"

    events = await store.list_events(error.job_id)
    rollback_started = [
        event.stage for event in events if event.event_type is CompilationEventType.ROLLBACK_STARTED
    ]
    assert rollback_started == expected_rollbacks
    assert any(
        event.event_type is CompilationEventType.STAGE_FAILED and event.stage is failed_stage
        for event in events
    )


@pytest.mark.asyncio
async def test_compile_workflow_retries_transient_stage_failure() -> None:
    store = InMemoryCompilationJobStore()
    activities = StubActivities(transient_failures={CompilationStage.ENHANCE: 1})
    workflow = CompilationWorkflow(store=store, activities=activities)

    result = await workflow.run(CompilationRequest(source_url="https://example.com/spec.yaml"))

    assert result.status is CompilationStatus.SUCCEEDED
    assert activities.attempts[CompilationStage.ENHANCE] == 2

    events = await store.list_events(result.job_id)
    retry_events = [
        event
        for event in events
        if event.event_type is CompilationEventType.STAGE_RETRYING
        and event.stage is CompilationStage.ENHANCE
    ]
    assert len(retry_events) == 1
    assert retry_events[0].attempt == 1
    assert retry_events[0].detail == {"next_attempt": 2}


@pytest.mark.asyncio
async def test_sqlalchemy_store_persists_workflow_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SQLAlchemyCompilationJobStore(session_factory)
    activities = StubActivities()
    workflow = CompilationWorkflow(store=store, activities=activities)

    result = await workflow.run(
        CompilationRequest(
            source_url="https://example.com/openapi.json",
            created_by="integration-tester",
            options={"tenant": "team-a"},
        )
    )

    job = await store.get_job(result.job_id)
    assert job is not None
    assert job.status is CompilationStatus.SUCCEEDED
    assert job.protocol == "openapi"
    assert job.service_name == "billing-api"

    events = await store.list_events(result.job_id)
    assert [event.sequence_number for event in events] == list(range(1, len(events) + 1))
    assert events[0].event_type is CompilationEventType.JOB_CREATED
    assert events[-1].event_type is CompilationEventType.JOB_SUCCEEDED
    assert [
        event.stage for event in events if event.event_type is CompilationEventType.STAGE_SUCCEEDED
    ] == [definition.stage for definition in DEFAULT_STAGE_DEFINITIONS]
