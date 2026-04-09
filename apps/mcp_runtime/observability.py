"""Observability wiring for the generic MCP runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from logging import Logger

from prometheus_client import CollectorRegistry

from libs.observability.logging import get_logger
from libs.observability.metrics import (
    create_counter,
    create_gauge,
    create_histogram,
    get_metrics_text,
)


@dataclass
class RuntimeObservability:
    """Metrics and logger handles for the runtime."""

    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    logger_name: str = "apps.mcp_runtime.proxy"
    logger: Logger = field(init=False)

    def __post_init__(self) -> None:
        self.tool_calls_total = create_counter(
            "mcp_runtime_tool_calls_total",
            "Total runtime tool calls.",
            ["operation_id", "outcome"],
            registry=self.registry,
        )
        self.tool_latency_seconds = create_histogram(
            "mcp_runtime_tool_latency_seconds",
            "Runtime tool call latency in seconds.",
            ["operation_id"],
            registry=self.registry,
        )
        self.upstream_errors_total = create_counter(
            "mcp_runtime_upstream_errors_total",
            "Total upstream errors encountered by runtime tools.",
            ["operation_id", "error_type"],
            registry=self.registry,
        )
        self.circuit_breaker_state = create_gauge(
            "mcp_runtime_circuit_breaker_state",
            "Circuit breaker state for runtime tools (0=closed, 1=open).",
            ["operation_id"],
            registry=self.registry,
        )
        self.sla_breaches_total = create_counter(
            "mcp_runtime_sla_breaches_total",
            "Total SLA latency budget breaches.",
            ["operation_id"],
            registry=self.registry,
        )
        self.sla_budget_ms = create_gauge(
            "mcp_runtime_sla_budget_ms",
            "Configured SLA latency budget in milliseconds.",
            ["operation_id"],
            registry=self.registry,
        )
        self.logger = get_logger(self.logger_name)

    def register_operation(self, operation_id: str) -> None:
        self.circuit_breaker_state.labels(operation_id=operation_id).set(0)

    def record_tool_call(self, operation_id: str, outcome: str) -> None:
        self.tool_calls_total.labels(operation_id=operation_id, outcome=outcome).inc()

    def record_latency(self, operation_id: str, seconds: float) -> None:
        self.tool_latency_seconds.labels(operation_id=operation_id).observe(seconds)

    def record_upstream_error(self, operation_id: str, error_type: str) -> None:
        self.upstream_errors_total.labels(operation_id=operation_id, error_type=error_type).inc()

    def set_circuit_breaker_state(self, operation_id: str, is_open: bool) -> None:
        self.circuit_breaker_state.labels(operation_id=operation_id).set(1 if is_open else 0)

    def check_sla(self, operation_id: str, latency_seconds: float, budget_ms: int) -> bool:
        """Check if a tool invocation breached its SLA budget.

        Returns True if SLA was breached.
        """
        latency_ms = latency_seconds * 1000
        if latency_ms > budget_ms:
            self.sla_breaches_total.labels(operation_id=operation_id).inc()
            self.logger.warning(
                "SLA breach: %s took %.1fms (budget: %dms)",
                operation_id,
                latency_ms,
                budget_ms,
            )
            return True
        return False

    def register_sla_budget(self, operation_id: str, budget_ms: int) -> None:
        """Record the configured SLA budget for an operation."""
        self.sla_budget_ms.labels(operation_id=operation_id).set(budget_ms)

    def render_metrics(self) -> bytes:
        return get_metrics_text(self.registry)
