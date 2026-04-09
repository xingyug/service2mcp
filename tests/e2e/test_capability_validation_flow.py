"""E2E: Capability matrix and protocol validation flow.

Tests the protocol_capability_matrix(), protocol_capability_for_service(), and
PreDeployValidator for protocol-specific ServiceIRs covering:
- Capability rows exist for each supported protocol
- Each protocol has expected extract/compile/runtime flags
- Protocol resolution for gRPC sub-types (unary, server-stream)
- IR validation passes for protocol-specific IRs
- Error schema presence for protocols that support it
- Unknown protocol handling
"""

from __future__ import annotations

import pytest

from libs.ir.models import (
    AuthConfig,
    ErrorResponse,
    ErrorSchema,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    JsonRpcOperationConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SoapOperationConfig,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
)
from libs.validator.capability_matrix import (
    ProtocolCapability,
    protocol_capability_for_service,
    protocol_capability_key,
    protocol_capability_matrix,
)
from libs.validator.pre_deploy import PreDeployValidator

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_op(
    op_id: str,
    *,
    method: str = "GET",
    path: str | None = None,
    risk_level: RiskLevel = RiskLevel.safe,
    error_schema: ErrorSchema | None = None,
) -> Operation:
    return Operation(
        id=op_id,
        name=op_id,
        description=f"Op {op_id}",
        method=method,
        path=path or f"/{op_id}",
        risk=RiskMetadata(risk_level=risk_level, confidence=0.9),
        params=[Param(name="q", type="string")],
        error_schema=error_schema or ErrorSchema(),
    )


def _base_ir(
    name: str,
    protocol: str,
    operations: list[Operation],
    *,
    events: list[EventDescriptor] | None = None,
) -> ServiceIR:
    return ServiceIR(
        source_url=f"https://example.com/{name}",
        source_hash="abc123",
        protocol=protocol,
        service_name=name,
        base_url=f"https://api.{name}.example.com",
        auth=AuthConfig(),
        operations=operations,
        event_descriptors=events or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCapabilityMatrixCompleteness:
    """All expected protocols are present in the capability matrix."""

    EXPECTED_PROTOCOLS = (
        "openapi",
        "rest",
        "graphql",
        "grpc",
        "grpc_unary",
        "grpc_stream",
        "soap",
        "sql",
        "odata",
        "scim",
        "jsonrpc",
        "cli",
        "asyncapi",
    )

    async def test_all_protocols_present(self) -> None:
        matrix = protocol_capability_matrix()
        keys = {cap.key for cap in matrix}
        for proto in self.EXPECTED_PROTOCOLS:
            assert proto in keys, f"Missing protocol in capability matrix: {proto}"

    async def test_matrix_returns_tuple(self) -> None:
        matrix = protocol_capability_matrix()
        assert isinstance(matrix, tuple)
        assert len(matrix) == len(self.EXPECTED_PROTOCOLS)

    async def test_each_row_is_protocol_capability(self) -> None:
        for cap in protocol_capability_matrix():
            assert isinstance(cap, ProtocolCapability)
            assert cap.key
            assert cap.label


class TestCapabilityFlags:
    """Verify extract/compile flags for key protocols."""

    async def test_openapi_capabilities(self) -> None:
        ir = _base_ir("petstore", "openapi", [_base_op("list_pets")])
        cap = protocol_capability_for_service(ir)
        assert cap.key == "openapi"
        assert cap.extract is True
        assert cap.compile is True
        assert cap.runtime is True

    async def test_graphql_capabilities(self) -> None:
        op = Operation(
            id="search",
            name="search",
            description="Search",
            method="POST",
            path="/graphql",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="Search",
                document="query Search { search { id } }",
            ),
        )
        ir = _base_ir("catalog", "graphql", [op])
        cap = protocol_capability_for_service(ir)
        assert cap.key == "graphql"
        assert cap.extract is True
        assert cap.compile is True

    async def test_soap_capabilities(self) -> None:
        op = Operation(
            id="place_order",
            name="PlaceOrder",
            description="Place an order",
            method="POST",
            path="/ws",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            soap=SoapOperationConfig(
                target_namespace="http://example.com/orders",
                request_element="PlaceOrderRequest",
            ),
        )
        ir = _base_ir("orders", "soap", [op])
        cap = protocol_capability_for_service(ir)
        assert cap.key == "soap"
        assert cap.extract is True

    async def test_sql_capabilities(self) -> None:
        op = Operation(
            id="query_users",
            name="query_users",
            description="Query users",
            method="GET",
            path="/sql/users",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            sql=SqlOperationConfig(
                schema_name="public",
                relation_name="users",
                relation_kind=SqlRelationKind.table,
                action=SqlOperationType.query,
                filterable_columns=["id", "name"],
            ),
        )
        ir = _base_ir("db", "sql", [op])
        cap = protocol_capability_for_service(ir)
        assert cap.key == "sql"
        assert cap.extract is True

    async def test_jsonrpc_capabilities(self) -> None:
        op = Operation(
            id="get_user",
            name="get_user",
            description="Get user by ID",
            method="POST",
            path="/rpc",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            jsonrpc=JsonRpcOperationConfig(method_name="user.getById"),
        )
        ir = _base_ir("rpc-svc", "jsonrpc", [op])
        cap = protocol_capability_for_service(ir)
        assert cap.key == "jsonrpc"
        assert cap.extract is True


class TestGrpcSubTypeResolution:
    """gRPC capability key resolution: generic, unary, server-stream."""

    async def test_generic_grpc(self) -> None:
        ir = _base_ir("grpc-svc", "grpc", [_base_op("rpc_call")])
        assert protocol_capability_key(ir) == "grpc"

    async def test_grpc_unary_resolution(self) -> None:
        op = Operation(
            id="unary_call",
            name="unary_call",
            description="Unary",
            method="POST",
            path="/pkg.Svc/Method",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            grpc_unary=GrpcUnaryRuntimeConfig(rpc_path="/pkg.Svc/Method"),
        )
        ir = _base_ir("grpc-svc", "grpc", [op])
        assert protocol_capability_key(ir) == "grpc_unary"

    async def test_grpc_stream_resolution(self) -> None:
        op = _base_op("stream_op")
        evt = EventDescriptor(
            id="stream_evt",
            name="stream",
            transport=EventTransport.grpc_stream,
            support=EventSupportLevel.supported,
            operation_id="stream_op",
            grpc_stream=GrpcStreamRuntimeConfig(
                rpc_path="/pkg.Svc/Stream",
                mode=GrpcStreamMode.server,
            ),
        )
        ir = _base_ir("grpc-svc", "grpc", [op], events=[evt])
        assert protocol_capability_key(ir) == "grpc_stream"


class TestUnknownProtocol:
    """Unknown protocol gets a conservative capability row."""

    async def test_unknown_protocol_capability(self) -> None:
        ir = _base_ir("custom-svc", "custom_protocol", [_base_op("op")])
        cap = protocol_capability_for_service(ir)
        assert cap.extract is False
        assert cap.compile is False
        assert cap.runtime is False
        assert "Unknown" in cap.label


class TestProtocolIRValidation:
    """Each protocol-specific IR should pass schema validation."""

    async def test_openapi_ir_validates(self) -> None:
        ir = _base_ir("petstore", "openapi", [_base_op("list_pets")])
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed

    async def test_graphql_ir_validates(self) -> None:
        op = Operation(
            id="search",
            name="search",
            description="Search",
            method="POST",
            path="/graphql",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="Search",
                document="query Search { search { id } }",
            ),
        )
        ir = _base_ir("catalog", "graphql", [op])
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed

    async def test_soap_ir_validates(self) -> None:
        op = Operation(
            id="op",
            name="op",
            description="SOAP op",
            method="POST",
            path="/ws",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            soap=SoapOperationConfig(
                target_namespace="http://example.com",
                request_element="Request",
            ),
        )
        ir = _base_ir("soap-svc", "soap", [op])
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed

    async def test_grpc_unary_ir_validates(self) -> None:
        op = Operation(
            id="unary",
            name="unary",
            description="Unary RPC",
            method="POST",
            path="/pkg.Svc/Method",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            grpc_unary=GrpcUnaryRuntimeConfig(rpc_path="/pkg.Svc/Method"),
        )
        ir = _base_ir("grpc-svc", "grpc", [op])
        async with PreDeployValidator(allow_native_grpc_unary=True) as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed


class TestErrorSchemaPresence:
    """Protocols should be able to carry error schemas."""

    async def test_openapi_error_schema(self) -> None:
        error = ErrorSchema(
            responses=[
                ErrorResponse(status_code=404, description="Not found"),
                ErrorResponse(status_code=500, description="Server error"),
            ],
            default_error_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        )
        op = _base_op("get_item", error_schema=error)
        ir = _base_ir("svc", "openapi", [op])

        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed

        assert len(ir.operations[0].error_schema.responses) == 2
        assert ir.operations[0].error_schema.default_error_schema is not None
