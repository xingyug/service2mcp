"""Tests for the gRPC proto extractor foundation."""

from __future__ import annotations

from pathlib import Path

from libs.extractors.base import SourceConfig, TypeDetector
from libs.extractors.grpc import GrpcProtoExtractor
from libs.ir.models import (
    EventDirection,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    RiskLevel,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures"
PROTO_FIXTURE_PATH = FIXTURES_DIR / "grpc_protos" / "inventory.proto"


def test_detects_proto_fixture() -> None:
    extractor = GrpcProtoExtractor()

    confidence = extractor.detect(SourceConfig(file_path=str(PROTO_FIXTURE_PATH)))

    assert confidence >= 0.9


def test_extracts_unary_rpcs_and_skips_streaming_methods() -> None:
    extractor = GrpcProtoExtractor()

    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(PROTO_FIXTURE_PATH),
            url="grpc://inventory.example.internal:443",
        )
    )

    assert service_ir.protocol == "grpc"
    assert service_ir.service_name == "catalog-v1-inventory-service"
    assert service_ir.base_url == "grpc://inventory.example.internal:443"
    assert service_ir.metadata["proto_package"] == "catalog.v1"
    assert service_ir.metadata["proto_service"] == "InventoryService"
    assert service_ir.metadata["ignored_streaming_rpcs"] == ["WatchInventory"]
    assert len(service_ir.event_descriptors) == 1
    assert service_ir.event_descriptors[0].id == "WatchInventory"
    assert service_ir.event_descriptors[0].transport is EventTransport.grpc_stream
    assert service_ir.event_descriptors[0].direction is EventDirection.inbound
    assert service_ir.event_descriptors[0].support is EventSupportLevel.unsupported
    assert service_ir.event_descriptors[0].grpc_stream is not None
    assert service_ir.event_descriptors[0].grpc_stream.rpc_path == (
        "/catalog.v1.InventoryService/WatchInventory"
    )
    assert service_ir.event_descriptors[0].grpc_stream.mode is GrpcStreamMode.server
    assert (
        service_ir.event_descriptors[0].channel
        == "/catalog.v1.InventoryService/WatchInventory"
    )
    assert len(service_ir.operations) == 2

    list_items = next(
        operation for operation in service_ir.operations if operation.id == "ListItems"
    )
    assert list_items.name == "List Items"
    assert list_items.method == "POST"
    assert list_items.path == "/catalog.v1.InventoryService/ListItems"
    assert list_items.grpc_unary is not None
    assert list_items.grpc_unary.rpc_path == "/catalog.v1.InventoryService/ListItems"
    assert list_items.risk.risk_level is RiskLevel.safe
    assert {param.name: param.type for param in list_items.params} == {
        "location_id": "string",
        "page_size": "integer",
        "page_token": "string",
        "filter": "object",
    }
    assert all(param.required is False for param in list_items.params)

    adjust_inventory = next(
        operation for operation in service_ir.operations if operation.id == "AdjustInventory"
    )
    assert adjust_inventory.grpc_unary is not None
    assert adjust_inventory.grpc_unary.rpc_path == "/catalog.v1.InventoryService/AdjustInventory"
    assert adjust_inventory.risk.risk_level is RiskLevel.cautious
    assert adjust_inventory.path == "/catalog.v1.InventoryService/AdjustInventory"
    assert {param.name: param.type for param in adjust_inventory.params} == {
        "sku": "string",
        "delta": "integer",
        "reason": "string",
    }


def test_type_detector_can_select_grpc_proto_extractor() -> None:
    detector = TypeDetector([GrpcProtoExtractor()])

    detection = detector.detect(SourceConfig(file_path=str(PROTO_FIXTURE_PATH)))

    assert detection.protocol_name == "grpc"


def test_extracts_supported_native_server_stream_when_enabled_via_hint() -> None:
    extractor = GrpcProtoExtractor()

    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(PROTO_FIXTURE_PATH),
            url="grpc://inventory.example.internal:443",
            hints={"enable_native_grpc_stream": "true"},
        )
    )

    assert service_ir.metadata["ignored_streaming_rpcs"] == []
    assert len(service_ir.operations) == 3

    watch_inventory = next(
        operation for operation in service_ir.operations if operation.id == "WatchInventory"
    )
    assert watch_inventory.method == "POST"
    assert watch_inventory.path == "/catalog.v1.InventoryService/WatchInventory"
    assert {param.name: param.type for param in watch_inventory.params} == {"sku": "string"}

    descriptor = next(
        descriptor
        for descriptor in service_ir.event_descriptors
        if descriptor.id == "WatchInventory"
    )
    assert descriptor.support is EventSupportLevel.supported
    assert descriptor.operation_id == "WatchInventory"
    assert descriptor.grpc_stream is not None
    assert descriptor.grpc_stream.mode is GrpcStreamMode.server
