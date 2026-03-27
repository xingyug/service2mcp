"""Unit tests for libs/observability/logging.py — StructuredFormatter."""

from __future__ import annotations

import json
import logging

from libs.observability.logging import StructuredFormatter, get_logger, setup_logging


def _make_record(
    msg: str = "test message",
    level: int = logging.INFO,
    *,
    name: str = "test.logger",
    exc_info: tuple[type, BaseException, None] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="test.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    return record


class TestStructuredFormatter:
    def test_output_is_json(self) -> None:
        fmt = StructuredFormatter(component="test-component")
        record = _make_record()
        output = fmt.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_required_fields(self) -> None:
        fmt = StructuredFormatter(component="my-app")
        record = _make_record("hello world")
        parsed = json.loads(fmt.format(record))
        assert parsed["level"] == "INFO"
        assert parsed["component"] == "my-app"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed
        assert parsed["logger"] == "test.logger"

    def test_custom_component(self) -> None:
        fmt = StructuredFormatter(component="compiler-api")
        record = _make_record()
        parsed = json.loads(fmt.format(record))
        assert parsed["component"] == "compiler-api"

    def test_trace_ids_from_record(self) -> None:
        fmt = StructuredFormatter()
        record = _make_record()
        record.trace_id = "abc123"
        record.span_id = "def456"
        parsed = json.loads(fmt.format(record))
        assert parsed["trace_id"] == "abc123"
        assert parsed["span_id"] == "def456"

    def test_extra_fields(self) -> None:
        fmt = StructuredFormatter()
        record = _make_record()
        record.extra_fields = {"request_id": "req-1", "user": "alice"}
        parsed = json.loads(fmt.format(record))
        assert parsed["extra"]["request_id"] == "req-1"

    def test_exception_info(self) -> None:
        fmt = StructuredFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
            record = _make_record(exc_info=exc_info)  # type: ignore[arg-type]
        parsed = json.loads(fmt.format(record))
        assert parsed["exception"]["type"] == "ValueError"
        assert "test error" in parsed["exception"]["message"]

    def test_no_trace_ids_when_absent(self) -> None:
        fmt = StructuredFormatter()
        record = _make_record()
        parsed = json.loads(fmt.format(record))
        assert "trace_id" not in parsed or parsed.get("trace_id") is None

    def test_warning_level(self) -> None:
        fmt = StructuredFormatter()
        record = _make_record(level=logging.WARNING)
        parsed = json.loads(fmt.format(record))
        assert parsed["level"] == "WARNING"


class TestGetLogger:
    def test_returns_logger(self) -> None:
        logger = get_logger("my.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "my.module"


class TestSetupLogging:
    def test_configures_root_logger(self) -> None:
        setup_logging("test-component", level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1
        # Clean up
        root.handlers.clear()

    def test_int_level(self) -> None:
        setup_logging("test-component", level=logging.WARNING)
        root = logging.getLogger()
        assert root.level == logging.WARNING
        root.handlers.clear()
