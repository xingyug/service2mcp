"""Unit tests for libs/observability/metrics.py — metric creation & deduplication."""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from libs.observability.metrics import (
    DEFAULT_BUCKETS,
    create_counter,
    create_gauge,
    create_histogram,
    get_metrics_text,
    reset_metrics,
)


class TestCreateCounter:
    def test_creates_counter(self) -> None:
        registry = CollectorRegistry()
        counter = create_counter("test_total", "Test counter", registry=registry)
        counter.inc()
        text = get_metrics_text(registry)
        assert b"test_total" in text

    def test_deduplication(self) -> None:
        registry = CollectorRegistry()
        c1 = create_counter("dedup_total", "Counter", registry=registry)
        c2 = create_counter("dedup_total", "Counter", registry=registry)
        assert c1 is c2

    def test_with_labels(self) -> None:
        registry = CollectorRegistry()
        counter = create_counter(
            "labeled_total", "Labeled counter", ["method", "status"], registry=registry
        )
        counter.labels(method="GET", status="200").inc()
        text = get_metrics_text(registry)
        assert b"labeled_total" in text


class TestCreateHistogram:
    def test_creates_histogram(self) -> None:
        registry = CollectorRegistry()
        hist = create_histogram("test_duration", "Test histogram", registry=registry)
        hist.observe(0.5)
        text = get_metrics_text(registry)
        assert b"test_duration" in text

    def test_deduplication(self) -> None:
        registry = CollectorRegistry()
        h1 = create_histogram("dedup_hist", "Hist", registry=registry)
        h2 = create_histogram("dedup_hist", "Hist", registry=registry)
        assert h1 is h2

    def test_custom_buckets(self) -> None:
        registry = CollectorRegistry()
        custom = (0.1, 0.5, 1.0)
        hist = create_histogram("custom_bucket_hist", "Custom", buckets=custom, registry=registry)
        hist.observe(0.3)
        text = get_metrics_text(registry)
        assert b"custom_bucket_hist" in text


class TestCreateGauge:
    def test_creates_gauge(self) -> None:
        registry = CollectorRegistry()
        gauge = create_gauge("test_gauge", "Test gauge", registry=registry)
        gauge.set(42)
        text = get_metrics_text(registry)
        assert b"test_gauge" in text

    def test_deduplication(self) -> None:
        registry = CollectorRegistry()
        g1 = create_gauge("dedup_gauge", "Gauge", registry=registry)
        g2 = create_gauge("dedup_gauge", "Gauge", registry=registry)
        assert g1 is g2


class TestDefaultBuckets:
    def test_starts_with_5ms(self) -> None:
        assert DEFAULT_BUCKETS[0] == 0.005

    def test_ends_with_30s(self) -> None:
        assert DEFAULT_BUCKETS[-1] == 30.0

    def test_twelve_buckets(self) -> None:
        assert len(DEFAULT_BUCKETS) == 12


class TestGetMetricsText:
    def test_returns_bytes(self) -> None:
        registry = CollectorRegistry()
        create_counter("text_test", "Test", registry=registry)
        result = get_metrics_text(registry)
        assert isinstance(result, bytes)


class TestResetMetrics:
    def test_clears_cache(self) -> None:
        registry = CollectorRegistry()
        c1 = create_counter("reset_test", "Test", registry=registry)
        reset_metrics()
        # After reset, a new counter with the same name on a NEW registry should work
        registry2 = CollectorRegistry()
        c2 = create_counter("reset_test", "Test", registry=registry2)
        assert c1 is not c2


class TestRegistryIsolation:
    def test_different_registries_independent(self) -> None:
        r1 = CollectorRegistry()
        r2 = CollectorRegistry()
        c1 = create_counter("isolated_total", "Test", registry=r1)
        c2 = create_counter("isolated_total", "Test", registry=r2)
        assert c1 is not c2
        c1.inc(5)
        text1 = get_metrics_text(r1)
        text2 = get_metrics_text(r2)
        assert b"5.0" in text1
        assert b"5.0" not in text2
