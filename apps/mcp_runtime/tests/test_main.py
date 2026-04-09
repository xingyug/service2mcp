"""Unit tests for apps/mcp_runtime/main.py — RuntimeState, build helpers, env checks."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.mcp_runtime.main import (
    RuntimeState,
    _native_grpc_stream_runtime_enabled,
    _native_grpc_unary_runtime_enabled,
    _native_sql_runtime_enabled,
    build_runtime_state,
)
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcUnaryRuntimeConfig,
    Operation,
    RequestSigningConfig,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
)


def _minimal_ir(**overrides: object) -> ServiceIR:
    defaults: dict[str, object] = {
        "service_id": "test-svc",
        "service_name": "Test Service",
        "base_url": "https://example.com",
        "source_hash": "sha256:test",
        "protocol": "openapi",
        "operations": [],
    }
    defaults.update(overrides)
    return ServiceIR(**defaults)


def _write_ir(ir: ServiceIR, dir_path: Path) -> Path:
    path = dir_path / "ir.json"
    path.write_text(ir.model_dump_json())
    return path


class TestRuntimeState:
    def test_not_loaded_by_default(self) -> None:
        state = RuntimeState()
        assert state.is_loaded is False

    def test_loaded_when_ir_set(self) -> None:
        state = RuntimeState()
        state.service_ir = _minimal_ir()
        assert state.is_loaded is True

    def test_not_loaded_when_error_set(self) -> None:
        state = RuntimeState()
        state.service_ir = _minimal_ir()
        state.load_error = "something went wrong"
        assert state.is_loaded is False

    @pytest.mark.asyncio
    async def test_aclose_without_proxy(self) -> None:
        state = RuntimeState()
        await state.aclose()  # should not raise

    @pytest.mark.asyncio
    async def test_aclose_with_proxy(self) -> None:
        from unittest.mock import AsyncMock

        state = RuntimeState()
        mock_proxy = AsyncMock()
        state.proxy = mock_proxy
        await state.aclose()
        mock_proxy.aclose.assert_awaited_once()


class TestBuildRuntimeState:
    def test_no_path_returns_error(self) -> None:
        state = build_runtime_state(None)
        assert state.is_loaded is False
        assert "not configured" in (state.load_error or "").lower()

    def test_missing_file_returns_error(self) -> None:
        state = build_runtime_state("/nonexistent/path/ir.json")
        assert state.is_loaded is False
        assert state.load_error is not None

    def test_valid_ir_loads(self) -> None:
        ir = _minimal_ir()
        with tempfile.TemporaryDirectory() as tmpdir:
            ir_path = _write_ir(ir, Path(tmpdir))
            state = build_runtime_state(str(ir_path))
        assert state.is_loaded is True
        assert state.service_ir is not None
        assert state.service_ir.service_name == "Test Service"

    def test_proxy_created(self) -> None:
        ir = _minimal_ir()
        with tempfile.TemporaryDirectory() as tmpdir:
            ir_path = _write_ir(ir, Path(tmpdir))
            state = build_runtime_state(str(ir_path))
        assert state.proxy is not None

    def test_rejects_ambiguous_runtime_secret_refs(self) -> None:
        ir = _minimal_ir(
            auth=AuthConfig(
                type=AuthType.bearer,
                runtime_secret_ref="client-id",
                request_signing=RequestSigningConfig(secret_ref="client_id"),
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ir_path = _write_ir(ir, Path(tmpdir))
            state = build_runtime_state(str(ir_path))
        assert state.is_loaded is False
        assert state.load_error is not None
        assert "normalize to the same env name" in state.load_error

    def test_operations_registered(self) -> None:
        op = Operation(
            id="get_item",
            operation_id="get_item",
            name="Get Item",
            description="Gets an item",
            method="GET",
            path="/items/{id}",
            enabled=True,
            risk=RiskMetadata(risk_level=RiskLevel.safe),
        )
        ir = _minimal_ir(operations=[op])
        with tempfile.TemporaryDirectory() as tmpdir:
            ir_path = _write_ir(ir, Path(tmpdir))
            state = build_runtime_state(str(ir_path))
        assert "get_item" in state.registered_operations


class TestNativeGrpcStreamEnabled:
    def _ir_with_grpc_stream(self) -> ServiceIR:
        from libs.ir.models import GrpcStreamMode, GrpcStreamRuntimeConfig

        return _minimal_ir(
            event_descriptors=[
                EventDescriptor(
                    id="stream1",
                    name="stream1",
                    transport=EventTransport.grpc_stream,
                    support=EventSupportLevel.supported,
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/test.Service/Stream",
                        mode=GrpcStreamMode.server,
                    ),
                )
            ]
        )

    def test_disabled_by_default(self) -> None:
        ir = self._ir_with_grpc_stream()
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ENABLE_NATIVE_GRPC_STREAM", None)
            assert _native_grpc_stream_runtime_enabled(ir) is False

    def test_enabled_with_env(self) -> None:
        ir = self._ir_with_grpc_stream()
        with patch.dict(os.environ, {"ENABLE_NATIVE_GRPC_STREAM": "1"}):
            assert _native_grpc_stream_runtime_enabled(ir) is True

    def test_enabled_but_no_descriptors(self) -> None:
        ir = _minimal_ir()
        with patch.dict(os.environ, {"ENABLE_NATIVE_GRPC_STREAM": "true"}):
            assert _native_grpc_stream_runtime_enabled(ir) is False


class TestNativeGrpcUnaryEnabled:
    def _ir_with_grpc_unary(self) -> ServiceIR:
        rpc_path = "/test.TestService/GetItem"
        op = Operation(
            id="grpc_op",
            operation_id="grpc_op",
            name="Grpc Op",
            description="A gRPC op",
            method="POST",
            path=rpc_path,
            enabled=True,
            risk=RiskMetadata(risk_level=RiskLevel.safe),
            grpc_unary=GrpcUnaryRuntimeConfig(rpc_path=rpc_path),
        )
        return _minimal_ir(operations=[op])

    def test_disabled_by_default(self) -> None:
        ir = self._ir_with_grpc_unary()
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ENABLE_NATIVE_GRPC_UNARY", None)
            assert _native_grpc_unary_runtime_enabled(ir) is False

    def test_enabled_with_env(self) -> None:
        ir = self._ir_with_grpc_unary()
        with patch.dict(os.environ, {"ENABLE_NATIVE_GRPC_UNARY": "true"}):
            assert _native_grpc_unary_runtime_enabled(ir) is True

    def test_enabled_but_no_grpc_ops(self) -> None:
        ir = _minimal_ir()
        with patch.dict(os.environ, {"ENABLE_NATIVE_GRPC_UNARY": "1"}):
            assert _native_grpc_unary_runtime_enabled(ir) is False


class TestNativeSqlEnabled:
    def _ir_with_sql(self) -> ServiceIR:
        op = Operation(
            id="sql_op",
            operation_id="sql_op",
            name="SQL Query",
            description="A SQL query",
            method="GET",
            path="/sql",
            enabled=True,
            risk=RiskMetadata(risk_level=RiskLevel.safe),
            sql=SqlOperationConfig(
                schema_name="public",
                relation_name="items",
                relation_kind=SqlRelationKind.table,
                action=SqlOperationType.query,
                filterable_columns=["id"],
            ),
        )
        return _minimal_ir(operations=[op])

    def test_enabled_when_sql_ops_present(self) -> None:
        ir = self._ir_with_sql()
        assert _native_sql_runtime_enabled(ir) is True

    def test_disabled_when_no_sql_ops(self) -> None:
        ir = _minimal_ir()
        assert _native_sql_runtime_enabled(ir) is False
