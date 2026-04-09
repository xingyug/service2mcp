"""Tests for libs/runtime_contracts.py — stream_result_failure_reason."""

from __future__ import annotations

from libs.runtime_contracts import stream_result_failure_reason


def _valid_sse_event() -> dict:
    return {"event": "update", "data": '{"key":"value"}', "parsed_data": {"key": "value"}}


def _valid_ws_event(*, message_type: str = "text") -> dict:
    return {"message_type": message_type, "parsed_data": {"key": "value"}}


def _valid_grpc_event() -> dict:
    return {"message_type": "protobuf", "parsed_data": {"key": "value"}}


def _sse_lifecycle() -> dict:
    return {
        "termination_reason": "idle_timeout",
        "events_collected": 3,
        "max_events": 10,
        "idle_timeout_seconds": 5.0,
    }


def _ws_lifecycle() -> dict:
    return {
        "termination_reason": "idle_timeout",
        "events_collected": 2,
        "max_messages": 10,
        "messages_sent": 1,
        "idle_timeout_seconds": 5.0,
    }


def _grpc_lifecycle() -> dict:
    return {
        "termination_reason": "completed",
        "rpc_path": "/svc/Method",
        "mode": "server_streaming",
        "messages_collected": 5,
    }


class TestStreamResultEventValidation:
    """individual event entries must be validated."""

    # -- Non-dict entries (transport-independent) --

    def test_string_in_events_fails(self) -> None:
        result = {
            "events": ["not-a-dict", _valid_ws_event()],
            "lifecycle": _ws_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="websocket")
        assert reason is not None
        assert "events[0]" in reason
        assert "str" in reason

    def test_int_in_events_fails(self) -> None:
        result = {
            "events": [_valid_ws_event(), 42],
            "lifecycle": _ws_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="websocket")
        assert reason is not None
        assert "events[1]" in reason

    # -- WebSocket: requires message_type --

    def test_ws_missing_message_type_fails(self) -> None:
        result = {
            "events": [{"parsed_data": {}}],
            "lifecycle": _ws_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="websocket")
        assert reason is not None
        assert "message_type" in reason

    def test_ws_valid_events_pass(self) -> None:
        result = {
            "events": [_valid_ws_event(), _valid_ws_event(message_type="bytes")],
            "lifecycle": _ws_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="websocket")
        assert reason is None

    # -- gRPC stream: events are raw decoded messages, only require dict --

    def test_grpc_non_dict_event_fails(self) -> None:
        result = {
            "events": ["not a dict"],
            "lifecycle": _grpc_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="grpc_stream")
        assert reason is not None
        assert "expected dict" in reason

    def test_grpc_valid_events_pass(self) -> None:
        result = {
            "events": [_valid_grpc_event()],
            "lifecycle": _grpc_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="grpc_stream")
        assert reason is None

    def test_grpc_raw_decoded_events_pass(self) -> None:
        """gRPC stream events may be raw protobuf-decoded dicts without message_type."""
        result = {
            "events": [{"sku": "x", "quantity": 1}],
            "lifecycle": _grpc_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="grpc_stream")
        assert reason is None

    # -- SSE: requires event + data --

    def test_sse_missing_event_field_fails(self) -> None:
        result = {
            "events": [{"data": "some data"}],
            "lifecycle": _sse_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="sse")
        assert reason is not None
        assert "event" in reason

    def test_sse_missing_data_field_fails(self) -> None:
        result = {
            "events": [{"event": "update"}],
            "lifecycle": _sse_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="sse")
        assert reason is not None
        assert "data" in reason

    def test_sse_valid_events_pass(self) -> None:
        result = {
            "events": [_valid_sse_event()],
            "lifecycle": _sse_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="sse")
        assert reason is None

    # -- Unknown transport: only requires dict events, no specific fields --

    def test_unknown_transport_non_dict_fails(self) -> None:
        result = {
            "events": [42],
            "lifecycle": {"termination_reason": "done"},
        }
        reason = stream_result_failure_reason(result, transport=None)
        assert reason is not None
        assert "expected dict" in reason

    def test_unknown_transport_dict_events_pass(self) -> None:
        result = {
            "events": [{"data": "hello"}],
            "lifecycle": {"termination_reason": "done"},
        }
        reason = stream_result_failure_reason(result, transport=None)
        assert reason is None

    # -- Empty events list (always OK) --

    def test_empty_events_list_passes(self) -> None:
        result = {
            "events": [],
            "lifecycle": _sse_lifecycle(),
        }
        reason = stream_result_failure_reason(result, transport="sse")
        assert reason is None
