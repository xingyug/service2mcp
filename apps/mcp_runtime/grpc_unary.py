"""Native gRPC unary executor backed by server reflection."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

import grpc
from google.protobuf import json_format
from google.protobuf.descriptor_pool import DescriptorPool
from google.protobuf.message_factory import GetMessageClass
from grpc_reflection.v1alpha.proto_reflection_descriptor_database import (
    ProtoReflectionDescriptorDatabase,
)
from mcp.server.fastmcp.exceptions import ToolError

from libs.ir.models import GrpcUnaryRuntimeConfig, Operation, ServiceIR


class ReflectionGrpcUnaryExecutor:
    """Execute native unary gRPC calls using upstream server reflection."""

    def __init__(self, service_ir: ServiceIR) -> None:
        self._service_ir = service_ir

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        config: GrpcUnaryRuntimeConfig,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._invoke_sync,
            operation,
            arguments,
            config,
        )

    def _invoke_sync(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        config: GrpcUnaryRuntimeConfig,
    ) -> dict[str, Any]:
        channel = self._build_channel()
        try:
            reflection_db = ProtoReflectionDescriptorDatabase(channel)
            pool = DescriptorPool(reflection_db)
            method_full_name = _method_full_name(config.rpc_path)
            _prime_service_descriptor(pool, method_full_name)
            method_descriptor = pool.FindMethodByName(method_full_name)
            request_class = GetMessageClass(method_descriptor.input_type)
            response_class = GetMessageClass(method_descriptor.output_type)

            request_message = request_class()
            json_format.ParseDict(
                _request_payload(arguments),
                request_message,
                ignore_unknown_fields=False,
            )

            invoke = channel.unary_unary(
                config.rpc_path,
                request_serializer=lambda message: message.SerializeToString(),
                response_deserializer=response_class.FromString,
            )
            response = invoke(request_message, timeout=config.timeout_seconds)
            response_payload = json_format.MessageToDict(
                response,
                preserving_proto_field_name=True,
            )
            if not isinstance(response_payload, dict):
                raise ToolError(
                    f"Native grpc unary invocation for {operation.id} returned "
                    "a non-object protobuf message."
                )
            return response_payload
        except grpc.RpcError as exc:
            raise ToolError(
                f"Native grpc unary invocation failed for {operation.id}: "
                f"{exc.code().name} {exc.details()}"
            ) from exc
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"Native grpc unary invocation failed for {operation.id}: {exc}"
            ) from exc
        finally:
            channel.close()

    def _build_channel(self) -> grpc.Channel:
        parsed = urlsplit(self._service_ir.base_url)
        target = parsed.netloc or parsed.path
        if not target:
            raise ToolError(
                f"Service base_url {self._service_ir.base_url!r} is not a valid grpc target."
            )
        if parsed.scheme == "grpcs":
            return grpc.secure_channel(target, grpc.ssl_channel_credentials())
        if parsed.scheme == "grpc":
            return grpc.insecure_channel(target)
        raise ToolError(
            f"Service base_url scheme {parsed.scheme!r} is not supported for grpc unary."
        )


def _method_full_name(rpc_path: str) -> str:
    trimmed = rpc_path.removeprefix("/")
    parts = trimmed.split("/")
    if len(parts) != 2 or not all(parts):
        raise ToolError(f"grpc unary rpc_path {rpc_path!r} is invalid.")
    service_name, method_name = parts
    return f"{service_name}.{method_name}"


def _request_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    payload = arguments.get("payload")
    if isinstance(payload, dict):
        return payload
    return dict(arguments)


def _prime_service_descriptor(pool: DescriptorPool, method_full_name: str) -> None:
    service_full_name, _, _ = method_full_name.rpartition(".")
    if service_full_name:
        pool.FindFileContainingSymbol(service_full_name)
