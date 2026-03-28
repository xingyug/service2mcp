"""Unit tests for apps/mcp_runtime/observability.py."""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from apps.mcp_runtime.observability import RuntimeObservability


class TestRuntimeObservabilityInit:
    def test_creates_all_metrics(self) -> None:
        obs = RuntimeObservability()
        assert obs.tool_calls_total is not None
        assert obs.tool_latency_seconds is not None
        assert obs.upstream_errors_total is not None
        assert obs.circuit_breaker_state is not None
        assert obs.logger is not None

    def test_custom_registry(self) -> None:
        registry = CollectorRegistry()
        obs = RuntimeObservability(registry=registry)
        assert obs.registry is registry

    def test_custom_logger_name(self) -> None:
        obs = RuntimeObservability(logger_name="test.runtime")
        assert obs.logger.name == "test.runtime"


class TestRegisterOperation:
    def test_sets_breaker_to_closed(self) -> None:
        obs = RuntimeObservability()
        obs.register_operation("op1")
        val = obs.circuit_breaker_state.labels(operation_id="op1")._value.get()
        assert val == 0.0


class TestRecordToolCall:
    def test_increments(self) -> None:
        obs = RuntimeObservability()
        obs.record_tool_call("op1", "success")
        obs.record_tool_call("op1", "success")
        obs.record_tool_call("op1", "error")
        val = obs.tool_calls_total.labels(operation_id="op1", outcome="success")._value.get()
        assert val == 2.0


class TestRecordLatency:
    def test_observes_histogram(self) -> None:
        obs = RuntimeObservability()
        obs.record_latency("op1", 0.5)
        obs.record_latency("op1", 1.5)
        sample = obs.tool_latency_seconds.labels(operation_id="op1")._sum.get()
        assert sample == 2.0


class TestRecordUpstreamError:
    def test_increments(self) -> None:
        obs = RuntimeObservability()
        obs.record_upstream_error("op1", "timeout")
        val = obs.upstream_errors_total.labels(
            operation_id="op1", error_type="timeout"
        )._value.get()
        assert val == 1.0


class TestSetCircuitBreakerState:
    def test_sets_open(self) -> None:
        obs = RuntimeObservability()
        obs.set_circuit_breaker_state("op1", is_open=True)
        val = obs.circuit_breaker_state.labels(operation_id="op1")._value.get()
        assert val == 1.0

    def test_sets_closed(self) -> None:
        obs = RuntimeObservability()
        obs.set_circuit_breaker_state("op1", is_open=True)
        obs.set_circuit_breaker_state("op1", is_open=False)
        val = obs.circuit_breaker_state.labels(operation_id="op1")._value.get()
        assert val == 0.0


class TestRenderMetrics:
    def test_returns_bytes_with_metric_names(self) -> None:
        obs = RuntimeObservability()
        obs.record_tool_call("op1", "success")
        data = obs.render_metrics()
        assert isinstance(data, bytes)
        assert b"mcp_runtime_tool_calls_total" in data
