"""Tests for shared observability utilities."""

from __future__ import annotations

import json
import logging

from prometheus_client import CollectorRegistry

from libs.observability.logging import StructuredFormatter, setup_logging
from libs.observability.metrics import (
    create_counter,
    create_gauge,
    create_histogram,
    get_metrics_text,
)
from libs.observability.tracing import _NoOpSpan, _NoOpTracer, get_tracer, trace_span

# ── Metrics Tests ──────────────────────────────────────────────────────────


class TestMetrics:
    def test_create_counter(self):
        registry = CollectorRegistry()
        counter = create_counter(
            "test_requests_total", "Total requests", ["method"], registry=registry
        )
        counter.labels(method="GET").inc()
        counter.labels(method="GET").inc()
        output = get_metrics_text(registry).decode()
        assert "test_requests_total" in output

    def test_create_histogram(self):
        registry = CollectorRegistry()
        hist = create_histogram(
            "test_duration_seconds", "Duration", ["endpoint"], registry=registry
        )
        hist.labels(endpoint="/api").observe(0.5)
        output = get_metrics_text(registry).decode()
        assert "test_duration_seconds" in output

    def test_create_gauge(self):
        registry = CollectorRegistry()
        gauge = create_gauge(
            "test_active_connections", "Active connections", ["service"], registry=registry
        )
        gauge.labels(service="api").set(42)
        output = get_metrics_text(registry).decode()
        assert "test_active_connections" in output
        assert "42.0" in output

    def test_counter_without_labels(self):
        registry = CollectorRegistry()
        counter = create_counter("test_simple_total", "Simple counter", registry=registry)
        counter.inc(5)
        output = get_metrics_text(registry).decode()
        assert "test_simple_total" in output

    def test_same_metric_name_in_different_registries_is_isolated(self):
        first = CollectorRegistry()
        second = CollectorRegistry()

        metric_one = create_counter("shared_total", "Shared metric", registry=first)
        metric_two = create_counter("shared_total", "Shared metric", registry=second)

        assert metric_one is not metric_two


# ── Tracing Tests ──────────────────────────────────────────────────────────


class TestTracing:
    def test_get_tracer_returns_noop_when_not_configured(self):
        tracer = get_tracer("test-component")
        assert isinstance(tracer, _NoOpTracer)

    def test_trace_span_noop_works(self):
        with trace_span("test-operation") as span:
            assert isinstance(span, _NoOpSpan)
            # Should not raise
            span.set_attribute("key", "value")
            span.record_exception(ValueError("test"))

    def test_noop_tracer_context_manager(self):
        tracer = _NoOpTracer()
        with tracer.start_as_current_span("test") as span:
            assert isinstance(span, _NoOpSpan)


# ── Logging Tests ──────────────────────────────────────────────────────────


class TestStructuredLogging:
    def test_formatter_produces_valid_json(self):
        formatter = StructuredFormatter(component="test-api")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["level"] == "INFO"
        assert parsed["component"] == "test-api"
        assert parsed["message"] == "Hello world"
        assert "timestamp" in parsed
        assert parsed["logger"] == "test.logger"

    def test_formatter_includes_trace_id(self):
        formatter = StructuredFormatter(component="runtime")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="traced", args=(), exc_info=None,
        )
        record.trace_id = "abc123"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["trace_id"] == "abc123"

    def test_formatter_includes_exception(self):
        formatter = StructuredFormatter(component="worker")
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="failed", args=(), exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["exception"]["type"] == "ValueError"
        assert "test error" in parsed["exception"]["message"]

    def test_setup_logging(self):
        setup_logging("test-component", level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1
        handler = root.handlers[0]
        assert isinstance(handler.formatter, StructuredFormatter)
        # Clean up
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_formatter_without_trace_id(self):
        formatter = StructuredFormatter(component="api")
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg="no trace", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "trace_id" not in parsed
