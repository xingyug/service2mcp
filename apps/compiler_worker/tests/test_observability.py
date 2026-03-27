"""Unit tests for apps/compiler_worker/observability.py."""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from apps.compiler_worker.models import CompilationStage, CompilationStatus
from apps.compiler_worker.observability import CompilationObservability


class TestCompilationObservabilityInit:
    def test_creates_all_metrics(self) -> None:
        obs = CompilationObservability()
        assert obs.jobs_total is not None
        assert obs.stage_duration_seconds is not None
        assert obs.extractor_runs_total is not None
        assert obs.llm_tokens_total is not None
        assert obs.logger is not None

    def test_custom_registry(self) -> None:
        registry = CollectorRegistry()
        obs = CompilationObservability(registry=registry)
        assert obs.registry is registry

    def test_custom_logger_name(self) -> None:
        obs = CompilationObservability(logger_name="test.logger")
        assert obs.logger.name == "test.logger"


class TestRecordJob:
    def test_increments_counter(self) -> None:
        obs = CompilationObservability()
        obs.record_job(CompilationStatus.SUCCEEDED)
        obs.record_job(CompilationStatus.SUCCEEDED)
        obs.record_job(CompilationStatus.FAILED)
        val = obs.jobs_total.labels(status="succeeded")._value.get()
        assert val == 2.0


class TestRecordStage:
    def test_observes_histogram(self) -> None:
        obs = CompilationObservability()
        obs.record_stage(CompilationStage.EXTRACT, outcome="success", duration_seconds=1.5)
        obs.record_stage(CompilationStage.EXTRACT, outcome="failure", duration_seconds=0.3)
        # histogram sum for extract/success should be 1.5
        sample = obs.stage_duration_seconds.labels(
            stage="extract", outcome="success"
        )._sum.get()
        assert sample == 1.5


class TestRecordExtractorRun:
    def test_increments(self) -> None:
        obs = CompilationObservability()
        obs.record_extractor_run(protocol="openapi", outcome="success")
        obs.record_extractor_run(protocol="openapi", outcome="success")
        val = obs.extractor_runs_total.labels(protocol="openapi", outcome="success")._value.get()
        assert val == 2.0


class TestRecordLlmTokenUsage:
    def test_increments_input_and_output(self) -> None:
        obs = CompilationObservability()
        obs.record_llm_token_usage(model="deepseek", input_tokens=100, output_tokens=50)
        input_val = obs.llm_tokens_total.labels(model="deepseek", direction="input")._value.get()
        output_val = obs.llm_tokens_total.labels(model="deepseek", direction="output")._value.get()
        assert input_val == 100.0
        assert output_val == 50.0


class TestRenderMetrics:
    def test_returns_bytes(self) -> None:
        obs = CompilationObservability()
        obs.record_job(CompilationStatus.SUCCEEDED)
        data = obs.render_metrics()
        assert isinstance(data, bytes)
        assert b"compiler_workflow_jobs_total" in data
