"""Unit tests for apps/compiler_worker/activities/production.py — pure helper functions."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from apps.compiler_worker.activities.production import (
    _apply_post_enhancement,
    _build_extractors,
    _close_extractors,
    _deserialize_manifest_set,
    _enhancement_enabled,
    _has_native_grpc_unary,
    _has_supported_native_grpc_stream,
    _is_safe_optional_grpc_sample_param,
    _manifest_set_from_context,
    _read_service_account_namespace,
    _resolve_extractor,
    _sample_graphql_arguments,
    _sample_grpc_arguments,
    _sample_sql_arguments,
    _sample_value,
    _serialize_manifest_set,
    _source_config_from_context,
    _stage_result,
    _tool_grouping_enabled,
    _validation_failure_message,
    build_sample_invocations,
)
from libs.extractors.base import SourceConfig
from libs.ir.models import (
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
)

_TEST_UUID = "00000000-0000-0000-0000-000000000001"


def _risk(level: RiskLevel = RiskLevel.safe) -> RiskMetadata:
    return RiskMetadata(risk_level=level)


def _op(
    op_id: str = "test_op",
    *,
    method: str = "GET",
    params: list[Param] | None = None,
    enabled: bool = True,
    risk_level: RiskLevel = RiskLevel.safe,
    graphql: GraphQLOperationConfig | None = None,
    sql: SqlOperationConfig | None = None,
    grpc_unary: GrpcUnaryRuntimeConfig | None = None,
) -> Operation:
    return Operation(
        id=op_id,
        operation_id=op_id,
        name=op_id,
        description=f"Test {op_id}",
        method=method,
        path=f"/{op_id}",
        params=params or [],
        risk=_risk(risk_level),
        enabled=enabled,
        graphql=graphql,
        sql=sql,
        grpc_unary=grpc_unary,
    )


def _ir(
    operations: list[Operation] | None = None,
    protocol: str = "openapi",
    event_descriptors: list[EventDescriptor] | None = None,
) -> ServiceIR:
    return ServiceIR(
        service_id="test-svc",
        service_name="Test Service",
        base_url="https://example.com",
        source_hash="sha256:abc123",
        protocol=protocol,
        operations=operations or [],
        event_descriptors=event_descriptors or [],
    )


# --- _sample_value ---


class TestSampleValue:
    def test_default_overrides(self) -> None:
        p = Param(name="x", type="string", required=True, default="hello")
        assert _sample_value(p) == "hello"

    def test_status_name(self) -> None:
        p = Param(name="status", type="string", required=True)
        assert _sample_value(p) == "available"

    def test_integer(self) -> None:
        p = Param(name="count", type="integer", required=True)
        assert _sample_value(p) == 1

    def test_number(self) -> None:
        p = Param(name="price", type="number", required=True)
        assert _sample_value(p) == 1.0

    def test_boolean(self) -> None:
        p = Param(name="active", type="boolean", required=True)
        assert _sample_value(p) is True

    def test_array(self) -> None:
        p = Param(name="items", type="array", required=True)
        assert _sample_value(p) == ["sample"]

    def test_object(self) -> None:
        p = Param(name="body", type="object", required=True)
        assert _sample_value(p) == {"name": "sample"}

    def test_id_suffix(self) -> None:
        p = Param(name="userId", type="string", required=True)
        assert _sample_value(p) == "1"

    def test_fallback_string(self) -> None:
        p = Param(name="q", type="string", required=True)
        assert _sample_value(p) == "sample"


# --- build_sample_invocations ---


class TestBuildSampleInvocations:
    def test_basic(self) -> None:
        ir = _ir(operations=[
            _op(
                "get_items",
                params=[Param(name="limit", type="integer", required=False, default=10)],
            ),
            _op("disabled_op", enabled=False),
        ])
        result = build_sample_invocations(ir)
        assert "get_items" in result
        assert "disabled_op" not in result
        assert result["get_items"]["limit"] == 10

    def test_empty_operations(self) -> None:
        ir = _ir(operations=[])
        assert build_sample_invocations(ir) == {}


# --- _sample_grpc_arguments ---


class TestSampleGrpcArguments:
    def test_required_only(self) -> None:
        op = _op(params=[
            Param(name="id", type="string", required=True),
            Param(name="data", type="object", required=False),
        ])
        result = _sample_grpc_arguments(op)
        assert "id" in result
        assert "data" not in result  # optional object skipped

    def test_safe_optional_included(self) -> None:
        op = _op(params=[
            Param(name="limit", type="integer", required=False),
            Param(name="page_token", type="string", required=False),
        ])
        result = _sample_grpc_arguments(op)
        assert "limit" in result
        assert "page_token" in result

    def test_id_suffix_param_included(self) -> None:
        op = _op(params=[
            Param(name="user_id", type="string", required=False),
        ])
        result = _sample_grpc_arguments(op)
        assert "user_id" in result


class TestIsSafeOptionalGrpcSampleParam:
    def test_id_suffix(self) -> None:
        p = Param(name="orderId", type="string", required=False)
        assert _is_safe_optional_grpc_sample_param(p) is True

    def test_known_names(self) -> None:
        for name in ["cursor", "limit", "page", "query", "sku"]:
            p = Param(name=name, type="string", required=False)
            assert _is_safe_optional_grpc_sample_param(p) is True, f"{name} should be safe"

    def test_unknown_name(self) -> None:
        p = Param(name="description", type="string", required=False)
        assert _is_safe_optional_grpc_sample_param(p) is False


# --- _sample_graphql_arguments ---


class TestSampleGraphqlArguments:
    def test_no_graphql_config(self) -> None:
        op = _op(params=[Param(name="id", type="string", required=True)])
        result = _sample_graphql_arguments(op)
        assert result == {"id": "1"}

    def test_query_with_no_required_returns_empty(self) -> None:
        op = _op(
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="GetUsers",
                document="query GetUsers { users { name } }",
            ),
            params=[Param(name="limit", type="integer", required=False)],
        )
        result = _sample_graphql_arguments(op)
        assert result == {}

    def test_mutation_without_required_includes_all(self) -> None:
        op = _op(
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.mutation,
                operation_name="CreateUser",
                document="mutation CreateUser($name: String!) { createUser(name: $name) { id } }",
            ),
            params=[Param(name="name", type="string", required=False)],
        )
        result = _sample_graphql_arguments(op)
        assert "name" in result

    def test_required_params_always_included(self) -> None:
        op = _op(
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="GetUser",
                document="query GetUser($id: ID!) { user(id: $id) { name } }",
            ),
            params=[Param(name="id", type="string", required=True)],
        )
        result = _sample_graphql_arguments(op)
        assert "id" in result


# --- _sample_sql_arguments ---


class TestSampleSqlArguments:
    def test_no_sql_config(self) -> None:
        op = _op(params=[Param(name="id", type="string", required=True)])
        result = _sample_sql_arguments(op)
        assert result == {"id": "1"}

    def test_query_limits_to_1(self) -> None:
        op = _op(
            sql=SqlOperationConfig(
                schema_name="public",
                relation_name="users",
                relation_kind=SqlRelationKind.table,
                action=SqlOperationType.query,
                filterable_columns=["id"],
            ),
            method="GET",
            params=[
                Param(name="limit", type="integer", required=False, default=100),
                Param(name="name", type="string", required=False),
            ],
        )
        result = _sample_sql_arguments(op)
        assert result["limit"] == 100  # uses default
        assert "name" not in result  # non-required, non-limit skipped for query

    def test_query_limit_no_default_gets_1(self) -> None:
        op = _op(
            sql=SqlOperationConfig(
                schema_name="public",
                relation_name="users",
                relation_kind=SqlRelationKind.table,
                action=SqlOperationType.query,
                filterable_columns=["id"],
            ),
            method="GET",
            params=[Param(name="limit", type="integer", required=False)],
        )
        result = _sample_sql_arguments(op)
        assert result["limit"] == 1

    def test_insert_includes_required_only(self) -> None:
        op = _op(
            sql=SqlOperationConfig(
                schema_name="public",
                relation_name="users",
                relation_kind=SqlRelationKind.table,
                action=SqlOperationType.insert,
                insertable_columns=["name", "bio"],
            ),
            method="POST",
            params=[
                Param(name="name", type="string", required=True),
                Param(name="bio", type="string", required=False),
            ],
        )
        result = _sample_sql_arguments(op)
        assert "name" in result
        assert "bio" not in result


# --- Feature flags ---


class TestEnhancementEnabled:
    def test_explicit_env(self) -> None:
        with patch.dict(os.environ, {"WORKER_ENABLE_LLM_ENHANCEMENT": "true"}, clear=False):
            assert _enhancement_enabled() is True

    def test_api_key_present(self) -> None:
        with patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=False):
            os.environ.pop("WORKER_ENABLE_LLM_ENHANCEMENT", None)
            assert _enhancement_enabled() is True

    def test_vertex_project(self) -> None:
        with patch.dict(os.environ, {"VERTEX_PROJECT_ID": "my-project"}, clear=False):
            os.environ.pop("WORKER_ENABLE_LLM_ENHANCEMENT", None)
            os.environ.pop("LLM_API_KEY", None)
            assert _enhancement_enabled() is True

    def test_nothing_set(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert _enhancement_enabled() is False


class TestToolGroupingEnabled:
    def test_true(self) -> None:
        with patch.dict(os.environ, {"WORKER_ENABLE_TOOL_GROUPING": "1"}):
            assert _tool_grouping_enabled() is True

    def test_false(self) -> None:
        with patch.dict(os.environ, {"WORKER_ENABLE_TOOL_GROUPING": "no"}):
            assert _tool_grouping_enabled() is False


# --- Manifest serialization ---


class TestManifestSerialization:
    def test_roundtrip(self) -> None:
        from libs.generator import GeneratedManifestSet

        original = GeneratedManifestSet(
            config_map={"key": "value"},
            deployment={"replicas": 1},
            service={"port": 8080},
            network_policy={"enabled": True},
            route_config={"path": "/api"},
            yaml="apiVersion: v1\nkind: Service",
        )
        serialized = _serialize_manifest_set(original)
        deserialized = _deserialize_manifest_set(serialized)
        assert deserialized.config_map == original.config_map
        assert deserialized.deployment == original.deployment
        assert deserialized.service == original.service
        assert deserialized.network_policy == original.network_policy
        assert deserialized.route_config == original.route_config
        assert deserialized.yaml == original.yaml


class TestManifestSetFromContext:
    def test_missing_raises(self) -> None:
        from uuid import UUID

        from apps.compiler_worker.models import CompilationContext, CompilationRequest

        context = CompilationContext(
            job_id=UUID(_TEST_UUID),
            request=CompilationRequest(
                source_url="https://example.com/api",
            ),
            payload={},
        )
        with pytest.raises(RuntimeError, match="Generated manifest set missing"):
            _manifest_set_from_context(context)


# --- Source config ---


class TestSourceConfigFromContext:
    def test_basic(self) -> None:
        from uuid import UUID

        from apps.compiler_worker.models import CompilationContext, CompilationRequest

        ctx = CompilationContext(
            job_id=UUID(_TEST_UUID),
            request=CompilationRequest(
                source_url="https://example.com/api.yaml",
                options={"protocol": "openapi"},
            ),
            payload={},
        )
        source = _source_config_from_context(ctx)
        assert source.url == "https://example.com/api.yaml"
        assert source.hints["protocol"] == "openapi"

    def test_with_auth(self) -> None:
        from uuid import UUID

        from apps.compiler_worker.models import CompilationContext, CompilationRequest

        ctx = CompilationContext(
            job_id=UUID(_TEST_UUID),
            request=CompilationRequest(
                source_url="https://example.com",
                options={
                    "auth_header": "Authorization",
                    "auth_token": "Bearer xyz",
                },
            ),
            payload={},
        )
        source = _source_config_from_context(ctx)
        assert source.auth_header == "Authorization"
        assert source.auth_token == "Bearer xyz"

    def test_non_mapping_hints_ignored(self) -> None:
        from uuid import UUID

        from apps.compiler_worker.models import CompilationContext, CompilationRequest

        ctx = CompilationContext(
            job_id=UUID(_TEST_UUID),
            request=CompilationRequest(
                source_url="https://example.com",
                options={"hints": "not a dict"},
            ),
            payload={},
        )
        source = _source_config_from_context(ctx)
        assert source.hints == {}


# --- Extractor helpers ---


class TestBuildExtractors:
    def test_returns_nine_extractors(self) -> None:
        extractors = _build_extractors()
        assert len(extractors) == 9
        names = {e.protocol_name for e in extractors}
        assert "openapi" in names
        assert "graphql" in names
        assert "grpc" in names
        assert "soap" in names
        assert "sql" in names
        assert "rest" in names
        assert "odata" in names
        assert "scim" in names
        assert "jsonrpc" in names


class TestResolveExtractor:
    def test_protocol_hint(self) -> None:
        from uuid import UUID

        from apps.compiler_worker.models import CompilationContext, CompilationRequest

        ctx = CompilationContext(
            job_id=UUID(_TEST_UUID),
            request=CompilationRequest(source_url="https://example.com"),
            protocol="graphql",
            payload={},
        )
        extractors = _build_extractors()
        source = SourceConfig(url="https://example.com")
        result = _resolve_extractor(ctx, source, extractors)
        assert result.protocol_name == "graphql"


class TestCloseExtractors:
    def test_calls_close_on_closeable(self) -> None:
        from unittest.mock import MagicMock

        mock_ext = MagicMock()
        mock_ext.close = MagicMock()
        _close_extractors([mock_ext])
        mock_ext.close.assert_called_once()

    def test_skips_non_closeable(self) -> None:
        mock_ext = SimpleNamespace(protocol_name="test")
        _close_extractors([mock_ext])  # type: ignore[arg-type,unused-ignore]
        # Should not raise


# --- Stage result builder ---


class TestStageResult:
    def test_defaults(self) -> None:
        result = _stage_result()
        assert result.context_updates == {}
        assert result.event_detail is None
        assert result.rollback_payload is None
        assert result.protocol is None
        assert result.service_name is None

    def test_with_values(self) -> None:
        result = _stage_result(
            context_updates={"key": "val"},
            event_detail={"stage": "extract"},
            protocol="openapi",
            service_name="My API",
        )
        assert result.context_updates == {"key": "val"}
        assert result.protocol == "openapi"


# --- Validation failure message ---


class TestValidationFailureMessage:
    def test_no_failures(self) -> None:
        report = SimpleNamespace(results=[
            SimpleNamespace(passed=True, stage="s1", details="ok"),
        ])
        msg = _validation_failure_message("Pre-deploy failed", report)
        assert msg == "Pre-deploy failed"

    def test_with_failures(self) -> None:
        report = SimpleNamespace(results=[
            SimpleNamespace(passed=False, stage="schema", details="missing field"),
            SimpleNamespace(passed=True, stage="lint", details="ok"),
            SimpleNamespace(passed=False, stage="auth", details="no key"),
        ])
        msg = _validation_failure_message("Pre-deploy failed", report)
        assert "schema: missing field" in msg
        assert "auth: no key" in msg
        assert "lint" not in msg


# --- gRPC/stream detection ---


class TestHasSupportedNativeGrpcStream:
    def test_with_supported_stream(self) -> None:
        ir = _ir(
            operations=[_op("stream_op")],
            event_descriptors=[
                EventDescriptor(
                    id="ed1",
                    name="Stream event",
                    operation_id="stream_op",
                    transport=EventTransport.grpc_stream,
                    support=EventSupportLevel.supported,
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/pkg.Service/Stream",
                        mode=GrpcStreamMode.server,
                    ),
                ),
            ],
        )
        assert _has_supported_native_grpc_stream(ir) is True

    def test_unsupported_stream(self) -> None:
        ir = _ir(
            operations=[_op("stream_op")],
            event_descriptors=[
                EventDescriptor(
                    id="ed1",
                    name="Stream event",
                    operation_id="stream_op",
                    transport=EventTransport.grpc_stream,
                    support=EventSupportLevel.unsupported,
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/pkg.Service/Stream",
                        mode=GrpcStreamMode.server,
                    ),
                ),
            ],
        )
        assert _has_supported_native_grpc_stream(ir) is False

    def test_no_descriptors(self) -> None:
        ir = _ir()
        assert _has_supported_native_grpc_stream(ir) is False


class TestHasNativeGrpcUnary:
    def test_with_grpc_unary(self) -> None:
        ir = _ir(operations=[
            _op(
                "grpc_op",
                method="POST",
                grpc_unary=GrpcUnaryRuntimeConfig(rpc_path="/grpc_op"),
            ),
        ])
        assert _has_native_grpc_unary(ir) is True

    def test_disabled_op_not_counted(self) -> None:
        ir = _ir(operations=[
            _op(
                "grpc_op",
                method="POST",
                enabled=False,
                risk_level=RiskLevel.unknown,
                grpc_unary=GrpcUnaryRuntimeConfig(rpc_path="/grpc_op"),
            ),
        ])
        assert _has_native_grpc_unary(ir) is False

    def test_no_grpc_unary(self) -> None:
        ir = _ir(operations=[_op("regular_op")])
        assert _has_native_grpc_unary(ir) is False


# --- _apply_post_enhancement ---


class TestApplyPostEnhancement:
    def test_basic_no_grouping(self) -> None:
        ir = _ir(operations=[
            _op("get_items", params=[Param(name="q", type="string", required=False)]),
        ])
        with patch.dict(os.environ, {"WORKER_ENABLE_TOOL_GROUPING": ""}):
            result = _apply_post_enhancement(ir)
        # Should still have operations with tool_intent set
        assert len(result.operations) == 1

    def test_grouping_failure_continues(self) -> None:
        ir = _ir(operations=[
            _op("get_items"),
        ])

        def bad_factory() -> Any:
            raise RuntimeError("LLM unavailable")

        with patch.dict(os.environ, {"WORKER_ENABLE_TOOL_GROUPING": "1"}):
            result = _apply_post_enhancement(ir, llm_client_factory=bad_factory)
        assert len(result.operations) == 1


# --- _read_service_account_namespace ---


class TestReadServiceAccountNamespace:
    def test_not_on_k8s(self) -> None:
        assert _read_service_account_namespace() is None
