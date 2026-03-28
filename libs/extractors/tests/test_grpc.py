"""Tests for the gRPC proto extractor foundation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from libs.extractors.base import SourceConfig, TypeDetector
from libs.extractors.grpc import GrpcProtoExtractor
from libs.ir.models import (
    EventDirection,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    RiskLevel,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures"
PROTO_FIXTURE_PATH = FIXTURES_DIR / "grpc_protos" / "inventory.proto"


def test_detects_proto_fixture() -> None:
    extractor = GrpcProtoExtractor()

    confidence = extractor.detect(SourceConfig(file_path=str(PROTO_FIXTURE_PATH)))

    assert confidence >= 0.9


def test_extracts_unary_rpcs_and_skips_streaming_methods() -> None:
    extractor = GrpcProtoExtractor()

    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(PROTO_FIXTURE_PATH),
            url="grpc://inventory.example.internal:443",
        )
    )

    assert service_ir.protocol == "grpc"
    assert service_ir.service_name == "catalog-v1-inventory-service"
    assert service_ir.base_url == "grpc://inventory.example.internal:443"
    assert service_ir.metadata["proto_package"] == "catalog.v1"
    assert service_ir.metadata["proto_service"] == "InventoryService"
    assert service_ir.metadata["ignored_streaming_rpcs"] == ["WatchInventory"]
    assert len(service_ir.event_descriptors) == 1
    assert service_ir.event_descriptors[0].id == "WatchInventory"
    assert service_ir.event_descriptors[0].transport is EventTransport.grpc_stream
    assert service_ir.event_descriptors[0].direction is EventDirection.inbound
    assert service_ir.event_descriptors[0].support is EventSupportLevel.unsupported
    assert service_ir.event_descriptors[0].grpc_stream is not None
    assert service_ir.event_descriptors[0].grpc_stream.rpc_path == (
        "/catalog.v1.InventoryService/WatchInventory"
    )
    assert service_ir.event_descriptors[0].grpc_stream.mode is GrpcStreamMode.server
    assert service_ir.event_descriptors[0].channel == "/catalog.v1.InventoryService/WatchInventory"
    assert len(service_ir.operations) == 2

    list_items = next(
        operation for operation in service_ir.operations if operation.id == "ListItems"
    )
    assert list_items.name == "List Items"
    assert list_items.method == "POST"
    assert list_items.path == "/catalog.v1.InventoryService/ListItems"
    assert list_items.grpc_unary is not None
    assert list_items.grpc_unary.rpc_path == "/catalog.v1.InventoryService/ListItems"
    assert list_items.risk.risk_level is RiskLevel.safe
    assert {param.name: param.type for param in list_items.params} == {
        "location_id": "string",
        "page_size": "integer",
        "page_token": "string",
        "filter": "object",
    }
    assert all(param.required is False for param in list_items.params)

    adjust_inventory = next(
        operation for operation in service_ir.operations if operation.id == "AdjustInventory"
    )
    assert adjust_inventory.grpc_unary is not None
    assert adjust_inventory.grpc_unary.rpc_path == "/catalog.v1.InventoryService/AdjustInventory"
    assert adjust_inventory.risk.risk_level is RiskLevel.cautious
    assert adjust_inventory.path == "/catalog.v1.InventoryService/AdjustInventory"
    assert {param.name: param.type for param in adjust_inventory.params} == {
        "sku": "string",
        "delta": "integer",
        "reason": "string",
    }


def test_type_detector_can_select_grpc_proto_extractor() -> None:
    detector = TypeDetector([GrpcProtoExtractor()])

    detection = detector.detect(SourceConfig(file_path=str(PROTO_FIXTURE_PATH)))

    assert detection.protocol_name == "grpc"


def test_extracts_supported_native_server_stream_when_enabled_via_hint() -> None:
    extractor = GrpcProtoExtractor()

    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(PROTO_FIXTURE_PATH),
            url="grpc://inventory.example.internal:443",
            hints={"enable_native_grpc_stream": "true"},
        )
    )

    assert service_ir.metadata["ignored_streaming_rpcs"] == []
    assert len(service_ir.operations) == 3

    watch_inventory = next(
        operation for operation in service_ir.operations if operation.id == "WatchInventory"
    )
    assert watch_inventory.method == "POST"
    assert watch_inventory.path == "/catalog.v1.InventoryService/WatchInventory"
    assert {param.name: param.type for param in watch_inventory.params} == {"sku": "string"}

    descriptor = next(
        descriptor
        for descriptor in service_ir.event_descriptors
        if descriptor.id == "WatchInventory"
    )
    assert descriptor.support is EventSupportLevel.supported
    assert descriptor.operation_id == "WatchInventory"
    assert descriptor.grpc_stream is not None
    assert descriptor.grpc_stream.mode is GrpcStreamMode.server


def test_grpc_operations_have_error_schema() -> None:
    extractor = GrpcProtoExtractor()

    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(PROTO_FIXTURE_PATH),
            url="grpc://inventory.example.internal:443",
        )
    )

    expected_codes = {
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "PERMISSION_DENIED",
        "INTERNAL",
        "UNAVAILABLE",
    }
    assert len(service_ir.operations) >= 1
    for op in service_ir.operations:
        assert op.error_schema is not None
        assert len(op.error_schema.responses) == 5
        actual_codes = {r.error_code for r in op.error_schema.responses}
        assert actual_codes == expected_codes
        for r in op.error_schema.responses:
            assert r.description


# ---------------------------------------------------------------------------
# detect() edge cases (lines 116, 124-128)
# ---------------------------------------------------------------------------


def test_detect_returns_zero_when_content_is_none() -> None:
    """detect() returns 0.0 when _get_content returns None (line 116)."""
    extractor = GrpcProtoExtractor()
    source = SourceConfig(url="http://nonexistent.invalid/proto")
    with patch.object(extractor, "_get_content", return_value=None):
        assert extractor.detect(source) == 0.0


def test_detect_weak_signal_for_service_rpc_keywords() -> None:
    """detect() returns 0.6 for content with 'service' and 'rpc' but no syntax decl (line 127)."""
    extractor = GrpcProtoExtractor()
    content = """\
service Foo {
  rpc Bar(Req) returns (Resp);
}
"""
    assert extractor.detect(SourceConfig(file_content=content)) == 0.6


def test_detect_returns_zero_for_no_rpc_content() -> None:
    """detect() returns 0.0 for content with no service/rpc signals."""
    extractor = GrpcProtoExtractor()
    assert extractor.detect(SourceConfig(file_content="just some random text")) == 0.0


# ---------------------------------------------------------------------------
# extract() edge cases (lines 133, 142, 203)
# ---------------------------------------------------------------------------


def test_extract_raises_when_content_is_none() -> None:
    """extract() raises ValueError when _get_content returns None (line 133)."""
    extractor = GrpcProtoExtractor()
    source = SourceConfig(url="http://nonexistent.invalid/proto")
    with patch.object(extractor, "_get_content", return_value=None):
        with pytest.raises(ValueError, match="Could not read source content"):
            extractor.extract(source)


def test_extract_raises_when_no_services() -> None:
    """extract() raises ValueError when proto has no service definitions (line 142)."""
    extractor = GrpcProtoExtractor()
    content = 'syntax = "proto3";\npackage test;\nmessage Foo { string name = 1; }'
    with pytest.raises(ValueError, match="No gRPC service definitions"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_raises_when_all_rpcs_are_streaming() -> None:
    """extract() raises ValueError when every RPC is streaming and hints are off (line 203)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package test;
service StreamOnly {
  rpc Watch(Req) returns (stream Event);
  rpc Chat(stream Msg) returns (stream Msg);
}
message Req { string id = 1; }
message Event { string data = 1; }
message Msg { string text = 1; }
"""
    with pytest.raises(ValueError, match="No unary RPC methods found"):
        extractor.extract(SourceConfig(file_content=content))


# ---------------------------------------------------------------------------
# Streaming RPC handling: client, server, bidirectional (lines 236-252, 276-286)
# ---------------------------------------------------------------------------


def test_client_streaming_rpc_event_descriptor() -> None:
    """Client-streaming RPC produces outbound EventDescriptor with client mode (lines 276-278, 284-286)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package myapp;
service Uploader {
  rpc Upload(stream Chunk) returns (Summary);
  rpc GetStatus(StatusReq) returns (StatusResp);
}
message Chunk { bytes data = 1; }
message Summary { int32 count = 1; }
message StatusReq { string id = 1; }
message StatusResp { string status = 1; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))

    assert len(ir.event_descriptors) == 1
    desc = ir.event_descriptors[0]
    assert desc.id == "Upload"
    assert desc.direction is EventDirection.outbound
    assert desc.support is EventSupportLevel.unsupported
    assert desc.grpc_stream is not None
    assert desc.grpc_stream.mode is GrpcStreamMode.client


def test_bidirectional_streaming_rpc_event_descriptor() -> None:
    """Bidirectional-streaming RPC produces bidirectional EventDescriptor (lines 276, 284)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package chat;
service ChatService {
  rpc Chat(stream ChatMessage) returns (stream ChatMessage);
  rpc Ping(PingReq) returns (PingResp);
}
message ChatMessage { string text = 1; }
message PingReq { string id = 1; }
message PingResp { string id = 1; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))

    assert len(ir.event_descriptors) == 1
    desc = ir.event_descriptors[0]
    assert desc.id == "Chat"
    assert desc.direction is EventDirection.bidirectional
    assert desc.grpc_stream is not None
    assert desc.grpc_stream.mode is GrpcStreamMode.bidirectional


# ---------------------------------------------------------------------------
# _get_content: URL fetch path (lines 236-244, 247-252)
# ---------------------------------------------------------------------------


def test_get_content_fetches_from_url() -> None:
    """_get_content fetches proto from URL when file_path/file_content not set (lines 236-244)."""
    extractor = GrpcProtoExtractor()
    proto_text = 'syntax = "proto3";\npackage url_test;\nservice Svc { rpc Do(Req) returns (Resp); }\nmessage Req { string id = 1; }\nmessage Resp { string ok = 1; }'

    def mock_get(*args, **kwargs):
        request = httpx.Request("GET", args[0] if args else "https://example.com/test.proto")
        resp = httpx.Response(200, text=proto_text, request=request)
        return resp

    with patch("libs.extractors.grpc.httpx.get", side_effect=mock_get):
        result = extractor._get_content(SourceConfig(url="https://example.com/test.proto"))

    assert result == proto_text


def test_get_content_returns_none_on_url_failure() -> None:
    """_get_content returns None when URL fetch fails (lines 241-243)."""
    extractor = GrpcProtoExtractor()

    with patch("libs.extractors.grpc.httpx.get", side_effect=httpx.ConnectError("fail")):
        result = extractor._get_content(SourceConfig(url="https://bad.example.com/test.proto"))

    assert result is None


def test_auth_headers_with_auth_header() -> None:
    """_auth_headers returns Authorization from source.auth_header (lines 247-249)."""
    extractor = GrpcProtoExtractor()
    source = SourceConfig(url="https://x.com", auth_header="Basic abc123")
    headers = extractor._auth_headers(source)
    assert headers == {"Authorization": "Basic abc123"}


def test_auth_headers_with_auth_token() -> None:
    """_auth_headers returns Bearer token from source.auth_token (lines 250-252)."""
    extractor = GrpcProtoExtractor()
    source = SourceConfig(url="https://x.com", auth_token="my-token")
    headers = extractor._auth_headers(source)
    assert headers == {"Authorization": "Bearer my-token"}


# ---------------------------------------------------------------------------
# Enum and map field handling (lines 311, 314, 323-326, 502-508)
# ---------------------------------------------------------------------------


def test_enum_field_produces_string_param() -> None:
    """Enum-typed fields are represented as 'string' params (line 503)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package fieldtest;
service Svc {
  rpc Create(CreateReq) returns (CreateResp);
}
enum Priority { LOW = 0; MEDIUM = 1; HIGH = 2; }
message CreateReq {
  string name = 1;
  Priority priority = 2;
}
message CreateResp { string id = 1; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))
    op = ir.operations[0]
    param_types = {p.name: p.type for p in op.params}
    assert param_types["priority"] == "string"


def test_map_field_produces_object_param() -> None:
    """map<K,V> fields are represented as 'object' params (lines 504-505)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package maptest;
service Svc {
  rpc Create(CreateReq) returns (CreateResp);
}
message CreateReq {
  string name = 1;
  map<string, string> metadata = 2;
}
message CreateResp { string id = 1; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))
    op = ir.operations[0]
    param_types = {p.name: p.type for p in op.params}
    assert param_types["metadata"] == "object"


def test_unknown_message_type_produces_object_param() -> None:
    """References to undefined message types fall back to 'object' (line 508)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package unktest;
service Svc {
  rpc Do(DoReq) returns (DoResp);
}
message DoReq {
  string id = 1;
  UnknownType extra = 2;
}
message DoResp { string ok = 1; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))
    op = ir.operations[0]
    param_types = {p.name: p.type for p in op.params}
    assert param_types["extra"] == "object"


# ---------------------------------------------------------------------------
# Field parsing edge cases (lines 311, 314, 323-326)
# ---------------------------------------------------------------------------


def test_required_and_repeated_field_labels() -> None:
    """required/repeated labels are parsed; repeated produces 'array' (lines 321-326)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto2";
package labels;
service Svc {
  rpc Do(Req) returns (Resp);
}
message Req {
  required string name = 1;
  repeated string tags = 2;
  optional int32 count = 3;
}
message Resp { string ok = 1; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))
    op = ir.operations[0]
    param_map = {p.name: p for p in op.params}
    assert param_map["name"].required is True
    assert param_map["tags"].type == "array"
    assert param_map["count"].required is False


def test_malformed_field_lines_are_skipped() -> None:
    """Lines with bad token structure are gracefully skipped (lines 311, 314, 326)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package skip;
service Svc {
  rpc Do(Req) returns (Resp);
}
message Req {
  string name = 1;
  = 2;
  badonly;
}
message Resp { string ok = 1; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))
    op = ir.operations[0]
    assert len(op.params) == 1
    assert op.params[0].name == "name"


# ---------------------------------------------------------------------------
# Missing message type for RPC request/response
# ---------------------------------------------------------------------------


def test_missing_request_message_produces_no_params() -> None:
    """When request message is not defined, operation has empty params."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package miss;
service Svc {
  rpc Do(MissingReq) returns (MissingResp);
}
"""
    ir = extractor.extract(SourceConfig(file_content=content))
    op = ir.operations[0]
    assert op.params == []
    assert op.response_schema is None


# ---------------------------------------------------------------------------
# response_schema_for_fields with required fields (lines 479, 487)
# ---------------------------------------------------------------------------


def test_response_schema_includes_required_fields() -> None:
    """Response schema includes 'required' key when fields have required=True (line 487)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto2";
package resp;
service Svc {
  rpc Get(Req) returns (Resp);
}
message Req { required string id = 1; }
message Resp { required string value = 1; optional string extra = 2; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))
    op = ir.operations[0]
    assert op.response_schema is not None
    assert "required" in op.response_schema
    assert "value" in op.response_schema["required"]


# ---------------------------------------------------------------------------
# Risk classification for Delete-prefixed RPCs (line 523)
# ---------------------------------------------------------------------------


def test_delete_rpc_risk_is_dangerous() -> None:
    """RPCs starting with 'Delete' are classified as dangerous (line 523)."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package risk;
service Svc {
  rpc DeleteItem(Req) returns (Resp);
}
message Req { string id = 1; }
message Resp { string ok = 1; }
"""
    ir = extractor.extract(SourceConfig(file_content=content))
    op = ir.operations[0]
    assert op.risk.risk_level is RiskLevel.dangerous
    assert op.risk.destructive is True


# ---------------------------------------------------------------------------
# _extract_pattern_value default fallback (line 546)
# ---------------------------------------------------------------------------


def test_extract_defaults_when_pattern_not_found() -> None:
    """Missing syntax/package declarations use defaults (line 546)."""
    extractor = GrpcProtoExtractor()
    content = """\
service Bare {
  rpc Do(Req) returns (Resp);
}
message Req { string id = 1; }
message Resp { string ok = 1; }
"""
    # detect should give 0.6 (no syntax match, but has service and rpc keywords)
    assert extractor.detect(SourceConfig(file_content=content)) == 0.6
    ir = extractor.extract(SourceConfig(file_content=content))
    assert ir.metadata["proto_syntax"] == "proto3"  # default
    assert ir.metadata["proto_package"] == ""  # default


# ---------------------------------------------------------------------------
# Server-stream native operation (lines 505, 508 in _stream_rpc_to_operation)
# ---------------------------------------------------------------------------


def test_native_server_stream_with_enum_and_map_fields() -> None:
    """Supported native server-stream produces Operation with enum/map param types."""
    extractor = GrpcProtoExtractor()
    content = """\
syntax = "proto3";
package streamfields;
service Svc {
  rpc Watch(WatchReq) returns (stream Event);
}
enum Level { LOW = 0; HIGH = 1; }
message WatchReq {
  string id = 1;
  Level level = 2;
  map<string, string> filters = 3;
}
message Event { string data = 1; }
"""
    ir = extractor.extract(
        SourceConfig(file_content=content, hints={"enable_native_grpc_stream": "true"})
    )
    op = next(o for o in ir.operations if o.id == "Watch")
    param_types = {p.name: p.type for p in op.params}
    assert param_types["level"] == "string"
    assert param_types["filters"] == "object"
