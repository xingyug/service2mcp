"""Unit tests for apps/mcp_runtime/grpc_stream.py helper functions and executor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestInvokeSyncErrorHandling:
    def _make_executor(
        self, base_url: str = "grpc://localhost:50051"
    ) -> ReflectionGrpcStreamExecutor:
        ir = ServiceIR(
            service_id="test-svc",
            service_name="Test",
            base_url=base_url,
            source_hash="sha256:test",
            protocol="grpc",
            operations=[],
        )
        return ReflectionGrpcStreamExecutor(ir)

    def test_max_messages_termination_with_cancel(self) -> None:
        executor = self._make_executor()
        op = Operation(
            id="op1",
            name="watch",
            method="grpc",
            path="/pkg.Svc/Watch",
            description="Watch stream",
            enabled=True,
        )
        config = GrpcStreamRuntimeConfig(
            rpc_path="/pkg.Svc/Watch",
            mode=GrpcStreamMode.server,
            max_messages=2,
            idle_timeout_seconds=10,
        )

        with patch.object(executor, "_build_channel") as mock_build_channel:
            mock_channel = MagicMock()
            mock_build_channel.return_value = mock_channel

            # Mock the reflection and protobuf setup
            with (
                patch("apps.mcp_runtime.grpc_stream.ProtoReflectionDescriptorDatabase"),
                patch("apps.mcp_runtime.grpc_stream.DescriptorPool") as mock_pool_cls,
                patch("apps.mcp_runtime.grpc_stream._method_full_name") as mock_method_name,
                patch("apps.mcp_runtime.grpc_stream._prime_service_descriptor"),
                patch("apps.mcp_runtime.grpc_stream.GetMessageClass") as mock_get_class,
                patch("apps.mcp_runtime.grpc_stream._request_payload") as mock_payload,
                patch("apps.mcp_runtime.grpc_stream.json_format") as mock_json,
            ):
                mock_method_name.return_value = "pkg.Svc.Watch"
                mock_payload.return_value = {}

                # Setup mock classes and descriptors
                mock_pool = MagicMock()
                mock_pool_cls.return_value = mock_pool
                mock_method_desc = MagicMock()
                mock_pool.FindMethodByName.return_value = mock_method_desc

                mock_request_class = MagicMock()
                mock_response_class = MagicMock()
                mock_get_class.side_effect = [mock_request_class, mock_response_class]

                # Mock the stream call - create a mock that supports cancel()
                mock_stream = MagicMock()
                mock_responses = MagicMock()
                mock_responses.cancel = MagicMock()  # Mock the cancel method
                mock_stream.return_value = mock_responses
                mock_channel.unary_stream.return_value = mock_stream

                # Mock responses that exceed max_messages
                mock_response1 = MagicMock()
                mock_response2 = MagicMock()
                mock_response3 = MagicMock()  # This one should trigger max_messages termination

                mock_json.MessageToDict.side_effect = [
                    {"data": "response1"},
                    {"data": "response2"},
                    {"data": "response3"},
                ]

                # Mock the iterator to return 3 responses
                mock_responses.__iter__ = lambda self: iter(
                    [mock_response1, mock_response2, mock_response3]
                )

                result = executor._invoke_sync(op, {}, config)

                assert result["lifecycle"]["termination_reason"] == "max_messages"
                assert len(result["events"]) == 2
                # Verify cancel was called due to max_messages reached
                mock_responses.cancel.assert_called_once()
                mock_channel.close.assert_called_once_with()

    def test_tool_error_passthrough(self) -> None:
        executor = self._make_executor()
        op = Operation(
            id="op1",
            name="watch",
            method="grpc",
            path="/pkg.Svc/Watch",
            description="Watch stream",
            enabled=True,
        )
        config = GrpcStreamRuntimeConfig(
            rpc_path="/pkg.Svc/Watch",
            mode=GrpcStreamMode.server,
        )

        with patch.object(executor, "_build_channel") as mock_build_channel:
            mock_channel = MagicMock()
            mock_build_channel.return_value = mock_channel

            # Mock a ToolError being raised
            tool_error = ToolError("Custom tool error")
            with patch(
                "apps.mcp_runtime.grpc_stream.ProtoReflectionDescriptorDatabase"
            ) as mock_reflection_db:
                mock_reflection_db.side_effect = tool_error

                # ToolError should be re-raised as-is
                with pytest.raises(ToolError, match="Custom tool error"):
                    executor._invoke_sync(op, {}, config)

    def test_grpc_rpc_error_handling(self) -> None:
        """Test general exception handling."""
        executor = self._make_executor()
        op = Operation(
            id="op1",
            name="watch",
            method="grpc",
            path="/pkg.Svc/Watch",
            description="Watch stream",
            enabled=True,
        )
        config = GrpcStreamRuntimeConfig(
            rpc_path="/pkg.Svc/Watch",
            mode=GrpcStreamMode.server,
        )

        with patch.object(executor, "_build_channel") as mock_build_channel:
            mock_channel = MagicMock()
            mock_build_channel.return_value = mock_channel

            # Mock a general exception
            general_error = ValueError("Test exception")
            with patch(
                "apps.mcp_runtime.grpc_stream.ProtoReflectionDescriptorDatabase"
            ) as mock_reflection_db:
                mock_reflection_db.side_effect = general_error

                with pytest.raises(ToolError, match="Native grpc_stream invocation failed"):
                    executor._invoke_sync(op, {}, config)
                mock_channel.close.assert_called_once_with()

    def test_general_exception_handling(self) -> None:
        executor = self._make_executor()
        op = Operation(
            id="op1",
            name="watch",
            method="grpc",
            path="/pkg.Svc/Watch",
            description="Watch stream",
            enabled=True,
        )
        config = GrpcStreamRuntimeConfig(
            rpc_path="/pkg.Svc/Watch",
            mode=GrpcStreamMode.server,
        )

        with patch.object(executor, "_build_channel") as mock_build_channel:
            mock_channel = MagicMock()
            mock_build_channel.return_value = mock_channel

            # Mock a general exception
            general_error = ValueError("Some unexpected error")
            with patch(
                "apps.mcp_runtime.grpc_stream.ProtoReflectionDescriptorDatabase"
            ) as mock_reflection_db:
                mock_reflection_db.side_effect = general_error

                with pytest.raises(
                    ToolError, match="Native grpc_stream invocation failed.*Some unexpected error"
                ):
                    executor._invoke_sync(op, {}, config)
