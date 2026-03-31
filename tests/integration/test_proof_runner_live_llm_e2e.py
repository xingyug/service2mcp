"""Focused tests for the live LLM-enabled proof runner helpers."""

from __future__ import annotations

import json
from argparse import Namespace

import httpx
import pytest
from mcp.types import TextContent
from pydantic import ValidationError

import apps.proof_runner.live_llm_e2e as live_llm_e2e
from apps.proof_runner.live_llm_e2e import (
    ProofCase,
    ProofResult,
    ToolInvocationResult,
    ToolInvocationSpec,
    _async_main,
    _audit_generated_tools,
    _build_proof_cases,
    _count_llm_fields,
    _json_safe,
    _parse_sse_events,
    _resolve_invocation_specs,
    _rewrite_wsdl_endpoint,
    _run_case,
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
from libs.sample_placeholders import PATH_PLACEHOLDER_ID_SAMPLE
from libs.validator.audit import AuditPolicy, ToolAuditResult, ToolAuditSummary


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
                    "lifecycle": {
                        "closed": True,
                        "termination_reason": "completed",
                        "events_collected": 1,
                        "max_events": 10,
                        "idle_timeout_seconds": 5.0,
                    },
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
    assert audit_summary.generated_tools == 4
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
async def test_audit_generated_tools_fails_unexpected_extra_runtime_tool() -> None:
    service_ir = ServiceIR(
        service_id="svc-1",
        service_name="fixture",
        service_description="fixture",
        base_url="http://runtime.example.test",
        source_hash="sha256:test",
        protocol="openapi",
        operations=[
            Operation(
                id="get_status",
                operation_id="get_status",
                name="get_status",
                description="Get status",
                method="GET",
                path="/status",
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
    )

    audit_summary = await _audit_generated_tools(
        "http://runtime.example.test",
        service_ir,
        representative_invocations=(ToolInvocationSpec(tool_name="get_status", arguments={}),),
        representative_results=[
            ToolInvocationResult(
                tool_name="get_status",
                result={"status": "ok", "result": {"healthy": True}},
            )
        ],
        available_tool_names={"get_status", "shadow_tool"},
    )

    results_by_tool = {result.tool_name: result for result in audit_summary.results}

    assert audit_summary.generated_tools == 1
    assert audit_summary.audited_tools == 2
    assert audit_summary.passed == 1
    assert audit_summary.failed == 1
    assert results_by_tool["shadow_tool"].outcome == "failed"
    assert "unexpected tool" in results_by_tool["shadow_tool"].reason


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
                    "lifecycle": {
                        "closed": True,
                        "termination_reason": "completed",
                        "events_collected": 1,
                        "max_events": 10,
                        "idle_timeout_seconds": 5.0,
                    },
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


def _build_single_placeholder_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="b" * 64,
        protocol="openapi",
        service_name="placeholder-fixture",
        service_description="Proof placeholder fixture",
        base_url="https://placeholder.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="get_user",
                name="Get User",
                description="Read a user by id.",
                method="GET",
                path="/users/{id}",
                params=[Param(name="id", type="string", required=True)],
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
            )
        ],
    )


def _build_destructive_only_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="c" * 64,
        protocol="openapi",
        service_name="destructive-proof-fixture",
        service_description="Proof destructive fixture",
        base_url="https://destructive.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="delete_user",
                name="Delete User",
                description="Delete a user.",
                method="DELETE",
                path="/users/1",
                params=[],
                risk=RiskMetadata(
                    risk_level=RiskLevel.dangerous,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=True,
                    external_side_effect=False,
                    idempotent=False,
                ),
                enabled=True,
            )
        ],
    )


def _patch_successful_run_case_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    artifact_ir: ServiceIR,
) -> None:
    async def fake_submit(
        client: httpx.AsyncClient, payload: dict[str, object]
    ) -> dict[str, object]:
        return {"id": "job-1"}

    async def fake_wait(
        client: httpx.AsyncClient,
        job_id: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        return {"status": "succeeded"}

    async def fake_events(client: httpx.AsyncClient, job_id: str) -> list[dict[str, object]]:
        return [
            {
                "data": {
                    "stage": "enhance",
                    "event_type": "stage.succeeded",
                    "detail": {"operations_enhanced": 1},
                }
            }
        ]

    async def fake_active(
        client: httpx.AsyncClient,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> int:
        del tenant, environment
        return 1

    async def fake_artifact(
        client: httpx.AsyncClient,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> dict[str, object]:
        del tenant, environment
        return {"ir_json": artifact_ir.model_dump(mode="json")}

    monkeypatch.setattr(live_llm_e2e, "_submit_compilation", fake_submit)
    monkeypatch.setattr(live_llm_e2e, "_wait_for_terminal_job", fake_wait)
    monkeypatch.setattr(live_llm_e2e, "_fetch_compilation_events", fake_events)
    monkeypatch.setattr(live_llm_e2e, "_active_version_for_service", fake_active)
    monkeypatch.setattr(live_llm_e2e, "_artifact_version", fake_artifact)


@pytest.mark.asyncio
async def test_run_case_validates_artifact_before_llm_field_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_submit(
        client: httpx.AsyncClient, payload: dict[str, object]
    ) -> dict[str, object]:
        return {"id": "job-1"}

    async def fake_wait(
        client: httpx.AsyncClient,
        job_id: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        return {"status": "succeeded"}

    async def fake_events(client: httpx.AsyncClient, job_id: str) -> list[dict[str, object]]:
        return [
            {
                "data": {
                    "stage": "enhance",
                    "event_type": "stage.succeeded",
                    "detail": {"operations_enhanced": 1},
                }
            }
        ]

    async def fake_active(
        client: httpx.AsyncClient,
        service_id: str,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> int:
        del tenant, environment
        return 1

    async def fake_artifact(
        client: httpx.AsyncClient,
        service_id: str,
        version_number: int,
        *,
        tenant: str | None = None,
        environment: str | None = None,
    ) -> dict[str, object]:
        del tenant, environment
        return {
            "ir_json": {
                "service_name": "Demo",
                "base_url": "https://api.example.com",
                "protocol": "openapi",
                "operations": "oops",
            }
        }

    monkeypatch.setattr(live_llm_e2e, "_submit_compilation", fake_submit)
    monkeypatch.setattr(live_llm_e2e, "_wait_for_terminal_job", fake_wait)
    monkeypatch.setattr(live_llm_e2e, "_fetch_compilation_events", fake_events)
    monkeypatch.setattr(live_llm_e2e, "_active_version_for_service", fake_active)
    monkeypatch.setattr(live_llm_e2e, "_artifact_version", fake_artifact)

    case = ProofCase(
        protocol="openapi",
        service_id="svc-1",
        request_payload={
            "created_by": "test",
            "service_name": "svc-1",
            "source_url": "https://example.com/openapi.json",
        },
        case_id="case-1",
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(ValidationError) as exc_info:
            await _run_case(
                client,
                case,
                namespace="ns",
                timeout_seconds=5,
                audit_all_generated_tools=False,
                audit_policy=AuditPolicy(),
                require_llm_artifacts=True,
            )

    assert "source_hash" in str(exc_info.value)
    assert "no llm-sourced fields in IR" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_case_sets_error_when_representative_invocation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_run_case_dependencies(
        monkeypatch,
        artifact_ir=_build_audit_fixture_ir(),
    )

    async def fake_invoke(
        runtime_base_url: str,
        invocation_specs: tuple[ToolInvocationSpec, ...],
    ) -> list[ToolInvocationResult]:
        return [
            ToolInvocationResult(
                tool_name="get_items_item_id",
                result={"status": "error", "error": "boom"},
            )
        ]

    monkeypatch.setattr(live_llm_e2e, "_invoke_runtime_tools", fake_invoke)

    case = ProofCase(
        protocol="openapi",
        service_id="svc-1",
        request_payload={
            "created_by": "test",
            "service_name": "svc-1",
            "source_url": "https://example.com/openapi.json",
        },
        tool_invocations=(
            ToolInvocationSpec(
                tool_name="get_items_item_id",
                arguments={"item_id": "sku-123"},
            ),
        ),
        case_id="case-1",
    )

    async with httpx.AsyncClient() as client:
        result = await _run_case(
            client,
            case,
            namespace="ns",
            timeout_seconds=5,
            audit_all_generated_tools=False,
            audit_policy=AuditPolicy(),
            require_llm_artifacts=False,
        )

    assert result.error is not None
    assert "Representative runtime invocation failed" in result.error
    assert "get_items_item_id" in result.error
    assert result.audit_summary is None
    assert result.invocation_results == [
        ToolInvocationResult(
            tool_name="get_items_item_id",
            result={"status": "error", "error": "boom"},
        )
    ]


@pytest.mark.asyncio
async def test_run_case_sets_error_when_generated_tool_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_successful_run_case_dependencies(
        monkeypatch,
        artifact_ir=_build_audit_fixture_ir(),
    )

    async def fake_invoke(
        runtime_base_url: str,
        invocation_specs: tuple[ToolInvocationSpec, ...],
    ) -> list[ToolInvocationResult]:
        return [
            ToolInvocationResult(
                tool_name="get_items_item_id",
                result={"status": "ok", "result": {"item_id": "sku-123"}},
            )
        ]

    async def fake_audit(*args: object, **kwargs: object) -> ToolAuditSummary:
        return ToolAuditSummary(
            discovered_operations=4,
            generated_tools=4,
            audited_tools=1,
            passed=0,
            failed=1,
            skipped=0,
            results=[
                ToolAuditResult(
                    tool_name="stream_updates",
                    outcome="failed",
                    reason="Invocation returned unexpected status: 'error'.",
                )
            ],
        )

    monkeypatch.setattr(live_llm_e2e, "_invoke_runtime_tools", fake_invoke)
    monkeypatch.setattr(live_llm_e2e, "_audit_generated_tools", fake_audit)

    case = ProofCase(
        protocol="openapi",
        service_id="svc-1",
        request_payload={
            "created_by": "test",
            "service_name": "svc-1",
            "source_url": "https://example.com/openapi.json",
        },
        tool_invocations=(
            ToolInvocationSpec(
                tool_name="get_items_item_id",
                arguments={"item_id": "sku-123"},
            ),
        ),
        case_id="case-1",
    )

    async with httpx.AsyncClient() as client:
        result = await _run_case(
            client,
            case,
            namespace="ns",
            timeout_seconds=5,
            audit_all_generated_tools=True,
            audit_policy=AuditPolicy(),
            require_llm_artifacts=False,
        )

    assert result.error is not None
    assert "Generated-tool audit failed" in result.error
    assert "stream_updates" in result.error
    assert result.audit_summary is not None
    assert result.audit_summary.failed == 1


def test_build_proof_cases_rejects_unknown_selected_case_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        live_llm_e2e,
        "_build_mock_proof_cases",
        lambda namespace, run_id: [
            ProofCase(
                protocol="openapi",
                service_id="svc-1",
                request_payload={},
                case_id="known-case",
            )
        ],
    )

    with pytest.raises(ValueError, match="does-not-exist"):
        _build_proof_cases(
            "ns",
            "rid",
            profile="mock",
            selected_protocols={"openapi"},
            selected_case_ids={"does-not-exist"},
        )


@pytest.mark.asyncio
async def test_async_main_exits_nonzero_when_any_result_failed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run_proofs(**kwargs: object) -> list[ProofResult]:
        return [
            ProofResult(
                protocol="openapi",
                service_id="svc-1",
                job_id="job-1",
                active_version=1,
                operations_enhanced=1,
                llm_field_count=1,
                invocation_results=[],
                case_id="case-1",
                error="boom",
            )
        ]

    monkeypatch.setattr(
        live_llm_e2e,
        "_parse_args",
        lambda: Namespace(
            namespace="ns",
            api_base_url="http://example.test",
            protocol="openapi",
            profile="mock",
            upstream_namespace=None,
            timeout_seconds=5.0,
            run_id="rid",
            audit_all_generated_tools=False,
            audit_mutating_tools=False,
            enable_llm_judge=False,
            case_ids=[],
            skip_llm_artifact_checks=False,
        ),
    )
    monkeypatch.setattr(live_llm_e2e, "run_proofs", fake_run_proofs)

    with pytest.raises(SystemExit) as exc_info:
        await _async_main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert '"error": "boom"' in captured.out


def test_resolve_invocation_specs_rejects_placeholder_path_samples() -> None:
    case = ProofCase(
        protocol="openapi",
        service_id="svc-1",
        request_payload={},
        case_id="placeholder-case",
    )

    with pytest.raises(RuntimeError, match="synthetic placeholder path samples"):
        _resolve_invocation_specs(_build_single_placeholder_ir(), case)


def test_resolve_invocation_specs_rejects_destructive_only_candidates() -> None:
    case = ProofCase(
        protocol="openapi",
        service_id="svc-1",
        request_payload={},
        case_id="destructive-case",
    )

    with pytest.raises(RuntimeError, match="state-mutating or destructive runtime tool samples"):
        _resolve_invocation_specs(_build_destructive_only_ir(), case)


@pytest.mark.asyncio
async def test_audit_generated_tools_skips_default_placeholder_path_samples() -> None:
    invoker_calls: list[tuple[str, dict[str, object]]] = []

    async def fake_invoker(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        invoker_calls.append((tool_name, arguments))
        return {"status": "ok", "result": {"id": arguments["id"]}}

    audit_summary = await _audit_generated_tools(
        "http://runtime.example.test",
        _build_single_placeholder_ir(),
        representative_invocations=(),
        representative_results=[],
        tool_invoker=fake_invoker,
        available_tool_names={"get_user"},
    )

    assert audit_summary.passed == 0
    assert audit_summary.failed == 0
    assert audit_summary.skipped == 1
    assert invoker_calls == []
    assert len(audit_summary.results) == 1
    result = audit_summary.results[0]
    assert result.tool_name == "get_user"
    assert result.outcome == "skipped"
    assert (
        result.reason
        == "Skipped tool because path parameters still use synthetic placeholder samples."
    )
    assert result.arguments == {"id": PATH_PLACEHOLDER_ID_SAMPLE}
