"""Unit tests for apps/mcp_runtime/grpc_stream.py helper functions and executor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.grpc_stream import (
    ReflectionGrpcStreamExecutor,
    _method_full_name,
    _prime_service_descriptor,
    _request_payload,
)
from libs.ir.models import GrpcStreamMode, GrpcStreamRuntimeConfig, Operation, ServiceIR


class TestMethodFullName:
    def test_standard_rpc_path(self) -> None:
        assert _method_full_name("/pkg.Svc/Watch") == "pkg.Svc.Watch"

    def test_no_leading_slash(self) -> None:
        assert _method_full_name("pkg.Svc/Watch") == "pkg.Svc.Watch"

    def test_empty_raises(self) -> None:
        with pytest.raises(ToolError, match="invalid"):
            _method_full_name("")

    def test_no_method_raises(self) -> None:
        with pytest.raises(ToolError, match="invalid"):
            _method_full_name("/pkg.Svc/")

    def test_no_service_raises(self) -> None:
        with pytest.raises(ToolError, match="invalid"):
            _method_full_name("//Watch")


class TestRequestPayload:
    def test_returns_payload_dict(self) -> None:
        assert _request_payload({"payload": {"k": "v"}}) == {"k": "v"}

    def test_filters_none_values(self) -> None:
        assert _request_payload({"a": 1, "b": None}) == {"a": 1}

    def test_payload_non_dict_falls_through(self) -> None:
        assert _request_payload({"payload": 42, "x": "y"}) == {"payload": 42, "x": "y"}

    def test_empty_args(self) -> None:
        assert _request_payload({}) == {}


class TestPrimeServiceDescriptor:
    def test_calls_find_file(self) -> None:
        pool = MagicMock()
        _prime_service_descriptor(pool, "pkg.Svc.Method")
        pool.FindFileContainingSymbol.assert_called_once_with("pkg.Svc")

    def test_no_call_without_package(self) -> None:
        pool = MagicMock()
        _prime_service_descriptor(pool, "Method")
        pool.FindFileContainingSymbol.assert_not_called()


class TestBuildChannel:
    def _make_executor(self, base_url: str) -> ReflectionGrpcStreamExecutor:
        ir = ServiceIR(
            service_id="test-svc",
            service_name="Test",
            base_url=base_url,
            source_hash="sha256:test",
            protocol="grpc",
            operations=[],
        )
        return ReflectionGrpcStreamExecutor(ir)

    def test_grpc_insecure(self) -> None:
        executor = self._make_executor("grpc://localhost:50051")
        channel = executor._build_channel()
        assert channel is not None
        channel.close()

    def test_grpcs_secure(self) -> None:
        executor = self._make_executor("grpcs://localhost:50051")
        channel = executor._build_channel()
        assert channel is not None
        channel.close()

    def test_unsupported_scheme_raises(self) -> None:
        executor = self._make_executor("http://localhost:50051")
        with pytest.raises(ToolError, match="not supported for grpc_stream"):
            executor._build_channel()

    def test_empty_target_raises(self) -> None:
        executor = self._make_executor("")
        with pytest.raises(ToolError, match="not a valid grpc target"):
            executor._build_channel()


class TestInvokeSyncRejectsNonServerMode:
    def test_client_mode_raises(self) -> None:
        ir = ServiceIR(
            service_id="test-svc",
            service_name="Test",
            base_url="grpc://localhost:50051",
            source_hash="sha256:test",
            protocol="grpc",
            operations=[],
        )
        executor = ReflectionGrpcStreamExecutor(ir)
        op = Operation(
            id="op1",
            name="watch",
            method="grpc",
            path="/pkg.Svc/Watch",
            description="Watch stream",
            enabled=False,
        )
        config = GrpcStreamRuntimeConfig(
            rpc_path="/pkg.Svc/Watch",
            mode=GrpcStreamMode.client,
        )
        with pytest.raises(ToolError, match="not implemented yet"):
            executor._invoke_sync(op, {}, config)

    def test_bidi_mode_raises(self) -> None:
        ir = ServiceIR(
            service_id="test-svc",
            service_name="Test",
            base_url="grpc://localhost:50051",
            source_hash="sha256:test",
            protocol="grpc",
            operations=[],
        )
        executor = ReflectionGrpcStreamExecutor(ir)
        op = Operation(
            id="op1",
            name="chat",
            method="grpc",
            path="/pkg.Svc/Chat",
            description="Bidi stream",
            enabled=False,
        )
        config = GrpcStreamRuntimeConfig(
            rpc_path="/pkg.Svc/Chat",
            mode=GrpcStreamMode.bidirectional,
        )
        with pytest.raises(ToolError, match="not implemented yet"):
            executor._invoke_sync(op, {}, config)
