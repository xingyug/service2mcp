"""Observability wiring for the compilation worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from logging import Logger

from prometheus_client import CollectorRegistry

from apps.compiler_worker.models import CompilationStage, CompilationStatus
from libs.observability.logging import get_logger
from libs.observability.metrics import create_counter, create_histogram, get_metrics_text


@dataclass
class CompilationObservability:
    """Metrics and logger handles for compilation workflows."""

    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    logger_name: str = "apps.compiler_worker.workflow"
    logger: Logger = field(init=False)

    def __post_init__(self) -> None:
        self.jobs_total = create_counter(
            "compiler_workflow_jobs_total",
            "Total compilation workflow jobs by terminal status.",
            ["status"],
            registry=self.registry,
        )
        self.stage_duration_seconds = create_histogram(
            "compiler_workflow_stage_duration_seconds",
            "Compilation stage duration in seconds.",
            ["stage", "outcome"],
            registry=self.registry,
        )
        self.extractor_runs_total = create_counter(
            "compiler_extractor_runs_total",
            "Extractor stage executions by detected protocol and outcome.",
            ["protocol", "outcome"],
            registry=self.registry,
        )
        self.llm_tokens_total = create_counter(
            "compiler_llm_tokens_total",
            "LLM tokens consumed during compilation enhancement stages.",
            ["model", "direction"],
            registry=self.registry,
        )
        self.logger = get_logger(self.logger_name)

    def record_job(self, status: CompilationStatus) -> None:
        self.jobs_total.labels(status=status.value).inc()

    def record_stage(
        self,
        stage: CompilationStage,
        *,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        self.stage_duration_seconds.labels(stage=stage.value, outcome=outcome).observe(
            duration_seconds
        )

    def record_extractor_run(self, *, protocol: str, outcome: str) -> None:
        self.extractor_runs_total.labels(protocol=protocol, outcome=outcome).inc()

    def record_llm_token_usage(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self.llm_tokens_total.labels(model=model, direction="input").inc(input_tokens)
        self.llm_tokens_total.labels(model=model, direction="output").inc(output_tokens)

    def render_metrics(self) -> bytes:
        return get_metrics_text(self.registry)


__all__ = ["CompilationObservability"]
