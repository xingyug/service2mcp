"""Prometheus metric helpers — shared across all components.

Provides factory functions for creating counters, histograms, and gauges
with consistent naming and labeling conventions.  Also provides a FastAPI
sub-app that serves the /metrics endpoint.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)
Metric = Counter | Histogram | Gauge

# Default histogram buckets (seconds) — covers 5ms to 30s
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0,
)

# Track registered metric names to avoid duplicate registration
_registered_metrics: dict[tuple[int, str], Metric] = {}


def _metric_key(name: str, registry: CollectorRegistry) -> tuple[int, str]:
    return id(registry), name


def create_counter(
    name: str,
    description: str,
    labels: Sequence[str] = (),
    registry: CollectorRegistry = REGISTRY,
) -> Counter:
    """Create or retrieve a Prometheus Counter."""
    key = _metric_key(name, registry)
    if key in _registered_metrics:
        return _registered_metrics[key]  # type: ignore[return-value]
    counter = Counter(name, description, list(labels), registry=registry)
    _registered_metrics[key] = counter
    return counter


def create_histogram(
    name: str,
    description: str,
    labels: Sequence[str] = (),
    buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    registry: CollectorRegistry = REGISTRY,
) -> Histogram:
    """Create or retrieve a Prometheus Histogram."""
    key = _metric_key(name, registry)
    if key in _registered_metrics:
        return _registered_metrics[key]  # type: ignore[return-value]
    histogram = Histogram(name, description, list(labels), buckets=buckets, registry=registry)
    _registered_metrics[key] = histogram
    return histogram


def create_gauge(
    name: str,
    description: str,
    labels: Sequence[str] = (),
    registry: CollectorRegistry = REGISTRY,
) -> Gauge:
    """Create or retrieve a Prometheus Gauge."""
    key = _metric_key(name, registry)
    if key in _registered_metrics:
        return _registered_metrics[key]  # type: ignore[return-value]
    gauge = Gauge(name, description, list(labels), registry=registry)
    _registered_metrics[key] = gauge
    return gauge


def get_metrics_text(registry: CollectorRegistry = REGISTRY) -> bytes:
    """Generate Prometheus metrics text output."""
    return generate_latest(registry)


def reset_metrics() -> None:
    """Reset all tracked metrics — useful for testing."""
    _registered_metrics.clear()
