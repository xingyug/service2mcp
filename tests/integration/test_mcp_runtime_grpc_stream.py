"""Focused integration tests for native grpc_stream runtime support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from google.protobuf import (
    descriptor_pb2,
    descriptor_pool,
    json_format,
)
from google.protobuf.descriptor_database import DescriptorDatabase
from google.protobuf.message_factory import GetMessageClass

from apps.mcp_runtime import create_app
from apps.mcp_runtime.grpc_stream import ReflectionGrpcStreamExecutor
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.ir.schema import serialize_ir


def _build_grpc_stream_ir(*, base_url: str = "grpc://inventory.example.test:443") -> ServiceIR:
    return ServiceIR(
        source_hash="d" * 64,
        protocol="grpc",
        service_name="catalog-v1-inventory-service",
        service_description="Inventory watch runtime test service",
        base_url=base_url,
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="watchInventory",
                name="Watch Inventory",
                description="Consume a native gRPC inventory stream.",
                method="POST",
                path="/catalog.v1.InventoryService/WatchInventory",
                params=[Param(name="payload", type="object", required=False)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        event_descriptors=[
            EventDescriptor(
                id="WatchInventory",
                name="WatchInventory",
                transport=EventTransport.grpc_stream,
                support=EventSupportLevel.supported,
                operation_id="watchInventory",
                channel="/catalog.v1.InventoryService/WatchInventory",
                grpc_stream=GrpcStreamRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/WatchInventory",
                    mode=GrpcStreamMode.server,
                    max_messages=1,
                    idle_timeout_seconds=2.0,
                ),
            )
        ],
    )


def _write_service_ir(tmp_path: Path, name: str, service_ir: ServiceIR) -> Path:
    output_path = tmp_path / name
    output_path.write_text(serialize_ir(service_ir), encoding="utf-8")
    return output_path


def _build_test_descriptor_database() -> tuple[DescriptorDatabase, Any, Any]:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "inventory_stream.proto"
    file_proto.package = "catalog.v1"
    file_proto.syntax = "proto3"

    request_message = file_proto.message_type.add()
    request_message.name = "WatchInventoryRequest"
    request_field = request_message.field.add()
    request_field.name = "sku"
    request_field.number = 1
    request_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    request_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    response_message = file_proto.message_type.add()
    response_message.name = "InventoryEvent"
    response_sku = response_message.field.add()
    response_sku.name = "sku"
    response_sku.number = 1
    response_sku.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    response_sku.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    response_status = response_message.field.add()
    response_status.name = "status"
    response_status.number = 2
    response_status.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    response_status.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    service = file_proto.service.add()
    service.name = "InventoryService"
    method = service.method.add()
    method.name = "WatchInventory"
    method.input_type = ".catalog.v1.WatchInventoryRequest"
    method.output_type = ".catalog.v1.InventoryEvent"
    method.server_streaming = True

    descriptor_db = DescriptorDatabase()
    descriptor_db.Add(file_proto)
    pool = descriptor_pool.DescriptorPool(descriptor_db)

    request_class = GetMessageClass(pool.FindMessageTypeByName("catalog.v1.WatchInventoryRequest"))
    response_class = GetMessageClass(pool.FindMessageTypeByName("catalog.v1.InventoryEvent"))
    return descriptor_db, request_class, response_class


class ServiceOnlyDescriptorDatabase:
    def __init__(self, delegate: DescriptorDatabase) -> None:
        self._delegate = delegate

    def FindFileByName(self, name: str) -> Any:  # noqa: N802
        return self._delegate.FindFileByName(name)

    def FindFileContainingSymbol(self, symbol: str) -> Any:  # noqa: N802
        if symbol in {
            "catalog.v1.InventoryService",
            "catalog.v1.WatchInventoryRequest",
            "catalog.v1.InventoryEvent",
        }:
            return self._delegate.FindFileContainingSymbol(symbol)
        raise KeyError(f"Couldn't find symbol {symbol}")


@pytest.mark.asyncio
async def test_runtime_auto_configures_native_grpc_stream_executor_when_opted_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeExecutor:
        def __init__(self, service_ir: ServiceIR) -> None:
            captured["service_name"] = service_ir.service_name

        async def invoke(
            self,
            *,
            operation: Operation,
            arguments: dict[str, object],
            descriptor: EventDescriptor,
            config: GrpcStreamRuntimeConfig,
        ) -> dict[str, object]:
            captured["operation_id"] = operation.id
            captured["arguments"] = dict(arguments)
            captured["descriptor_id"] = descriptor.id
            captured["rpc_path"] = config.rpc_path
            return {
                "events": [
                    {
                        "message_type": "json",
                        "parsed_data": {"sku": "sku-1", "status": "updated"},
                    }
                ],
                "lifecycle": {
                    "termination_reason": "max_messages",
                    "messages_collected": 1,
                },
            }

    service_ir_path = _write_service_ir(
        tmp_path,
        "service_ir_grpc_stream_auto.json",
        _build_grpc_stream_ir(),
    )
    monkeypatch.setenv("ENABLE_NATIVE_GRPC_STREAM", "true")
    monkeypatch.setattr("apps.mcp_runtime.main.ReflectionGrpcStreamExecutor", FakeExecutor)

    app = create_app(service_ir_path=service_ir_path)
    _, structured = await app.state.runtime_state.mcp_server.call_tool(
        "watchInventory",
        {"payload": {"sku": "sku-1"}},
    )

    assert captured == {
        "service_name": "catalog-v1-inventory-service",
        "operation_id": "watchInventory",
        "arguments": {"payload": {"sku": "sku-1"}},
        "descriptor_id": "WatchInventory",
        "rpc_path": "/catalog.v1.InventoryService/WatchInventory",
    }
    assert structured["status"] == "ok"
    assert structured["transport"] == "grpc_stream"
    assert structured["result"]["events"] == [
        {
            "message_type": "json",
            "parsed_data": {"sku": "sku-1", "status": "updated"},
        }
    ]


@pytest.mark.asyncio
async def test_reflection_grpc_stream_executor_resolves_descriptors_and_collects_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    descriptor_db, request_class, response_class = _build_test_descriptor_database()
    service_ir = _build_grpc_stream_ir()
    executor = ReflectionGrpcStreamExecutor(service_ir)
    operation = service_ir.operations[0]
    descriptor = service_ir.event_descriptors[0]
    assert descriptor.grpc_stream is not None

    class FakeChannel:
        def unary_stream(
            self,
            rpc_path: str,
            *,
            request_serializer: Any,
            response_deserializer: Any,
        ) -> Any:
            def invoke(request_message: Any, timeout: float | None = None) -> list[Any]:
                captured["rpc_path"] = rpc_path
                captured["timeout"] = timeout
                request_bytes = request_serializer(request_message)
                parsed_request = request_class.FromString(request_bytes)
                captured["request"] = json_format.MessageToDict(
                    parsed_request,
                    preserving_proto_field_name=True,
                )

                ready = response_class()
                ready.sku = parsed_request.sku
                ready.status = "ready"
                done = response_class()
                done.sku = parsed_request.sku
                done.status = "done"
                return [
                    response_deserializer(ready.SerializeToString()),
                    response_deserializer(done.SerializeToString()),
                ]

            return invoke

        def close(self, grace: float | None = None) -> None:
            captured["closed"] = True

    monkeypatch.setattr(
        "apps.mcp_runtime.grpc_stream.ProtoReflectionDescriptorDatabase",
        lambda _: descriptor_db,
    )
    monkeypatch.setattr(executor, "_build_channel", lambda: FakeChannel())

    result = await executor.invoke(
        operation=operation,
        arguments={"payload": {"sku": "sku-1"}},
        descriptor=descriptor,
        config=descriptor.grpc_stream,
    )

    assert captured == {
        "rpc_path": "/catalog.v1.InventoryService/WatchInventory",
        "timeout": 2.0,
        "request": {"sku": "sku-1"},
        "closed": True,
    }
    assert result == {
        "events": [
            {
                "message_type": "protobuf",
                "parsed_data": {"sku": "sku-1", "status": "ready"},
            }
        ],
        "lifecycle": {
            "termination_reason": "max_messages",
            "messages_collected": 1,
            "rpc_path": "/catalog.v1.InventoryService/WatchInventory",
            "mode": "server",
        },
    }


@pytest.mark.asyncio
async def test_reflection_grpc_stream_executor_primes_service_descriptor_before_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor_db, request_class, response_class = _build_test_descriptor_database()
    gated_descriptor_db = ServiceOnlyDescriptorDatabase(descriptor_db)
    service_ir = _build_grpc_stream_ir()
    executor = ReflectionGrpcStreamExecutor(service_ir)
    operation = service_ir.operations[0]
    descriptor = service_ir.event_descriptors[0]
    assert descriptor.grpc_stream is not None

    class FakeChannel:
        def unary_stream(
            self,
            rpc_path: str,
            *,
            request_serializer: Any,
            response_deserializer: Any,
        ) -> Any:
            def invoke(request_message: Any, timeout: float | None = None) -> list[Any]:
                del timeout
                request_bytes = request_serializer(request_message)
                parsed_request = request_class.FromString(request_bytes)

                ready = response_class()
                ready.sku = parsed_request.sku
                ready.status = "ready"
                return [response_deserializer(ready.SerializeToString())]

            return invoke

        def close(self, grace: float | None = None) -> None:
            return None

    monkeypatch.setattr(
        "apps.mcp_runtime.grpc_stream.ProtoReflectionDescriptorDatabase",
        lambda _: gated_descriptor_db,
    )
    monkeypatch.setattr(executor, "_build_channel", lambda: FakeChannel())

    result = await executor.invoke(
        operation=operation,
        arguments={"payload": {"sku": "sku-2"}},
        descriptor=descriptor,
        config=descriptor.grpc_stream,
    )

    assert result["events"] == [
        {
            "message_type": "protobuf",
            "parsed_data": {"sku": "sku-2", "status": "ready"},
        }
    ]
    assert result["lifecycle"]["messages_collected"] == 1
