"""Unit tests for apps/compiler_worker/activities/production.py — pure helper functions."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.compiler_worker.activities.production import (
    _apply_auth_override,
    _apply_scope_override,
    _apply_post_enhancement,
    _build_extractors,
    _close_extractors,
    _deserialize_manifest_set,
    _enhancement_enabled,
    _has_native_grpc_unary,
    _has_supported_native_grpc_stream,
    _is_safe_optional_grpc_sample_param,
    _manifest_set_from_context,
    _preferred_smoke_tool_ids,
    _read_service_account_namespace,
    _resolve_extractor,
    _sample_graphql_arguments,
    _sample_grpc_arguments,
    _sample_invocation_overrides,
    _sample_sql_arguments,
    _sample_value,
    _serialize_manifest_set,
    _source_config_from_context,
    _stage_result,
    _tool_grouping_enabled,
    _validation_failure_message,
    build_sample_invocations,
    create_default_activity_registry,
)
from apps.compiler_worker.models import CompilationContext, CompilationRequest, CompilationStage
from apps.compiler_worker.models import StageExecutionResult
from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthType,
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
from libs.sample_placeholders import PATH_PLACEHOLDER_ID_SAMPLE

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
        ir = _ir(
            operations=[
                _op(
                    "get_items",
                    params=[Param(name="limit", type="integer", required=False, default=10)],
                ),
                _op("disabled_op", enabled=False),
            ]
        )
        result = build_sample_invocations(ir)
        assert "get_items" in result
        assert "disabled_op" not in result
        assert result["get_items"]["limit"] == 10

    def test_skips_optional_http_like_params_without_defaults(self) -> None:
        ir = _ir(
            operations=[
                _op(
                    "list_products",
                    params=[
                        Param(name="limit", type="integer", required=False),
                        Param(name="search", type="string", required=False),
                        Param(name="status", type="string", required=True),
                    ],
                )
            ]
        )

        result = build_sample_invocations(ir)

        assert result["list_products"] == {"status": "available"}

    def test_includes_path_params_even_when_not_marked_required(self) -> None:
        operation = _op(
            "get_comment",
            params=[Param(name="id", type="string", required=False)],
        )
        operation = operation.model_copy(update={"path": "/comments/{id}"})
        ir = _ir(operations=[operation])

        result = build_sample_invocations(ir)

        assert result["get_comment"] == {"id": PATH_PLACEHOLDER_ID_SAMPLE}

    def test_empty_operations(self) -> None:
        ir = _ir(operations=[])
        assert build_sample_invocations(ir) == {}


class TestPreferredSmokeToolIds:
    def test_returns_string_ids_only(self) -> None:
        result = _preferred_smoke_tool_ids(
            {"preferred_smoke_tool_ids": ["list_users", "", 123, "GetOrderStatus"]}
        )

        assert result == ("list_users", "GetOrderStatus")


class TestSampleInvocationOverrides:
    def test_filters_non_mapping_overrides(self) -> None:
        result = _sample_invocation_overrides(
            {
                "sample_invocation_overrides": {
                    "GetOrderStatus": {"orderId": "ORD-1001"},
                    "bad": "not-a-mapping",
                }
            }
        )

        assert result == {"GetOrderStatus": {"orderId": "ORD-1001"}}


# --- _sample_grpc_arguments ---


class TestSampleGrpcArguments:
    def test_required_only(self) -> None:
        op = _op(
            params=[
                Param(name="id", type="string", required=True),
                Param(name="data", type="object", required=False),
            ]
        )
        result = _sample_grpc_arguments(op)
        assert "id" in result
        assert "data" not in result  # optional object skipped

    def test_safe_optional_included(self) -> None:
        op = _op(
            params=[
                Param(name="limit", type="integer", required=False),
                Param(name="page_token", type="string", required=False),
            ]
        )
        result = _sample_grpc_arguments(op)
        assert "limit" in result
        assert "page_token" in result

    def test_id_suffix_param_included(self) -> None:
        op = _op(
            params=[
                Param(name="user_id", type="string", required=False),
            ]
        )
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

    def test_skip_enhancement_option_disables_even_with_api_key(self) -> None:
        with patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}, clear=False):
            assert _enhancement_enabled({"skip_enhancement": True}) is False


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

    def test_force_protocol_takes_precedence_over_legacy_protocol_option(self) -> None:
        from uuid import UUID

        from apps.compiler_worker.models import CompilationContext, CompilationRequest

        ctx = CompilationContext(
            job_id=UUID(_TEST_UUID),
            request=CompilationRequest(
                source_url="https://example.com/api.yaml",
                options={"protocol": "rest", "force_protocol": "openapi"},
            ),
            payload={},
        )
        source = _source_config_from_context(ctx)
        assert source.hints["protocol"] == "openapi"


class TestApplyAuthOverride:
    def test_returns_original_ir_without_auth_override(self) -> None:
        ir = _ir()
        assert _apply_auth_override(ir, {}) == ir

    def test_applies_runtime_auth_override(self) -> None:
        ir = _ir()
        updated = _apply_auth_override(
            ir,
            {
                "auth": {
                    "type": "bearer",
                    "runtime_secret_ref": "directus-access-token",
                }
            },
        )
        assert updated.auth.type == AuthType.bearer
        assert updated.auth.runtime_secret_ref == "directus-access-token"

    def test_rejects_invalid_auth_override(self) -> None:
        ir = _ir()
        with pytest.raises(ValueError, match="custom_header auth requires header_name"):
            _apply_auth_override(ir, {"auth": {"type": "custom_header"}})

    def test_applies_frontend_auth_config_key(self) -> None:
        ir = _ir()
        updated = _apply_auth_override(
            ir,
            {
                "auth_config": {
                    "type": "bearer",
                    "runtime_secret_ref": "frontend-bearer-secret",
                }
            },
        )

        assert updated.auth.type == AuthType.bearer
        assert updated.auth.runtime_secret_ref == "frontend-bearer-secret"

    def test_normalizes_legacy_frontend_basic_override(self) -> None:
        ir = _ir()
        updated = _apply_auth_override(
            ir,
            {
                "auth_config": {
                    "type": "basic",
                    "username": "svc-user",
                    "password_secret_ref": "secret://password",
                }
            },
        )

        assert updated.auth.type == AuthType.basic
        assert updated.auth.basic_username == "svc-user"
        assert updated.auth.basic_password_ref == "secret://password"

    def test_normalizes_legacy_frontend_oauth2_override(self) -> None:
        ir = _ir()
        updated = _apply_auth_override(
            ir,
            {
                "auth_config": {
                    "type": "oauth2",
                    "token_url": "https://auth.example.com/token",
                    "client_id": "client-id",
                    "client_secret_ref": "secret://oauth2-secret",
                }
            },
        )

        assert updated.auth.type == AuthType.oauth2
        assert updated.auth.oauth2 is not None
        assert updated.auth.oauth2.token_url == "https://auth.example.com/token"
        assert updated.auth.oauth2.client_id == "client-id"
        assert updated.auth.oauth2.client_secret_ref == "secret://oauth2-secret"


class TestApplyScopeOverride:
    def test_applies_tenant_and_environment_from_options(self) -> None:
        ir = _ir()
        updated = _apply_scope_override(
            ir,
            {"tenant": "team-a", "environment": "prod"},
        )

        assert updated.tenant == "team-a"
        assert updated.environment == "prod"


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

    @pytest.mark.asyncio
    async def test_extract_stage_prefers_explicit_service_id_over_display_name(self) -> None:
        registry = create_default_activity_registry(session_factory=MagicMock())
        extractor = MagicMock()
        extractor.extract.return_value = _ir()
        context = CompilationContext(
            job_id=MagicMock(),
            request=CompilationRequest(
                source_url="https://example.com/spec.yaml",
                service_id="billing-api",
                service_name="Billing API",
            ),
        )

        with (
            patch(
                "apps.compiler_worker.activities.production._build_extractors",
                return_value=[],
            ),
            patch(
                "apps.compiler_worker.activities.production._resolve_extractor",
                return_value=extractor,
            ),
            patch(
                "apps.compiler_worker.activities.production._next_version_number",
                new=AsyncMock(return_value=1),
            ),
        ):
            result = await registry.run_stage(CompilationStage.EXTRACT, context)

        assert result.context_updates["service_id"] == "billing-api"
        assert result.service_name == "billing-api"
        assert result.context_updates["service_ir"]["service_name"] == "Billing API"


# --- Validation failure message ---


class TestValidationFailureMessage:
    def test_no_failures(self) -> None:
        report = SimpleNamespace(
            results=[
                SimpleNamespace(passed=True, stage="s1", details="ok"),
            ]
        )
        msg = _validation_failure_message("Pre-deploy failed", report)
        assert msg == "Pre-deploy failed"

    def test_with_failures(self) -> None:
        report = SimpleNamespace(
            results=[
                SimpleNamespace(passed=False, stage="schema", details="missing field"),
                SimpleNamespace(passed=True, stage="lint", details="ok"),
                SimpleNamespace(passed=False, stage="auth", details="no key"),
            ]
        )
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
        ir = _ir(
            operations=[
                _op(
                    "grpc_op",
                    method="POST",
                    grpc_unary=GrpcUnaryRuntimeConfig(rpc_path="/grpc_op"),
                ),
            ]
        )
        assert _has_native_grpc_unary(ir) is True

    def test_disabled_op_not_counted(self) -> None:
        ir = _ir(
            operations=[
                _op(
                    "grpc_op",
                    method="POST",
                    enabled=False,
                    risk_level=RiskLevel.unknown,
                    grpc_unary=GrpcUnaryRuntimeConfig(rpc_path="/grpc_op"),
                ),
            ]
        )
        assert _has_native_grpc_unary(ir) is False

    def test_no_grpc_unary(self) -> None:
        ir = _ir(operations=[_op("regular_op")])
        assert _has_native_grpc_unary(ir) is False


# --- _apply_post_enhancement ---


class TestApplyPostEnhancement:
    def test_basic_no_grouping(self) -> None:
        ir = _ir(
            operations=[
                _op("get_items", params=[Param(name="q", type="string", required=False)]),
            ]
        )
        with patch.dict(os.environ, {"WORKER_ENABLE_TOOL_GROUPING": ""}):
            result = _apply_post_enhancement(ir)
        # Should still have operations with tool_intent set
        assert len(result.operations) == 1

    def test_grouping_failure_continues(self) -> None:
        ir = _ir(
            operations=[
                _op("get_items"),
            ]
        )

        def bad_factory() -> Any:
            raise RuntimeError("LLM unavailable")

        with patch.dict(os.environ, {"WORKER_ENABLE_TOOL_GROUPING": "1"}):
            result = _apply_post_enhancement(ir, llm_client_factory=bad_factory)
        assert len(result.operations) == 1


# --- _read_service_account_namespace ---


class TestReadServiceAccountNamespace:
    def test_not_on_k8s(self) -> None:
        assert _read_service_account_namespace() is None


# --- _float_env ---


class TestFloatEnv:
    def test_returns_default_when_unset(self) -> None:
        from apps.compiler_worker.activities.production import _float_env

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MY_TEST_FLOAT", None)
            assert _float_env("MY_TEST_FLOAT", 42.0) == 42.0

    def test_returns_parsed_value(self) -> None:
        from apps.compiler_worker.activities.production import _float_env

        with patch.dict(os.environ, {"MY_TEST_FLOAT": "3.14"}):
            assert _float_env("MY_TEST_FLOAT", 0.0) == pytest.approx(3.14)

    def test_returns_default_on_non_numeric(self) -> None:
        from apps.compiler_worker.activities.production import _float_env

        with patch.dict(os.environ, {"MY_TEST_FLOAT": "not-a-number"}):
            assert _float_env("MY_TEST_FLOAT", 99.0) == 99.0


class TestRoutePublishConfiguration:
    def test_from_env_requires_explicit_route_publish_mode(self) -> None:
        from apps.compiler_worker.activities.production import ProductionActivitySettings

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTE_PUBLISH_MODE", None)
            settings = ProductionActivitySettings.from_env()

        assert settings.route_publish_mode is None

    def test_resolve_route_publisher_rejects_missing_mode(self) -> None:
        from apps.compiler_worker.activities.production import (
            ProductionActivitySettings,
            _resolve_route_publisher,
        )

        with pytest.raises(
            RuntimeError,
            match="ROUTE_PUBLISH_MODE must be explicitly set",
        ):
            _resolve_route_publisher(
                ProductionActivitySettings(route_publish_mode=None),
                None,
            )

    def test_resolve_route_publisher_allows_explicit_deferred_mode(self) -> None:
        from apps.compiler_worker.activities.production import (
            DeferredRoutePublisher,
            ProductionActivitySettings,
            _resolve_route_publisher,
        )

        publisher = _resolve_route_publisher(
            ProductionActivitySettings(route_publish_mode="deferred"),
            None,
        )

        assert isinstance(publisher, DeferredRoutePublisher)

    @pytest.mark.asyncio
    async def test_route_stage_requires_explicit_mode(self) -> None:
        from apps.compiler_worker.activities.production import ProductionActivitySettings

        registry = create_default_activity_registry(
            session_factory=MagicMock(),
            settings=ProductionActivitySettings(route_publish_mode=None),
        )
        context = CompilationContext(
            job_id=MagicMock(),
            request=CompilationRequest(service_name="billing-api"),
            payload={
                "service_id": "billing-api",
                "route_config": {
                    "default_route": {"route_id": "billing-api-active"},
                    "version_route": {"route_id": "billing-api-v1"},
                },
            },
            protocol="openapi",
        )

        with pytest.raises(
            RuntimeError,
            match="ROUTE_PUBLISH_MODE must be explicitly set",
        ):
            await registry.run_stage(CompilationStage.ROUTE, context)


# --- DeferredRoutePublisher ---


class TestDeferredRoutePublisher:
    @pytest.mark.asyncio
    async def test_rollback_is_noop(self) -> None:
        from apps.compiler_worker.activities.production import DeferredRoutePublisher

        publisher = DeferredRoutePublisher()
        # rollback should complete without raising
        await publisher.rollback({"default_route": {"route_id": "r1"}}, None)


# --- AccessControlRoutePublisher._post() edge cases ---


class TestAccessControlRoutePublisherPost:
    @pytest.mark.asyncio
    async def test_non_json_response_raises(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from apps.compiler_worker.activities.production import AccessControlRoutePublisher

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(side_effect=ValueError("No JSON"))

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        publisher = AccessControlRoutePublisher(
            base_url="http://fake",
            client=mock_client,
            auth_token="fake-token",
        )

        with pytest.raises(RuntimeError, match="non-JSON response"):
            await publisher._post("/test", route_config={"key": "value"})

    @pytest.mark.asyncio
    async def test_non_dict_response_raises(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from apps.compiler_worker.activities.production import AccessControlRoutePublisher

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[])  # array, not dict

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        publisher = AccessControlRoutePublisher(
            base_url="http://fake",
            client=mock_client,
            auth_token="fake-token",
        )

        with pytest.raises(RuntimeError, match="non-object response"):
            await publisher._post("/test", route_config={"key": "value"})

    @pytest.mark.asyncio
    async def test_rollback_rejects_non_object_publication(self) -> None:
        from apps.compiler_worker.activities.production import AccessControlRoutePublisher

        publisher = AccessControlRoutePublisher(
            base_url="http://fake",
            client=AsyncMock(),
            auth_token="fake-token",
        )

        with pytest.raises(RuntimeError, match="publication must be an object"):
            await publisher.rollback({"default_route": {"route_id": "r1"}}, "published")  # type: ignore[arg-type]


class TestKubernetesManifestDeployerResponseShape:
    @pytest.mark.asyncio
    async def test_apply_manifest_non_object_response_raises(self) -> None:
        from apps.compiler_worker.activities.production import KubernetesManifestDeployer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_error = False
        mock_response.json.return_value = []

        mock_client = AsyncMock()
        mock_client.patch = AsyncMock(return_value=mock_response)

        deployer = KubernetesManifestDeployer(
            api=SimpleNamespace(client=mock_client, namespace="default"),
            owns_api_client=False,
        )

        with pytest.raises(RuntimeError, match="non-object response"):
            await deployer._apply_manifest(
                "deployments",
                "apps/v1",
                {"metadata": {"name": "demo"}},
            )

    @pytest.mark.asyncio
    async def test_wait_for_rollout_non_object_response_raises(self) -> None:
        from apps.compiler_worker.activities.production import KubernetesManifestDeployer

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        deployer = KubernetesManifestDeployer(
            api=SimpleNamespace(client=mock_client, namespace="default"),
            owns_api_client=False,
        )

        with pytest.raises(RuntimeError, match="non-object response"):
            await deployer._wait_for_rollout("demo", expected_replicas=1)

    @pytest.mark.asyncio
    async def test_deploy_cleans_up_partial_apply_failure(self) -> None:
        from apps.compiler_worker.activities.production import KubernetesManifestDeployer

        deployer = KubernetesManifestDeployer(
            api=SimpleNamespace(client=AsyncMock(), namespace="default"),
            owns_api_client=False,
        )
        deployer._apply_manifest = AsyncMock(
            side_effect=[
                {"metadata": {"resourceVersion": "1"}},
                {"metadata": {"resourceVersion": "2"}},
                RuntimeError("service apply failed"),
            ]
        )
        deployer._delete_manifest = AsyncMock()
        manifest_set = SimpleNamespace(
            config_map={"metadata": {"name": "demo-ir"}},
            deployment={"metadata": {"name": "demo"}, "spec": {"replicas": 1}},
            service={"metadata": {"name": "demo"}, "spec": {"ports": [{"port": 8003}]}},
            network_policy={"metadata": {"name": "demo"}},
        )

        with pytest.raises(RuntimeError, match="service apply failed"):
            await deployer.deploy(manifest_set)

        deleted = [call.args for call in deployer._delete_manifest.await_args_list]
        assert deleted == [
            ("deployments", "apps/v1", "demo"),
            ("configmaps", "v1", "demo-ir"),
        ]

    @pytest.mark.asyncio
    async def test_rollback_attempts_all_deletes_before_raising(self) -> None:
        from apps.compiler_worker.activities.production import KubernetesManifestDeployer

        deployer = KubernetesManifestDeployer(
            api=SimpleNamespace(client=AsyncMock(), namespace="default"),
            owns_api_client=False,
        )
        deployer._delete_manifest = AsyncMock(
            side_effect=[RuntimeError("np failed"), None, None, None]
        )
        manifest_set = SimpleNamespace(
            config_map={"metadata": {"name": "demo-ir"}},
            deployment={"metadata": {"name": "demo"}},
            service={"metadata": {"name": "demo"}},
            network_policy={"metadata": {"name": "demo"}},
        )

        with pytest.raises(RuntimeError, match="networkpolicies/demo"):
            await deployer.rollback(manifest_set, MagicMock())

        assert deployer._delete_manifest.await_count == 4

    @pytest.mark.asyncio
    async def test_wait_for_rollout_counts_request_time_against_timeout(self) -> None:
        from apps.compiler_worker.activities.production import KubernetesManifestDeployer

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "metadata": {"generation": 1},
            "status": {
                "observedGeneration": 0,
                "availableReplicas": 0,
                "updatedReplicas": 0,
            },
        }
        calls = 0

        async def slow_get(_: str) -> MagicMock:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.2)
            return response

        deployer = KubernetesManifestDeployer(
            api=SimpleNamespace(client=SimpleNamespace(get=slow_get), namespace="default"),
            owns_api_client=False,
            rollout_poll_seconds=0.01,
            rollout_timeout_seconds=0.1,
        )

        with pytest.raises(RuntimeError, match="Timed out waiting for Kubernetes rollout"):
            await deployer._wait_for_rollout("demo", expected_replicas=1)

        assert calls == 1

    @pytest.mark.asyncio
    async def test_wait_for_rollout_requires_updated_replicas(self) -> None:
        from apps.compiler_worker.activities.production import KubernetesManifestDeployer

        first = MagicMock()
        first.raise_for_status = MagicMock()
        first.json.return_value = {
            "metadata": {"generation": 2},
            "status": {
                "observedGeneration": 2,
                "availableReplicas": 1,
                "updatedReplicas": 0,
            },
        }
        second = MagicMock()
        second.raise_for_status = MagicMock()
        second.json.return_value = {
            "metadata": {"generation": 2},
            "status": {
                "observedGeneration": 2,
                "availableReplicas": 1,
                "updatedReplicas": 1,
            },
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[first, second])

        deployer = KubernetesManifestDeployer(
            api=SimpleNamespace(client=mock_client, namespace="default"),
            owns_api_client=False,
            rollout_poll_seconds=0.01,
            rollout_timeout_seconds=1.0,
        )

        with patch("apps.compiler_worker.activities.production._sleep_seconds", new=AsyncMock()):
            observed_generation = await deployer._wait_for_rollout("demo", expected_replicas=1)

        assert observed_generation == 2
        assert mock_client.get.await_count == 2


class TestRuntimeReadinessTimeouts:
    @pytest.mark.asyncio
    async def test_wait_for_runtime_http_ready_counts_request_time_against_timeout(self) -> None:
        from apps.compiler_worker.activities.production import _wait_for_runtime_http_ready

        client_calls = 0

        class SlowClient:
            async def get(self, _: str) -> SimpleNamespace:
                await asyncio.sleep(0.2)
                return SimpleNamespace(status_code=503)

            async def aclose(self) -> None:
                return None

        def client_factory(_: str) -> SlowClient:
            nonlocal client_calls
            client_calls += 1
            return SlowClient()

        with pytest.raises(RuntimeError, match="Runtime readiness check timed out"):
            await _wait_for_runtime_http_ready(
                "http://runtime.example.test",
                client_factory=client_factory,
                timeout_seconds=0.1,
                poll_seconds=0.01,
            )

        assert client_calls == 1


class TestRollbackHandlerPayloadValidation:
    @pytest.mark.asyncio
    async def test_deploy_rollback_skips_partial_manifest_payload(self) -> None:
        deployer = AsyncMock()
        deployer.rollback = AsyncMock()
        registry = create_default_activity_registry(session_factory=MagicMock(), deployer=deployer)

        await registry.rollback_handlers[CompilationStage.DEPLOY](
            CompilationContext(
                job_id=MagicMock(),
                request=CompilationRequest(source_url="https://example.com/spec.yaml"),
            ),
            StageExecutionResult(
                rollback_payload={
                    "manifest_set": {
                        "deployment": {},
                        "service": {},
                        "network_policy": {},
                        "route_config": {},
                        "yaml": "apiVersion: v1",
                    },
                    "deployment": {
                        "deployment_revision": "rev-1",
                        "runtime_base_url": "http://svc.default",
                        "manifest_storage_path": "k8s://default/deployments/demo",
                    },
                }
            ),
        )

        deployer.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_route_rollback_skips_non_dict_publication_payload(self) -> None:
        route_publisher = AsyncMock()
        route_publisher.rollback = AsyncMock()
        registry = create_default_activity_registry(
            session_factory=MagicMock(),
            route_publisher=route_publisher,
        )

        await registry.rollback_handlers[CompilationStage.ROUTE](
            CompilationContext(
                job_id=MagicMock(),
                request=CompilationRequest(source_url="https://example.com/spec.yaml"),
            ),
            StageExecutionResult(
                rollback_payload={
                    "route_config": {"default_route": {"route_id": "r1"}},
                    "publication": "published",
                }
            ),
        )

        route_publisher.rollback.assert_not_awaited()


# --- KubernetesAPISession.from_in_cluster() ---


class TestKubernetesAPISessionFromInCluster:
    def test_missing_host_raises(self) -> None:
        from apps.compiler_worker.activities.production import KubernetesAPISession

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KUBERNETES_SERVICE_HOST", None)
            with pytest.raises(RuntimeError, match="KUBERNETES_SERVICE_HOST"):
                KubernetesAPISession.from_in_cluster(namespace="default")

    def test_missing_token_or_cert_raises(self) -> None:
        from unittest.mock import patch as mock_patch

        from apps.compiler_worker.activities.production import KubernetesAPISession

        k8s_env = {
            "KUBERNETES_SERVICE_HOST": "10.0.0.1",
            "KUBERNETES_SERVICE_PORT": "443",
        }
        with patch.dict(os.environ, k8s_env):
            with mock_patch("apps.compiler_worker.activities.production.Path") as mock_path:
                # Make token_path.exists() return False
                mock_token = MagicMock()
                mock_token.exists.return_value = False
                mock_cert = MagicMock()
                mock_cert.exists.return_value = True
                mock_path.side_effect = [mock_token, mock_cert]

                with pytest.raises(RuntimeError, match="in-cluster service account token"):
                    KubernetesAPISession.from_in_cluster(namespace="default")
