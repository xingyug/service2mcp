"""Native gRPC streaming executor backed by server reflection."""

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

from libs.ir.models import (
    EventDescriptor,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    Operation,
    ServiceIR,
)


class ReflectionGrpcStreamExecutor:
    """Execute native gRPC server-stream calls using upstream server reflection."""

    def __init__(self, service_ir: ServiceIR) -> None:
        self._service_ir = service_ir

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        descriptor: EventDescriptor,
        config: GrpcStreamRuntimeConfig,
    ) -> dict[str, Any]:
        del descriptor
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
        config: GrpcStreamRuntimeConfig,
    ) -> dict[str, Any]:
        if config.mode is not GrpcStreamMode.server:
            raise ToolError(
                f"Native grpc_stream mode {config.mode.value} for operation "
                f"{operation.id} is not implemented yet."
            )

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

            stream = channel.unary_stream(
                config.rpc_path,
                request_serializer=lambda message: message.SerializeToString(),
                response_deserializer=response_class.FromString,
            )

            # gRPC timeout is a hard RPC deadline, not an idle timeout.
            # Scale the deadline to allow for max_messages at the idle rate.
            rpc_deadline = config.idle_timeout_seconds * config.max_messages

            responses = stream(
                request_message,
                timeout=rpc_deadline,
            )
            events: list[dict[str, Any]] = []
            termination_reason = "completed"

            try:
                for response in responses:
                    events.append(
                        {
                            "message_type": "protobuf",
                            "parsed_data": json_format.MessageToDict(
                                response,
                                preserving_proto_field_name=True,
                            ),
                        }
                    )
                    if len(events) >= config.max_messages:
                        termination_reason = "max_messages"
                        break
            finally:
                if hasattr(responses, "cancel"):
                    responses.cancel()

            return {
                "events": events,
                "lifecycle": {
                    "termination_reason": termination_reason,
                    "messages_collected": len(events),
                    "rpc_path": config.rpc_path,
                    "mode": config.mode.value,
                },
            }
        except ToolError:
            raise
        except grpc.RpcError as exc:
            raise ToolError(
                f"Native grpc_stream invocation failed for {operation.id}: "
                f"{exc.code().name} {exc.details()}"
            ) from exc
        except Exception as exc:
            raise ToolError(
                f"Native grpc_stream invocation failed for {operation.id}: {exc}"
            ) from exc
        finally:
            channel.close(grace=5)

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
            f"Service base_url scheme {parsed.scheme!r} is not supported for grpc_stream."
        )


def _method_full_name(rpc_path: str) -> str:
    trimmed = rpc_path.lstrip("/")
    service_name, _, method_name = trimmed.partition("/")
    if not service_name or not method_name:
        raise ToolError(f"grpc_stream rpc_path {rpc_path!r} is invalid.")
    return f"{service_name}.{method_name}"


def _request_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    payload = arguments.get("payload")
    if isinstance(payload, dict):
        return payload
    return {key: value for key, value in arguments.items() if value is not None}


def _prime_service_descriptor(pool: DescriptorPool, method_full_name: str) -> None:
    service_full_name, _, _ = method_full_name.rpartition(".")
    if service_full_name:
        pool.FindFileContainingSymbol(service_full_name)
