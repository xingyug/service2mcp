"""Focused tests for the live LLM-enabled proof runner helpers."""

from __future__ import annotations

import json

import pytest
from mcp.types import TextContent

from apps.proof_runner.live_llm_e2e import (
    ToolInvocationResult,
    ToolInvocationSpec,
    _audit_generated_tools,
    _count_llm_fields,
    _json_safe,
    _parse_sse_events,
    _rewrite_wsdl_endpoint,
    _strip_descriptions,
)
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.validator.audit import AuditPolicy


def test_parse_sse_events_extracts_event_payloads() -> None:
    enhance_event = json.dumps(
        {
            "stage": "enhance",
            "event_type": "stage.succeeded",
            "detail": {"operations_enhanced": 2},
        },
        separators=(",", ":"),
    )
    payload = (
        "event: stage.succeeded\n"
        f"data: {enhance_event}\n"
        "\n"
        "event: job.succeeded\n"
        'data: {"event_type":"job.succeeded"}\n'
        "\n"
    )

    events = _parse_sse_events(payload)

    assert events == [
        {
            "event": "stage.succeeded",
            "data": {
                "stage": "enhance",
                "event_type": "stage.succeeded",
                "detail": {"operations_enhanced": 2},
            },
        },
        {
            "event": "job.succeeded",
            "data": {"event_type": "job.succeeded"},
        },
    ]


def test_count_llm_fields_counts_operation_and_param_sources() -> None:
    ir_json = {
        "operations": [
            {
                "id": "searchProducts",
                "source": "llm",
                "params": [
                    {"name": "term", "source": "llm"},
                    {"name": "limit", "source": "extractor"},
                ],
            },
            {
                "id": "adjustInventory",
                "source": "extractor",
                "params": [{"name": "sku", "source": "llm"}],
            },
        ]
    }

    assert _count_llm_fields(ir_json) == 3


def test_strip_descriptions_recursively_clears_graphql_descriptions() -> None:
    payload = {
        "description": "Catalog",
        "types": [
            {
                "name": "Query",
                "description": "Root query",
                "fields": [{"name": "searchProducts", "description": "Search"}],
            }
        ],
    }

    stripped = _strip_descriptions(payload)

    assert stripped["description"] == ""
    assert stripped["types"][0]["description"] == ""
    assert stripped["types"][0]["fields"][0]["description"] == ""


def test_rewrite_wsdl_endpoint_updates_first_soap_address() -> None:
    wsdl = '<soap:address location="https://orders.example.com/soap/order-service" />'

    rewritten = _rewrite_wsdl_endpoint(wsdl, "http://proof-http/soap/order-service")

    assert rewritten == '<soap:address location="http://proof-http/soap/order-service" />'


def test_json_safe_serializes_mcp_sdk_objects() -> None:
    payload = {
        "status": "error",
        "error": [TextContent(type="text", text="boom")],
    }

    serialized = _json_safe(payload)

    assert json.loads(json.dumps(serialized)) == serialized
    assert serialized["error"][0]["type"] == "text"
    assert serialized["error"][0]["text"] == "boom"


def _build_audit_fixture_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol="rest",
        service_name="audit-fixture",
        service_description="Generated-tool audit fixture",
        base_url="https://audit.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="get_items_item_id",
                name="Get Item",
                description="Read a single item.",
                method="GET",
                path="/items/{item_id}",
                params=[Param(name="item_id", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
            Operation(
                id="stream_updates",
                name="Stream Updates",
                description="Stream inventory updates.",
                method="GET",
                path="/stream/updates",
                params=[],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
            Operation(
                id="adjust_inventory",
                name="Adjust Inventory",
                description="Mutate inventory.",
                method="POST",
                path="/inventory/adjust",
                params=[Param(name="sku", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=False,
                ),
                enabled=True,
            ),
            Operation(
                id="get_missing",
                name="Get Missing",
                description="Read an item missing from runtime tool listing.",
                method="GET",
                path="/missing",
                params=[],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
        ],
        event_descriptors=[
            EventDescriptor(
                id="stream_updates_descriptor",
                name="stream_updates_descriptor",
                transport=EventTransport.sse,
                support=EventSupportLevel.supported,
                operation_id="stream_updates",
                channel="/stream/updates",
            )
        ],
    )


@pytest.mark.asyncio
async def test_audit_generated_tools_reports_passed_failed_and_skipped() -> None:
    service_ir = _build_audit_fixture_ir()
    invoker_calls: list[tuple[str, dict[str, object]]] = []

    async def fake_invoker(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        invoker_calls.append((tool_name, arguments))
        if tool_name == "stream_updates":
            return {
                "status": "ok",
                "transport": "sse",
                "result": {
                    "events": [{"sku": "sku-123", "available": True}],
                    "lifecycle": {"closed": True},
                },
            }
        raise AssertionError(f"Unexpected tool invocation during audit: {tool_name}")

    audit_summary = await _audit_generated_tools(
        "http://runtime.example.test",
        service_ir,
        representative_invocations=(
            ToolInvocationSpec(
                tool_name="get_items_item_id",
                arguments={"item_id": "sku-123"},
            ),
        ),
        representative_results=[
            ToolInvocationResult(
                tool_name="get_items_item_id",
                result={"status": "ok", "result": {"item_id": "sku-123"}},
            )
        ],
        tool_invoker=fake_invoker,
        available_tool_names={
            "get_items_item_id",
            "stream_updates",
            "adjust_inventory",
        },
    )

    results_by_tool = {result.tool_name: result for result in audit_summary.results}

    assert audit_summary.discovered_operations == 4
    assert audit_summary.generated_tools == 3
    assert audit_summary.audited_tools == 3
    assert audit_summary.passed == 2
    assert audit_summary.failed == 1
    assert audit_summary.skipped == 1
    assert results_by_tool["get_items_item_id"].outcome == "passed"
    assert results_by_tool["get_items_item_id"].arguments == {"item_id": "sku-123"}
    assert results_by_tool["stream_updates"].outcome == "passed"
    assert results_by_tool["adjust_inventory"].outcome == "skipped"
    assert results_by_tool["adjust_inventory"].reason == "Skipped state-mutating tool by policy."
    assert results_by_tool["get_missing"].outcome == "failed"
    assert invoker_calls == [("stream_updates", {})]


@pytest.mark.asyncio
async def test_audit_with_allow_idempotent_writes_audits_safe_mutation() -> None:
    """When allow_idempotent_writes=True, an idempotent write-state tool is audited."""
    service_ir = _build_audit_fixture_ir()
    # Make adjust_inventory idempotent so the policy lets it through
    for operation in service_ir.operations:
        if operation.id == "adjust_inventory":
            object.__setattr__(operation.risk, "idempotent", True)

    async def fake_invoker(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        if tool_name == "adjust_inventory":
            return {"status": "ok", "result": {"adjusted": True}}
        if tool_name == "stream_updates":
            return {
                "status": "ok",
                "transport": "sse",
                "result": {
                    "events": [{"sku": "sku-123"}],
                    "lifecycle": {"closed": True},
                },
            }
        raise AssertionError(f"Unexpected invocation: {tool_name}")

    audit_summary = await _audit_generated_tools(
        "http://runtime.example.test",
        service_ir,
        representative_invocations=(
            ToolInvocationSpec(
                tool_name="get_items_item_id",
                arguments={"item_id": "sku-123"},
            ),
        ),
        representative_results=[
            ToolInvocationResult(
                tool_name="get_items_item_id",
                result={"status": "ok", "result": {"item_id": "sku-123"}},
            )
        ],
        tool_invoker=fake_invoker,
        available_tool_names={
            "get_items_item_id",
            "stream_updates",
            "adjust_inventory",
        },
        audit_policy=AuditPolicy(allow_idempotent_writes=True),
    )

    results_by_tool = {r.tool_name: r for r in audit_summary.results}
    assert results_by_tool["adjust_inventory"].outcome == "passed"
    assert audit_summary.passed == 3
    assert audit_summary.skipped == 0
