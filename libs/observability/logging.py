"""Structured JSON logging — shared across all components.

Every log line is a single JSON object with guaranteed fields:
  timestamp, level, component, message

Optional fields added when available:
  trace_id, span_id, extra
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def __init__(self, component: str = "unknown") -> None:
        super().__init__()
        self.component = component

    def format(self, record: logging.LogRecord) -> str:
        trace_id = getattr(record, "trace_id", None)
        span_id = getattr(record, "span_id", None)
        if not trace_id or not span_id:
            active_trace_id, active_span_id = _get_active_trace_context()
            trace_id = trace_id or active_trace_id
            span_id = span_id or active_span_id

        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "component": self.component,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if trace_id:
            log_entry["trace_id"] = trace_id
        if span_id:
            log_entry["span_id"] = span_id

        # Add any extra fields
        extra = getattr(record, "extra_fields", None)
        if extra and isinstance(extra, dict):
            log_entry["extra"] = extra

        # Add exception info if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Unknown",
                "message": str(record.exc_info[1]),
            }

        return json.dumps(log_entry, default=str)


def _get_active_trace_context() -> tuple[str | None, str | None]:
    """Return the current OTel trace/span IDs when a valid span is active."""
    try:
        from opentelemetry import trace
    except ImportError:
        return None, None

    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not getattr(span_context, "is_valid", False):
        return None, None

    return f"{span_context.trace_id:032x}", f"{span_context.span_id:016x}"


def setup_logging(
    component: str,
    level: str | int = "INFO",
) -> None:
    """Configure the root logger with structured JSON output.

    Args:
        component: Logical component name (e.g. "compiler-api", "mcp-runtime").
        level: Log level string or constant.
    """
    root = logging.getLogger()

    # Remove existing handlers to avoid duplicate output
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter(component=component))

    root.addHandler(handler)
    resolved_level = (
        level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO)
    )
    root.setLevel(resolved_level)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (convenience wrapper)."""
    return logging.getLogger(name)
