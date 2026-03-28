"""Focused integration tests for native grpc unary runtime support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, json_format
from google.protobuf.descriptor_database import DescriptorDatabase
from google.protobuf.message_factory import GetMessageClass

from apps.mcp_runtime import create_app
from apps.mcp_runtime.grpc_unary import ReflectionGrpcUnaryExecutor
from libs.ir.models import (
    AuthConfig,
    AuthType,
    GrpcUnaryRuntimeConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.ir.schema import serialize_ir


def _build_grpc_unary_ir(*, base_url: str = "grpc://inventory.example.test:443") -> ServiceIR:
    return ServiceIR(
        source_hash="e" * 64,
        protocol="grpc",
        service_name="catalog-v1-inventory-service",
        service_description="Inventory unary runtime test service",
        base_url=base_url,
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="LookupInventory",
                name="Lookup Inventory",
                description="Fetch current inventory for a SKU.",
                method="POST",
                path="/catalog.v1.InventoryService/LookupInventory",
                params=[Param(name="sku", type="string", required=True)],
                grpc_unary=GrpcUnaryRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/LookupInventory",
                    timeout_seconds=2.0,
                ),
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
    )


def _write_service_ir(tmp_path: Path, name: str, service_ir: ServiceIR) -> Path:
    output_path = tmp_path / name
    output_path.write_text(serialize_ir(service_ir), encoding="utf-8")
    return output_path


def _build_test_descriptor_database() -> tuple[DescriptorDatabase, Any, Any]:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "inventory_unary.proto"
    file_proto.package = "catalog.v1"
    file_proto.syntax = "proto3"

    request_message = file_proto.message_type.add()
    request_message.name = "LookupInventoryRequest"
    request_field = request_message.field.add()
    request_field.name = "sku"
    request_field.number = 1
    request_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    request_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    response_message = file_proto.message_type.add()
    response_message.name = "LookupInventoryResponse"
    response_sku = response_message.field.add()
    response_sku.name = "sku"
    response_sku.number = 1
    response_sku.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    response_sku.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    response_count = response_message.field.add()
    response_count.name = "count"
    response_count.number = 2
    response_count.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    response_count.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT32

    service = file_proto.service.add()
    service.name = "InventoryService"
    method = service.method.add()
    method.name = "LookupInventory"
    method.input_type = ".catalog.v1.LookupInventoryRequest"
    method.output_type = ".catalog.v1.LookupInventoryResponse"

    descriptor_db = DescriptorDatabase()
    descriptor_db.Add(file_proto)
    pool = descriptor_pool.DescriptorPool(descriptor_db)
    request_class = GetMessageClass(pool.FindMessageTypeByName("catalog.v1.LookupInventoryRequest"))
    response_class = GetMessageClass(
        pool.FindMessageTypeByName("catalog.v1.LookupInventoryResponse")
    )
    return descriptor_db, request_class, response_class


class ServiceOnlyDescriptorDatabase:
    def __init__(self, delegate: DescriptorDatabase) -> None:
        self._delegate = delegate

    def FindFileByName(self, name: str) -> Any:  # noqa: N802
        return self._delegate.FindFileByName(name)

    def FindFileContainingSymbol(self, symbol: str) -> Any:  # noqa: N802
        if symbol in {
            "catalog.v1.InventoryService",
            "catalog.v1.LookupInventoryRequest",
            "catalog.v1.LookupInventoryResponse",
        }:
            return self._delegate.FindFileContainingSymbol(symbol)
        raise KeyError(f"Couldn't find symbol {symbol}")


@pytest.mark.asyncio
async def test_runtime_auto_configures_native_grpc_unary_executor_when_opted_in(
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
            config: GrpcUnaryRuntimeConfig,
        ) -> dict[str, object]:
            captured["operation_id"] = operation.id
            captured["arguments"] = dict(arguments)
            captured["rpc_path"] = config.rpc_path
            return {"sku": "sku-1", "count": 7}

    service_ir_path = _write_service_ir(
        tmp_path,
        "service_ir_grpc_unary_auto.json",
        _build_grpc_unary_ir(),
    )
    monkeypatch.setenv("ENABLE_NATIVE_GRPC_UNARY", "true")
    monkeypatch.setattr("apps.mcp_runtime.main.ReflectionGrpcUnaryExecutor", FakeExecutor)

    app = create_app(service_ir_path=service_ir_path)
    _, structured = await app.state.runtime_state.mcp_server.call_tool(
        "LookupInventory",
        {"sku": "sku-1"},
    )

    assert captured == {
        "service_name": "catalog-v1-inventory-service",
        "operation_id": "LookupInventory",
        "arguments": {"sku": "sku-1"},
        "rpc_path": "/catalog.v1.InventoryService/LookupInventory",
    }
    assert structured["status"] == "ok"
    assert structured["result"] == {"sku": "sku-1", "count": 7}


@pytest.mark.asyncio
async def test_reflection_grpc_unary_executor_resolves_descriptors_and_invokes_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    descriptor_db, request_class, response_class = _build_test_descriptor_database()
    gated_descriptor_db = ServiceOnlyDescriptorDatabase(descriptor_db)
    service_ir = _build_grpc_unary_ir()
    executor = ReflectionGrpcUnaryExecutor(service_ir)
    operation = service_ir.operations[0]
    assert operation.grpc_unary is not None

    class FakeChannel:
        def unary_unary(
            self,
            rpc_path: str,
            *,
            request_serializer: Any,
            response_deserializer: Any,
        ) -> Any:
            def invoke(request_message: Any, timeout: float | None = None) -> Any:
                captured["rpc_path"] = rpc_path
                captured["timeout"] = timeout
                request_bytes = request_serializer(request_message)
                parsed_request = request_class.FromString(request_bytes)
                captured["request"] = json_format.MessageToDict(
                    parsed_request,
                    preserving_proto_field_name=True,
                )

                response = response_class()
                response.sku = parsed_request.sku
                response.count = 4
                return response_deserializer(response.SerializeToString())

            return invoke

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(
        "apps.mcp_runtime.grpc_unary.ProtoReflectionDescriptorDatabase",
        lambda _: gated_descriptor_db,
    )
    monkeypatch.setattr(executor, "_build_channel", lambda: FakeChannel())

    result = await executor.invoke(
        operation=operation,
        arguments={"sku": "sku-2"},
        config=operation.grpc_unary,
    )

    assert captured == {
        "rpc_path": "/catalog.v1.InventoryService/LookupInventory",
        "timeout": 2.0,
        "request": {"sku": "sku-2"},
        "closed": True,
    }
    assert result == {"sku": "sku-2", "count": 4}
