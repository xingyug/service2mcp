"""SSE, WebSocket, and gRPC streaming proxy helpers."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import httpx
import websockets
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.proxy_http import prepare_request_payload, split_query_and_body
from apps.mcp_runtime.proxy_utils import (
    _DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS,
    _STREAM_MESSAGE_PARAM_NAMES,
    _parse_stream_payload,
    _to_websocket_url,
    build_auth,
    build_request_kwargs,
    resolve_url,
)
from libs.ir.models import EventDescriptor, EventSupportLevel, EventTransport, Operation, ServiceIR

logger = logging.getLogger(__name__)

_SUPPORTED_STREAM_TRANSPORTS = {EventTransport.sse, EventTransport.websocket}

# Hard cap on overall stream collection time to prevent unbounded connections.
_MAX_OVERALL_STREAM_TIMEOUT_SECONDS = 300.0


# ---------------------------------------------------------------------------
# Descriptor resolution
# ---------------------------------------------------------------------------


def stream_descriptor_for_operation(
    operation: Operation,
    event_descriptors: list[EventDescriptor],
) -> EventDescriptor | None:
    descriptors = [
        descriptor for descriptor in event_descriptors if descriptor.operation_id == operation.id
    ]
    if not descriptors:
        return None
    supported_descriptors = [
        descriptor
        for descriptor in descriptors
        if descriptor.support is EventSupportLevel.supported
    ]
    if len(supported_descriptors) > 1:
        raise ToolError(
            f"Operation {operation.id} has multiple streaming descriptors and "
            "cannot be invoked unambiguously."
        )
    if not supported_descriptors:
        declared_transports = ", ".join(
            sorted({descriptor.transport.value for descriptor in descriptors})
        )
        raise ToolError(
            f"Streaming transport(s) {declared_transports} for operation "
            f"{operation.id} are declared but not enabled."
        )
    descriptor = supported_descriptors[0]
    if descriptor.support is not EventSupportLevel.supported:
        raise ToolError(
            f"Streaming transport {descriptor.transport.value} for operation "
            f"{operation.id} is declared but not enabled."
        )
    if descriptor.transport is EventTransport.grpc_stream:
        if descriptor.grpc_stream is None:
            raise ToolError(
                f"Native grpc_stream transport for operation {operation.id} is "
                "missing grpc_stream runtime configuration."
            )
        return descriptor
    if descriptor.transport not in _SUPPORTED_STREAM_TRANSPORTS:
        raise ToolError(
            f"Streaming transport {descriptor.transport.value} for operation "
            f"{operation.id} is not supported by the runtime."
        )
    return descriptor


# ---------------------------------------------------------------------------
# Stream session dispatch
# ---------------------------------------------------------------------------


async def perform_stream_session(
    operation: Operation,
    arguments: dict[str, Any],
    descriptor: EventDescriptor,
    *,
    service_ir: ServiceIR,
    oauth_token_cache: dict[str, tuple[str, float | None]],
    oauth_lock: Any,
    get_client: Any,
    timeout: float,
    grpc_stream_executor: Any | None,
) -> dict[str, Any]:
    if descriptor.transport is EventTransport.sse:
        return await consume_sse_stream(
            operation,
            arguments,
            descriptor,
            service_ir=service_ir,
            oauth_token_cache=oauth_token_cache,
            oauth_lock=oauth_lock,
            get_client=get_client,
            timeout=timeout,
        )
    if descriptor.transport is EventTransport.websocket:
        return await consume_websocket_stream(
            operation,
            arguments,
            descriptor,
            service_ir=service_ir,
            oauth_token_cache=oauth_token_cache,
            oauth_lock=oauth_lock,
            get_client=get_client,
            timeout=timeout,
        )
    if descriptor.transport is EventTransport.grpc_stream:
        return await consume_grpc_stream(
            operation,
            arguments,
            descriptor,
            grpc_stream_executor=grpc_stream_executor,
        )
    raise ToolError(
        f"Streaming transport {descriptor.transport.value} is not supported by the runtime."
    )


# ---------------------------------------------------------------------------
# gRPC stream
# ---------------------------------------------------------------------------


async def consume_grpc_stream(
    operation: Operation,
    arguments: dict[str, Any],
    descriptor: EventDescriptor,
    *,
    grpc_stream_executor: Any | None,
) -> dict[str, Any]:
    if descriptor.grpc_stream is None:
        raise ToolError(
            f"Native grpc_stream transport for operation {operation.id} is "
            "missing grpc_stream runtime configuration."
        )
    if grpc_stream_executor is None:
        raise ToolError(
            f"Native grpc_stream transport for operation {operation.id} requires "
            "a configured grpc stream executor."
        )
    result = await grpc_stream_executor.invoke(
        operation=operation,
        arguments=arguments,
        descriptor=descriptor,
        config=descriptor.grpc_stream,
    )
    if not isinstance(result, dict):
        raise ToolError(
            f"Native grpc_stream executor for operation {operation.id} returned a non-dict result."
        )
    return result


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


async def consume_sse_stream(
    operation: Operation,
    arguments: dict[str, Any],
    descriptor: EventDescriptor,
    *,
    service_ir: ServiceIR,
    oauth_token_cache: dict[str, tuple[str, float | None]],
    oauth_lock: Any,
    get_client: Any,
    timeout: float,
    collect_fn: Any | None = None,
) -> dict[str, Any]:
    if not operation.method or not operation.path:
        raise ToolError(f"Operation {operation.id} is missing method or path metadata.")

    url, path_arguments = resolve_url(operation.path, arguments, service_ir)
    payload = prepare_request_payload(
        operation,
        arguments,
        path_argument_names=path_arguments,
        service_ir=service_ir,
        split_query_and_body=split_query_and_body,
    )
    if payload.files is not None:
        raise ToolError(
            f"SSE streaming does not support multipart payloads for operation {operation.id}."
        )

    headers, auth_query_params = await build_auth(
        operation.id,
        method=operation.method.upper(),
        url=url,
        query_params=payload.query_params,
        body_for_signing=payload.signable_body,
        service_ir=service_ir,
        oauth_token_cache=oauth_token_cache,
        oauth_lock=oauth_lock,
        get_client=get_client,
        timeout=timeout,
    )
    headers.setdefault("Accept", "text/event-stream")
    query_params = dict(payload.query_params)
    query_params.update(auth_query_params)
    request_kwargs = build_request_kwargs(
        headers=headers,
        params=query_params or None,
        payload=payload,
        timeout=timeout,
    )

    async with get_client().stream(
        operation.method.upper(),
        url,
        **request_kwargs,
    ) as response:
        if response.status_code >= 400:
            raise ToolError(
                f"Upstream SSE request failed for {operation.id} "
                f"with status {response.status_code}."
            )
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" not in content_type:
            raise ToolError(
                f"Upstream SSE request for {operation.id} returned unsupported "
                f"content type {content_type or '<missing>'}."
            )

        max_events = _descriptor_positive_int(descriptor, "max_events", default=10)
        idle_timeout = _descriptor_positive_float(
            descriptor,
            "idle_timeout_seconds",
            default=_DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS,
        )
        _do_collect = collect_fn if collect_fn is not None else _collect_sse_events
        try:
            events, termination_reason = await asyncio.wait_for(
                _do_collect(
                    response,
                    max_events=max_events,
                    idle_timeout_seconds=idle_timeout,
                ),
                timeout=_MAX_OVERALL_STREAM_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            events = []
            termination_reason = "overall_timeout"
        return {
            "transport": descriptor.transport.value,
            "upstream_status": response.status_code,
            "events": events,
            "lifecycle": {
                "termination_reason": termination_reason,
                "events_collected": len(events),
                "max_events": max_events,
                "idle_timeout_seconds": idle_timeout,
            },
        }


# ---------------------------------------------------------------------------
# WebSocket stream
# ---------------------------------------------------------------------------


async def consume_websocket_stream(
    operation: Operation,
    arguments: dict[str, Any],
    descriptor: EventDescriptor,
    *,
    service_ir: ServiceIR,
    oauth_token_cache: dict[str, tuple[str, float | None]],
    oauth_lock: Any,
    get_client: Any,
    timeout: float,
    collect_fn: Any | None = None,
    ws_connect_fn: Any | None = None,
) -> dict[str, Any]:
    if not operation.path:
        raise ToolError(f"Operation {operation.id} is missing path metadata.")

    url, path_arguments = resolve_url(operation.path, arguments, service_ir)
    query_params, outbound_messages = prepare_websocket_session(
        operation,
        arguments,
        path_argument_names=path_arguments,
    )
    headers, auth_query_params = await build_auth(
        operation.id,
        method=(operation.method or "GET").upper(),
        url=url,
        query_params=query_params,
        body_for_signing=outbound_messages,
        service_ir=service_ir,
        oauth_token_cache=oauth_token_cache,
        oauth_lock=oauth_lock,
        get_client=get_client,
        timeout=timeout,
    )
    query_params.update(auth_query_params)

    max_messages = _descriptor_positive_int(descriptor, "max_messages", default=10)
    idle_timeout = _descriptor_positive_float(
        descriptor,
        "idle_timeout_seconds",
        default=_DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS,
    )
    websocket_url = _to_websocket_url(url, query_params)

    _do_connect = ws_connect_fn if ws_connect_fn is not None else websockets.connect
    _do_collect = collect_fn if collect_fn is not None else _collect_websocket_messages

    try:
        async with _do_connect(
            websocket_url,
            additional_headers=headers or None,
            open_timeout=timeout,
            close_timeout=timeout,
            max_queue=max_messages,
            write_limit=32768,
        ) as websocket:
            for message in outbound_messages:
                await websocket.send(message)
            try:
                events, termination_reason = await asyncio.wait_for(
                    _do_collect(
                        websocket,
                        max_messages=max_messages,
                        idle_timeout_seconds=idle_timeout,
                    ),
                    timeout=_MAX_OVERALL_STREAM_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                events = []
                termination_reason = "overall_timeout"
    except websockets.exceptions.WebSocketException as ws_exc:
        raise ToolError(f"WebSocket communication failed: {ws_exc}") from ws_exc
    except OSError as os_exc:
        raise ToolError(f"WebSocket connection error: {os_exc}") from os_exc

    return {
        "transport": descriptor.transport.value,
        "events": events,
        "lifecycle": {
            "termination_reason": termination_reason,
            "events_collected": len(events),
            "max_messages": max_messages,
            "idle_timeout_seconds": idle_timeout,
            "messages_sent": len(outbound_messages),
        },
    }


def prepare_websocket_session(
    operation: Operation,
    arguments: dict[str, Any],
    *,
    path_argument_names: set[str],
) -> tuple[dict[str, Any], list[str | bytes]]:
    remaining = {
        key: value
        for key, value in arguments.items()
        if key not in path_argument_names and value is not None
    }
    if not remaining:
        return {}, []

    body_param_name = operation.body_param_name
    body_value: Any | None = None
    if body_param_name and body_param_name in remaining:
        body_value = remaining.pop(body_param_name)
    else:
        for candidate in _STREAM_MESSAGE_PARAM_NAMES:
            if candidate in remaining:
                body_value = remaining.pop(candidate)
                break

    return remaining, _normalize_websocket_messages(operation.id, body_value)


# ---------------------------------------------------------------------------
# Module-level SSE helpers
# ---------------------------------------------------------------------------


def _descriptor_positive_int(descriptor: EventDescriptor, key: str, *, default: int) -> int:
    value = descriptor.metadata.get(key, default)
    if isinstance(value, int) and value > 0:
        return value
    return default


def _descriptor_positive_float(
    descriptor: EventDescriptor,
    key: str,
    *,
    default: float,
) -> float:
    value = descriptor.metadata.get(key, default)
    if isinstance(value, int | float) and float(value) > 0:
        return float(value)
    return default


async def _collect_sse_events(
    response: httpx.Response,
    *,
    max_events: int,
    idle_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], str]:
    lines = response.aiter_lines()
    event_type = "message"
    data_lines: list[str] = []
    event_id: str | None = None
    events: list[dict[str, Any]] = []

    while len(events) < max_events:
        try:
            line = await asyncio.wait_for(anext(lines), timeout=idle_timeout_seconds)
        except StopAsyncIteration:
            break
        except TimeoutError:
            return events, "idle_timeout"
        except (httpx.ReadError, httpx.RemoteProtocolError) as exc:
            logger.warning("SSE connection error: %s", exc)
            return events, "connection_error"

        if line == "":
            event = _build_sse_event(event_type, data_lines, event_id)
            if event is not None:
                events.append(event)
                if len(events) >= max_events:
                    return events, "max_events"
            event_type = "message"
            data_lines = []
            event_id = None
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            raw = line.partition(":")[2]
            event_type = raw[1:] if raw.startswith(" ") else raw
            event_type = event_type or "message"
            continue
        if line.startswith("data:"):
            raw = line.partition(":")[2]
            data_lines.append(raw[1:] if raw.startswith(" ") else raw)
            continue
        if line.startswith("id:"):
            raw = line.partition(":")[2]
            event_id = raw[1:] if raw.startswith(" ") else raw

    trailing_event = _build_sse_event(event_type, data_lines, event_id)
    if trailing_event is not None and len(events) < max_events:
        events.append(trailing_event)
        if len(events) >= max_events:
            return events, "max_events"
    return events, "eof"


def _build_sse_event(
    event_type: str,
    data_lines: list[str],
    event_id: str | None,
) -> dict[str, Any] | None:
    if not data_lines and event_id is None:
        return None
    payload = "\n".join(data_lines)
    event: dict[str, Any] = {
        "event": event_type,
        "data": payload,
    }
    if event_id is not None:
        event["id"] = event_id
    parsed_payload = _parse_stream_payload(payload)
    if parsed_payload is not payload:
        event["parsed_data"] = parsed_payload
    return event


# ---------------------------------------------------------------------------
# Module-level WebSocket helpers
# ---------------------------------------------------------------------------


def _normalize_websocket_messages(operation_id: str, value: Any | None) -> list[str | bytes]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_normalize_websocket_message(item) for item in value]
    return [_normalize_websocket_message(value)]


def _normalize_websocket_message(value: Any) -> str | bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "bytes_base64" in value and isinstance(value["bytes_base64"], str):
            try:
                return base64.b64decode(value["bytes_base64"], validate=True)
            except ValueError as exc:
                raise ToolError("WebSocket bytes_base64 contains invalid base64 data.") from exc
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
        if "json" in value:
            return json.dumps(value["json"], ensure_ascii=True, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


async def _collect_websocket_messages(
    websocket: websockets.ClientConnection,
    *,
    max_messages: int,
    idle_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    while len(events) < max_messages:
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=idle_timeout_seconds)
        except TimeoutError:
            return events, "idle_timeout"
        except websockets.ConnectionClosed:
            return events, "connection_closed"

        if isinstance(message, bytes):
            events.append(
                {
                    "message_type": "bytes",
                    "content_base64": base64.b64encode(message).decode("ascii"),
                    "size_bytes": len(message),
                }
            )
        else:
            event: dict[str, Any] = {
                "message_type": "text",
                "text": message,
            }
            parsed_payload = _parse_stream_payload(message)
            if parsed_payload is not message:
                event["parsed_data"] = parsed_payload
            events.append(event)

        if len(events) >= max_messages:
            return events, "max_messages"

    return events, "connection_closed"
