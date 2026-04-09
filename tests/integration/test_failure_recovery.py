"""Integration tests for failure recovery across compilation and runtime paths.

Validates that the system recovers gracefully from upstream timeouts,
invalid inputs, worker crashes, database/Redis failures, circuit breaker
state transitions, partial extraction failures, LLM enhancer errors,
and concurrent compilation race conditions.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.access_control.authn.service import JWTSettings, build_service_jwt
from apps.compiler_api.dispatcher import (
    CallbackCompilationDispatcher,
    InMemoryCompilationDispatcher,
)
from apps.compiler_api.main import create_app as create_compiler_api_app
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
from apps.compiler_worker.workflows.compile_workflow import (
    CompilationWorkflow,
    CompilationWorkflowError,
)
from apps.mcp_runtime.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from apps.mcp_runtime.observability import RuntimeObservability
from apps.mcp_runtime.proxy import RuntimeProxy
from libs.enhancer.enhancer import (
    EnhancerConfig,
    IREnhancer,
    LLMResponse,
)
from libs.extractors.base import SourceConfig, TypeDetector
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

_TEST_JWT_SECRET = "test-failure-recovery-jwt-secret"
_TEST_JWT_SETTINGS = JWTSettings(secret=_TEST_JWT_SECRET)


def _mock_session_factory() -> MagicMock:
    """Build a mock session factory that supports ``async with factory() as s:``."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = ctx
    return factory


def _auth_headers(subject: str = "test-caller") -> dict[str, str]:
    token = build_service_jwt(subject=subject, jwt_settings=_TEST_JWT_SETTINGS)
    return {"Authorization": f"Bearer {token}"}


def _fake_job_response() -> Any:
    """Build a fake CompilationJobResponse for mocked repository."""
    from apps.compiler_api.models import CompilationJobResponse

    now = datetime.now(UTC)
    return CompilationJobResponse(
        id=uuid4(),
        source_url="https://example.com/spec.json",
        status="pending",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Helpers: in-memory job store (mirrors test_compile_workflow.py pattern)
# ---------------------------------------------------------------------------


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

    async def mark_job_running(self, job_id: UUID, stage: CompilationStage, **kwargs: Any) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = CompilationJobRecord(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=kwargs.get("protocol") or job.protocol,
            status=CompilationStatus.RUNNING,
            current_stage=stage,
            error_detail=None,
            options=job.options,
            created_by=job.created_by,
            service_name=kwargs.get("service_name") or job.service_name,
            created_at=job.created_at,
            updated_at=datetime.now(UTC),
            service_id=kwargs.get("service_id") or job.service_id,
        )

    async def mark_job_succeeded(
        self, job_id: UUID, stage: CompilationStage, **kwargs: Any
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = CompilationJobRecord(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=kwargs.get("protocol") or job.protocol,
            status=CompilationStatus.SUCCEEDED,
            current_stage=stage,
            error_detail=None,
            options=job.options,
            created_by=job.created_by,
            service_name=kwargs.get("service_name") or job.service_name,
            created_at=job.created_at,
            updated_at=datetime.now(UTC),
            service_id=kwargs.get("service_id") or job.service_id,
        )

    async def mark_job_failed(
        self,
        job_id: UUID,
        stage: CompilationStage,
        error_detail: str,
        *,
        rolled_back: bool,
        **kwargs: Any,
    ) -> None:
        job = self.jobs[job_id]
        self.jobs[job_id] = CompilationJobRecord(
            id=job.id,
            source_url=job.source_url,
            source_hash=job.source_hash,
            protocol=kwargs.get("protocol") or job.protocol,
            status=CompilationStatus.ROLLED_BACK if rolled_back else CompilationStatus.FAILED,
            current_stage=stage,
            error_detail=error_detail,
            options=job.options,
            created_by=job.created_by,
            service_name=kwargs.get("service_name") or job.service_name,
            created_at=job.created_at,
            updated_at=datetime.now(UTC),
            service_id=kwargs.get("service_id") or job.service_id,
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
        from dataclasses import replace

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
        return job.status if job else None


# ---------------------------------------------------------------------------
# Helpers: stub activities
# ---------------------------------------------------------------------------


class ConfigurableActivities:
    """Activities stub that can fail specific stages and recover on retry."""

    def __init__(
        self,
        *,
        fail_stages: set[CompilationStage] | None = None,
        transient_failures: dict[CompilationStage, int] | None = None,
    ) -> None:
        self.fail_stages = fail_stages or set()
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

        if stage in self.fail_stages:
            raise RuntimeError(f"Permanent failure in {stage.value}")

        allowed_failures = self.transient_failures.get(stage, 0)
        if self.attempts[stage] <= allowed_failures:
            raise RuntimeError(
                f"Transient failure in {stage.value} (attempt {self.attempts[stage]})"
            )

        return StageExecutionResult(
            context_updates={f"{stage.value}_done": True},
            event_detail={"attempt": self.attempts[stage]},
            rollback_payload={"stage": stage.value},
            protocol="openapi" if stage is CompilationStage.DETECT else None,
            service_name="test-svc" if stage is CompilationStage.EXTRACT else None,
        )

    async def rollback_stage(
        self,
        stage: CompilationStage,
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        self.rollback_calls.append(stage)


# ---------------------------------------------------------------------------
# Helpers: IR / proxy building
# ---------------------------------------------------------------------------


def _build_test_ir(
    service_name: str = "test-api",
    ops: list[Operation] | None = None,
) -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol="openapi",
        service_name=service_name,
        service_description="Test service for failure recovery",
        base_url="https://api.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=ops
        or [
            Operation(
                id="getItem",
                name="Get Item",
                description="Retrieve an item by ID.",
                method="GET",
                path="/items/{item_id}",
                params=[Param(name="item_id", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
        ],
    )


def _build_proxy(ir: ServiceIR | None = None, **kwargs: Any) -> RuntimeProxy:
    return RuntimeProxy(
        ir or _build_test_ir(),
        observability=RuntimeObservability(),
        **kwargs,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Upstream timeout during compilation (extractor timeout)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upstream_timeout_records_failure_and_system_remains_usable() -> None:
    """Extractor timeout should fail the job but leave the workflow reusable."""
    store = InMemoryCompilationJobStore()
    activities = ConfigurableActivities(
        fail_stages={CompilationStage.EXTRACT},
    )
    workflow = CompilationWorkflow(store=store, activities=activities)

    with pytest.raises(CompilationWorkflowError) as exc_info:
        await workflow.run(
            CompilationRequest(source_url="https://example.com/spec.json", created_by="t")
        )

    assert exc_info.value.failed_stage is CompilationStage.EXTRACT

    # System should still accept new jobs (recovery)
    activities_ok = ConfigurableActivities()
    workflow_ok = CompilationWorkflow(store=store, activities=activities_ok)
    result = await workflow_ok.run(
        CompilationRequest(source_url="https://example.com/another.json", created_by="t")
    )
    assert result.status is CompilationStatus.SUCCEEDED


# ═══════════════════════════════════════════════════════════════════════════
# 2. Invalid / corrupt spec submission
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_malformed_json_submission_returns_422() -> None:
    """Posting malformed JSON to the compilation endpoint should return 422."""
    app = create_compiler_api_app(
        session_factory=_mock_session_factory(),
        compilation_dispatcher=InMemoryCompilationDispatcher(),
        jwt_settings=_TEST_JWT_SETTINGS,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/compilations",
            content=b"{this is not valid json",
            headers={**_auth_headers(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_body_submission_returns_422() -> None:
    """An empty body should be rejected by the API validation."""
    app = create_compiler_api_app(
        session_factory=_mock_session_factory(),
        compilation_dispatcher=InMemoryCompilationDispatcher(),
        jwt_settings=_TEST_JWT_SETTINGS,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/compilations",
            json={},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_source_submission_returns_422() -> None:
    """source_url and source_content both missing should be rejected."""
    app = create_compiler_api_app(
        session_factory=_mock_session_factory(),
        compilation_dispatcher=InMemoryCompilationDispatcher(),
        jwt_settings=_TEST_JWT_SETTINGS,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/compilations",
            json={"service_name": "oops"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# 3. Worker crash mid-compilation (simulated via Celery task failure)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_crash_mid_compilation_marks_job_failed() -> None:
    """A crash in a mid-pipeline stage should fail the job with error detail."""
    store = InMemoryCompilationJobStore()
    activities = ConfigurableActivities(
        fail_stages={CompilationStage.GENERATE},
    )
    workflow = CompilationWorkflow(store=store, activities=activities)

    with pytest.raises(CompilationWorkflowError) as exc_info:
        await workflow.run(
            CompilationRequest(source_url="https://example.com/spec.json", created_by="t")
        )

    err = exc_info.value
    assert err.failed_stage is CompilationStage.GENERATE
    job = store.jobs[err.job_id]
    assert job.status in {CompilationStatus.FAILED, CompilationStatus.ROLLED_BACK}
    assert job.error_detail is not None
    assert "GENERATE" in (job.error_detail or "").upper() or "generate" in (job.error_detail or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_crash_allows_subsequent_jobs() -> None:
    """After a worker crash, new jobs should still be processable."""
    store = InMemoryCompilationJobStore()
    crash_activities = ConfigurableActivities(fail_stages={CompilationStage.DEPLOY})
    crash_workflow = CompilationWorkflow(store=store, activities=crash_activities)

    with pytest.raises(CompilationWorkflowError):
        await crash_workflow.run(
            CompilationRequest(source_url="https://example.com/crash.json", created_by="t")
        )

    ok_activities = ConfigurableActivities()
    ok_workflow = CompilationWorkflow(store=store, activities=ok_activities)
    result = await ok_workflow.run(
        CompilationRequest(source_url="https://example.com/ok.json", created_by="t")
    )
    assert result.status is CompilationStatus.SUCCEEDED


# ═══════════════════════════════════════════════════════════════════════════
# 4. Database connection loss during compilation (mocked)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_db_connection_loss_during_dispatch_returns_503() -> None:
    """If the dispatcher fails due to DB issues, the API should return 503."""

    async def failing_dispatch(request: CompilationRequest) -> None:
        raise ConnectionError("Database connection lost")

    app = create_compiler_api_app(
        session_factory=_mock_session_factory(),
        compilation_dispatcher=CallbackCompilationDispatcher(callback=failing_dispatch),
        jwt_settings=_TEST_JWT_SETTINGS,
    )

    fake_job = _fake_job_response()
    with (
        patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo,
        patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit,
    ):
        repo_instance = mock_repo.return_value
        repo_instance.create_job = AsyncMock(return_value=fake_job)
        repo_instance.delete_job = AsyncMock()
        audit_instance = mock_audit.return_value
        audit_instance.append_entry = AsyncMock()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/compilations",
                json={"source_url": "https://example.com/spec.json"},
                headers=_auth_headers(),
            )
            assert resp.status_code == 503
            assert "dispatch failed" in resp.json()["detail"].lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_healthz_after_simulated_db_loss() -> None:
    """Health endpoint should stay responsive even after DB-related dispatch failures."""
    call_count = 0

    async def intermittent_dispatch(request: CompilationRequest) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("DB gone")

    app = create_compiler_api_app(
        session_factory=_mock_session_factory(),
        compilation_dispatcher=CallbackCompilationDispatcher(callback=intermittent_dispatch),
        jwt_settings=_TEST_JWT_SETTINGS,
    )

    fake_job = _fake_job_response()
    with (
        patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo,
        patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit,
    ):
        repo_instance = mock_repo.return_value
        repo_instance.create_job = AsyncMock(return_value=fake_job)
        repo_instance.delete_job = AsyncMock()
        audit_instance = mock_audit.return_value
        audit_instance.append_entry = AsyncMock()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # First call fails
            resp1 = await client.post(
                "/api/v1/compilations",
                json={"source_url": "https://example.com/spec.json"},
                headers=_auth_headers(),
            )
            assert resp1.status_code == 503

            # Healthz still works
            resp2 = await client.get("/healthz")
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Redis connection loss (mocked via dispatcher enqueue failure)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_loss_during_dispatch_returns_503() -> None:
    """Simulated Redis failure (Celery broker down) should yield 503."""

    async def redis_down(request: CompilationRequest) -> None:
        raise ConnectionError("Redis connection refused")

    app = create_compiler_api_app(
        session_factory=_mock_session_factory(),
        compilation_dispatcher=CallbackCompilationDispatcher(callback=redis_down),
        jwt_settings=_TEST_JWT_SETTINGS,
    )

    fake_job = _fake_job_response()
    with (
        patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo,
        patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit,
    ):
        repo_instance = mock_repo.return_value
        repo_instance.create_job = AsyncMock(return_value=fake_job)
        repo_instance.delete_job = AsyncMock()
        audit_instance = mock_audit.return_value
        audit_instance.append_entry = AsyncMock()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/compilations",
                json={"source_url": "https://example.com/spec.json"},
                headers=_auth_headers(),
            )
            assert resp.status_code == 503
            assert "dispatch failed" in resp.json()["detail"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# 6. Circuit breaker recovery (trip → half-open → close cycle)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
def test_circuit_breaker_trip_and_recovery() -> None:
    """Circuit breaker should open after threshold failures and close on success."""
    cb = CircuitBreaker(operation_id="op1", failure_threshold=3)
    assert not cb.is_open

    # Trip it
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open

    with pytest.raises(CircuitBreakerOpenError):
        cb.before_request()

    # Half-open → close: a single success resets the breaker
    cb.is_open = False  # simulate half-open probe
    cb.record_success()
    assert not cb.is_open
    assert cb.consecutive_failures == 0

    # Breaker should accept requests again
    cb.before_request()  # no exception


@pytest.mark.integration
@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_proxy_failures() -> None:
    """Repeated upstream failures should open the per-operation circuit breaker."""
    ir = _build_test_ir()
    proxy = _build_proxy(ir, failure_threshold=2)
    op = ir.operations[0]

    # Simulate two timeouts
    for _ in range(2):
        with patch(
            "apps.mcp_runtime.proxy.perform_request",
            side_effect=httpx.TimeoutException("upstream timeout"),
        ):
            with pytest.raises(ToolError, match="timeout"):
                await proxy.invoke(op, {"item_id": "1"})

    breaker = proxy.breakers[op.id]
    assert breaker.is_open

    # Now confirm the breaker rejects further calls without making a request
    with pytest.raises(ToolError, match="Circuit breaker is open"):
        await proxy.invoke(op, {"item_id": "2"})

    # Recovery: reset breaker and verify proxy works
    breaker.is_open = False
    breaker.consecutive_failures = 0
    mock_resp = httpx.Response(200, json={"id": "2", "name": "item-2"})
    with patch(
        "apps.mcp_runtime.proxy.perform_request",
        return_value=mock_resp,
    ):
        result = await proxy.invoke(op, {"item_id": "2"})
    assert result["status"] == "ok"
    assert not breaker.is_open

    await proxy.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# 7. Partial extraction failure (one protocol arm fails, others succeed)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
def test_type_detector_partial_failure_selects_best_working_extractor() -> None:
    """When one extractor raises during detect, others should still be probed."""

    class FailingExtractor:
        protocol_name = "broken"

        def detect(self, source: SourceConfig) -> float:
            raise RuntimeError("Extractor init error")

        def extract(self, source: SourceConfig) -> ServiceIR:
            raise NotImplementedError

    class WorkingExtractor:
        protocol_name = "working"

        def detect(self, source: SourceConfig) -> float:
            return 0.9

        def extract(self, source: SourceConfig) -> ServiceIR:
            return _build_test_ir(service_name="working-svc")

    detector = TypeDetector([FailingExtractor(), WorkingExtractor()])
    result = detector.detect(SourceConfig(file_content='{"openapi":"3.0.0"}'))
    assert result.protocol_name == "working"
    assert result.confidence == 0.9


@pytest.mark.integration
def test_extraction_failure_does_not_corrupt_other_extractors() -> None:
    """After one extractor fails, a second detection attempt should succeed."""

    class SometimesFailingExtractor:
        protocol_name = "flaky"
        call_count = 0

        def detect(self, source: SourceConfig) -> float:
            self.call_count += 1
            if self.call_count == 1:
                raise RuntimeError("First-call failure")
            return 0.8

        def extract(self, source: SourceConfig) -> ServiceIR:
            return _build_test_ir(service_name="flaky-svc")

    ext = SometimesFailingExtractor()
    detector = TypeDetector([ext])

    # First detection: extractor raises, so no results
    with pytest.raises(ValueError, match="No extractor"):
        detector.detect(SourceConfig(file_content="some content"))

    # Second detection: same extractor succeeds
    result = detector.detect(SourceConfig(file_content="some content"))
    assert result.protocol_name == "flaky"


# ═══════════════════════════════════════════════════════════════════════════
# 8. LLM enhancer timeout / error
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
def test_enhancer_llm_timeout_returns_original_ir() -> None:
    """If the LLM client times out, the enhancer should return the original IR."""

    class TimeoutLLMClient:
        def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
            raise httpx.TimeoutException("LLM request timed out")

    ir = _build_test_ir()
    # Ensure at least one op needs enhancement
    ir.operations[0].description = "x"
    enhancer = IREnhancer(
        client=TimeoutLLMClient(),
        config=EnhancerConfig(skip_if_description_exists=True),
    )
    result = enhancer.enhance(ir)

    # Should gracefully degrade: return original IR with zero enhancements
    assert result.operations_enhanced == 0
    assert result.enhanced_ir.service_name == ir.service_name


@pytest.mark.integration
def test_enhancer_llm_invalid_json_returns_original_ir() -> None:
    """If the LLM returns unparseable JSON, the enhancer gracefully degrades."""

    class BadJsonLLMClient:
        def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
            return LLMResponse(content="not json at all {{{", input_tokens=10, output_tokens=5)

    ir = _build_test_ir()
    ir.operations[0].description = "x"
    enhancer = IREnhancer(
        client=BadJsonLLMClient(),
        config=EnhancerConfig(skip_if_description_exists=True),
    )
    result = enhancer.enhance(ir)
    assert result.operations_enhanced == 0
    assert result.enhanced_ir is ir


@pytest.mark.integration
def test_enhancer_llm_error_does_not_block_workflow() -> None:
    """An LLM error in enhance should not prevent the workflow from reporting failure."""

    class ErrorLLMClient:
        def complete(self, prompt: str, max_tokens: int = 4096) -> LLMResponse:
            raise RuntimeError("LLM provider is down")

    ir = _build_test_ir()
    ir.operations[0].description = "x"
    enhancer = IREnhancer(
        client=ErrorLLMClient(),
        config=EnhancerConfig(skip_if_description_exists=True),
    )
    result = enhancer.enhance(ir)
    # Graceful fallback: original IR preserved
    assert result.enhanced_ir.service_name == ir.service_name
    assert result.operations_enhanced == 0


# ═══════════════════════════════════════════════════════════════════════════
# 9. Concurrent compilation race conditions
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_compilations_do_not_interfere() -> None:
    """Multiple concurrent workflow runs should each produce independent results."""
    store = InMemoryCompilationJobStore()

    async def run_one(idx: int) -> CompilationStatus:
        activities = ConfigurableActivities()
        workflow = CompilationWorkflow(store=store, activities=activities)
        result = await workflow.run(
            CompilationRequest(
                source_url=f"https://example.com/spec-{idx}.json",
                created_by=f"tester-{idx}",
            )
        )
        return result.status

    results = await asyncio.gather(*[run_one(i) for i in range(5)])
    assert all(s is CompilationStatus.SUCCEEDED for s in results)
    assert len(store.jobs) == 5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_compilations_one_failure_does_not_affect_others() -> None:
    """One failing concurrent compilation should not corrupt other parallel runs."""
    store = InMemoryCompilationJobStore()

    async def run_one(idx: int, should_fail: bool) -> CompilationStatus:
        if should_fail:
            activities = ConfigurableActivities(fail_stages={CompilationStage.VALIDATE_IR})
        else:
            activities = ConfigurableActivities()
        workflow = CompilationWorkflow(store=store, activities=activities)
        try:
            result = await workflow.run(
                CompilationRequest(
                    source_url=f"https://example.com/spec-{idx}.json",
                    created_by=f"tester-{idx}",
                )
            )
            return result.status
        except CompilationWorkflowError:
            return CompilationStatus.FAILED

    tasks = [run_one(i, should_fail=(i == 2)) for i in range(5)]
    results = await asyncio.gather(*tasks)

    # Exactly 1 failure, 4 successes
    assert results.count(CompilationStatus.SUCCEEDED) == 4
    assert results.count(CompilationStatus.FAILED) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Additional coverage: workflow retry recovery
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transient_extract_failure_recovers_via_retry() -> None:
    """A stage that fails transiently should succeed after retries."""
    store = InMemoryCompilationJobStore()
    activities = ConfigurableActivities(
        transient_failures={CompilationStage.EXTRACT: 2},
    )
    workflow = CompilationWorkflow(store=store, activities=activities)
    result = await workflow.run(
        CompilationRequest(source_url="https://example.com/spec.json", created_by="t")
    )
    assert result.status is CompilationStatus.SUCCEEDED
    # Extract should have been attempted 3 times (2 transient + 1 success)
    assert activities.attempts[CompilationStage.EXTRACT] == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_proxy_timeout_then_success_resets_breaker() -> None:
    """After a timeout, a subsequent success should reset the circuit breaker."""
    ir = _build_test_ir()
    proxy = _build_proxy(ir, failure_threshold=5)
    op = ir.operations[0]

    # One timeout
    with patch(
        "apps.mcp_runtime.proxy.perform_request",
        side_effect=httpx.TimeoutException("timeout"),
    ):
        with pytest.raises(ToolError, match="timeout"):
            await proxy.invoke(op, {"item_id": "1"})

    breaker = proxy.breakers[op.id]
    assert breaker.consecutive_failures == 1
    assert not breaker.is_open

    # Successful call resets
    mock_resp = httpx.Response(200, json={"id": "1"})
    with patch(
        "apps.mcp_runtime.proxy.perform_request",
        return_value=mock_resp,
    ):
        result = await proxy.invoke(op, {"item_id": "1"})

    assert result["status"] == "ok"
    assert breaker.consecutive_failures == 0

    await proxy.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_proxy_http_error_records_failure_on_breaker() -> None:
    """HTTP errors should increment the circuit breaker failure count."""
    ir = _build_test_ir()
    proxy = _build_proxy(ir, failure_threshold=5)
    op = ir.operations[0]

    with patch(
        "apps.mcp_runtime.proxy.perform_request",
        side_effect=httpx.ConnectError("connection refused"),
    ):
        with pytest.raises(ToolError, match="request failed"):
            await proxy.invoke(op, {"item_id": "1"})

    breaker = proxy.breakers[op.id]
    assert breaker.consecutive_failures == 1

    await proxy.aclose()
