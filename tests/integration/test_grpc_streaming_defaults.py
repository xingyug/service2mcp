"""Tests for gRPC server-streaming enabled by default (COV-005)."""

from __future__ import annotations

from typing import Any

from libs.extractors.base import SourceConfig
from libs.extractors.grpc import GrpcProtoExtractor

_PROTO_CONTENT = """\
syntax = "proto3";
package streaming.v1;

service EventService {
  // Unary
  rpc GetEvent(GetEventRequest) returns (GetEventResponse);
  // Server-streaming
  rpc WatchEvents(WatchEventsRequest) returns (stream EventUpdate);
  // Client-streaming
  rpc SendMetrics(stream MetricSample) returns (MetricSummary);
  // Bidirectional
  rpc Chat(stream ChatMessage) returns (stream ChatMessage);
}

message GetEventRequest { string id = 1; }
message GetEventResponse { string name = 1; }
message WatchEventsRequest { string filter = 1; }
message EventUpdate { string id = 1; string payload = 2; }
message MetricSample { string name = 1; double value = 2; }
message MetricSummary { int32 count = 1; }
message ChatMessage { string text = 1; }
"""


def _extract(hints: dict[str, str] | None = None) -> Any:
    extractor = GrpcProtoExtractor()
    return extractor.extract(
        SourceConfig(file_content=_PROTO_CONTENT, hints=hints or {}),
    )


def _op_ids(ir: Any) -> set[str]:
    return {op.id for op in ir.operations}


# ---------- 1. Server-streaming enabled by default ----------


def test_server_streaming_enabled_by_default() -> None:
    ir = _extract()
    ids = _op_ids(ir)
    assert "WatchEvents" in ids, "Server-streaming RPC should appear in operations by default"
    assert "GetEvent" in ids


# ---------- 2. Server-streaming can be disabled ----------


def test_server_streaming_can_be_disabled() -> None:
    ir = _extract(hints={"disable_grpc_server_stream": "true"})
    ids = _op_ids(ir)
    assert "WatchEvents" not in ids, (
        "Server-streaming RPC should be excluded when disable_grpc_server_stream is set"
    )
    assert "GetEvent" in ids
    ignored = ir.metadata.get("ignored_streaming_rpcs", [])
    assert "WatchEvents" in ignored


# ---------- 3. Client-streaming disabled by default ----------


def test_client_streaming_disabled_by_default() -> None:
    ir = _extract()
    ids = _op_ids(ir)
    assert "SendMetrics" not in ids, (
        "Client-streaming RPC should NOT appear in operations by default"
    )
    ignored = ir.metadata.get("ignored_streaming_rpcs", [])
    assert "SendMetrics" in ignored


# ---------- 4. Client-streaming opt-in ----------


def test_client_streaming_opt_in() -> None:
    ir = _extract(hints={"enable_grpc_client_stream": "true"})
    ids = _op_ids(ir)
    assert "SendMetrics" in ids, (
        "Client-streaming RPC should appear when enable_grpc_client_stream hint is set"
    )


# ---------- 5. Backward compat: old hint false disables server streaming ----------


def test_backward_compat_old_hint() -> None:
    ir = _extract(hints={"enable_native_grpc_stream": "false"})
    ids = _op_ids(ir)
    assert "WatchEvents" not in ids, (
        "Old hint enable_native_grpc_stream=false should disable server streaming"
    )
    ignored = ir.metadata.get("ignored_streaming_rpcs", [])
    assert "WatchEvents" in ignored
