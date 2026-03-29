"""Run live cross-protocol LLM-enabled proof submissions against compiler-api."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

import httpx

from apps.compiler_worker.activities import (
    build_sample_invocations,
    build_streamable_http_tool_invoker,
)
from libs.ir.models import EventDescriptor, EventSupportLevel, ServiceIR, ToolIntent
from libs.validator.audit import AuditPolicy, ToolAuditResult, ToolAuditSummary
from libs.validator.llm_judge import JudgeEvaluation, LLMJudge

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
GRAPHQL_INTROSPECTION_PATH = FIXTURES_ROOT / "graphql_schemas" / "catalog_introspection.json"
GRPC_PROTO_PATH = FIXTURES_ROOT / "grpc_protos" / "inventory.proto"
SOAP_WSDL_PATH = FIXTURES_ROOT / "wsdl" / "order_service.wsdl"
_SOAP_ADDRESS_PATTERN = re.compile(r'location="[^"]+"')
_TERMINAL_JOB_STATUSES = {"succeeded", "failed", "rolled_back"}
_SUPPORTED_PROTOCOLS = (
    "graphql",
    "rest",
    "openapi",
    "grpc",
    "jsonrpc",
    "odata",
    "scim",
    "soap",
    "sql",
)
_SUPPORTED_PROFILES = ("mock", "real-targets")
ToolInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolInvocationSpec:
    """A single runtime tool call used to prove a compiled service works."""

    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProofCase:
    """End-to-end proof inputs for a single protocol."""

    protocol: str
    service_id: str
    request_payload: dict[str, Any]
    tool_invocations: tuple[ToolInvocationSpec, ...] = ()
    preferred_tool_ids: tuple[str, ...] = ()
    audit_skip_tool_ids: tuple[str, ...] = ()
    case_id: str | None = None


@dataclass(frozen=True)
class ToolInvocationResult:
    """Serialized result of a single runtime tool call."""

    tool_name: str
    result: dict[str, Any]


# ToolAuditResult and ToolAuditSummary are imported from libs.validator.audit


@dataclass(frozen=True)
class ToolIntentCounts:
    """Counts of tool_intent values found in compiled IR operations."""

    discovery: int
    action: int
    unset: int


@dataclass(frozen=True)
class ProofResult:
    """Summary emitted for a completed proof case."""

    protocol: str
    service_id: str
    job_id: str
    active_version: int
    operations_enhanced: int
    llm_field_count: int
    invocation_results: list[ToolInvocationResult]
    audit_summary: ToolAuditSummary | None = None
    tool_intent_counts: ToolIntentCounts | None = None
    judge_evaluation: JudgeEvaluation | None = None
    case_id: str | None = None
    error: str | None = None


async def run_proofs(
    *,
    namespace: str,
    api_base_url: str,
    protocol: str,
    profile: str = "mock",
    upstream_namespace: str | None = None,
    timeout_seconds: float,
    run_id: str,
    audit_all_generated_tools: bool = False,
    audit_policy: AuditPolicy | None = None,
    enable_llm_judge: bool = False,
    llm_judge: LLMJudge | None = None,
    selected_case_ids: set[str] | None = None,
    require_llm_artifacts: bool = True,
) -> list[ProofResult]:
    """Execute one or more live proof cases and return serialized results."""

    selected_protocols = list(_SUPPORTED_PROTOCOLS) if protocol == "all" else [protocol]
    cases = _build_proof_cases(
        namespace,
        run_id,
        profile=profile,
        upstream_namespace=upstream_namespace,
        selected_protocols=set(selected_protocols),
        selected_case_ids=selected_case_ids,
    )
    results: list[ProofResult] = []

    async with httpx.AsyncClient(base_url=api_base_url, timeout=30.0) as client:
        for case in cases:
            try:
                results.append(
                    await _run_case(
                        client,
                        case,
                        namespace=namespace,
                        timeout_seconds=timeout_seconds,
                        audit_all_generated_tools=audit_all_generated_tools,
                        audit_policy=audit_policy or AuditPolicy(),
                        enable_llm_judge=enable_llm_judge,
                        llm_judge=llm_judge,
                        require_llm_artifacts=require_llm_artifacts,
                    )
                )
            except Exception as exc:
                import logging

                logging.getLogger(__name__).error(
                    "Proof case %s (%s) failed: %s",
                    case.case_id or case.service_id,
                    case.protocol,
                    exc,
                )
                results.append(
                    ProofResult(
                        protocol=case.protocol,
                        service_id=case.service_id,
                        job_id="",
                        active_version=0,
                        operations_enhanced=0,
                        llm_field_count=0,
                        invocation_results=[],
                        case_id=case.case_id,
                        error=str(exc),
                    )
                )
    return results


def _build_proof_cases(
    namespace: str,
    run_id: str,
    *,
    profile: str = "mock",
    upstream_namespace: str | None = None,
    selected_protocols: set[str] | None = None,
    selected_case_ids: set[str] | None = None,
) -> list[ProofCase]:
    if profile == "mock":
        cases = _build_mock_proof_cases(namespace, run_id)
    elif profile == "real-targets":
        cases = _build_real_target_proof_cases(
            namespace,
            run_id,
            upstream_namespace=upstream_namespace or "tc-real-targets",
        )
    else:
        raise ValueError(f"Unsupported proof profile: {profile}")

    if selected_protocols is None:
        filtered_cases = cases
    else:
        filtered_cases = [case for case in cases if case.protocol in selected_protocols]

    if selected_case_ids is None:
        return filtered_cases
    return [
        case
        for case in filtered_cases
        if case.case_id is not None and case.case_id in selected_case_ids
    ]


def _build_mock_proof_cases(namespace: str, run_id: str) -> list[ProofCase]:
    http_base_url = _cluster_http_url(namespace, "llm-proof-http", 8080)
    grpc_base_url = _cluster_grpc_url(namespace, "llm-proof-grpc", 50051)
    sql_database_url = (
        f"postgresql://proofsql:proofsql@llm-proof-sql.{namespace}.svc.cluster.local:5432/proofsql"
    )

    graphql_service_id = f"graphql-llm-e2e-{run_id}"
    graphql_payload = json.loads(GRAPHQL_INTROSPECTION_PATH.read_text(encoding="utf-8"))
    graphql_source_content = json.dumps(_strip_descriptions(graphql_payload), indent=2)

    rest_service_id = f"rest-llm-e2e-{run_id}"

    grpc_service_id = f"grpc-llm-e2e-{run_id}"
    grpc_source_content = GRPC_PROTO_PATH.read_text(encoding="utf-8")

    soap_service_id = f"soap-llm-e2e-{run_id}"
    soap_source_content = _rewrite_wsdl_endpoint(
        SOAP_WSDL_PATH.read_text(encoding="utf-8"),
        f"{http_base_url}/soap/order-service",
    )

    sql_service_id = f"sql-llm-e2e-{run_id}"

    return [
        ProofCase(
            protocol="graphql",
            service_id=graphql_service_id,
            case_id="mock-graphql",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": graphql_service_id,
                "source_content": graphql_source_content,
                "options": {
                    "protocol": "graphql",
                    "hints": {
                        "service_name": graphql_service_id,
                        "base_url": http_base_url,
                        "graphql_path": "/graphql",
                    },
                },
            },
            tool_invocations=(
                ToolInvocationSpec(
                    tool_name="searchProducts",
                    arguments={"term": "puzzle", "limit": 1},
                ),
            ),
        ),
        ProofCase(
            protocol="rest",
            service_id=rest_service_id,
            case_id="mock-rest",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": rest_service_id,
                "source_url": f"{http_base_url}/rest/catalog",
                "options": {"protocol": "rest"},
            },
            tool_invocations=(
                ToolInvocationSpec(
                    tool_name="get_items_item_id",
                    arguments={"item_id": "sku-123"},
                ),
            ),
        ),
        ProofCase(
            protocol="grpc",
            service_id=grpc_service_id,
            case_id="mock-grpc",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": grpc_service_id,
                "source_url": grpc_base_url,
                "source_content": grpc_source_content,
                "options": {
                    "protocol": "grpc",
                    "hints": {"enable_native_grpc_stream": "true"},
                },
            },
            tool_invocations=(
                ToolInvocationSpec(
                    tool_name="ListItems",
                    arguments={"location_id": "warehouse-1", "page_size": 1},
                ),
                ToolInvocationSpec(
                    tool_name="WatchInventory",
                    arguments={"sku": "sku-live"},
                ),
            ),
        ),
        ProofCase(
            protocol="soap",
            service_id=soap_service_id,
            case_id="mock-soap",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": soap_service_id,
                "source_content": soap_source_content,
                "options": {"protocol": "soap"},
            },
            tool_invocations=(
                ToolInvocationSpec(
                    tool_name="GetOrderStatus",
                    arguments={"orderId": "ORD-100", "includeHistory": True},
                ),
            ),
        ),
        ProofCase(
            protocol="sql",
            service_id=sql_service_id,
            case_id="mock-sql",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": sql_service_id,
                "source_url": sql_database_url,
                "options": {"protocol": "sql", "hints": {"schema": "public"}},
            },
            tool_invocations=(
                ToolInvocationSpec(
                    tool_name="query_order_summaries",
                    arguments={"limit": 1},
                ),
            ),
        ),
    ]


def _build_real_target_proof_cases(
    namespace: str,
    run_id: str,
    *,
    upstream_namespace: str,
) -> list[ProofCase]:
    del namespace

    directus_token = _required_env("PROOF_DIRECTUS_ACCESS_TOKEN")
    pocketbase_token = _required_env("PROOF_POCKETBASE_ACCESS_TOKEN")
    gitea_basic_auth = _required_env("PROOF_GITEA_BASIC_AUTH")
    jackson_scim_base_url = _required_env("PROOF_JACKSON_SCIM_BASE_URL")
    jackson_scim_secret = _required_env("PROOF_JACKSON_SCIM_SECRET")
    gitea_basic_header = _basic_auth_header(gitea_basic_auth)

    directus_base_url = _cluster_http_url(upstream_namespace, "directus", 8055)
    gitea_base_url = _cluster_http_url(upstream_namespace, "gitea", 3000)
    pocketbase_base_url = _cluster_http_url(upstream_namespace, "pocketbase", 8090)
    northbreeze_base_url = _cluster_http_url(upstream_namespace, "northbreeze", 4004)
    soap_base_url = _cluster_http_url(upstream_namespace, "soap-cxf", 8080)
    openfga_grpc_base_url = _cluster_grpc_url(upstream_namespace, "openfga", 8081)
    sql_database_url = (
        "postgresql://catalog:catalog@"
        f"real-postgres.{upstream_namespace}.svc.cluster.local:5432/catalog_v2"
    )

    return [
        ProofCase(
            protocol="graphql",
            service_id=f"directus-graphql-{run_id}",
            case_id="directus-graphql",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"directus-graphql-{run_id}",
                "source_url": f"{directus_base_url}/graphql",
                "options": {
                    "protocol": "graphql",
                    "auth_token": directus_token,
                    "auth": {
                        "type": "bearer",
                        "runtime_secret_ref": "directus-access-token",
                    },
                },
            },
            preferred_tool_ids=("products", "customers", "orders"),
        ),
        ProofCase(
            protocol="rest",
            service_id=f"directus-rest-{run_id}",
            case_id="directus-rest",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"directus-rest-{run_id}",
                "source_url": f"{directus_base_url}/items/products",
                "options": {
                    "protocol": "rest",
                    "auth_token": directus_token,
                    "auth": {
                        "type": "bearer",
                        "runtime_secret_ref": "directus-access-token",
                    },
                },
            },
            preferred_tool_ids=("get_items_products", "get_items_products_id"),
        ),
        ProofCase(
            protocol="openapi",
            service_id=f"directus-openapi-{run_id}",
            case_id="directus-openapi",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"directus-openapi-{run_id}",
                "source_url": f"{directus_base_url}/server/specs/oas",
                "options": {
                    "protocol": "openapi",
                    "auth_token": directus_token,
                    "auth": {
                        "type": "bearer",
                        "runtime_secret_ref": "directus-access-token",
                    },
                    "preferred_smoke_tool_ids": [
                        "readItemsProducts",
                        "readItemsCustomers",
                        "readItemsOrders",
                    ],
                },
            },
            tool_invocations=(
                ToolInvocationSpec(
                    tool_name="readItemsProducts",
                    arguments={},
                ),
            ),
            audit_skip_tool_ids=("oauth",),
        ),
        ProofCase(
            protocol="openapi",
            service_id=f"gitea-openapi-{run_id}",
            case_id="gitea-openapi",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"gitea-openapi-{run_id}",
                "source_url": f"{gitea_base_url}/swagger.v1.json",
                "options": {
                    "protocol": "openapi",
                    "auth_header": gitea_basic_header,
                    "auth": {
                        "type": "basic",
                        "runtime_secret_ref": "gitea-basic-auth",
                    },
                    "preferred_smoke_tool_ids": [
                        "repoSearch",
                        "orgGetAll",
                        "listGitignoresTemplates",
                    ],
                },
            },
            preferred_tool_ids=("repoSearch", "orgGetAll", "listGitignoresTemplates"),
            audit_skip_tool_ids=("getNodeInfo",),
        ),
        ProofCase(
            protocol="jsonrpc",
            service_id=f"aria2-jsonrpc-{run_id}",
            case_id="aria2-jsonrpc",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"aria2-jsonrpc-{run_id}",
                "source_content": _aria2_manual_service_definition(upstream_namespace),
                "options": {"protocol": "jsonrpc"},
            },
            preferred_tool_ids=("aria2_getVersion", "aria2_getGlobalStat", "system_listMethods"),
        ),
        ProofCase(
            protocol="grpc",
            service_id=f"openfga-grpc-{run_id}",
            case_id="openfga-grpc",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"openfga-grpc-{run_id}",
                "source_url": openfga_grpc_base_url,
                "source_content": _openfga_minimal_service_definition(),
                "options": {"protocol": "grpc"},
            },
            preferred_tool_ids=("ListStores",),
        ),
        ProofCase(
            protocol="odata",
            service_id=f"northbreeze-odata-{run_id}",
            case_id="northbreeze-odata",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"northbreeze-odata-{run_id}",
                "source_url": f"{northbreeze_base_url}/odata/v4/northbreeze/$metadata",
                "options": {"protocol": "odata"},
            },
            preferred_tool_ids=("list_products", "func_get_top_products"),
        ),
        ProofCase(
            protocol="rest",
            service_id=f"pocketbase-rest-{run_id}",
            case_id="pocketbase-rest",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"pocketbase-rest-{run_id}",
                "source_url": f"{pocketbase_base_url}/api/collections/products/records",
                "options": {
                    "protocol": "rest",
                    "auth_token": pocketbase_token,
                    "auth": {
                        "type": "bearer",
                        "runtime_secret_ref": "pocketbase-access-token",
                    },
                },
            },
        ),
        ProofCase(
            protocol="scim",
            service_id=f"jackson-scim-{run_id}",
            case_id="jackson-scim",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"jackson-scim-{run_id}",
                "source_url": f"{jackson_scim_base_url}/Users",
                "options": {
                    "protocol": "scim",
                    "auth_token": jackson_scim_secret,
                    "auth": {
                        "type": "bearer",
                        "runtime_secret_ref": "jackson-scim-secret",
                    },
                    "preferred_smoke_tool_ids": ["list_users"],
                },
            },
            preferred_tool_ids=("list_users",),
        ),
        ProofCase(
            protocol="soap",
            service_id=f"soap-cxf-{run_id}",
            case_id="soap-cxf",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"soap-cxf-{run_id}",
                "source_url": f"{soap_base_url}/services/OrderService?wsdl",
                "options": {
                    "protocol": "soap",
                    "preferred_smoke_tool_ids": ["GetOrderStatus"],
                    "sample_invocation_overrides": {
                        "GetOrderStatus": {"orderId": "ORD-1001"},
                    },
                },
            },
            tool_invocations=(
                ToolInvocationSpec(
                    tool_name="GetOrderStatus",
                    arguments={"orderId": "ORD-1001"},
                ),
            ),
        ),
        ProofCase(
            protocol="sql",
            service_id=f"real-postgres-sql-{run_id}",
            case_id="real-postgres-sql",
            request_payload={
                "created_by": "llm-e2e",
                "service_name": f"real-postgres-sql-{run_id}",
                "source_url": sql_database_url,
                "options": {"protocol": "sql", "hints": {"schema": "public"}},
            },
        ),
    ]


async def _run_case(
    client: httpx.AsyncClient,
    case: ProofCase,
    *,
    namespace: str,
    timeout_seconds: float,
    audit_all_generated_tools: bool,
    audit_policy: AuditPolicy = AuditPolicy(),
    enable_llm_judge: bool = False,
    llm_judge: LLMJudge | None = None,
    require_llm_artifacts: bool = True,
) -> ProofResult:
    job = await _submit_compilation(client, case.request_payload)
    job_id = str(job["id"])
    final_job = await _wait_for_terminal_job(client, job_id, timeout_seconds=timeout_seconds)
    events = await _fetch_compilation_events(client, job_id)

    if final_job["status"] != "succeeded":
        raise RuntimeError(
            f"{case.protocol} proof job {job_id} ended with status {final_job['status']}: "
            f"{final_job.get('error_detail') or 'no error detail'}"
        )

    operations_enhanced = _operations_enhanced_from_events(events)
    if require_llm_artifacts and operations_enhanced <= 0:
        raise RuntimeError(
            f"{case.protocol} proof job {job_id} did not record any LLM enhancements."
        )

    active_version = await _active_version_for_service(client, case.service_id)
    artifact = await _artifact_version(client, case.service_id, active_version)
    artifact_ir = cast(dict[str, Any], artifact["ir_json"])
    llm_field_count = _count_llm_fields(artifact_ir)
    if require_llm_artifacts and llm_field_count <= 0:
        raise RuntimeError(
            f"{case.protocol} proof service {case.service_id} has no llm-sourced fields in IR."
        )
    service_ir = ServiceIR.model_validate(artifact_ir)
    invocation_specs = _resolve_invocation_specs(service_ir, case)

    # Verify tool_intent derivation in compiled IR.
    tool_intent_counts = _compute_tool_intent_counts(service_ir)

    runtime_base_url = _cluster_http_url(
        namespace,
        f"{case.service_id}-v{active_version}",
        8003,
    )
    invocation_results = await _invoke_runtime_tools(runtime_base_url, invocation_specs)
    audit_summary = (
        await _audit_generated_tools(
            runtime_base_url,
            service_ir,
            representative_invocations=invocation_specs,
            representative_results=invocation_results,
            audit_policy=audit_policy,
            forced_skip_tool_ids=case.audit_skip_tool_ids,
        )
        if audit_all_generated_tools
        else None
    )

    # Run LLM-as-a-Judge evaluation if enabled.
    judge_evaluation: JudgeEvaluation | None = None
    if enable_llm_judge and llm_judge is not None:
        try:
            judge_evaluation = llm_judge.evaluate(service_ir)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "LLM judge evaluation failed for %s; continuing without judge results",
                case.protocol,
                exc_info=True,
            )

    return ProofResult(
        protocol=case.protocol,
        service_id=case.service_id,
        job_id=job_id,
        active_version=active_version,
        operations_enhanced=operations_enhanced,
        llm_field_count=llm_field_count,
        invocation_results=invocation_results,
        audit_summary=audit_summary,
        tool_intent_counts=tool_intent_counts,
        judge_evaluation=judge_evaluation,
        case_id=case.case_id,
    )


async def _submit_compilation(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post("/api/v1/compilations", json=payload)
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


async def _wait_for_terminal_job(
    client: httpx.AsyncClient,
    job_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        response = await client.get(f"/api/v1/compilations/{job_id}")
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        if payload["status"] in _TERMINAL_JOB_STATUSES:
            return payload
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Timed out waiting for compilation job {job_id}.")
        await asyncio.sleep(2.0)


async def _fetch_compilation_events(
    client: httpx.AsyncClient,
    job_id: str,
) -> list[dict[str, Any]]:
    response = await client.get(f"/api/v1/compilations/{job_id}/events")
    response.raise_for_status()
    return _parse_sse_events(response.text)


def _parse_sse_events(payload: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event: dict[str, Any] = {}

    for line in payload.splitlines():
        if not line.strip():
            if current_event:
                events.append(current_event)
                current_event = {}
            continue
        if line.startswith("event:"):
            current_event["event"] = line.partition(":")[2].strip()
            continue
        if line.startswith("data:"):
            raw_data = line.partition(":")[2].strip()
            try:
                current_event["data"] = json.loads(raw_data)
            except json.JSONDecodeError:
                current_event["data"] = raw_data

    if current_event:
        events.append(current_event)
    return events


def _operations_enhanced_from_events(events: list[dict[str, Any]]) -> int:
    for event in events:
        payload = event.get("data")
        if not isinstance(payload, dict):
            continue
        if payload.get("stage") != "enhance":
            continue
        if payload.get("event_type") != "stage.succeeded":
            continue
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return int(detail.get("operations_enhanced", 0) or 0)
    return 0


async def _active_version_for_service(client: httpx.AsyncClient, service_id: str) -> int:
    response = await client.get("/api/v1/services")
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    for service in payload.get("services", []):
        if isinstance(service, dict) and service.get("service_id") == service_id:
            return int(service["active_version"])
    raise RuntimeError(f"Service {service_id} not found in service catalog.")


async def _artifact_version(
    client: httpx.AsyncClient,
    service_id: str,
    version_number: int,
) -> dict[str, Any]:
    response = await client.get(f"/api/v1/artifacts/{service_id}/versions/{version_number}")
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


async def _invoke_runtime_tools(
    runtime_base_url: str,
    invocation_specs: tuple[ToolInvocationSpec, ...],
) -> list[ToolInvocationResult]:
    invoker = build_streamable_http_tool_invoker(runtime_base_url)
    results: list[ToolInvocationResult] = []
    for spec in invocation_specs:
        result = _json_safe(await invoker(spec.tool_name, spec.arguments))
        results.append(ToolInvocationResult(tool_name=spec.tool_name, result=result))
    return results


def _resolve_invocation_specs(
    service_ir: ServiceIR,
    case: ProofCase,
) -> tuple[ToolInvocationSpec, ...]:
    if case.tool_invocations:
        return case.tool_invocations

    sample_invocations = build_sample_invocations(service_ir)
    operation_by_id = {
        operation.id: operation
        for operation in service_ir.operations
        if operation.enabled and operation.id in sample_invocations
    }

    for preferred_tool_id in case.preferred_tool_ids:
        arguments = sample_invocations.get(preferred_tool_id)
        if arguments is None:
            continue
        return (ToolInvocationSpec(tool_name=preferred_tool_id, arguments=arguments),)

    safe_candidates = [
        operation.id
        for operation in sorted(operation_by_id.values(), key=lambda operation: operation.id)
        if not operation.risk.writes_state and not operation.risk.destructive
    ]
    for tool_name in safe_candidates:
        return (ToolInvocationSpec(tool_name=tool_name, arguments=sample_invocations[tool_name]),)

    for tool_name in sorted(operation_by_id):
        return (ToolInvocationSpec(tool_name=tool_name, arguments=sample_invocations[tool_name]),)

    raise RuntimeError(
        f"{case.case_id or case.protocol} proof case produced no invocable runtime tool samples."
    )


async def _audit_generated_tools(
    runtime_base_url: str,
    service_ir: ServiceIR,
    *,
    representative_invocations: tuple[ToolInvocationSpec, ...],
    representative_results: list[ToolInvocationResult],
    tool_invoker: ToolInvoker | None = None,
    available_tool_names: set[str] | None = None,
    audit_policy: AuditPolicy = AuditPolicy(),
    forced_skip_tool_ids: tuple[str, ...] = (),
) -> ToolAuditSummary:
    runtime_tool_names = (
        available_tool_names
        if available_tool_names is not None
        else await _fetch_runtime_tool_names(runtime_base_url)
    )
    sample_invocations = build_sample_invocations(service_ir)
    for spec in representative_invocations:
        sample_invocations[spec.tool_name] = spec.arguments

    cached_results = {
        invocation_result.tool_name: invocation_result.result
        for invocation_result in representative_results
    }
    invoker = tool_invoker or build_streamable_http_tool_invoker(runtime_base_url)
    enabled_operations = sorted(
        (operation for operation in service_ir.operations if operation.enabled),
        key=lambda operation: operation.id,
    )
    forced_skip_tool_id_set = set(forced_skip_tool_ids)
    audit_results: list[ToolAuditResult] = []

    for operation in enabled_operations:
        if operation.id not in runtime_tool_names:
            audit_results.append(
                ToolAuditResult(
                    tool_name=operation.id,
                    outcome="failed",
                    reason="Runtime /tools listing does not expose this generated tool.",
                )
            )
            continue

        if operation.id in forced_skip_tool_id_set:
            audit_results.append(
                ToolAuditResult(
                    tool_name=operation.id,
                    outcome="skipped",
                    reason=(
                        "Skipped by proof-case policy because this endpoint is "
                        "disabled in the target deployment."
                    ),
                )
            )
            continue

        skip_reason = audit_policy.skip_reason(operation, sample_invocations)
        if skip_reason is not None:
            audit_results.append(
                ToolAuditResult(
                    tool_name=operation.id,
                    outcome="skipped",
                    reason=skip_reason,
                )
            )
            continue

        arguments = sample_invocations[operation.id]
        result = cached_results.get(operation.id)
        if result is None:
            try:
                result = _json_safe(await invoker(operation.id, arguments))
            except Exception as exc:
                failure_skip_reason = audit_policy.failure_skip_reason(operation, arguments)
                if failure_skip_reason is not None:
                    audit_results.append(
                        ToolAuditResult(
                            tool_name=operation.id,
                            outcome="skipped",
                            reason=failure_skip_reason,
                            arguments=arguments,
                        )
                    )
                    continue
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="failed",
                        reason=f"Invocation raised: {exc}",
                        arguments=arguments,
                    )
                )
                continue

        failure_reason = _generated_tool_audit_failure_reason(service_ir, operation.id, result)
        if failure_reason is not None:
            failure_skip_reason = audit_policy.failure_skip_reason(operation, arguments)
            if failure_skip_reason is not None:
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="skipped",
                        reason=failure_skip_reason,
                        arguments=arguments,
                        result=result,
                    )
                )
                continue
            audit_results.append(
                ToolAuditResult(
                    tool_name=operation.id,
                    outcome="failed",
                    reason=failure_reason,
                    arguments=arguments,
                    result=result,
                )
            )
            continue

        audit_results.append(
            ToolAuditResult(
                tool_name=operation.id,
                outcome="passed",
                reason="Invocation succeeded.",
                arguments=arguments,
                result=result,
            )
        )

    passed = sum(result.outcome == "passed" for result in audit_results)
    failed = sum(result.outcome == "failed" for result in audit_results)
    skipped = sum(result.outcome == "skipped" for result in audit_results)
    return ToolAuditSummary(
        discovered_operations=len(enabled_operations),
        generated_tools=len(runtime_tool_names),
        audited_tools=passed + failed,
        passed=passed,
        failed=failed,
        skipped=skipped,
        results=audit_results,
    )


async def _fetch_runtime_tool_names(runtime_base_url: str) -> set[str]:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(f"{runtime_base_url.rstrip('/')}/tools")
        response.raise_for_status()
        payload = response.json()
    return {
        tool["name"]
        for tool in payload.get("tools", [])
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }


# _generated_tool_audit_skip_reason is now replaced by AuditPolicy.skip_reason()


def _generated_tool_audit_failure_reason(
    expected_ir: ServiceIR,
    operation_id: str,
    result: dict[str, Any],
) -> str | None:
    status = result.get("status")
    if status != "ok":
        return f"Invocation returned unexpected status: {status!r}."

    descriptor = _supported_descriptor_for_operation(expected_ir, operation_id)
    if descriptor is None:
        return None

    transport = result.get("transport")
    if transport != descriptor.transport.value:
        return (
            f"Invocation returned transport {transport!r}, expected {descriptor.transport.value!r}."
        )

    stream_result = result.get("result")
    if not isinstance(stream_result, dict):
        return "Invocation returned a non-object stream payload."

    events = stream_result.get("events")
    lifecycle = stream_result.get("lifecycle")
    if not isinstance(events, list) or not isinstance(lifecycle, dict):
        return "Invocation did not return the expected streaming lifecycle structure."
    return None


def _supported_descriptor_for_operation(
    expected_ir: ServiceIR,
    operation_id: str,
) -> EventDescriptor | None:
    descriptors = [
        descriptor
        for descriptor in expected_ir.event_descriptors
        if descriptor.operation_id == operation_id
        and descriptor.support is EventSupportLevel.supported
    ]
    if not descriptors:
        return None
    if len(descriptors) > 1:
        raise ValueError(
            f"Generated-tool audit does not support multiple descriptors for {operation_id}."
        )
    return descriptors[0]


def _compute_tool_intent_counts(service_ir: ServiceIR) -> ToolIntentCounts:
    """Count tool_intent values across all enabled operations in a compiled IR."""
    discovery = 0
    action = 0
    unset = 0
    for op in service_ir.operations:
        if not op.enabled:
            continue
        if op.tool_intent is None:
            unset += 1
        elif op.tool_intent == ToolIntent.discovery:
            discovery += 1
        elif op.tool_intent == ToolIntent.action:
            action += 1
        else:
            unset += 1
    return ToolIntentCounts(discovery=discovery, action=action, unset=unset)


def _count_llm_fields(ir_json: dict[str, Any]) -> int:
    count = 0
    for operation in ir_json.get("operations", []):
        if isinstance(operation, dict) and operation.get("source") == "llm":
            count += 1
        if not isinstance(operation, dict):
            continue
        for param in operation.get("params", []):
            if isinstance(param, dict) and param.get("source") == "llm":
                count += 1
    return count


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe(model_dump(mode="json"))
        except TypeError:
            return _json_safe(model_dump())

    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def _strip_descriptions(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("" if key == "description" else _strip_descriptions(nested))
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_strip_descriptions(item) for item in value]
    return value


def _rewrite_wsdl_endpoint(content: str, endpoint: str) -> str:
    return _SOAP_ADDRESS_PATTERN.sub(f'location="{endpoint}"', content, count=1)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise RuntimeError(f"Required environment variable {name} is not set.")


def _basic_auth_header(secret: str) -> str:
    encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _aria2_manual_service_definition(namespace: str) -> str:
    endpoint = _cluster_http_url(namespace, "aria2", 6800) + "/jsonrpc"
    return json.dumps(
        {
            "jsonrpc_service": True,
            "info": {
                "title": "aria2 JSON-RPC",
                "description": "Real aria2 download service for live proof.",
                "version": "2.0.0",
            },
            "endpoint": endpoint,
            "methods": [
                {
                    "name": "system.listMethods",
                    "description": "List supported aria2 JSON-RPC methods.",
                    "params_type": "positional",
                    "params": [
                        {
                            "name": "token",
                            "required": True,
                            "default": "token:test-secret",
                            "schema": {"type": "string"},
                        }
                    ],
                },
                {
                    "name": "aria2.getVersion",
                    "description": "Return aria2 version metadata.",
                    "params_type": "positional",
                    "params": [
                        {
                            "name": "token",
                            "required": True,
                            "default": "token:test-secret",
                            "schema": {"type": "string"},
                        }
                    ],
                },
                {
                    "name": "aria2.getGlobalStat",
                    "description": "Return aggregate aria2 download state.",
                    "params_type": "positional",
                    "params": [
                        {
                            "name": "token",
                            "required": True,
                            "default": "token:test-secret",
                            "schema": {"type": "string"},
                        }
                    ],
                },
            ],
        }
    )


def _openfga_minimal_service_definition() -> str:
    return """syntax = "proto3";

package openfga.v1;

message ListStoresRequest {}

message ListStoresResponse {}

service OpenFGAService {
  rpc ListStores(ListStoresRequest) returns (ListStoresResponse);
}
"""


def _cluster_http_url(namespace: str, service_name: str, port: int) -> str:
    return f"http://{service_name}.{namespace}.svc.cluster.local:{port}"


def _cluster_grpc_url(namespace: str, service_name: str, port: int) -> str:
    return f"grpc://{service_name}.{namespace}.svc.cluster.local:{port}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--profile", choices=_SUPPORTED_PROFILES, default="mock")
    parser.add_argument("--upstream-namespace")
    parser.add_argument(
        "--protocol",
        choices=("all",) + _SUPPORTED_PROTOCOLS,
        default="all",
    )
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--run-id", default=uuid.uuid4().hex[:6])
    parser.add_argument("--audit-all-generated-tools", action="store_true")
    parser.add_argument("--enable-llm-judge", action="store_true")
    parser.add_argument("--case-id", action="append", dest="case_ids", default=[])
    parser.add_argument(
        "--skip-llm-artifact-checks",
        action="store_true",
        help="Do not require enhancement-stage counts or llm-sourced IR fields.",
    )
    return parser.parse_args()


def _build_llm_judge_from_env() -> LLMJudge | None:
    """Build an LLM judge using the same provider config as the compiler worker."""
    try:
        from libs.enhancer.enhancer import EnhancerConfig, create_llm_client

        config = EnhancerConfig.from_env()
        client = create_llm_client(config)
        return LLMJudge(client)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Could not build LLM judge from env config; judge evaluation disabled",
            exc_info=True,
        )
        return None


async def _async_main() -> None:
    args = _parse_args()
    selected_case_ids = {case_id for case_id in args.case_ids if case_id} or None
    judge: LLMJudge | None = None
    if args.enable_llm_judge:
        judge = _build_llm_judge_from_env()
    results = await run_proofs(
        namespace=args.namespace,
        api_base_url=args.api_base_url,
        protocol=cast(
            Literal[
                "all",
                "graphql",
                "rest",
                "openapi",
                "grpc",
                "jsonrpc",
                "odata",
                "scim",
                "soap",
                "sql",
            ],
            args.protocol,
        ),
        profile=cast(Literal["mock", "real-targets"], args.profile),
        upstream_namespace=cast(str | None, args.upstream_namespace),
        timeout_seconds=float(args.timeout_seconds),
        run_id=str(args.run_id),
        audit_all_generated_tools=bool(args.audit_all_generated_tools),
        enable_llm_judge=bool(args.enable_llm_judge),
        llm_judge=judge,
        selected_case_ids=selected_case_ids,
        require_llm_artifacts=not bool(args.skip_llm_artifact_checks),
    )
    print(
        json.dumps(
            _json_safe([asdict(result) for result in results]),
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
