"""Unit tests for apps/mcp_runtime/grpc_unary.py helper functions and executor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.grpc_unary import (
    ReflectionGrpcUnaryExecutor,
    _method_full_name,
    _prime_service_descriptor,
    _request_payload,
)
from libs.ir.models import ServiceIR


class TestMethodFullName:
    def test_standard_rpc_path(self) -> None:
        assert _method_full_name("/mypackage.MyService/MyMethod") == "mypackage.MyService.MyMethod"

    def test_no_leading_slash(self) -> None:
        assert _method_full_name("mypackage.MyService/MyMethod") == "mypackage.MyService.MyMethod"

    def test_empty_service_raises(self) -> None:
        with pytest.raises(ToolError, match="invalid"):
            _method_full_name("/")

    def test_no_method_raises(self) -> None:
        with pytest.raises(ToolError, match="invalid"):
            _method_full_name("/mypackage.MyService/")

    def test_no_service_raises(self) -> None:
        with pytest.raises(ToolError, match="invalid"):
            _method_full_name("//MyMethod")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ToolError, match="invalid"):
            _method_full_name("")

    def test_multiple_slashes(self) -> None:
        # partition splits on first /
        result = _method_full_name("/pkg.Svc/Method/Extra")
        assert result == "pkg.Svc.Method/Extra"


class TestRequestPayload:
    def test_returns_payload_dict_if_present(self) -> None:
        args = {"payload": {"field1": "value1"}, "other": "ignored"}
        assert _request_payload(args) == {"field1": "value1"}

    def test_returns_non_none_args_without_payload(self) -> None:
        args = {"field1": "value1", "field2": None, "field3": 42}
        result = _request_payload(args)
        assert result == {"field1": "value1", "field3": 42}

    def test_payload_non_dict_falls_through(self) -> None:
        args = {"payload": "not_a_dict", "field1": "value1"}
        result = _request_payload(args)
        assert result == {"payload": "not_a_dict", "field1": "value1"}

    def test_empty_args(self) -> None:
        assert _request_payload({}) == {}

    def test_all_none_values(self) -> None:
        assert _request_payload({"a": None, "b": None}) == {}


class TestPrimeServiceDescriptor:
    def test_calls_find_file_for_service(self) -> None:
        pool = MagicMock()
        _prime_service_descriptor(pool, "mypackage.MyService.MyMethod")
        pool.FindFileContainingSymbol.assert_called_once_with("mypackage.MyService")

    def test_no_call_for_empty_service(self) -> None:
        pool = MagicMock()
        _prime_service_descriptor(pool, "MyMethod")
        pool.FindFileContainingSymbol.assert_not_called()

    def test_nested_package(self) -> None:
        pool = MagicMock()
        _prime_service_descriptor(pool, "com.example.v1.GreeterService.SayHello")
        pool.FindFileContainingSymbol.assert_called_once_with("com.example.v1.GreeterService")


class TestBuildChannel:
    def _make_executor(self, base_url: str) -> ReflectionGrpcUnaryExecutor:
        ir = ServiceIR(
            service_id="test-svc",
            service_name="Test",
            base_url=base_url,
            source_hash="sha256:test",
            protocol="grpc",
            operations=[],
        )
        return ReflectionGrpcUnaryExecutor(ir)

    def test_grpc_scheme_returns_insecure_channel(self) -> None:
        executor = self._make_executor("grpc://localhost:50051")
        channel = executor._build_channel()
        assert channel is not None
        channel.close()

    def test_grpcs_scheme_returns_secure_channel(self) -> None:
        executor = self._make_executor("grpcs://localhost:50051")
        channel = executor._build_channel()
        assert channel is not None
        channel.close()

    def test_unsupported_scheme_raises(self) -> None:
        executor = self._make_executor("http://localhost:50051")
        with pytest.raises(ToolError, match="not supported for grpc unary"):
            executor._build_channel()

    def test_empty_target_raises(self) -> None:
        executor = self._make_executor("")
        with pytest.raises(ToolError, match="not a valid grpc target"):
            executor._build_channel()
