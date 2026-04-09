"""Shared validators for runtime response contracts used by proof and validation flows."""

from __future__ import annotations

from typing import Any, cast

_STREAM_EVENT_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "sse": ("event", "data"),
    "websocket": ("message_type",),
}

_STREAM_LIFECYCLE_FIELD_RULES = {
    "grpc_stream": {
        "string": ("termination_reason", "rpc_path", "mode"),
        "integer": ("messages_collected",),
        "number": (),
    },
    "sse": {
        "string": ("termination_reason",),
        "integer": ("events_collected", "max_events"),
        "number": ("idle_timeout_seconds",),
    },
    "websocket": {
        "string": ("termination_reason",),
        "integer": ("events_collected", "max_messages", "messages_sent"),
        "number": ("idle_timeout_seconds",),
    },
}


def validate_tool_listing_payload(payload: Any, *, context: str) -> list[dict[str, Any]]:
    """Validate a runtime `/tools` payload and return normalized tool documents."""

    if not isinstance(payload, dict):
        raise RuntimeError(f"{context} returned JSON {type(payload).__name__}, expected object.")

    if "tools" not in payload:
        raise RuntimeError(f"{context} did not include required field 'tools'.")

    tools = payload["tools"]
    if not isinstance(tools, list):
        raise RuntimeError(f"{context} did not include a valid 'tools' array.")

    validated_tools: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise RuntimeError(
                f"{context} tools[{index}] was {type(tool).__name__}, expected object."
            )

        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(f"{context} tools[{index}] did not include a valid 'name' string.")
        if name in seen_names:
            raise RuntimeError(f"{context} included duplicate tool name {name!r}.")

        seen_names.add(name)
        validated_tools.append(cast(dict[str, Any], tool))

    return validated_tools


def stream_result_failure_reason(
    stream_result: Any,
    *,
    transport: str | None,
) -> str | None:
    """Return a contract failure reason for a streaming invocation envelope, if any."""

    if not isinstance(stream_result, dict):
        return "Invocation returned a non-object stream payload."

    events = stream_result.get("events")
    lifecycle = stream_result.get("lifecycle")
    if not isinstance(events, list) or not isinstance(lifecycle, dict):
        return "Invocation did not return the expected streaming lifecycle structure."

    required_event_fields = _STREAM_EVENT_REQUIRED_FIELDS.get(transport or "", ())
    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            return f"events[{idx}] is {type(event).__name__}, expected dict."
        for field in required_event_fields:
            if field not in event:
                return f"events[{idx}] missing required '{field}' field."

    rules = _STREAM_LIFECYCLE_FIELD_RULES.get(
        transport or "",
        {
            "string": ("termination_reason",),
            "integer": (),
            "number": (),
        },
    )

    for field in rules["string"]:
        value = lifecycle.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"Invocation returned streaming lifecycle without valid {field!r}."

    for field in rules["integer"]:
        value = lifecycle.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"Invocation returned streaming lifecycle without valid {field!r}."

    for field in rules["number"]:
        value = lifecycle.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"Invocation returned streaming lifecycle without valid {field!r}."

    return None


__all__ = ["stream_result_failure_reason", "validate_tool_listing_payload"]
