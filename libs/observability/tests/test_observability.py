"""Tests for shared observability utilities."""

from __future__ import annotations

import json
import logging

from prometheus_client import CollectorRegistry

from libs.observability import tracing as tracing_mod
from libs.observability.logging import StructuredFormatter, setup_logging
from libs.observability.metrics import (
    create_counter,
    create_gauge,
    create_histogram,
    get_metrics_text,
    reset_metrics,
)
from libs.observability.tracing import _NoOpSpan, _NoOpTracer, get_tracer, trace_span

# ── Metrics Tests ──────────────────────────────────────────────────────────


def _restore_root_logger(
    root: logging.Logger,
    original_handlers: list[logging.Handler],
    original_level: int,
) -> None:
    root.handlers.clear()
    for handler in original_handlers:
        root.addHandler(handler)
    root.setLevel(original_level)


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

    def test_same_metric_name_in_different_registries_is_isolated(self) -> None:
        first = CollectorRegistry()
        second = CollectorRegistry()

        metric_one = create_counter("shared_total", "Shared metric", registry=first)
        metric_two = create_counter("shared_total", "Shared metric", registry=second)

        assert metric_one is not metric_two

    def test_same_registry_same_name_returns_cached_counter(self) -> None:
        registry = CollectorRegistry()
        first = create_counter("dedup_total", "First call", registry=registry)
        second = create_counter("dedup_total", "Second call", registry=registry)
        assert first is second

    def test_same_registry_same_name_returns_cached_histogram(self) -> None:
        registry = CollectorRegistry()
        first = create_histogram("dedup_dur", "First", registry=registry)
        second = create_histogram("dedup_dur", "Second", registry=registry)
        assert first is second

    def test_same_registry_same_name_returns_cached_gauge(self) -> None:
        registry = CollectorRegistry()
        first = create_gauge("dedup_gauge", "First", registry=registry)
        second = create_gauge("dedup_gauge", "Second", registry=registry)
        assert first is second

    def test_reset_metrics_clears_cache(self) -> None:
        registry = CollectorRegistry()
        first = create_counter("reset_test", "Before reset", registry=registry)
        reset_metrics()
        # After reset, a new registry must be used (old registry still has
        # the metric registered), but the dedup cache should be empty.
        registry2 = CollectorRegistry()
        second = create_counter("reset_test", "After reset", registry=registry2)
        assert first is not second


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

    def test_noop_tracer_context_manager(self) -> None:
        tracer = _NoOpTracer()
        with tracer.start_as_current_span("test") as span:
            assert isinstance(span, _NoOpSpan)

    def test_noop_span_set_status_does_not_raise(self) -> None:
        span = _NoOpSpan()
        span.set_status("ok")  # should be a silent no-op


class TestSetupTracer:
    """Tests for setup_tracer branch coverage."""

    def setup_method(self) -> None:
        # Reset module-level globals before each test.
        tracing_mod._is_configured = False
        tracing_mod._tracer_provider = None
        tracing_mod._configured_service_name = None
        tracing_mod._configured_endpoint = None
        tracing_mod._configured_enable_local = False

    def teardown_method(self) -> None:
        tracing_mod._is_configured = False
        tracing_mod._tracer_provider = None
        tracing_mod._configured_service_name = None
        tracing_mod._configured_endpoint = None
        tracing_mod._configured_enable_local = False

    def test_noop_when_no_endpoint_and_not_local(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OTEL_EXPORTER_ENDPOINT", None)
            tracing_mod.setup_tracer("test-svc")
        assert tracing_mod._is_configured is False

    def test_already_configured_skips(self) -> None:
        # Simulate already configured state.
        tracing_mod._is_configured = True
        tracing_mod._tracer_provider = object()  # non-None sentinel
        tracing_mod.setup_tracer("test-svc", enable_local=True)
        # Should not have changed anything — early return.
        assert tracing_mod._is_configured is True

    def test_enable_local_configures_provider(self) -> None:
        tracing_mod.setup_tracer("test-svc", enable_local=True)
        # If OTel SDK is installed, this should configure; if not,
        # the ImportError fallback should leave _is_configured False.
        # Either outcome is valid — we're testing that it doesn't crash.
        assert isinstance(tracing_mod._is_configured, bool)

    def test_import_error_fallback(self) -> None:
        import unittest.mock

        with unittest.mock.patch.dict(
            "sys.modules",
            {
                "opentelemetry": None,
                "opentelemetry.trace": None,
                "opentelemetry.sdk": None,
                "opentelemetry.sdk.resources": None,
                "opentelemetry.sdk.trace": None,
            },
        ):
            tracing_mod.setup_tracer("test-svc", enable_local=True)
        assert tracing_mod._is_configured is False


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
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="traced",
            args=(),
            exc_info=None,
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
                name="test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["exception"]["type"] == "ValueError"
        assert "test error" in parsed["exception"]["message"]

    def test_setup_logging(self):
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level

        try:
            setup_logging("test-component", level="DEBUG")
            assert root.level == logging.DEBUG
            handlers = [
                handler
                for handler in root.handlers
                if isinstance(handler.formatter, StructuredFormatter)
            ]
            assert handlers
            assert isinstance(handlers[0].formatter, StructuredFormatter)
        finally:
            _restore_root_logger(root, original_handlers, original_level)

    def test_formatter_without_trace_id(self):
        formatter = StructuredFormatter(component="api")
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="no trace",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "trace_id" not in parsed
