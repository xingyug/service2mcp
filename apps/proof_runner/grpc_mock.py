"""gRPC mock service used by the live LLM-enabled proof track."""

from __future__ import annotations

import os
from concurrent import futures
from typing import Any

import grpc
from google.protobuf import descriptor_pb2
from google.protobuf.descriptor_database import DescriptorDatabase
from google.protobuf.descriptor_pool import DescriptorPool
from google.protobuf.message_factory import GetMessageClass
from grpc_reflection.v1alpha import reflection

_PACKAGE_NAME = "catalog.v1"
_SERVICE_NAME = "InventoryService"


def build_inventory_descriptor_pool() -> DescriptorPool:
    """Build a descriptor pool matching the proof inventory proto."""

    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "inventory.proto"
    file_proto.package = _PACKAGE_NAME
    file_proto.syntax = "proto3"

    adjustment_reason = file_proto.enum_type.add()
    adjustment_reason.name = "AdjustmentReason"
    for number, name in enumerate(
        (
            "ADJUSTMENT_REASON_UNSPECIFIED",
            "ADJUSTMENT_REASON_SALE",
            "ADJUSTMENT_REASON_RESTOCK",
        )
    ):
        enum_value = adjustment_reason.value.add()
        enum_value.name = name
        enum_value.number = number

    item_filter = file_proto.message_type.add()
    item_filter.name = "ItemFilter"
    categories_field = item_filter.field.add()
    categories_field.name = "categories"
    categories_field.number = 1
    categories_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    categories_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    include_inactive_field = item_filter.field.add()
    include_inactive_field.name = "include_inactive"
    include_inactive_field.number = 2
    include_inactive_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    include_inactive_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

    list_items_request = file_proto.message_type.add()
    list_items_request.name = "ListItemsRequest"
    _scalar_field(
        list_items_request,
        name="location_id",
        number=1,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    _scalar_field(
        list_items_request,
        name="page_size",
        number=2,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_INT32,
    )
    _scalar_field(
        list_items_request,
        name="page_token",
        number=3,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    filter_field = list_items_request.field.add()
    filter_field.name = "filter"
    filter_field.number = 4
    filter_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    filter_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    filter_field.type_name = f".{_PACKAGE_NAME}.ItemFilter"

    item_message = file_proto.message_type.add()
    item_message.name = "Item"
    _scalar_field(
        item_message,
        name="sku",
        number=1,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    _scalar_field(
        item_message,
        name="title",
        number=2,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )

    list_items_response = file_proto.message_type.add()
    list_items_response.name = "ListItemsResponse"
    items_field = list_items_response.field.add()
    items_field.name = "items"
    items_field.number = 1
    items_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    items_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    items_field.type_name = f".{_PACKAGE_NAME}.Item"
    _scalar_field(
        list_items_response,
        name="next_page_token",
        number=2,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )

    adjust_inventory_request = file_proto.message_type.add()
    adjust_inventory_request.name = "AdjustInventoryRequest"
    _scalar_field(
        adjust_inventory_request,
        name="sku",
        number=1,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    _scalar_field(
        adjust_inventory_request,
        name="delta",
        number=2,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_INT32,
    )
    reason_field = adjust_inventory_request.field.add()
    reason_field.name = "reason"
    reason_field.number = 3
    reason_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    reason_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_ENUM
    reason_field.type_name = f".{_PACKAGE_NAME}.AdjustmentReason"

    adjust_inventory_response = file_proto.message_type.add()
    adjust_inventory_response.name = "AdjustInventoryResponse"
    _scalar_field(
        adjust_inventory_response,
        name="operation_id",
        number=1,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )

    watch_inventory_request = file_proto.message_type.add()
    watch_inventory_request.name = "WatchInventoryRequest"
    _scalar_field(
        watch_inventory_request,
        name="sku",
        number=1,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )

    inventory_event = file_proto.message_type.add()
    inventory_event.name = "InventoryEvent"
    _scalar_field(
        inventory_event,
        name="sku",
        number=1,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )

    service = file_proto.service.add()
    service.name = _SERVICE_NAME
    _service_method(
        service,
        name="ListItems",
        input_type=".catalog.v1.ListItemsRequest",
        output_type=".catalog.v1.ListItemsResponse",
    )
    _service_method(
        service,
        name="AdjustInventory",
        input_type=".catalog.v1.AdjustInventoryRequest",
        output_type=".catalog.v1.AdjustInventoryResponse",
    )
    _service_method(
        service,
        name="WatchInventory",
        input_type=".catalog.v1.WatchInventoryRequest",
        output_type=".catalog.v1.InventoryEvent",
        server_streaming=True,
    )

    descriptor_db = DescriptorDatabase()
    descriptor_db.Add(file_proto)
    return DescriptorPool(descriptor_db)


def serve(*, port: int = 50051) -> None:
    """Start the mock gRPC server and block forever."""

    pool = build_inventory_descriptor_pool()
    list_items_request = GetMessageClass(pool.FindMessageTypeByName("catalog.v1.ListItemsRequest"))
    list_items_response = GetMessageClass(
        pool.FindMessageTypeByName("catalog.v1.ListItemsResponse")
    )
    item_class = GetMessageClass(pool.FindMessageTypeByName("catalog.v1.Item"))
    adjust_inventory_request = GetMessageClass(
        pool.FindMessageTypeByName("catalog.v1.AdjustInventoryRequest")
    )
    adjust_inventory_response = GetMessageClass(
        pool.FindMessageTypeByName("catalog.v1.AdjustInventoryResponse")
    )
    watch_inventory_request = GetMessageClass(
        pool.FindMessageTypeByName("catalog.v1.WatchInventoryRequest")
    )
    inventory_event = GetMessageClass(pool.FindMessageTypeByName("catalog.v1.InventoryEvent"))

    def list_items(request: Any, context: grpc.ServicerContext) -> Any:
        del context
        response = list_items_response()
        item = item_class()
        item.sku = f"{request.location_id or 'warehouse'}-sku"
        item.title = "Puzzle Box"
        response.items.append(item)
        response.next_page_token = ""
        return response

    def adjust_inventory(request: Any, context: grpc.ServicerContext) -> Any:
        del context
        response = adjust_inventory_response()
        response.operation_id = f"adj-{request.sku}-{request.delta}"
        return response

    def watch_inventory(request: Any, context: grpc.ServicerContext) -> Any:
        del context
        for _ in range(2):
            event = inventory_event()
            event.sku = request.sku or "sku-live"
            yield event

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                f"{_PACKAGE_NAME}.{_SERVICE_NAME}",
                {
                    "ListItems": grpc.unary_unary_rpc_method_handler(
                        list_items,
                        request_deserializer=list_items_request.FromString,
                        response_serializer=lambda message: message.SerializeToString(),
                    ),
                    "AdjustInventory": grpc.unary_unary_rpc_method_handler(
                        adjust_inventory,
                        request_deserializer=adjust_inventory_request.FromString,
                        response_serializer=lambda message: message.SerializeToString(),
                    ),
                    "WatchInventory": grpc.unary_stream_rpc_method_handler(
                        watch_inventory,
                        request_deserializer=watch_inventory_request.FromString,
                        response_serializer=lambda message: message.SerializeToString(),
                    ),
                },
            ),
        )
    )
    reflection.enable_server_reflection(
        (f"{_PACKAGE_NAME}.{_SERVICE_NAME}", reflection.SERVICE_NAME),
        server,
        pool=pool,
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    server.wait_for_termination()


def _scalar_field(
    container: descriptor_pb2.DescriptorProto,
    *,
    name: str,
    number: int,
    field_type: int,
) -> None:
    field = container.field.add()
    field.name = name
    field.number = number
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = field_type


def _service_method(
    container: descriptor_pb2.ServiceDescriptorProto,
    *,
    name: str,
    input_type: str,
    output_type: str,
    server_streaming: bool = False,
) -> None:
    method = container.method.add()
    method.name = name
    method.input_type = input_type
    method.output_type = output_type
    method.server_streaming = server_streaming


def main() -> None:
    port = int(os.getenv("GRPC_PORT", "50051"))
    serve(port=port)


if __name__ == "__main__":
    main()
