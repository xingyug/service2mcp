"""Unit tests for apps/mcp_runtime/grpc_unary.py helper functions and executor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.grpc_unary import (
    ReflectionGrpcUnaryExecutor,
    _method_full_name,
    _prime_service_descriptor,
    _request_payload,
)
from libs.ir.models import GrpcUnaryRuntimeConfig, Operation, ServiceIR


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


class TestInvokeSyncErrorHandling:
    def _make_executor(
        self, base_url: str = "grpc://localhost:50051"
    ) -> ReflectionGrpcUnaryExecutor:
        ir = ServiceIR(
            service_id="test-svc",
            service_name="Test",
            base_url=base_url,
            source_hash="sha256:test",
            protocol="grpc",
            operations=[],
        )
        return ReflectionGrpcUnaryExecutor(ir)

    def test_non_dict_response_raises_tool_error(self) -> None:
        executor = self._make_executor()
        op = Operation(
            id="op1",
            name="test",
            method="grpc",
            path="/pkg.Svc/Method",
            description="Test operation",
            enabled=True,
        )
        config = GrpcUnaryRuntimeConfig(
            rpc_path="/pkg.Svc/Method",
            timeout_seconds=30,
        )

        # Mock the channel and response to return a non-dict
        with patch.object(executor, "_build_channel") as mock_build_channel:
            mock_channel = MagicMock()
            mock_build_channel.return_value = mock_channel

            # Mock the reflection and protobuf setup
            with (
                patch("apps.mcp_runtime.grpc_unary.ProtoReflectionDescriptorDatabase"),
                patch("apps.mcp_runtime.grpc_unary.DescriptorPool") as mock_pool_cls,
                patch("apps.mcp_runtime.grpc_unary._method_full_name") as mock_method_name,
                patch("apps.mcp_runtime.grpc_unary._prime_service_descriptor"),
                patch("apps.mcp_runtime.grpc_unary.GetMessageClass") as mock_get_class,
                patch("apps.mcp_runtime.grpc_unary._request_payload") as mock_payload,
                patch("apps.mcp_runtime.grpc_unary.json_format") as mock_json,
            ):
                mock_method_name.return_value = "pkg.Svc.Method"
                mock_payload.return_value = {}

                # Setup mock classes and descriptors
                mock_pool = MagicMock()
                mock_pool_cls.return_value = mock_pool
                mock_method_desc = MagicMock()
                mock_pool.FindMethodByName.return_value = mock_method_desc

                mock_request_class = MagicMock()
                mock_response_class = MagicMock()
                mock_get_class.side_effect = [mock_request_class, mock_response_class]

                # Mock the actual unary call
                mock_invoke = MagicMock()
                mock_response = MagicMock()
                mock_invoke.return_value = mock_response
                mock_channel.unary_unary.return_value = mock_invoke

                # Make MessageToDict return a non-dict (like a string)
                mock_json.MessageToDict.return_value = "not_a_dict"

                with pytest.raises(ToolError, match="returned a non-object protobuf message"):
                    executor._invoke_sync(op, {}, config)
                mock_channel.close.assert_called_once_with()

    def test_grpc_rpc_error_handling(self) -> None:
        """Test general exception handling - covers line 87-90."""
        executor = self._make_executor()
        op = Operation(
            id="op1",
            name="test",
            method="grpc",
            path="/pkg.Svc/Method",
            description="Test operation",
            enabled=True,
        )
        config = GrpcUnaryRuntimeConfig(
            rpc_path="/pkg.Svc/Method",
            timeout_seconds=30,
        )

        with patch.object(executor, "_build_channel") as mock_build_channel:
            mock_channel = MagicMock()
            mock_build_channel.return_value = mock_channel

            # Mock a general exception
            general_error = ValueError("Test exception")
            with patch(
                "apps.mcp_runtime.grpc_unary.ProtoReflectionDescriptorDatabase"
            ) as mock_reflection_db:
                mock_reflection_db.side_effect = general_error

                with pytest.raises(ToolError, match="Native grpc unary invocation failed"):
                    executor._invoke_sync(op, {}, config)
                mock_channel.close.assert_called_once_with()

    def test_tool_error_passthrough(self) -> None:
        executor = self._make_executor()
        op = Operation(
            id="op1",
            name="test",
            method="grpc",
            path="/pkg.Svc/Method",
            description="Test operation",
            enabled=True,
        )
        config = GrpcUnaryRuntimeConfig(
            rpc_path="/pkg.Svc/Method",
            timeout_seconds=30,
        )

        with patch.object(executor, "_build_channel") as mock_build_channel:
            mock_channel = MagicMock()
            mock_build_channel.return_value = mock_channel

            # Mock a ToolError being raised
            tool_error = ToolError("Custom tool error")
            with patch(
                "apps.mcp_runtime.grpc_unary.ProtoReflectionDescriptorDatabase"
            ) as mock_reflection_db:
                mock_reflection_db.side_effect = tool_error

                # ToolError should be re-raised as-is
                with pytest.raises(ToolError, match="Custom tool error"):
                    executor._invoke_sync(op, {}, config)

    def test_general_exception_handling(self) -> None:
        executor = self._make_executor()
        op = Operation(
            id="op1",
            name="test",
            method="grpc",
            path="/pkg.Svc/Method",
            description="Test operation",
            enabled=True,
        )
        config = GrpcUnaryRuntimeConfig(
            rpc_path="/pkg.Svc/Method",
            timeout_seconds=30,
        )

        with patch.object(executor, "_build_channel") as mock_build_channel:
            mock_channel = MagicMock()
            mock_build_channel.return_value = mock_channel

            # Mock a general exception
            general_error = ValueError("Some unexpected error")
            with patch(
                "apps.mcp_runtime.grpc_unary.ProtoReflectionDescriptorDatabase"
            ) as mock_reflection_db:
                mock_reflection_db.side_effect = general_error

                with pytest.raises(
                    ToolError, match="Native grpc unary invocation failed.*Some unexpected error"
                ):
                    executor._invoke_sync(op, {}, config)
