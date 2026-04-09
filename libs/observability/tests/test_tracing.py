"""Tests for OpenTelemetry tracing configuration."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def test_setup_tracer_without_endpoint():
    """Test setup_tracer works without endpoint (disabled by default)."""
    # Reset state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import setup_tracer

    tracing_module._is_configured = False
    tracing_module._tracer_provider = None

    # Clean up env var if it exists
    old_endpoint = os.environ.pop("OTEL_EXPORTER_ENDPOINT", None)

    try:
        setup_tracer("test-service")

        # Should not be configured without endpoint and enable_local=False
        assert tracing_module._is_configured is False
    finally:
        if old_endpoint:
            os.environ["OTEL_EXPORTER_ENDPOINT"] = old_endpoint


def test_setup_tracer_with_endpoint():
    """Test setup_tracer works with OTLP endpoint."""
    # Reset state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import setup_tracer

    tracing_module._is_configured = False
    tracing_module._tracer_provider = None

    with (
        patch("opentelemetry.sdk.trace.TracerProvider") as mock_provider_class,
        patch("opentelemetry.sdk.resources.Resource") as mock_resource_class,
        patch("opentelemetry.trace") as mock_trace,
        patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class,
        patch("opentelemetry.sdk.trace.export.BatchSpanProcessor") as mock_processor_class,
    ):
        mock_resource = MagicMock()
        mock_resource_class.create.return_value = mock_resource

        mock_provider = MagicMock()
        mock_provider_class.return_value = mock_provider

        mock_exporter = MagicMock()
        mock_exporter_class.return_value = mock_exporter

        mock_processor = MagicMock()
        mock_processor_class.return_value = mock_processor

        setup_tracer("test-service", endpoint="http://localhost:4317")

        # Should create resource and provider
        assert mock_resource_class.create.called
        assert {"service.name": "test-service"} in [
            call[0][0] for call in mock_resource_class.create.call_args_list
        ]
        mock_provider_class.assert_called_with(resource=mock_resource)
        mock_trace.set_tracer_provider.assert_called_with(mock_provider)

        # Should create exporter and processor
        mock_exporter_class.assert_called_once_with(
            endpoint="http://localhost:4317", insecure=False
        )
        mock_processor_class.assert_called_once_with(mock_exporter)
        mock_provider.add_span_processor.assert_called_once_with(mock_processor)


def test_setup_tracer_with_insecure_endpoint():
    """Test setup_tracer respects OTEL_EXPORTER_OTLP_INSECURE env var."""
    # Reset state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import setup_tracer

    tracing_module._is_configured = False
    tracing_module._tracer_provider = None

    with (
        patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_INSECURE": "true"}),
        patch("opentelemetry.sdk.trace.TracerProvider") as mock_provider_class,
        patch("opentelemetry.sdk.resources.Resource") as mock_resource_class,
        patch("opentelemetry.trace"),
        patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class,
        patch("opentelemetry.sdk.trace.export.BatchSpanProcessor") as mock_processor_class,
    ):
        mock_resource = MagicMock()
        mock_resource_class.create.return_value = mock_resource

        mock_provider = MagicMock()
        mock_provider_class.return_value = mock_provider

        mock_exporter = MagicMock()
        mock_exporter_class.return_value = mock_exporter

        mock_processor = MagicMock()
        mock_processor_class.return_value = mock_processor

        setup_tracer("test-service", endpoint="http://localhost:4317")

        # Should create insecure exporter
        mock_exporter_class.assert_called_once_with(endpoint="http://localhost:4317", insecure=True)


def test_setup_tracer_with_enable_local():
    """Test setup_tracer works with enable_local=True."""
    # Reset state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import setup_tracer

    tracing_module._is_configured = False
    tracing_module._tracer_provider = None

    # Clean up env var if it exists
    old_endpoint = os.environ.pop("OTEL_EXPORTER_ENDPOINT", None)

    try:
        with (
            patch("opentelemetry.sdk.trace.TracerProvider") as mock_provider_class,
            patch("opentelemetry.sdk.resources.Resource") as mock_resource_class,
            patch("opentelemetry.trace") as mock_trace,
        ):
            mock_resource = MagicMock()
            mock_resource_class.create.return_value = mock_resource

            mock_provider = MagicMock()
            mock_provider_class.return_value = mock_provider

            setup_tracer("test-service", enable_local=True)

            # Should create resource and provider
            mock_resource_class.create.assert_called_once_with({"service.name": "test-service"})
            mock_provider_class.assert_called_once_with(resource=mock_resource)
            mock_trace.set_tracer_provider.assert_called_once_with(mock_provider)

            # Should not add span processor without endpoint
            mock_provider.add_span_processor.assert_not_called()

            assert tracing_module._is_configured is True
    finally:
        if old_endpoint:
            os.environ["OTEL_EXPORTER_ENDPOINT"] = old_endpoint


def test_setup_tracer_uses_env_var():
    """Test setup_tracer uses OTEL_EXPORTER_ENDPOINT env var."""
    # Reset state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import setup_tracer

    tracing_module._is_configured = False
    tracing_module._tracer_provider = None

    with (
        patch.dict(os.environ, {"OTEL_EXPORTER_ENDPOINT": "http://env-endpoint:4317"}),
        patch("opentelemetry.sdk.trace.TracerProvider") as mock_provider_class,
        patch("opentelemetry.sdk.resources.Resource") as mock_resource_class,
        patch("opentelemetry.trace"),
        patch(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
        ) as mock_exporter_class,
        patch("opentelemetry.sdk.trace.export.BatchSpanProcessor") as mock_processor_class,
    ):
        mock_resource = MagicMock()
        mock_resource_class.create.return_value = mock_resource

        mock_provider = MagicMock()
        mock_provider_class.return_value = mock_provider

        mock_exporter = MagicMock()
        mock_exporter_class.return_value = mock_exporter

        mock_processor = MagicMock()
        mock_processor_class.return_value = mock_processor

        setup_tracer("test-service")

        # Should use env var endpoint
        mock_exporter_class.assert_called_once_with(
            endpoint="http://env-endpoint:4317", insecure=False
        )


def test_setup_tracer_handles_import_error():
    """Test setup_tracer handles ImportError gracefully."""
    # Reset state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import setup_tracer

    original_configured = tracing_module._is_configured
    original_provider = tracing_module._tracer_provider

    tracing_module._is_configured = False
    tracing_module._tracer_provider = None

    try:
        # Create an import error by mocking the opentelemetry.trace import
        import sys

        if "opentelemetry.trace" in sys.modules:
            del sys.modules["opentelemetry.trace"]
        if "opentelemetry" in sys.modules:
            del sys.modules["opentelemetry"]

        # Make sure imports will fail
        with patch.dict(sys.modules, {"opentelemetry": None}):
            setup_tracer("test-service", enable_local=True)

            # Should set _is_configured to False due to import error
            assert tracing_module._is_configured is False
    finally:
        # Restore original state
        tracing_module._is_configured = original_configured
        tracing_module._tracer_provider = original_provider


def test_setup_tracer_handles_general_exception():
    """Test setup_tracer handles general exceptions gracefully."""
    # Reset state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import setup_tracer

    tracing_module._is_configured = False
    tracing_module._tracer_provider = None

    with patch(
        "opentelemetry.sdk.trace.TracerProvider", side_effect=RuntimeError("Configuration failed")
    ):
        setup_tracer("test-service", enable_local=True)

        # Should set _is_configured to False
        assert tracing_module._is_configured is False


def test_setup_tracer_already_configured():
    """Test setup_tracer returns early when already configured."""
    # Set up already configured state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import setup_tracer

    tracing_module._is_configured = True
    tracing_module._tracer_provider = MagicMock()

    with patch("opentelemetry.sdk.trace.TracerProvider") as mock_provider_class:
        setup_tracer("test-service", enable_local=True)

        # Should not create new provider
        mock_provider_class.assert_not_called()


def test_setup_tracer_reconfigures_for_new_service_name():
    """A second service in the same process should get a fresh provider."""
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import get_tracer, setup_tracer

    tracing_module._is_configured = False
    tracing_module._tracer_provider = None
    tracing_module._configured_service_name = None
    tracing_module._configured_endpoint = None
    tracing_module._configured_enable_local = False

    with (
        patch("opentelemetry.sdk.trace.TracerProvider") as mock_provider_class,
        patch("opentelemetry.sdk.resources.Resource") as mock_resource_class,
        patch("opentelemetry.trace") as mock_trace,
    ):
        first_resource = MagicMock()
        second_resource = MagicMock()
        mock_resource_class.create.side_effect = [first_resource, second_resource]

        first_provider = MagicMock()
        second_provider = MagicMock()
        first_provider.get_tracer.return_value = MagicMock(name="first_tracer")
        second_provider.get_tracer.return_value = MagicMock(name="second_tracer")
        mock_provider_class.side_effect = [first_provider, second_provider]

        setup_tracer("service-a", enable_local=True)
        setup_tracer("service-b", enable_local=True)

        assert mock_resource_class.create.call_args_list[0].args[0] == {"service.name": "service-a"}
        assert mock_resource_class.create.call_args_list[1].args[0] == {"service.name": "service-b"}
        mock_trace.set_tracer_provider.assert_called_once_with(first_provider)
        tracer = get_tracer("runtime")
        second_provider.get_tracer.assert_called_once_with("runtime")
        assert tracer is second_provider.get_tracer.return_value


def test_get_tracer_when_configured():
    """Test get_tracer returns tracer when configured."""
    # Mock configured state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import get_tracer

    tracing_module._is_configured = True
    tracing_module._tracer_provider = MagicMock()
    tracing_module._tracer_provider.get_tracer.return_value = MagicMock()

    tracer = get_tracer("test-tracer")

    tracing_module._tracer_provider.get_tracer.assert_called_once_with("test-tracer")
    assert tracer == tracing_module._tracer_provider.get_tracer.return_value


def test_get_tracer_when_not_configured():
    """Test get_tracer returns NoOpTracer when not configured."""
    # Mock unconfigured state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import _NoOpTracer, get_tracer

    tracing_module._is_configured = False

    tracer = get_tracer("test-tracer")

    assert isinstance(tracer, _NoOpTracer)


def test_trace_span_when_configured():
    """Test trace_span creates real span when configured."""
    # Mock configured state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import trace_span

    tracing_module._is_configured = True

    with patch("libs.observability.tracing.get_tracer") as mock_get_tracer:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_get_tracer.return_value = mock_tracer

        with trace_span("test-span", {"key": "value"}) as span:
            assert span == mock_span

        mock_tracer.start_as_current_span.assert_called_once_with(
            "test-span", attributes={"key": "value"}
        )


def test_trace_span_when_not_configured():
    """Test trace_span returns NoOpSpan when not configured."""
    # Mock unconfigured state
    import libs.observability.tracing as tracing_module
    from libs.observability.tracing import _NoOpSpan, trace_span

    tracing_module._is_configured = False

    with trace_span("test-span") as span:
        assert isinstance(span, _NoOpSpan)


def test_noop_span_methods():
    """Test NoOpSpan methods don't raise errors."""
    from libs.observability.tracing import _NoOpSpan

    span = _NoOpSpan()

    # Should not raise exceptions
    span.set_attribute("key", "value")
    span.set_status("OK")
    span.record_exception(RuntimeError("test error"))


def test_noop_tracer_context_manager():
    """Test NoOpTracer context manager returns NoOpSpan."""
    from libs.observability.tracing import _NoOpSpan, _NoOpTracer

    tracer = _NoOpTracer()

    with tracer.start_as_current_span("test-span") as span:
        assert isinstance(span, _NoOpSpan)
