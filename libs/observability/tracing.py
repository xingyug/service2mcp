"""OpenTelemetry tracing helpers — shared across all components.

Provides tracer setup and span context managers.  Falls back to no-op
when OTEL_EXPORTER_ENDPOINT is not configured, so importing this module
in tests or local dev causes no side-effects.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-initialised globals
_tracer_provider: Any = None
_is_configured: bool = False
_configured_service_name: str | None = None
_configured_endpoint: str | None = None
_configured_enable_local: bool = False


def setup_tracer(
    service_name: str,
    endpoint: str | None = None,
    *,
    enable_local: bool = False,
) -> None:
    """Configure the OpenTelemetry tracer provider.

    Args:
        service_name: Logical name of the service (e.g. "compiler-api").
        endpoint: OTel collector gRPC endpoint.  Falls back to
                  ``OTEL_EXPORTER_ENDPOINT`` env var.  If neither is set,
                  tracing runs in no-op mode unless ``enable_local`` is true.
        enable_local: When true, configure in-process spans even when no
                  exporter endpoint is provided.
    """
    global _tracer_provider, _is_configured  # noqa: PLW0603
    global _configured_service_name, _configured_endpoint, _configured_enable_local  # noqa: PLW0603

    endpoint = endpoint or os.environ.get("OTEL_EXPORTER_ENDPOINT")

    if (
        _is_configured
        and _tracer_provider is not None
        and (
            _configured_service_name is None
            or (
                _configured_service_name == service_name
                and _configured_endpoint == endpoint
                and _configured_enable_local == enable_local
            )
        )
    ):
        return

    if not endpoint and not enable_local:
        logger.info("OTEL_EXPORTER_ENDPOINT not set — tracing disabled (no-op mode)")
        _is_configured = False
        _configured_service_name = None
        _configured_endpoint = None
        _configured_enable_local = False
        return

    existing_provider = _tracer_provider
    had_provider = existing_provider is not None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        if endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "false").lower() in ("1", "true")
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
            provider.add_span_processor(BatchSpanProcessor(exporter))

        if not had_provider:
            trace.set_tracer_provider(provider)
        _tracer_provider = provider
        _is_configured = True
        _configured_service_name = service_name
        _configured_endpoint = endpoint
        _configured_enable_local = enable_local
        if endpoint:
            logger.info("OTel tracing configured for %s → %s", service_name, endpoint)
        else:
            logger.info("OTel tracing configured for %s (local spans only)", service_name)
    except ImportError:
        logger.warning("opentelemetry SDK not installed — tracing disabled")
        _is_configured = False
        _tracer_provider = existing_provider
        _configured_service_name = None
        _configured_endpoint = None
        _configured_enable_local = False
    except Exception:
        logger.warning("Failed to configure OTel tracing", exc_info=True)
        _is_configured = False
        _tracer_provider = existing_provider
        _configured_service_name = None
        _configured_endpoint = None
        _configured_enable_local = False


def get_tracer(name: str) -> Any:
    """Return a tracer instance.  Returns a no-op tracer if not configured."""
    if _is_configured and _tracer_provider is not None:
        get_provider_tracer = getattr(_tracer_provider, "get_tracer", None)
        if callable(get_provider_tracer):
            return get_provider_tracer(name)

        from opentelemetry import trace

        return trace.get_tracer(name)
    return _NoOpTracer()


@contextmanager
def trace_span(name: str, attributes: dict[str, str] | None = None) -> Generator[Any, None, None]:
    """Context manager that creates a span or acts as a no-op."""
    tracer = get_tracer("tool-compiler")
    if _is_configured:
        with tracer.start_as_current_span(name, attributes=attributes or {}) as span:
            yield span
    else:
        yield _NoOpSpan()


class _NoOpSpan:
    """Placeholder span when tracing is not configured."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: BaseException) -> None:
        pass


class _NoOpTracer:
    """Placeholder tracer when tracing is not configured."""

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Generator[_NoOpSpan, None, None]:
        yield _NoOpSpan()
