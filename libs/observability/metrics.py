"""Prometheus metric helpers — shared across all components.

Provides factory functions for creating counters, histograms, and gauges
with consistent naming and labeling conventions.  Also provides a FastAPI
sub-app that serves the /metrics endpoint.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import cast

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)
type Metric = Counter | Histogram | Gauge

# Default histogram buckets (seconds) — covers 5ms to 30s
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)

# Track registered metric names to avoid duplicate registration
_registered_metrics: dict[
    tuple[int, str], tuple[CollectorRegistry, Metric, tuple[str, tuple[str, ...]]]
] = {}


def _metric_key(name: str, registry: CollectorRegistry) -> tuple[int, str]:
    return id(registry), name


def _metric_contract(metric_type: str, labels: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    return metric_type, tuple(labels)


def _get_registered_metric(
    name: str,
    *,
    registry: CollectorRegistry,
    metric_type: str,
    labels: Sequence[str],
) -> Metric | None:
    entry = _registered_metrics.get(_metric_key(name, registry))
    if entry is None:
        return None

    _, metric, existing_contract = entry
    requested_contract = _metric_contract(metric_type, labels)
    if existing_contract != requested_contract:
        existing_type, existing_labels = existing_contract
        raise ValueError(
            f"Metric {name!r} is already registered on this registry as {existing_type} "
            f"with labels {list(existing_labels)!r}; cannot reuse it as {metric_type} "
            f"with labels {list(requested_contract[1])!r}."
        )
    return metric


def _remember_metric(
    name: str,
    *,
    registry: CollectorRegistry,
    metric_type: str,
    labels: Sequence[str],
    metric: Metric,
) -> None:
    _registered_metrics[_metric_key(name, registry)] = (
        registry,
        metric,
        _metric_contract(metric_type, labels),
    )


def create_counter(
    name: str,
    description: str,
    labels: Sequence[str] = (),
    registry: CollectorRegistry = REGISTRY,
) -> Counter:
    """Create or retrieve a Prometheus Counter."""
    label_names = tuple(labels)
    cached = _get_registered_metric(
        name,
        registry=registry,
        metric_type="counter",
        labels=label_names,
    )
    if cached is not None:
        return cast(Counter, cached)
    counter = Counter(name, description, list(label_names), registry=registry)
    _remember_metric(
        name,
        registry=registry,
        metric_type="counter",
        labels=label_names,
        metric=counter,
    )
    return counter


def create_histogram(
    name: str,
    description: str,
    labels: Sequence[str] = (),
    buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    registry: CollectorRegistry = REGISTRY,
) -> Histogram:
    """Create or retrieve a Prometheus Histogram."""
    label_names = tuple(labels)
    cached = _get_registered_metric(
        name,
        registry=registry,
        metric_type="histogram",
        labels=label_names,
    )
    if cached is not None:
        return cast(Histogram, cached)
    histogram = Histogram(name, description, list(label_names), buckets=buckets, registry=registry)
    _remember_metric(
        name,
        registry=registry,
        metric_type="histogram",
        labels=label_names,
        metric=histogram,
    )
    return histogram


def create_gauge(
    name: str,
    description: str,
    labels: Sequence[str] = (),
    registry: CollectorRegistry = REGISTRY,
) -> Gauge:
    """Create or retrieve a Prometheus Gauge."""
    label_names = tuple(labels)
    cached = _get_registered_metric(
        name,
        registry=registry,
        metric_type="gauge",
        labels=label_names,
    )
    if cached is not None:
        return cast(Gauge, cached)
    gauge = Gauge(name, description, list(label_names), registry=registry)
    _remember_metric(
        name,
        registry=registry,
        metric_type="gauge",
        labels=label_names,
        metric=gauge,
    )
    return gauge


def get_metrics_text(registry: CollectorRegistry = REGISTRY) -> bytes:
    """Generate Prometheus metrics text output."""
    return cast(bytes, generate_latest(registry))


def reset_metrics() -> None:
    """Reset all tracked metrics — useful for testing."""
    for registry, metric, _ in list(_registered_metrics.values()):
        registry.unregister(metric)
    _registered_metrics.clear()
