"""Unit tests for apps/proof_runner/live_llm_e2e.py — pure helper functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.proof_runner.live_llm_e2e import (
    ToolIntentCounts,
    _cluster_grpc_url,
    _cluster_http_url,
    _compute_tool_intent_counts,
    _count_llm_fields,
    _json_safe,
    _operations_enhanced_from_events,
    _parse_sse_events,
    _rewrite_wsdl_endpoint,
    _strip_descriptions,
    _supported_descriptor_for_operation,
)
from libs.ir.models import (
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    ToolIntent,
)


def _risk(level: RiskLevel = RiskLevel.safe) -> RiskMetadata:
    return RiskMetadata(risk_level=level)


def _op(op_id: str = "test_op") -> Operation:
    return Operation(
        id=op_id,
        operation_id=op_id,
        name=op_id,
        description=f"Test {op_id}",
        method="GET",
        path=f"/{op_id}",
        risk=_risk(),
        enabled=True,
    )


def _ir(
    operations: list[Any] | None = None,
    event_descriptors: list[EventDescriptor] | None = None,
) -> ServiceIR:
    return ServiceIR(
        service_id="test-svc",
        service_name="Test",
        base_url="https://example.com",
        source_hash="sha256:abc",
        protocol="openapi",
        operations=operations or [],
        event_descriptors=event_descriptors or [],
    )


# --- _parse_sse_events ---


class TestParseSseEvents:
    def test_single_event(self) -> None:
        payload = 'event: message\ndata: {"key": "value"}\n\n'
        events = _parse_sse_events(payload)
        assert len(events) == 1
        assert events[0]["event"] == "message"
        assert events[0]["data"] == {"key": "value"}

    def test_multiple_events(self) -> None:
        payload = (
            'event: start\ndata: {"stage": "extract"}\n\nevent: end\ndata: {"stage": "deploy"}\n\n'
        )
        events = _parse_sse_events(payload)
        assert len(events) == 2
        assert events[0]["data"]["stage"] == "extract"
        assert events[1]["data"]["stage"] == "deploy"

    def test_trailing_event_no_newline(self) -> None:
        payload = 'event: done\ndata: {"ok": true}'
        events = _parse_sse_events(payload)
        assert len(events) == 1
        assert events[0]["data"]["ok"] is True

    def test_empty_payload(self) -> None:
        assert _parse_sse_events("") == []

    def test_blank_lines_only(self) -> None:
        assert _parse_sse_events("\n\n\n") == []

    def test_event_without_data(self) -> None:
        payload = "event: ping\n\n"
        events = _parse_sse_events(payload)
        assert len(events) == 1
        assert events[0]["event"] == "ping"
        assert "data" not in events[0]


# --- _operations_enhanced_from_events ---


class TestOperationsEnhancedFromEvents:
    def test_found(self) -> None:
        events: list[dict[str, Any]] = [
            {"data": {"stage": "extract", "event_type": "stage.succeeded"}},
            {
                "data": {
                    "stage": "enhance",
                    "event_type": "stage.succeeded",
                    "detail": {"operations_enhanced": 5},
                },
            },
        ]
        assert _operations_enhanced_from_events(events) == 5

    def test_not_found_returns_zero(self) -> None:
        events: list[dict[str, Any]] = [
            {"data": {"stage": "extract", "event_type": "stage.succeeded"}},
        ]
        assert _operations_enhanced_from_events(events) == 0

    def test_wrong_event_type(self) -> None:
        events: list[dict[str, Any]] = [
            {
                "data": {
                    "stage": "enhance",
                    "event_type": "stage.failed",
                    "detail": {"operations_enhanced": 3},
                },
            },
        ]
        assert _operations_enhanced_from_events(events) == 0

    def test_empty_events(self) -> None:
        assert _operations_enhanced_from_events([]) == 0

    def test_missing_detail(self) -> None:
        events: list[dict[str, Any]] = [
            {"data": {"stage": "enhance", "event_type": "stage.succeeded"}},
        ]
        assert _operations_enhanced_from_events(events) == 0

    def test_non_dict_data_skipped(self) -> None:
        events: list[dict[str, Any]] = [{"data": "not a dict"}]
        assert _operations_enhanced_from_events(events) == 0


# --- _count_llm_fields ---


class TestCountLlmFields:
    def test_operation_level(self) -> None:
        ir_json: dict[str, Any] = {
            "operations": [
                {"source": "llm", "params": []},
                {"source": "extractor", "params": []},
            ]
        }
        assert _count_llm_fields(ir_json) == 1

    def test_param_level(self) -> None:
        ir_json: dict[str, Any] = {
            "operations": [
                {
                    "source": "extractor",
                    "params": [
                        {"source": "llm"},
                        {"source": "extractor"},
                        {"source": "llm"},
                    ],
                }
            ]
        }
        assert _count_llm_fields(ir_json) == 2

    def test_both_levels(self) -> None:
        ir_json: dict[str, Any] = {
            "operations": [
                {
                    "source": "llm",
                    "params": [{"source": "llm"}, {"source": "extractor"}],
                }
            ]
        }
        assert _count_llm_fields(ir_json) == 2

    def test_empty(self) -> None:
        assert _count_llm_fields({}) == 0
        assert _count_llm_fields({"operations": []}) == 0

    def test_non_dict_operation_skipped(self) -> None:
        ir_json: dict[str, Any] = {"operations": ["not a dict"]}
        assert _count_llm_fields(ir_json) == 0


# --- _json_safe ---


class TestJsonSafe:
    def test_primitives(self) -> None:
        assert _json_safe(None) is None
        assert _json_safe("hello") == "hello"
        assert _json_safe(42) == 42
        assert _json_safe(3.14) == 3.14
        assert _json_safe(True) is True

    def test_dict(self) -> None:
        assert _json_safe({"key": "value"}) == {"key": "value"}

    def test_nested_dict(self) -> None:
        assert _json_safe({"a": {"b": 1}}) == {"a": {"b": 1}}

    def test_list(self) -> None:
        assert _json_safe([1, "two", 3]) == [1, "two", 3]

    def test_set_to_list(self) -> None:
        result = _json_safe({1, 2, 3})
        assert isinstance(result, list)
        assert sorted(result) == [1, 2, 3]

    def test_tuple_to_list(self) -> None:
        assert _json_safe((1, 2)) == [1, 2]

    def test_pydantic_model(self) -> None:
        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"field": "value"}
        result = _json_safe(mock_model)
        assert result == {"field": "value"}

    def test_object_with_dict(self) -> None:
        @dataclass
        class Simple:
            x: int
            y: str

        result = _json_safe(Simple(x=1, y="hello"))
        assert result == {"x": 1, "y": "hello"}

    def test_unknown_fallback_to_str(self) -> None:
        class Custom:
            __slots__: tuple[str, ...] = ()

            def __str__(self) -> str:
                return "custom-repr"

        result = _json_safe(Custom())
        assert result == "custom-repr"


# --- _strip_descriptions ---


class TestStripDescriptions:
    def test_dict_with_description(self) -> None:
        data = {"name": "test", "description": "long text", "value": 42}
        result = _strip_descriptions(data)
        assert result["description"] == ""
        assert result["name"] == "test"
        assert result["value"] == 42

    def test_nested_dict(self) -> None:
        data = {"op": {"name": "get", "description": "Fetches items"}}
        result = _strip_descriptions(data)
        assert result["op"]["description"] == ""

    def test_list_of_dicts(self) -> None:
        data = [{"description": "A"}, {"description": "B"}]
        result = _strip_descriptions(data)
        assert result == [{"description": ""}, {"description": ""}]

    def test_primitive(self) -> None:
        assert _strip_descriptions("hello") == "hello"
        assert _strip_descriptions(42) == 42


# --- _rewrite_wsdl_endpoint ---


class TestRewriteWsdlEndpoint:
    def test_rewrites_location(self) -> None:
        content = '<soap:address location="http://old.example.com/service"/>'
        result = _rewrite_wsdl_endpoint(content, "http://new.example.com/service")
        assert 'location="http://new.example.com/service"' in result
        assert "old.example.com" not in result

    def test_only_first_occurrence(self) -> None:
        content = (
            '<soap:address location="http://first.com"/>'
            '<soap:address location="http://second.com"/>'
        )
        result = _rewrite_wsdl_endpoint(content, "http://new.com")
        assert result.count('location="http://new.com"') == 1
        assert 'location="http://second.com"' in result

    def test_no_match(self) -> None:
        content = "<wsdl:service>no address here</wsdl:service>"
        result = _rewrite_wsdl_endpoint(content, "http://new.com")
        assert result == content


# --- Cluster URL helpers ---


class TestClusterUrls:
    def test_http_url(self) -> None:
        result = _cluster_http_url("my-ns", "my-service", 8080)
        assert result == "http://my-service.my-ns.svc.cluster.local:8080"

    def test_grpc_url(self) -> None:
        result = _cluster_grpc_url("my-ns", "grpc-service", 50051)
        assert result == "grpc://grpc-service.my-ns.svc.cluster.local:50051"


# --- _supported_descriptor_for_operation ---


class TestSupportedDescriptorForOperation:
    def test_found(self) -> None:
        descriptor = EventDescriptor(
            id="ed1",
            name="Stream event",
            operation_id="stream_op",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/pkg.Svc/Stream",
                mode=GrpcStreamMode.server,
            ),
        )
        ir = _ir(operations=[_op("stream_op")], event_descriptors=[descriptor])
        result = _supported_descriptor_for_operation(ir, "stream_op")
        assert result is not None
        assert result.operation_id == "stream_op"

    def test_not_found(self) -> None:
        ir = _ir()
        assert _supported_descriptor_for_operation(ir, "nonexistent") is None

    def test_unsupported_filtered(self) -> None:
        descriptor = EventDescriptor(
            id="ed1",
            name="Stream event",
            operation_id="stream_op",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.unsupported,
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/pkg.Svc/Stream",
                mode=GrpcStreamMode.server,
            ),
        )
        ir = _ir(operations=[_op("stream_op")], event_descriptors=[descriptor])
        assert _supported_descriptor_for_operation(ir, "stream_op") is None

    def test_multiple_supported_raises(self) -> None:
        descriptors = [
            EventDescriptor(
                id=f"ed{i}",
                name=f"Stream event {i}",
                operation_id="dup_op",
                transport=EventTransport.grpc_stream,
                support=EventSupportLevel.supported,
                grpc_stream=GrpcStreamRuntimeConfig(
                    rpc_path=f"/pkg.Svc/Stream{i}",
                    mode=GrpcStreamMode.server,
                ),
            )
            for i in range(2)
        ]
        ir = _ir(operations=[_op("dup_op")], event_descriptors=descriptors)
        with pytest.raises(ValueError, match="multiple descriptors"):
            _supported_descriptor_for_operation(ir, "dup_op")


# --- _compute_tool_intent_counts ---


class TestComputeToolIntentCounts:
    def test_empty_ir(self) -> None:
        ir = _ir(operations=[])
        counts = _compute_tool_intent_counts(ir)
        assert counts == ToolIntentCounts(discovery=0, action=0, unset=0)

    def test_all_discovery(self) -> None:
        ops = [
            Operation(
                id=f"op{i}",
                operation_id=f"op{i}",
                name=f"op{i}",
                description="test",
                method="GET",
                path=f"/op{i}",
                risk=_risk(),
                enabled=True,
                tool_intent=ToolIntent.discovery,
            )
            for i in range(3)
        ]
        ir = _ir(operations=ops)
        counts = _compute_tool_intent_counts(ir)
        assert counts.discovery == 3
        assert counts.action == 0
        assert counts.unset == 0

    def test_all_action(self) -> None:
        ops = [
            Operation(
                id=f"op{i}",
                operation_id=f"op{i}",
                name=f"op{i}",
                description="test",
                method="POST",
                path=f"/op{i}",
                risk=_risk(RiskLevel.cautious),
                enabled=True,
                tool_intent=ToolIntent.action,
            )
            for i in range(2)
        ]
        ir = _ir(operations=ops)
        counts = _compute_tool_intent_counts(ir)
        assert counts.discovery == 0
        assert counts.action == 2
        assert counts.unset == 0

    def test_mixed_intents(self) -> None:
        ops = [
            Operation(
                id="get_op",
                operation_id="get_op",
                name="get_op",
                description="test",
                method="GET",
                path="/get",
                risk=_risk(),
                enabled=True,
                tool_intent=ToolIntent.discovery,
            ),
            Operation(
                id="post_op",
                operation_id="post_op",
                name="post_op",
                description="test",
                method="POST",
                path="/post",
                risk=_risk(RiskLevel.cautious),
                enabled=True,
                tool_intent=ToolIntent.action,
            ),
            Operation(
                id="unset_op",
                operation_id="unset_op",
                name="unset_op",
                description="test",
                method="GET",
                path="/unset",
                risk=_risk(),
                enabled=True,
                tool_intent=None,
            ),
        ]
        ir = _ir(operations=ops)
        counts = _compute_tool_intent_counts(ir)
        assert counts.discovery == 1
        assert counts.action == 1
        assert counts.unset == 1

    def test_disabled_operations_excluded(self) -> None:
        ops = [
            Operation(
                id="enabled_op",
                operation_id="enabled_op",
                name="enabled_op",
                description="test",
                method="GET",
                path="/enabled",
                risk=_risk(),
                enabled=True,
                tool_intent=ToolIntent.discovery,
            ),
            Operation(
                id="disabled_op",
                operation_id="disabled_op",
                name="disabled_op",
                description="test",
                method="DELETE",
                path="/disabled",
                risk=RiskMetadata(risk_level=RiskLevel.unknown),
                enabled=False,
                tool_intent=ToolIntent.action,
            ),
        ]
        ir = _ir(operations=ops)
        counts = _compute_tool_intent_counts(ir)
        assert counts.discovery == 1
        assert counts.action == 0
        assert counts.unset == 0
