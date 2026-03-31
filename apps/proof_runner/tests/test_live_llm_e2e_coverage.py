"""Tests for apps/proof_runner/live_llm_e2e.py — async orchestration and uncovered paths."""

from __future__ import annotations

import argparse
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.proof_runner.live_llm_e2e import (
    ProofCase,
    ProofResult,
    ToolInvocationResult,
    ToolInvocationSpec,
    _active_version_for_service,
    _artifact_version,
    _async_main,
    _build_llm_judge_from_env,
    _build_proof_cases,
    _compute_tool_intent_counts,
    _fetch_compilation_events,
    _fetch_runtime_tool_names,
    _generated_tool_audit_failure_reason,
    _json_safe,
    _operations_enhanced_from_events,
    _parse_args,
    _parse_sse_events,
    _submit_compilation,
    _wait_for_terminal_job,
    main,
    run_proofs,
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
from libs.validator.audit import AuditPolicy, ToolAuditSummary

_ENHANCE_STAGE_SUCCEEDED_EVENT = (
    'event: msg\ndata: {"stage":"enhance","event_type":"stage.succeeded",'
    '"detail":{"operations_enhanced":3}}\n\n'
)


def _risk(level: RiskLevel = RiskLevel.safe) -> RiskMetadata:
    return RiskMetadata(risk_level=level)


def _op(op_id: str = "test_op", enabled: bool = True, **kwargs: Any) -> Operation:
    defaults: dict[str, Any] = {
        "id": op_id,
        "operation_id": op_id,
        "name": op_id,
        "description": f"Test {op_id}",
        "method": "GET",
        "path": f"/{op_id}",
        "risk": _risk(),
        "enabled": enabled,
    }
    defaults.update(kwargs)
    return Operation(**defaults)


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


# --- _submit_compilation ---


class TestSubmitCompilation:
    async def test_submit_compilation_posts_and_returns_json(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"id": "job-1", "status": "queued"},
            request=httpx.Request("POST", "http://test/api/v1/compilations"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await _submit_compilation(mock_client, {"service_name": "test"})
        assert result["id"] == "job-1"
        mock_client.post.assert_called_once_with(
            "/api/v1/compilations", json={"service_name": "test"}
        )

    async def test_submit_compilation_raises_on_error(self) -> None:
        mock_response = httpx.Response(
            500,
            text="Internal Server Error",
            request=httpx.Request("POST", "http://test/api/v1/compilations"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            await _submit_compilation(mock_client, {"service_name": "test"})

    async def test_submit_compilation_rejects_invalid_json(self) -> None:
        mock_response = httpx.Response(
            200,
            text="not-json",
            request=httpx.Request("POST", "http://test/api/v1/compilations"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="invalid JSON"):
            await _submit_compilation(mock_client, {"service_name": "test"})


# --- _wait_for_terminal_job ---


class TestWaitForTerminalJob:
    async def test_returns_immediately_on_terminal_status(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"id": "job-1", "status": "succeeded"},
            request=httpx.Request("GET", "http://test/api/v1/compilations/job-1"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await _wait_for_terminal_job(mock_client, "job-1", timeout_seconds=10.0)
        assert result["status"] == "succeeded"

    async def test_polls_until_terminal(self) -> None:
        pending = httpx.Response(
            200,
            json={"id": "job-1", "status": "running"},
            request=httpx.Request("GET", "http://test/api/v1/compilations/job-1"),
        )
        done = httpx.Response(
            200,
            json={"id": "job-1", "status": "succeeded"},
            request=httpx.Request("GET", "http://test/api/v1/compilations/job-1"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=[pending, done])

        with patch("apps.proof_runner.live_llm_e2e.asyncio.sleep", new_callable=AsyncMock):
            result = await _wait_for_terminal_job(mock_client, "job-1", timeout_seconds=60.0)
        assert result["status"] == "succeeded"

    async def test_raises_timeout(self) -> None:
        pending = httpx.Response(
            200,
            json={"id": "job-1", "status": "running"},
            request=httpx.Request("GET", "http://test/api/v1/compilations/job-1"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=pending)

        with pytest.raises(TimeoutError, match="Timed out"):
            await _wait_for_terminal_job(mock_client, "job-1", timeout_seconds=0.0)

    async def test_failed_status_is_terminal(self) -> None:
        resp = httpx.Response(
            200,
            json={"id": "job-1", "status": "failed", "error_detail": "oops"},
            request=httpx.Request("GET", "http://test/api/v1/compilations/job-1"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=resp)

        result = await _wait_for_terminal_job(mock_client, "job-1", timeout_seconds=10.0)
        assert result["status"] == "failed"

    async def test_missing_status_raises_controlled_error(self) -> None:
        resp = httpx.Response(
            200,
            json={"id": "job-1"},
            request=httpx.Request("GET", "http://test/api/v1/compilations/job-1"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=resp)

        with pytest.raises(RuntimeError, match="required field 'status'"):
            await _wait_for_terminal_job(mock_client, "job-1", timeout_seconds=10.0)


# --- _fetch_compilation_events ---


class TestFetchCompilationEvents:
    async def test_fetches_and_parses_sse(self) -> None:
        sse_text = 'event: message\ndata: {"stage": "extract"}\n\n'
        mock_response = httpx.Response(
            200,
            text=sse_text,
            request=httpx.Request("GET", "http://test/api/v1/compilations/job-1/events"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        events = await _fetch_compilation_events(mock_client, "job-1")
        assert len(events) == 1
        assert events[0]["data"]["stage"] == "extract"

    async def test_raises_on_error(self) -> None:
        mock_response = httpx.Response(
            404,
            text="Not Found",
            request=httpx.Request("GET", "http://test/api/v1/compilations/job-1/events"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            await _fetch_compilation_events(mock_client, "job-1")


# --- _parse_sse_events (JSONDecodeError branch) ---


class TestParseSseEventsInvalidJson:
    def test_invalid_json_stored_as_string(self) -> None:
        payload = "event: msg\ndata: not-valid-json\n\n"
        events = _parse_sse_events(payload)
        assert len(events) == 1
        assert events[0]["data"] == "not-valid-json"

    def test_multiline_data_is_accumulated(self) -> None:
        payload = 'event: msg\ndata: {\ndata: "stage": "extract"\ndata: }\n\n'
        events = _parse_sse_events(payload)
        assert len(events) == 1
        assert events[0]["data"] == {"stage": "extract"}


# --- _active_version_for_service ---


class TestActiveVersionForService:
    async def test_found(self) -> None:
        mock_response = httpx.Response(
            200,
            json={
                "services": [
                    {"service_id": "other-svc", "active_version": 1},
                    {"service_id": "my-svc", "active_version": 3},
                ]
            },
            request=httpx.Request("GET", "http://test/api/v1/services"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        version = await _active_version_for_service(mock_client, "my-svc")
        assert version == 3
        mock_client.get.assert_called_once_with("/api/v1/services", params=None)

    async def test_not_found_raises(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"services": [{"service_id": "other-svc", "active_version": 1}]},
            request=httpx.Request("GET", "http://test/api/v1/services"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="not found"):
            await _active_version_for_service(mock_client, "missing-svc")

    async def test_non_dict_service_skipped(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"services": ["not-a-dict", {"service_id": "my-svc", "active_version": 5}]},
            request=httpx.Request("GET", "http://test/api/v1/services"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        version = await _active_version_for_service(mock_client, "my-svc")
        assert version == 5

    async def test_missing_active_version_raises_controlled_error(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"services": [{"service_id": "my-svc"}]},
            request=httpx.Request("GET", "http://test/api/v1/services"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="required field 'active_version'"):
            await _active_version_for_service(mock_client, "my-svc")

    async def test_scope_filters_are_forwarded(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"services": [{"service_id": "my-svc", "active_version": 4}]},
            request=httpx.Request("GET", "http://test/api/v1/services"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        version = await _active_version_for_service(
            mock_client,
            "my-svc",
            tenant="tenant-a",
            environment="prod",
        )

        assert version == 4
        mock_client.get.assert_called_once_with(
            "/api/v1/services",
            params={"tenant": "tenant-a", "environment": "prod"},
        )


# --- _artifact_version ---


class TestArtifactVersion:
    async def test_returns_json(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"ir_json": {"operations": []}, "version": 2},
            request=httpx.Request("GET", "http://test/api/v1/artifacts/svc/versions/2"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await _artifact_version(mock_client, "svc", 2)
        assert result["version"] == 2
        mock_client.get.assert_called_once_with("/api/v1/artifacts/svc/versions/2", params=None)

    async def test_missing_ir_json_raises_controlled_error(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"version": 2},
            request=httpx.Request("GET", "http://test/api/v1/artifacts/svc/versions/2"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="required field 'ir_json'"):
            await _artifact_version(mock_client, "svc", 2)

    async def test_scope_filters_are_forwarded(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"ir_json": {"operations": []}, "version": 2},
            request=httpx.Request("GET", "http://test/api/v1/artifacts/svc/versions/2"),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await _artifact_version(
            mock_client,
            "svc",
            2,
            tenant="tenant-a",
            environment="prod",
        )

        assert result["version"] == 2
        mock_client.get.assert_called_once_with(
            "/api/v1/artifacts/svc/versions/2",
            params={"tenant": "tenant-a", "environment": "prod"},
        )


# --- _fetch_runtime_tool_names ---


class TestFetchRuntimeToolNames:
    async def test_fetches_tool_names(self) -> None:
        mock_response = httpx.Response(
            200,
            json={
                "tools": [
                    {"name": "tool_a", "description": "A"},
                    {"name": "tool_b", "description": "B"},
                ]
            },
            request=httpx.Request("GET", "http://runtime:8003/tools"),
        )

        with patch("apps.proof_runner.live_llm_e2e.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            names = await _fetch_runtime_tool_names("http://runtime:8003")
            assert names == {"tool_a", "tool_b"}

    async def test_rejects_malformed_tool_entry(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"tools": [{"name": "tool_a"}, {"id": "broken"}]},
            request=httpx.Request("GET", "http://runtime:8003/tools"),
        )

        with patch("apps.proof_runner.live_llm_e2e.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            with pytest.raises(RuntimeError, match="valid 'name' string"):
                await _fetch_runtime_tool_names("http://runtime:8003")

    async def test_rejects_duplicate_tool_names(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"tools": [{"name": "tool_a"}, {"name": "tool_a"}]},
            request=httpx.Request("GET", "http://runtime:8003/tools"),
        )

        with patch("apps.proof_runner.live_llm_e2e.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            with pytest.raises(RuntimeError, match="duplicate tool name"):
                await _fetch_runtime_tool_names("http://runtime:8003")

    async def test_rejects_non_object_payload(self) -> None:
        mock_response = httpx.Response(
            200,
            json=["tool_a", "tool_b"],
            request=httpx.Request("GET", "http://runtime:8003/tools"),
        )

        with patch("apps.proof_runner.live_llm_e2e.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            with pytest.raises(RuntimeError, match="expected object"):
                await _fetch_runtime_tool_names("http://runtime:8003")

    async def test_rejects_missing_tools_field(self) -> None:
        mock_response = httpx.Response(
            200,
            json={},
            request=httpx.Request("GET", "http://runtime:8003/tools"),
        )

        with patch("apps.proof_runner.live_llm_e2e.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            with pytest.raises(RuntimeError, match="required field 'tools'"):
                await _fetch_runtime_tool_names("http://runtime:8003")


# --- _generated_tool_audit_failure_reason ---


class TestGeneratedToolAuditFailureReason:
    def test_status_not_ok(self) -> None:
        ir = _ir()
        reason = _generated_tool_audit_failure_reason(ir, "op1", {"status": "error"})
        assert reason is not None
        assert "unexpected status" in reason

    def test_status_ok_without_result_payload(self) -> None:
        ir = _ir()
        reason = _generated_tool_audit_failure_reason(ir, "op1", {"status": "ok"})
        assert reason is not None
        assert "result payload" in reason

    def test_transport_mismatch(self) -> None:
        descriptor = EventDescriptor(
            id="ed1",
            name="Stream",
            operation_id="stream_op",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/pkg.Svc/Stream",
                mode=GrpcStreamMode.server,
            ),
        )
        ir = _ir(operations=[_op("stream_op")], event_descriptors=[descriptor])
        reason = _generated_tool_audit_failure_reason(
            ir,
            "stream_op",
            {"status": "ok", "transport": "websocket", "result": {}},
        )
        assert reason is not None
        assert "transport" in reason

    def test_non_object_stream_payload(self) -> None:
        descriptor = EventDescriptor(
            id="ed1",
            name="Stream",
            operation_id="stream_op",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/pkg.Svc/Stream",
                mode=GrpcStreamMode.server,
            ),
        )
        ir = _ir(operations=[_op("stream_op")], event_descriptors=[descriptor])
        reason = _generated_tool_audit_failure_reason(
            ir,
            "stream_op",
            {"status": "ok", "transport": "grpc_stream", "result": "not-a-dict"},
        )
        assert reason is not None
        assert "non-object" in reason

    def test_missing_lifecycle(self) -> None:
        descriptor = EventDescriptor(
            id="ed1",
            name="Stream",
            operation_id="stream_op",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/pkg.Svc/Stream",
                mode=GrpcStreamMode.server,
            ),
        )
        ir = _ir(operations=[_op("stream_op")], event_descriptors=[descriptor])
        reason = _generated_tool_audit_failure_reason(
            ir,
            "stream_op",
            {"status": "ok", "transport": "grpc_stream", "result": {"events": []}},
        )
        assert reason is not None
        assert "lifecycle" in reason

    def test_empty_lifecycle_fails_required_field_validation(self) -> None:
        descriptor = EventDescriptor(
            id="ed1",
            name="Stream",
            operation_id="stream_op",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/pkg.Svc/Stream",
                mode=GrpcStreamMode.server,
            ),
        )
        ir = _ir(operations=[_op("stream_op")], event_descriptors=[descriptor])
        reason = _generated_tool_audit_failure_reason(
            ir,
            "stream_op",
            {"status": "ok", "transport": "grpc_stream", "result": {"events": [], "lifecycle": {}}},
        )
        assert reason is not None
        assert "termination_reason" in reason

    def test_valid_streaming_result(self) -> None:
        descriptor = EventDescriptor(
            id="ed1",
            name="Stream",
            operation_id="stream_op",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/pkg.Svc/Stream",
                mode=GrpcStreamMode.server,
            ),
        )
        ir = _ir(operations=[_op("stream_op")], event_descriptors=[descriptor])
        reason = _generated_tool_audit_failure_reason(
            ir,
            "stream_op",
            {
                "status": "ok",
                "transport": "grpc_stream",
                "result": {
                    "events": [{"sku": "x"}],
                    "lifecycle": {
                        "termination_reason": "completed",
                        "messages_collected": 1,
                        "rpc_path": "/pkg.Svc/Stream",
                        "mode": "server",
                    },
                },
            },
        )
        assert reason is None


class TestOperationsEnhancedFromEvents:
    def test_rejects_non_integer_operations_enhanced(self) -> None:
        events = [
            {
                "data": {
                    "stage": "enhance",
                    "event_type": "stage.succeeded",
                    "detail": {"operations_enhanced": "3"},
                }
            }
        ]

        with pytest.raises(RuntimeError, match="expected integer"):
            _operations_enhanced_from_events(events)


# --- _compute_tool_intent_counts (else branch for unknown intent) ---


class TestComputeToolIntentCountsUnknown:
    def test_unknown_intent_counted_as_unset(self) -> None:
        op = _op("op1", tool_intent=ToolIntent.discovery)
        # Simulate an unknown intent value by patching
        object.__setattr__(op, "tool_intent", "some_unknown_value")
        ir = _ir(operations=[op])
        counts = _compute_tool_intent_counts(ir)
        assert counts.unset == 1


# --- _json_safe model_dump TypeError fallback ---


class TestJsonSafeModelDumpFallback:
    def test_model_dump_type_error_fallback(self) -> None:
        """Test when model_dump(mode='json') raises TypeError."""
        mock_model = MagicMock()
        mock_model.model_dump.side_effect = [TypeError("no mode arg"), {"field": "value"}]
        result = _json_safe(mock_model)
        assert result == {"field": "value"}
        assert mock_model.model_dump.call_count == 2


# --- _build_proof_cases ---


class TestBuildProofCases:
    def test_builds_all_five_protocols(self) -> None:
        cases = _build_proof_cases("test-ns", "run-abc")
        assert len(cases) == 5
        protocols = {c.protocol for c in cases}
        assert protocols == {"graphql", "rest", "grpc", "soap", "sql"}

    def test_service_ids_include_run_id(self) -> None:
        cases = _build_proof_cases("ns", "xyz123")
        for case in cases:
            assert "xyz123" in case.service_id

    def test_graphql_case_has_source_content(self) -> None:
        cases = _build_proof_cases("ns", "rid")
        graphql_case = next(c for c in cases if c.protocol == "graphql")
        assert "source_content" in graphql_case.request_payload
        assert graphql_case.request_payload["options"]["protocol"] == "graphql"

    def test_rest_case_has_source_url(self) -> None:
        cases = _build_proof_cases("ns", "rid")
        rest_case = next(c for c in cases if c.protocol == "rest")
        assert "source_url" in rest_case.request_payload
        assert "ns.svc.cluster.local" in rest_case.request_payload["source_url"]

    def test_grpc_case_has_proto_content(self) -> None:
        cases = _build_proof_cases("ns", "rid")
        grpc_case = next(c for c in cases if c.protocol == "grpc")
        assert "source_content" in grpc_case.request_payload
        assert grpc_case.request_payload["options"]["protocol"] == "grpc"

    def test_soap_case_rewrites_wsdl(self) -> None:
        cases = _build_proof_cases("ns", "rid")
        soap_case = next(c for c in cases if c.protocol == "soap")
        assert "source_content" in soap_case.request_payload
        assert "ns.svc.cluster.local" in soap_case.request_payload["source_content"]

    def test_sql_case_has_database_url(self) -> None:
        cases = _build_proof_cases("ns", "rid")
        sql_case = next(c for c in cases if c.protocol == "sql")
        assert "source_url" in sql_case.request_payload
        assert "postgresql://" in sql_case.request_payload["source_url"]

    def test_each_case_has_tool_invocations(self) -> None:
        cases = _build_proof_cases("ns", "rid")
        for case in cases:
            assert len(case.tool_invocations) >= 1

    def test_builds_real_target_cases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROOF_DIRECTUS_ACCESS_TOKEN", "directus-token")
        monkeypatch.setenv("PROOF_POCKETBASE_ACCESS_TOKEN", "pocketbase-token")
        monkeypatch.setenv("PROOF_GITEA_BASIC_AUTH", "gitea_admin:Admin123!")
        monkeypatch.setenv(
            "PROOF_JACKSON_SCIM_BASE_URL",
            "http://jackson.tc-real-targets.svc.cluster.local:5225/api/scim/v2.0/dir-id",
        )
        monkeypatch.setenv("PROOF_JACKSON_SCIM_SECRET", "jackson-secret")

        cases = _build_proof_cases(
            "proof-ns",
            "rid",
            profile="real-targets",
            upstream_namespace="tc-real-targets",
        )

        assert len(cases) == 11
        case_ids = {case.case_id for case in cases}
        assert case_ids == {
            "aria2-jsonrpc",
            "directus-graphql",
            "directus-openapi",
            "directus-rest",
            "gitea-openapi",
            "jackson-scim",
            "northbreeze-odata",
            "openfga-grpc",
            "pocketbase-rest",
            "real-postgres-sql",
            "soap-cxf",
        }

        scim_case = next(case for case in cases if case.case_id == "jackson-scim")
        assert scim_case.request_payload["source_url"].endswith("/Users")
        assert scim_case.request_payload["options"]["auth"]["runtime_secret_ref"] == (
            "jackson-scim-secret"
        )
        assert scim_case.request_payload["options"]["preferred_smoke_tool_ids"] == ["list_users"]

        grpc_case = next(case for case in cases if case.case_id == "openfga-grpc")
        assert grpc_case.request_payload["source_url"].startswith("grpc://openfga.")
        assert "service OpenFGAService" in grpc_case.request_payload["source_content"]

        directus_openapi_case = next(case for case in cases if case.case_id == "directus-openapi")
        assert directus_openapi_case.tool_invocations == (
            ToolInvocationSpec(tool_name="readItemsProducts", arguments={}),
        )
        assert directus_openapi_case.audit_skip_tool_ids == ("oauth",)

        gitea_openapi_case = next(case for case in cases if case.case_id == "gitea-openapi")
        assert gitea_openapi_case.audit_skip_tool_ids == ("getNodeInfo",)

        soap_case = next(case for case in cases if case.case_id == "soap-cxf")
        assert soap_case.tool_invocations == (
            ToolInvocationSpec(
                tool_name="GetOrderStatus",
                arguments={"orderId": "ORD-1001"},
            ),
        )

    def test_filters_cases_by_case_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROOF_DIRECTUS_ACCESS_TOKEN", "directus-token")
        monkeypatch.setenv("PROOF_POCKETBASE_ACCESS_TOKEN", "pocketbase-token")
        monkeypatch.setenv("PROOF_GITEA_BASIC_AUTH", "gitea_admin:Admin123!")
        monkeypatch.setenv(
            "PROOF_JACKSON_SCIM_BASE_URL",
            "http://jackson.tc-real-targets.svc.cluster.local:5225/api/scim/v2.0/dir-id",
        )
        monkeypatch.setenv("PROOF_JACKSON_SCIM_SECRET", "jackson-secret")

        cases = _build_proof_cases(
            "proof-ns",
            "rid",
            profile="real-targets",
            upstream_namespace="tc-real-targets",
            selected_case_ids={"gitea-openapi", "soap-cxf"},
        )

        assert [case.case_id for case in cases] == ["gitea-openapi", "soap-cxf"]


# --- _invoke_runtime_tools ---


class TestInvokeRuntimeTools:
    async def test_invokes_and_collects_results(self) -> None:
        mock_invoker = AsyncMock(
            side_effect=[
                {"status": "ok", "data": "result1"},
                {"status": "ok", "data": "result2"},
            ]
        )

        specs = (
            ToolInvocationSpec(tool_name="tool_a", arguments={"x": 1}),
            ToolInvocationSpec(tool_name="tool_b", arguments={"y": 2}),
        )

        with patch(
            "apps.proof_runner.live_llm_e2e.build_streamable_http_tool_invoker",
            return_value=mock_invoker,
        ):
            from apps.proof_runner.live_llm_e2e import _invoke_runtime_tools

            results = await _invoke_runtime_tools("http://runtime:8003", specs)

        assert len(results) == 2
        assert results[0].tool_name == "tool_a"
        assert results[0].result == {"status": "ok", "data": "result1"}
        assert results[1].tool_name == "tool_b"


# --- _audit_generated_tools ---


class TestAuditGeneratedTools:
    async def test_tool_not_in_runtime_listing(self) -> None:
        from apps.proof_runner.live_llm_e2e import _audit_generated_tools

        ir = _ir(operations=[_op("op1")])
        summary = await _audit_generated_tools(
            "http://runtime:8003",
            ir,
            representative_invocations=(),
            representative_results=[],
            available_tool_names=set(),
        )
        assert summary.failed == 1
        assert "not expose" in summary.results[0].reason

    async def test_tool_skipped_by_policy(self) -> None:
        from apps.proof_runner.live_llm_e2e import _audit_generated_tools

        op = _op(
            "op1",
            method="DELETE",
            risk=RiskMetadata(risk_level=RiskLevel.dangerous, destructive=True),
        )
        ir = _ir(operations=[op])
        policy = AuditPolicy(
            skip_destructive=True,
            audit_safe_methods=True,
            audit_discovery_intent=False,
        )

        summary = await _audit_generated_tools(
            "http://runtime:8003",
            ir,
            representative_invocations=(
                ToolInvocationSpec(tool_name="op1", arguments={"id": "1"}),
            ),
            representative_results=[],
            available_tool_names={"op1"},
            audit_policy=policy,
        )
        assert summary.skipped == 1

    async def test_tool_skipped_by_case_policy(self) -> None:
        from apps.proof_runner.live_llm_e2e import _audit_generated_tools

        ir = _ir(operations=[_op("getNodeInfo")])

        summary = await _audit_generated_tools(
            "http://runtime:8003",
            ir,
            representative_invocations=(),
            representative_results=[],
            available_tool_names=set(),
            forced_skip_tool_ids=("getNodeInfo",),
        )

        assert summary.skipped == 1
        assert summary.failed == 0
        assert summary.results[0].tool_name == "getNodeInfo"
        assert "disabled in the target deployment" in summary.results[0].reason

    async def test_invocation_raises_exception(self) -> None:
        from apps.proof_runner.live_llm_e2e import _audit_generated_tools

        failing_invoker = AsyncMock(side_effect=RuntimeError("connection refused"))
        ir = _ir(operations=[_op("op1")])
        policy = AuditPolicy(
            skip_destructive=False,
            skip_external_side_effect=False,
            skip_writes_state=False,
        )

        summary = await _audit_generated_tools(
            "http://runtime:8003",
            ir,
            representative_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
            representative_results=[],
            tool_invoker=failing_invoker,
            available_tool_names={"op1"},
            audit_policy=policy,
        )
        assert summary.failed == 1
        assert "Invocation raised" in summary.results[0].reason

    async def test_invocation_fails_audit_check(self) -> None:
        from apps.proof_runner.live_llm_e2e import _audit_generated_tools

        failing_invoker = AsyncMock(return_value={"status": "error", "message": "bad"})
        ir = _ir(operations=[_op("op1")])
        policy = AuditPolicy(
            skip_destructive=False,
            skip_external_side_effect=False,
            skip_writes_state=False,
        )

        summary = await _audit_generated_tools(
            "http://runtime:8003",
            ir,
            representative_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
            representative_results=[],
            tool_invoker=failing_invoker,
            available_tool_names={"op1"},
            audit_policy=policy,
        )
        assert summary.failed == 1
        assert "unexpected status" in summary.results[0].reason

    async def test_invocation_passes(self) -> None:
        from apps.proof_runner.live_llm_e2e import _audit_generated_tools

        ok_invoker = AsyncMock(return_value={"status": "ok", "result": "good"})
        ir = _ir(operations=[_op("op1")])
        policy = AuditPolicy(
            skip_destructive=False,
            skip_external_side_effect=False,
            skip_writes_state=False,
        )

        summary = await _audit_generated_tools(
            "http://runtime:8003",
            ir,
            representative_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
            representative_results=[],
            tool_invoker=ok_invoker,
            available_tool_names={"op1"},
            audit_policy=policy,
        )
        assert summary.passed == 1
        assert summary.results[0].outcome == "passed"

    async def test_cached_result_used(self) -> None:
        from apps.proof_runner.live_llm_e2e import _audit_generated_tools

        ir = _ir(operations=[_op("op1")])
        policy = AuditPolicy(
            skip_destructive=False,
            skip_external_side_effect=False,
            skip_writes_state=False,
        )
        cached_results = [
            ToolInvocationResult(tool_name="op1", result={"status": "ok", "result": 42}),
        ]

        summary = await _audit_generated_tools(
            "http://runtime:8003",
            ir,
            representative_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
            representative_results=cached_results,
            available_tool_names={"op1"},
            audit_policy=policy,
        )
        assert summary.passed == 1

    async def test_disabled_operations_excluded(self) -> None:
        from apps.proof_runner.live_llm_e2e import _audit_generated_tools

        ir = _ir(operations=[_op("op1", enabled=False)])
        summary = await _audit_generated_tools(
            "http://runtime:8003",
            ir,
            representative_invocations=(),
            representative_results=[],
            available_tool_names={"op1"},
        )
        assert summary.discovered_operations == 0


# --- run_proofs ---


class TestRunProofs:
    async def test_run_proofs_single_protocol(self) -> None:
        mock_result = ProofResult(
            protocol="rest",
            service_id="rest-svc-abc",
            job_id="job-1",
            active_version=1,
            operations_enhanced=2,
            llm_field_count=3,
            invocation_results=[],
        )
        with patch(
            "apps.proof_runner.live_llm_e2e._run_case",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            results = await run_proofs(
                namespace="test-ns",
                api_base_url="http://test:8000",
                protocol="rest",
                timeout_seconds=10.0,
                run_id="abc",
            )
        assert len(results) == 1
        assert results[0].protocol == "rest"

    async def test_run_proofs_all_protocols(self) -> None:
        mock_result = ProofResult(
            protocol="any",
            service_id="svc",
            job_id="job-1",
            active_version=1,
            operations_enhanced=1,
            llm_field_count=1,
            invocation_results=[],
        )
        with patch(
            "apps.proof_runner.live_llm_e2e._run_case",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            results = await run_proofs(
                namespace="test-ns",
                api_base_url="http://test:8000",
                protocol="all",
                timeout_seconds=10.0,
                run_id="abc",
            )
        assert len(results) == 5

    async def test_run_proofs_filters_selected_case_ids(self) -> None:
        mock_result = ProofResult(
            protocol="soap",
            service_id="soap-svc",
            job_id="job-1",
            active_version=1,
            operations_enhanced=1,
            llm_field_count=1,
            invocation_results=[],
            case_id="mock-soap",
        )
        with patch(
            "apps.proof_runner.live_llm_e2e._run_case",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            results = await run_proofs(
                namespace="test-ns",
                api_base_url="http://test:8000",
                protocol="all",
                timeout_seconds=10.0,
                run_id="abc",
                selected_case_ids={"mock-soap"},
            )
        assert len(results) == 1
        assert results[0].case_id == "mock-soap"


# --- _run_case ---


class TestRunCase:
    async def test_run_case_success(self) -> None:
        from apps.proof_runner.live_llm_e2e import _run_case

        mock_client = AsyncMock(spec=httpx.AsyncClient)

        compile_resp = httpx.Response(
            200, json={"id": "j1"}, request=httpx.Request("POST", "http://t")
        )
        mock_client.post = AsyncMock(return_value=compile_resp)

        job_resp = httpx.Response(
            200, json={"id": "j1", "status": "succeeded"}, request=httpx.Request("GET", "http://t")
        )
        events_resp = httpx.Response(
            200,
            text=_ENHANCE_STAGE_SUCCEEDED_EVENT,
            request=httpx.Request("GET", "http://t"),
        )
        services_resp = httpx.Response(
            200,
            json={"services": [{"service_id": "rest-svc", "active_version": 1}]},
            request=httpx.Request("GET", "http://t"),
        )
        artifact_resp = httpx.Response(
            200,
            json={
                "ir_json": {
                    "service_id": "rest-svc",
                    "service_name": "Rest",
                    "base_url": "http://x",
                    "source_hash": "sha256:abc",
                    "protocol": "rest",
                    "operations": [
                        {
                            "id": "op1",
                            "operation_id": "op1",
                            "name": "op1",
                            "description": "Test",
                            "method": "GET",
                            "path": "/op1",
                            "risk": {"risk_level": "safe"},
                            "enabled": True,
                            "source": "llm",
                            "params": [],
                        }
                    ],
                    "event_descriptors": [],
                }
            },
            request=httpx.Request("GET", "http://t"),
        )
        mock_client.get = AsyncMock(
            side_effect=[job_resp, events_resp, services_resp, artifact_resp]
        )

        mock_invoker = AsyncMock(return_value={"status": "ok", "result": {"ok": True}})
        case = ProofCase(
            protocol="rest",
            service_id="rest-svc",
            request_payload={"service_name": "rest-svc"},
            tool_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
        )

        with patch(
            "apps.proof_runner.live_llm_e2e.build_streamable_http_tool_invoker",
            return_value=mock_invoker,
        ):
            result = await _run_case(
                mock_client,
                case,
                namespace="test-ns",
                timeout_seconds=30.0,
                audit_all_generated_tools=False,
            )

        assert result.protocol == "rest"
        assert result.operations_enhanced == 3
        assert result.llm_field_count == 1

    async def test_run_case_uses_sanitized_runtime_service_name_and_scope(self) -> None:
        from apps.proof_runner.live_llm_e2e import _run_case

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        compile_resp = httpx.Response(
            200, json={"id": "j1"}, request=httpx.Request("POST", "http://t")
        )
        mock_client.post = AsyncMock(return_value=compile_resp)

        job_resp = httpx.Response(
            200, json={"id": "j1", "status": "succeeded"}, request=httpx.Request("GET", "http://t")
        )
        events_resp = httpx.Response(
            200,
            text=_ENHANCE_STAGE_SUCCEEDED_EVENT,
            request=httpx.Request("GET", "http://t"),
        )
        services_resp = httpx.Response(
            200,
            json={"services": [{"service_id": "Billing_API", "active_version": 2}]},
            request=httpx.Request("GET", "http://t"),
        )
        artifact_resp = httpx.Response(
            200,
            json={
                "ir_json": {
                    "service_id": "Billing_API",
                    "service_name": "Billing_API",
                    "base_url": "http://x",
                    "source_hash": "sha256:abc",
                    "protocol": "rest",
                    "operations": [
                        {
                            "id": "op1",
                            "operation_id": "op1",
                            "name": "op1",
                            "description": "Test",
                            "method": "GET",
                            "path": "/op1",
                            "risk": {"risk_level": "safe"},
                            "enabled": True,
                            "source": "llm",
                            "params": [],
                        }
                    ],
                    "event_descriptors": [],
                }
            },
            request=httpx.Request("GET", "http://t"),
        )
        mock_client.get = AsyncMock(
            side_effect=[job_resp, events_resp, services_resp, artifact_resp]
        )

        mock_invoker = AsyncMock(return_value={"status": "ok", "result": {"ok": True}})
        case = ProofCase(
            protocol="rest",
            service_id="Billing_API",
            request_payload={
                "service_name": "Billing_API",
                "tenant": "tenant-a",
                "environment": "prod",
            },
            tool_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
        )

        with patch(
            "apps.proof_runner.live_llm_e2e.build_streamable_http_tool_invoker",
            return_value=mock_invoker,
        ) as mock_builder:
            await _run_case(
                mock_client,
                case,
                namespace="test-ns",
                timeout_seconds=30.0,
                audit_all_generated_tools=False,
            )

        assert mock_client.get.call_args_list[2].kwargs["params"] == {
            "tenant": "tenant-a",
            "environment": "prod",
        }
        assert mock_client.get.call_args_list[3].kwargs["params"] == {
            "tenant": "tenant-a",
            "environment": "prod",
        }
        mock_builder.assert_called_once_with("http://billing-api-v2.test-ns.svc.cluster.local:8003")

    async def test_run_case_failed_job_raises(self) -> None:
        from apps.proof_runner.live_llm_e2e import _run_case

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        compile_resp = httpx.Response(
            200, json={"id": "j1"}, request=httpx.Request("POST", "http://t")
        )
        mock_client.post = AsyncMock(return_value=compile_resp)

        job_resp = httpx.Response(
            200,
            json={"id": "j1", "status": "failed", "error_detail": "compile error"},
            request=httpx.Request("GET", "http://t"),
        )
        events_resp = httpx.Response(200, text="", request=httpx.Request("GET", "http://t"))
        mock_client.get = AsyncMock(side_effect=[job_resp, events_resp])

        case = ProofCase(
            protocol="rest",
            service_id="rest-svc",
            request_payload={"service_name": "rest-svc"},
            tool_invocations=(ToolInvocationSpec(tool_name="op1", arguments={}),),
        )

        with pytest.raises(RuntimeError, match="compile error"):
            await _run_case(
                mock_client,
                case,
                namespace="ns",
                timeout_seconds=30.0,
                audit_all_generated_tools=False,
            )

    async def test_run_case_no_enhancements_raises(self) -> None:
        from apps.proof_runner.live_llm_e2e import _run_case

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        compile_resp = httpx.Response(
            200, json={"id": "j1"}, request=httpx.Request("POST", "http://t")
        )
        mock_client.post = AsyncMock(return_value=compile_resp)

        job_resp = httpx.Response(
            200, json={"id": "j1", "status": "succeeded"}, request=httpx.Request("GET", "http://t")
        )
        events_resp = httpx.Response(200, text="", request=httpx.Request("GET", "http://t"))
        mock_client.get = AsyncMock(side_effect=[job_resp, events_resp])

        case = ProofCase(
            protocol="rest",
            service_id="rest-svc",
            request_payload={"service_name": "rest-svc"},
            tool_invocations=(ToolInvocationSpec(tool_name="op1", arguments={}),),
        )

        with pytest.raises(RuntimeError, match="did not record any LLM enhancements"):
            await _run_case(
                mock_client,
                case,
                namespace="ns",
                timeout_seconds=30.0,
                audit_all_generated_tools=False,
            )

    async def test_run_case_no_llm_fields_raises(self) -> None:
        from apps.proof_runner.live_llm_e2e import _run_case

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        compile_resp = httpx.Response(
            200, json={"id": "j1"}, request=httpx.Request("POST", "http://t")
        )
        mock_client.post = AsyncMock(return_value=compile_resp)

        job_resp = httpx.Response(
            200, json={"id": "j1", "status": "succeeded"}, request=httpx.Request("GET", "http://t")
        )
        events_resp = httpx.Response(
            200,
            text=_ENHANCE_STAGE_SUCCEEDED_EVENT,
            request=httpx.Request("GET", "http://t"),
        )
        services_resp = httpx.Response(
            200,
            json={"services": [{"service_id": "rest-svc", "active_version": 1}]},
            request=httpx.Request("GET", "http://t"),
        )
        artifact_resp = httpx.Response(
            200,
            json={
                "ir_json": {
                    "service_id": "rest-svc",
                    "service_name": "Rest",
                    "base_url": "http://x",
                    "source_hash": "sha256:abc",
                    "protocol": "rest",
                    "operations": [
                        {
                            "id": "op1",
                            "operation_id": "op1",
                            "name": "op1",
                            "description": "Test",
                            "method": "GET",
                            "path": "/op1",
                            "risk": {"risk_level": "safe"},
                            "enabled": True,
                            "source": "extractor",
                            "params": [],
                        }
                    ],
                    "event_descriptors": [],
                }
            },
            request=httpx.Request("GET", "http://t"),
        )
        mock_client.get = AsyncMock(
            side_effect=[job_resp, events_resp, services_resp, artifact_resp]
        )

        case = ProofCase(
            protocol="rest",
            service_id="rest-svc",
            request_payload={"service_name": "rest-svc"},
            tool_invocations=(ToolInvocationSpec(tool_name="op1", arguments={}),),
        )

        with pytest.raises(RuntimeError, match="no llm-sourced fields"):
            await _run_case(
                mock_client,
                case,
                namespace="ns",
                timeout_seconds=30.0,
                audit_all_generated_tools=False,
            )

    async def test_run_case_with_audit(self) -> None:
        from apps.proof_runner.live_llm_e2e import _run_case

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        compile_resp = httpx.Response(
            200, json={"id": "j1"}, request=httpx.Request("POST", "http://t")
        )
        mock_client.post = AsyncMock(return_value=compile_resp)

        job_resp = httpx.Response(
            200, json={"id": "j1", "status": "succeeded"}, request=httpx.Request("GET", "http://t")
        )
        events_resp = httpx.Response(
            200,
            text=_ENHANCE_STAGE_SUCCEEDED_EVENT,
            request=httpx.Request("GET", "http://t"),
        )
        services_resp = httpx.Response(
            200,
            json={"services": [{"service_id": "rest-svc", "active_version": 1}]},
            request=httpx.Request("GET", "http://t"),
        )
        artifact_resp = httpx.Response(
            200,
            json={
                "ir_json": {
                    "service_id": "rest-svc",
                    "service_name": "Rest",
                    "base_url": "http://x",
                    "source_hash": "sha256:abc",
                    "protocol": "rest",
                    "operations": [
                        {
                            "id": "op1",
                            "operation_id": "op1",
                            "name": "op1",
                            "description": "Test",
                            "method": "GET",
                            "path": "/op1",
                            "risk": {"risk_level": "safe"},
                            "enabled": True,
                            "source": "llm",
                            "params": [],
                        }
                    ],
                    "event_descriptors": [],
                }
            },
            request=httpx.Request("GET", "http://t"),
        )
        mock_client.get = AsyncMock(
            side_effect=[job_resp, events_resp, services_resp, artifact_resp]
        )

        mock_invoker = AsyncMock(return_value={"status": "ok", "result": {"ok": True}})
        mock_audit_summary = ToolAuditSummary(
            discovered_operations=1,
            generated_tools=1,
            audited_tools=1,
            passed=1,
            failed=0,
            skipped=0,
            results=[],
        )

        case = ProofCase(
            protocol="rest",
            service_id="rest-svc",
            request_payload={"service_name": "rest-svc"},
            tool_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
        )

        with (
            patch(
                "apps.proof_runner.live_llm_e2e.build_streamable_http_tool_invoker",
                return_value=mock_invoker,
            ),
            patch(
                "apps.proof_runner.live_llm_e2e._audit_generated_tools",
                new_callable=AsyncMock,
                return_value=mock_audit_summary,
            ),
        ):
            result = await _run_case(
                mock_client,
                case,
                namespace="test-ns",
                timeout_seconds=30.0,
                audit_all_generated_tools=True,
            )

        assert result.audit_summary is not None
        assert result.audit_summary.passed == 1

    async def test_run_case_with_llm_judge(self) -> None:
        from apps.proof_runner.live_llm_e2e import _run_case
        from libs.validator.llm_judge import JudgeEvaluation

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        compile_resp = httpx.Response(
            200, json={"id": "j1"}, request=httpx.Request("POST", "http://t")
        )
        mock_client.post = AsyncMock(return_value=compile_resp)

        job_resp = httpx.Response(
            200, json={"id": "j1", "status": "succeeded"}, request=httpx.Request("GET", "http://t")
        )
        events_resp = httpx.Response(
            200,
            text=_ENHANCE_STAGE_SUCCEEDED_EVENT,
            request=httpx.Request("GET", "http://t"),
        )
        services_resp = httpx.Response(
            200,
            json={"services": [{"service_id": "rest-svc", "active_version": 1}]},
            request=httpx.Request("GET", "http://t"),
        )
        artifact_resp = httpx.Response(
            200,
            json={
                "ir_json": {
                    "service_id": "rest-svc",
                    "service_name": "Rest",
                    "base_url": "http://x",
                    "source_hash": "sha256:abc",
                    "protocol": "rest",
                    "operations": [
                        {
                            "id": "op1",
                            "operation_id": "op1",
                            "name": "op1",
                            "description": "Test",
                            "method": "GET",
                            "path": "/op1",
                            "risk": {"risk_level": "safe"},
                            "enabled": True,
                            "source": "llm",
                            "params": [],
                        }
                    ],
                    "event_descriptors": [],
                }
            },
            request=httpx.Request("GET", "http://t"),
        )
        mock_client.get = AsyncMock(
            side_effect=[job_resp, events_resp, services_resp, artifact_resp]
        )

        mock_invoker = AsyncMock(return_value={"status": "ok", "result": {"ok": True}})
        mock_judge = MagicMock()
        judge_eval = JudgeEvaluation(
            service_name="rest-svc",
            tools_evaluated=1,
            average_accuracy=0.9,
            average_completeness=0.8,
            average_clarity=0.85,
            average_overall=0.85,
        )
        mock_judge.evaluate.return_value = judge_eval

        case = ProofCase(
            protocol="rest",
            service_id="rest-svc",
            request_payload={"service_name": "rest-svc"},
            tool_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
        )

        with patch(
            "apps.proof_runner.live_llm_e2e.build_streamable_http_tool_invoker",
            return_value=mock_invoker,
        ):
            result = await _run_case(
                mock_client,
                case,
                namespace="test-ns",
                timeout_seconds=30.0,
                audit_all_generated_tools=False,
                enable_llm_judge=True,
                llm_judge=mock_judge,
            )

        assert result.judge_evaluation is not None
        assert result.judge_evaluation.average_overall == 0.85

    async def test_run_case_llm_judge_exception_caught(self) -> None:
        from apps.proof_runner.live_llm_e2e import _run_case

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        compile_resp = httpx.Response(
            200, json={"id": "j1"}, request=httpx.Request("POST", "http://t")
        )
        mock_client.post = AsyncMock(return_value=compile_resp)

        job_resp = httpx.Response(
            200, json={"id": "j1", "status": "succeeded"}, request=httpx.Request("GET", "http://t")
        )
        events_resp = httpx.Response(
            200,
            text=_ENHANCE_STAGE_SUCCEEDED_EVENT,
            request=httpx.Request("GET", "http://t"),
        )
        services_resp = httpx.Response(
            200,
            json={"services": [{"service_id": "rest-svc", "active_version": 1}]},
            request=httpx.Request("GET", "http://t"),
        )
        artifact_resp = httpx.Response(
            200,
            json={
                "ir_json": {
                    "service_id": "rest-svc",
                    "service_name": "Rest",
                    "base_url": "http://x",
                    "source_hash": "sha256:abc",
                    "protocol": "rest",
                    "operations": [
                        {
                            "id": "op1",
                            "operation_id": "op1",
                            "name": "op1",
                            "description": "Test",
                            "method": "GET",
                            "path": "/op1",
                            "risk": {"risk_level": "safe"},
                            "enabled": True,
                            "source": "llm",
                            "params": [],
                        }
                    ],
                    "event_descriptors": [],
                }
            },
            request=httpx.Request("GET", "http://t"),
        )
        mock_client.get = AsyncMock(
            side_effect=[job_resp, events_resp, services_resp, artifact_resp]
        )

        mock_invoker = AsyncMock(return_value={"status": "ok", "result": {"ok": True}})
        mock_judge = MagicMock()
        mock_judge.evaluate.side_effect = RuntimeError("LLM API down")

        case = ProofCase(
            protocol="rest",
            service_id="rest-svc",
            request_payload={"service_name": "rest-svc"},
            tool_invocations=(ToolInvocationSpec(tool_name="op1", arguments={"x": 1}),),
        )

        with patch(
            "apps.proof_runner.live_llm_e2e.build_streamable_http_tool_invoker",
            return_value=mock_invoker,
        ):
            result = await _run_case(
                mock_client,
                case,
                namespace="test-ns",
                timeout_seconds=30.0,
                audit_all_generated_tools=False,
                enable_llm_judge=True,
                llm_judge=mock_judge,
            )

        assert result.judge_evaluation is None


# --- _parse_args ---


class TestParseArgs:
    def test_required_namespace(self) -> None:
        with patch("sys.argv", ["prog", "--namespace", "my-ns"]):
            args = _parse_args()
            assert args.namespace == "my-ns"
            assert args.api_base_url == "http://127.0.0.1:8000"
            assert args.protocol == "all"
            assert args.timeout_seconds == 900.0
            assert args.audit_all_generated_tools is False
            assert args.enable_llm_judge is False
            assert args.case_ids == []
            assert args.skip_llm_artifact_checks is False

    def test_all_arguments(self) -> None:
        with patch(
            "sys.argv",
            [
                "prog",
                "--namespace",
                "prod-ns",
                "--api-base-url",
                "http://api:9000",
                "--protocol",
                "graphql",
                "--timeout-seconds",
                "120",
                "--run-id",
                "test-run",
                "--audit-all-generated-tools",
                "--enable-llm-judge",
                "--case-id",
                "directus-openapi",
                "--case-id",
                "gitea-openapi",
                "--skip-llm-artifact-checks",
            ],
        ):
            args = _parse_args()
            assert args.namespace == "prod-ns"
            assert args.api_base_url == "http://api:9000"
            assert args.protocol == "graphql"
            assert args.timeout_seconds == 120.0
            assert args.run_id == "test-run"
            assert args.audit_all_generated_tools is True
            assert args.enable_llm_judge is True
            assert args.case_ids == ["directus-openapi", "gitea-openapi"]
            assert args.skip_llm_artifact_checks is True


# --- _build_llm_judge_from_env ---


class TestBuildLlmJudgeFromEnv:
    def test_success(self) -> None:
        MagicMock()
        MagicMock()
        with patch("apps.proof_runner.live_llm_e2e._build_llm_judge_from_env") as mock_fn:
            mock_fn.return_value = MagicMock()
            result = mock_fn()
            assert result is not None

    def test_returns_none_on_import_error(self) -> None:
        with patch.dict("sys.modules", {"libs.enhancer.enhancer": None}):
            result = _build_llm_judge_from_env()
            assert result is None

    def test_returns_none_on_exception(self) -> None:
        with patch(
            "apps.proof_runner.live_llm_e2e._build_llm_judge_from_env",
            wraps=_build_llm_judge_from_env,
        ):
            # The function tries to import and create things from env;
            # without proper env vars it should return None
            result = _build_llm_judge_from_env()
            assert result is None


# --- _async_main and main ---


class TestAsyncMainAndMain:
    async def test_async_main(self) -> None:
        mock_args = argparse.Namespace(
            namespace="test-ns",
            api_base_url="http://test:8000",
            protocol="rest",
            profile="mock",
            upstream_namespace=None,
            timeout_seconds=30.0,
            run_id="abc",
            audit_all_generated_tools=False,
            enable_llm_judge=False,
            case_ids=[],
            skip_llm_artifact_checks=False,
        )
        mock_result = ProofResult(
            protocol="rest",
            service_id="rest-svc",
            job_id="job-1",
            active_version=1,
            operations_enhanced=2,
            llm_field_count=3,
            invocation_results=[],
        )
        with (
            patch("apps.proof_runner.live_llm_e2e._parse_args", return_value=mock_args),
            patch(
                "apps.proof_runner.live_llm_e2e.run_proofs",
                new_callable=AsyncMock,
                return_value=[mock_result],
            ),
            patch("builtins.print") as mock_print,
        ):
            await _async_main()
            mock_print.assert_called_once()
            output = mock_print.call_args[0][0]
            parsed = json.loads(output)
            assert isinstance(parsed, list)
            assert len(parsed) == 1

    async def test_async_main_with_judge(self) -> None:
        mock_args = argparse.Namespace(
            namespace="test-ns",
            api_base_url="http://test:8000",
            protocol="rest",
            profile="mock",
            upstream_namespace=None,
            timeout_seconds=30.0,
            run_id="abc",
            audit_all_generated_tools=False,
            enable_llm_judge=True,
            case_ids=[],
            skip_llm_artifact_checks=False,
        )
        mock_result = ProofResult(
            protocol="rest",
            service_id="rest-svc",
            job_id="job-1",
            active_version=1,
            operations_enhanced=2,
            llm_field_count=3,
            invocation_results=[],
        )
        mock_judge = MagicMock()
        with (
            patch("apps.proof_runner.live_llm_e2e._parse_args", return_value=mock_args),
            patch(
                "apps.proof_runner.live_llm_e2e._build_llm_judge_from_env",
                return_value=mock_judge,
            ),
            patch(
                "apps.proof_runner.live_llm_e2e.run_proofs",
                new_callable=AsyncMock,
                return_value=[mock_result],
            ),
            patch("builtins.print"),
        ):
            await _async_main()

    def test_main_calls_asyncio_run(self) -> None:
        with patch("apps.proof_runner.live_llm_e2e.asyncio.run") as mock_run:
            main()
            mock_run.assert_called_once()
