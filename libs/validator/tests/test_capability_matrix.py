"""Tests for the protocol capability matrix."""

from __future__ import annotations

from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.validator.capability_matrix import (
    protocol_capability_for_service,
    protocol_capability_key,
    protocol_capability_matrix,
)


def _safe_operation(
    operation_id: str,
    *,
    method: str = "GET",
    path: str = "/items",
) -> Operation:
    return Operation(
        id=operation_id,
        name=operation_id,
        description="fixture operation",
        method=method,
        path=path,
        params=[Param(name="item_id", type="string", required=False)],
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


def _service_ir(
    protocol: str,
    *,
    operations: list[Operation],
    event_descriptors: list[EventDescriptor] | None = None,
) -> ServiceIR:
    return ServiceIR(
        source_hash="1" * 64,
        protocol=protocol,
        service_name=f"{protocol}-service",
        service_description=f"{protocol} fixture",
        base_url="https://api.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=operations,
        event_descriptors=event_descriptors or [],
    )


def test_protocol_capability_matrix_contains_expected_rows() -> None:
    rows = protocol_capability_matrix()

    assert [row.key for row in rows] == [
        "openapi",
        "rest",
        "graphql",
        "grpc",
        "grpc_unary",
        "grpc_stream",
        "soap",
        "sql",
    ]

    openapi = next(row for row in rows if row.key == "openapi")
    rest = next(row for row in rows if row.key == "rest")
    graphql = next(row for row in rows if row.key == "graphql")
    grpc_unary = next(row for row in rows if row.key == "grpc_unary")
    grpc_stream = next(row for row in rows if row.key == "grpc_stream")
    soap = next(row for row in rows if row.key == "soap")
    sql = next(row for row in rows if row.key == "sql")

    assert openapi.live_proof is True
    assert openapi.llm_e2e is True
    assert rest.live_proof is True
    assert rest.llm_e2e is True
    assert graphql.live_proof is True
    assert graphql.llm_e2e is True
    assert grpc_unary.live_proof is True
    assert grpc_unary.llm_e2e is True
    assert grpc_stream.live_proof is True
    assert grpc_stream.llm_e2e is True
    assert soap.live_proof is True
    assert soap.llm_e2e is True
    assert sql.runtime is True
    assert sql.live_proof is True
    assert sql.llm_e2e is True


def test_protocol_capability_key_distinguishes_grpc_runtime_slices() -> None:
    grpc_unary_ir = _service_ir(
        "grpc",
        operations=[
            _safe_operation(
                "lookupInventory",
                method="POST",
                path="/catalog.v1.InventoryService/LookupInventory",
            ).model_copy(
                update={
                    "grpc_unary": GrpcUnaryRuntimeConfig(
                        rpc_path="/catalog.v1.InventoryService/LookupInventory"
                    )
                }
            )
        ],
    )
    grpc_stream_ir = _service_ir(
        "grpc",
        operations=[
            _safe_operation(
                "watchInventory",
                method="POST",
                path="/catalog.v1.InventoryService/WatchInventory",
            )
        ],
        event_descriptors=[
            EventDescriptor(
                id="watchInventory:grpc",
                name="watchInventory",
                transport=EventTransport.grpc_stream,
                support=EventSupportLevel.supported,
                operation_id="watchInventory",
                channel="/catalog.v1.InventoryService/WatchInventory",
                grpc_stream=GrpcStreamRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/WatchInventory",
                    mode=GrpcStreamMode.server,
                ),
            )
        ],
    )
    generic_grpc_ir = _service_ir("grpc", operations=[_safe_operation("listCatalog")])

    assert protocol_capability_key(grpc_unary_ir) == "grpc_unary"
    assert protocol_capability_key(grpc_stream_ir) == "grpc_stream"
    assert protocol_capability_key(generic_grpc_ir) == "grpc"

    assert protocol_capability_for_service(grpc_unary_ir).runtime is True
    assert protocol_capability_for_service(grpc_stream_ir).live_proof is True
    assert protocol_capability_for_service(generic_grpc_ir).runtime is False


def test_all_protocols_mention_error_model() -> None:
    """Every main protocol capability note must reference 'error model'."""
    rows = protocol_capability_matrix()
    for row in rows:
        assert "error model" in row.notes.lower(), (
            f"Protocol {row.key!r} notes should mention 'error model', got: {row.notes!r}"
        )
