"""Tests for IR Pydantic models — validation, invariants, and round-trip serialization."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from libs.ir.models import (
    AsyncJobConfig,
    AsyncStatusUrlSource,
    AuthConfig,
    AuthType,
    ErrorResponse,
    ErrorSchema,
    EventDescriptor,
    EventDirection,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    MTLSConfig,
    OAuth2ClientCredentialsConfig,
    Operation,
    OperationChain,
    PaginationConfig,
    Param,
    RequestBodyMode,
    RequestSigningConfig,
    ResponseExample,
    ResponseStrategy,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SoapOperationConfig,
    SourceType,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
    ToolGroup,
    ToolIntent,
    TruncationPolicy,
)
from libs.ir.schema import (
    deserialize_ir,
    generate_json_schema,
    ir_from_dict,
    ir_to_dict,
    serialize_ir,
)

# ── Fixtures ───────────────────────────────────────────────────────────────

def make_param(**overrides: Any) -> Param:
    defaults: dict[str, Any] = {"name": "pet_id", "type": "integer", "required": True}
    return Param(**(defaults | overrides))


def make_risk(level: RiskLevel = RiskLevel.safe) -> RiskMetadata:
    return RiskMetadata(
        writes_state=level != RiskLevel.safe,
        destructive=level == RiskLevel.dangerous,
        risk_level=level,
        confidence=0.9,
    )


def make_operation(
    id: str = "get_pet",
    enabled: bool = True,
    **overrides: Any,
) -> Operation:
    defaults: dict[str, Any] = {
        "id": id,
        "name": f"Get {id}",
        "description": f"Retrieve {id}",
        "method": "GET",
        "path": f"/{id}",
        "params": [make_param()],
        "risk": make_risk(RiskLevel.safe),
        "enabled": enabled,
    }
    return Operation(**(defaults | overrides))


def make_service_ir(**overrides: Any) -> ServiceIR:
    defaults: dict[str, Any] = {
        "source_hash": "abc123def456",
        "protocol": "openapi",
        "service_name": "petstore",
        "base_url": "https://petstore.example.com/v1",
        "operations": [make_operation()],
    }
    return ServiceIR(**(defaults | overrides))


# ── Param Tests ────────────────────────────────────────────────────────────

class TestParam:
    def test_valid_param(self):
        p = make_param()
        assert p.name == "pet_id"
        assert p.type == "integer"
        assert p.required is True
        assert p.confidence == 1.0

    def test_extractor_source_low_confidence_rejected(self):
        with pytest.raises(ValueError, match="confidence >= 0.8"):
            make_param(source=SourceType.extractor, confidence=0.5)

    def test_llm_source_low_confidence_allowed(self):
        p = make_param(source=SourceType.llm, confidence=0.3)
        assert p.confidence == 0.3

    def test_default_values(self):
        p = Param(name="x", type="string")
        assert p.required is False
        assert p.description == ""
        assert p.default is None
        assert p.source == SourceType.extractor

    def test_confidence_bounds(self):
        with pytest.raises(ValueError):
            make_param(confidence=1.5)
        with pytest.raises(ValueError):
            make_param(confidence=-0.1)


# ── RiskMetadata Tests ─────────────────────────────────────────────────────

class TestRiskMetadata:
    def test_defaults(self):
        r = RiskMetadata()
        assert r.risk_level == RiskLevel.unknown
        assert r.confidence == 0.5

    def test_all_fields(self):
        r = RiskMetadata(
            writes_state=True,
            destructive=True,
            external_side_effect=True,
            idempotent=False,
            risk_level=RiskLevel.dangerous,
            confidence=0.95,
            source=SourceType.extractor,
        )
        assert r.destructive is True
        assert r.risk_level == RiskLevel.dangerous


# ── Operation Tests ────────────────────────────────────────────────────────

class TestOperation:
    def test_valid_operation(self):
        op = make_operation()
        assert op.id == "get_pet"
        assert op.enabled is True

    def test_unknown_risk_enabled_rejected(self):
        with pytest.raises(ValueError, match="unknown.*disabled"):
            make_operation(risk=RiskMetadata(risk_level=RiskLevel.unknown), enabled=True)

    def test_unknown_risk_disabled_allowed(self):
        op = make_operation(
            risk=RiskMetadata(risk_level=RiskLevel.unknown),
            enabled=False,
        )
        assert op.enabled is False

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError):
            make_operation(id="")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError):
            make_operation(name="")

    def test_operation_supports_request_body_modes_and_async_job(self):
        op = make_operation(
            id="upload_document",
            method="POST",
            request_body_mode=RequestBodyMode.multipart,
            body_param_name="payload",
            async_job=AsyncJobConfig(
                status_url_source=AsyncStatusUrlSource.response_body,
                status_url_field="job.status_url",
                status_field="job.state",
            ),
        )

        assert op.request_body_mode == RequestBodyMode.multipart
        assert op.body_param_name == "payload"
        assert op.async_job is not None
        assert op.async_job.status_url_source == AsyncStatusUrlSource.response_body

    def test_operation_supports_typed_graphql_execution_contract(self):
        op = make_operation(
            id="searchProducts",
            method="POST",
            path="/graphql",
            graphql=GraphQLOperationConfig(
                operation_type=GraphQLOperationType.query,
                operation_name="searchProducts",
                document=(
                    "query searchProducts($term: String!) {\n"
                    "  searchProducts(term: $term) { id name }\n"
                    "}"
                ),
                variable_names=["term"],
            ),
        )

        assert op.graphql is not None
        assert op.graphql.operation_type is GraphQLOperationType.query
        assert op.graphql.variable_names == ["term"]

    def test_operation_supports_typed_grpc_unary_execution_contract(self):
        op = make_operation(
            id="ListItems",
            method="POST",
            path="/catalog.v1.InventoryService/ListItems",
            grpc_unary=GrpcUnaryRuntimeConfig(
                rpc_path="/catalog.v1.InventoryService/ListItems",
                timeout_seconds=4.0,
            ),
        )

        assert op.grpc_unary is not None
        assert op.grpc_unary.rpc_path == "/catalog.v1.InventoryService/ListItems"
        assert op.grpc_unary.timeout_seconds == 4.0

    def test_operation_supports_typed_soap_execution_contract(self):
        op = make_operation(
            id="GetOrderStatus",
            method="POST",
            path="/soap/order-service",
            soap=SoapOperationConfig(
                target_namespace="http://example.com/orders/wsdl",
                request_element="GetOrderStatusRequest",
                response_element="GetOrderStatusResponse",
                soap_action="http://example.com/orders/GetOrderStatus",
            ),
        )

        assert op.soap is not None
        assert op.soap.request_element == "GetOrderStatusRequest"
        assert op.soap.response_element == "GetOrderStatusResponse"

    def test_operation_supports_typed_sql_execution_contract(self):
        op = make_operation(
            id="query_orders",
            method="GET",
            path="/sql/public/orders",
            sql=SqlOperationConfig(
                schema_name="public",
                relation_name="orders",
                relation_kind=SqlRelationKind.table,
                action=SqlOperationType.query,
                filterable_columns=["id", "customer_id"],
            ),
        )

        assert op.sql is not None
        assert op.sql.action is SqlOperationType.query
        assert op.sql.filterable_columns == ["id", "customer_id"]

    def test_grpc_unary_contract_rejects_non_post_methods(self):
        with pytest.raises(ValueError, match="method='POST'"):
            make_operation(
                method="GET",
                grpc_unary=GrpcUnaryRuntimeConfig(rpc_path="/catalog.v1.InventoryService/ListItems"),
            )

    def test_soap_contract_rejects_non_post_methods(self):
        with pytest.raises(ValueError, match="method='POST'"):
            make_operation(
                method="GET",
                soap=SoapOperationConfig(
                    target_namespace="http://example.com/orders/wsdl",
                    request_element="GetOrderStatusRequest",
                ),
            )

    def test_sql_query_contract_rejects_non_get_methods(self):
        with pytest.raises(ValueError, match="method='GET'"):
            make_operation(
                method="POST",
                sql=SqlOperationConfig(
                    schema_name="public",
                    relation_name="orders",
                    relation_kind=SqlRelationKind.table,
                    action=SqlOperationType.query,
                    filterable_columns=["id"],
                ),
            )

    def test_sql_insert_contract_rejects_non_post_methods(self):
        with pytest.raises(ValueError, match="method='POST'"):
            make_operation(
                method="GET",
                sql=SqlOperationConfig(
                    schema_name="public",
                    relation_name="orders",
                    relation_kind=SqlRelationKind.table,
                    action=SqlOperationType.insert,
                    filterable_columns=["id"],
                    insertable_columns=["customer_id"],
                ),
            )

    def test_grpc_unary_contract_requires_matching_path(self):
        with pytest.raises(ValueError, match="must match operation.path"):
            make_operation(
                method="POST",
                path="/wrong/path",
                grpc_unary=GrpcUnaryRuntimeConfig(rpc_path="/catalog.v1.InventoryService/ListItems"),
            )

    def test_async_job_response_body_source_requires_status_url_field(self):
        with pytest.raises(ValueError, match="status_url_field"):
            AsyncJobConfig(status_url_source=AsyncStatusUrlSource.response_body)


# ── ServiceIR Tests ────────────────────────────────────────────────────────

class TestServiceIR:
    def test_valid_service_ir(self):
        ir = make_service_ir()
        assert ir.service_name == "petstore"
        assert len(ir.operations) == 1
        assert ir.ir_version == "1.0.0"

    def test_duplicate_operation_ids_rejected(self):
        with pytest.raises(ValueError, match="Duplicate operation IDs"):
            make_service_ir(operations=[
                make_operation(id="op1"),
                make_operation(id="op1"),
            ])

    def test_unique_operation_ids_accepted(self):
        ir = make_service_ir(operations=[
            make_operation(id="op1"),
            make_operation(id="op2"),
        ])
        assert len(ir.operations) == 2

    def test_empty_operations_accepted(self):
        ir = make_service_ir(operations=[])
        assert len(ir.operations) == 0

    def test_chain_references_valid_operations(self):
        ir = make_service_ir(
            operations=[make_operation(id="step1"), make_operation(id="step2")],
            operation_chains=[OperationChain(id="chain1", name="Chain", steps=["step1", "step2"])],
        )
        assert len(ir.operation_chains) == 1

    def test_chain_references_invalid_operations_rejected(self):
        with pytest.raises(ValueError, match="unknown operations"):
            make_service_ir(
                operations=[make_operation(id="step1")],
                operation_chains=[
                    OperationChain(
                        id="chain1",
                        name="Chain",
                        steps=["step1", "nonexistent"],
                    )
                ],
            )

    def test_event_descriptors_accept_multiple_transports(self):
        ir = make_service_ir(
            event_descriptors=[
                EventDescriptor(
                    id="invoiceSigned",
                    name="invoiceSigned",
                    transport=EventTransport.webhook,
                    direction=EventDirection.inbound,
                ),
                EventDescriptor(
                    id="inventoryChanged",
                    name="inventoryChanged",
                    transport=EventTransport.graphql_subscription,
                    direction=EventDirection.inbound,
                    channel="/graphql",
                ),
                EventDescriptor(
                    id="watchInventory",
                    name="watchInventory",
                    transport=EventTransport.grpc_stream,
                    direction=EventDirection.bidirectional,
                    support=EventSupportLevel.planned,
                    channel="/catalog.v1.InventoryService/WatchInventory",
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/catalog.v1.InventoryService/WatchInventory",
                        mode=GrpcStreamMode.bidirectional,
                    ),
                ),
            ]
        )

        assert len(ir.event_descriptors) == 3
        assert ir.event_descriptors[0].transport is EventTransport.webhook
        assert ir.event_descriptors[1].channel == "/graphql"
        assert ir.event_descriptors[2].support is EventSupportLevel.planned

    def test_grpc_stream_descriptor_requires_runtime_config(self):
        with pytest.raises(ValueError, match="grpc_stream runtime config"):
            EventDescriptor(
                id="watchInventory",
                name="watchInventory",
                transport=EventTransport.grpc_stream,
                direction=EventDirection.bidirectional,
            )

    def test_non_grpc_transport_rejects_grpc_stream_runtime_config(self):
        with pytest.raises(ValueError, match="only valid for grpc_stream transport"):
            EventDescriptor(
                id="inventoryChanged",
                name="inventoryChanged",
                transport=EventTransport.sse,
                direction=EventDirection.inbound,
                grpc_stream=GrpcStreamRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/WatchInventory",
                    mode=GrpcStreamMode.server,
                ),
            )

    def test_event_descriptor_operation_reference_must_exist(self):
        with pytest.raises(ValueError, match="unknown operations"):
            make_service_ir(
                event_descriptors=[
                    EventDescriptor(
                        id="callback",
                        name="callback",
                        transport=EventTransport.callback,
                        operation_id="missing-operation",
                    )
                ]
            )

    def test_empty_service_name_rejected(self):
        with pytest.raises(ValueError):
            make_service_ir(service_name="")

    def test_created_at_auto_set(self):
        ir = make_service_ir()
        assert ir.created_at is not None

    def test_optional_fields_default_none(self):
        ir = make_service_ir()
        assert ir.source_url is None
        assert ir.tenant is None
        assert ir.environment is None


# ── ResponseStrategy Validation Tests ──────────────────────────────────────


class TestResponseStrategy:
    def test_max_array_items_accepts_positive_int(self):
        rs = ResponseStrategy(max_array_items=5)
        assert rs.max_array_items == 5

    def test_max_array_items_defaults_to_none(self):
        rs = ResponseStrategy()
        assert rs.max_array_items is None

    def test_max_array_items_rejects_zero(self):
        with pytest.raises(ValidationError):
            ResponseStrategy(max_array_items=0)

    def test_max_array_items_rejects_negative(self):
        with pytest.raises(ValidationError):
            ResponseStrategy(max_array_items=-1)

    def test_max_array_items_round_trip(self):
        rs = ResponseStrategy(max_array_items=10, field_filter=["id", "name"])
        data = rs.model_dump()
        restored = ResponseStrategy.model_validate(data)
        assert restored.max_array_items == 10
        assert restored.field_filter == ["id", "name"]


# ── Serialization Round-Trip Tests ─────────────────────────────────────────

class TestSerialization:
    def test_json_round_trip(self):
        original = make_service_ir()
        json_str = serialize_ir(original)
        restored = deserialize_ir(json_str)

        assert restored.service_name == original.service_name
        assert restored.protocol == original.protocol
        assert restored.base_url == original.base_url
        assert len(restored.operations) == len(original.operations)
        assert restored.operations[0].id == original.operations[0].id

    def test_dict_round_trip(self):
        original = make_service_ir()
        d = ir_to_dict(original)
        restored = ir_from_dict(d)

        assert restored.service_name == original.service_name
        assert len(restored.operations) == len(original.operations)

    def test_json_schema_is_valid(self):
        schema = generate_json_schema()
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "service_name" in schema["properties"]
        assert "operations" in schema["properties"]

    def test_complex_ir_round_trip(self):
        """Round-trip a fully populated IR with all optional fields."""
        ir = ServiceIR(
            source_url="https://api.example.com/openapi.json",
            source_hash="deadbeef" * 8,
            protocol="openapi",
            service_name="complex-api",
            service_description="A complex API for testing",
            base_url="https://api.example.com/v2",
            auth=AuthConfig(
                type=AuthType.bearer,
                header_name="Authorization",
                header_prefix="Bearer",
                runtime_secret_ref="complex-api-secret",
            ),
            operations=[
                Operation(
                    id="list_items",
                    name="List Items",
                    description="List all items with pagination",
                    method="GET",
                    path="/items",
                    params=[
                        Param(name="page", type="integer", required=False, default=1),
                        Param(name="size", type="integer", required=False, default=20),
                    ],
                    risk=RiskMetadata(
                        writes_state=False,
                        destructive=False,
                        idempotent=True,
                        risk_level=RiskLevel.safe,
                        confidence=0.95,
                    ),
                    response_strategy=ResponseStrategy(
                        pagination=PaginationConfig(style="offset"),
                        max_response_bytes=1_000_000,
                        max_array_items=50,
                        truncation_policy=TruncationPolicy.truncate,
                    ),
                    tags=["items", "read"],
                ),
                Operation(
                    id="delete_item",
                    name="Delete Item",
                    description="Delete an item by ID",
                    method="DELETE",
                    path="/items/{id}",
                    params=[Param(name="id", type="string", required=True)],
                    risk=RiskMetadata(
                        writes_state=True,
                        destructive=True,
                        idempotent=True,
                        risk_level=RiskLevel.dangerous,
                        confidence=0.99,
                    ),
                    tags=["items", "write"],
                ),
            ],
            operation_chains=[
                OperationChain(
                    id="list_then_delete",
                    name="List then Delete",
                    steps=["list_items", "delete_item"],
                ),
            ],
            tenant="acme-corp",
            environment="staging",
            metadata={"openapi_version": "3.1.0", "spec_title": "Complex API"},
        )

        json_str = serialize_ir(ir)
        restored = deserialize_ir(json_str)

        assert restored.auth.type == AuthType.bearer
        assert restored.auth.runtime_secret_ref == "complex-api-secret"
        assert len(restored.operations) == 2
        assert restored.operations[0].response_strategy.pagination is not None
        assert restored.operations[0].response_strategy.pagination.style == "offset"
        assert restored.operations[0].response_strategy.max_array_items == 50
        assert restored.operations[1].risk.destructive is True
        assert len(restored.operation_chains) == 1
        assert restored.tenant == "acme-corp"
        assert restored.metadata["openapi_version"] == "3.1.0"

    def test_advanced_auth_ir_round_trip(self):
        ir = make_service_ir(
            auth=AuthConfig(
                type=AuthType.oauth2,
                oauth2=OAuth2ClientCredentialsConfig(
                    token_url="https://auth.example.com/oauth/token",
                    client_id_ref="oauth-client-id",
                    client_secret_ref="oauth-client-secret",
                    scopes=["inventory.read", "inventory.write"],
                    audience="inventory-api",
                ),
                mtls=MTLSConfig(
                    cert_ref="inventory-client-cert",
                    key_ref="inventory-client-key",
                    ca_ref="inventory-ca-cert",
                ),
                request_signing=RequestSigningConfig(
                    secret_ref="inventory-signing-secret",
                    key_id="inventory-runtime",
                ),
            )
        )

        restored = deserialize_ir(serialize_ir(ir))

        assert restored.auth.type == AuthType.oauth2
        assert restored.auth.oauth2 is not None
        assert restored.auth.oauth2.client_id_ref == "oauth-client-id"
        assert restored.auth.oauth2.client_secret_ref == "oauth-client-secret"
        assert restored.auth.oauth2.audience == "inventory-api"
        assert restored.auth.mtls is not None
        assert restored.auth.mtls.cert_ref == "inventory-client-cert"
        assert restored.auth.mtls.key_ref == "inventory-client-key"
        assert restored.auth.request_signing is not None
        assert restored.auth.request_signing.secret_ref == "inventory-signing-secret"
        assert restored.auth.request_signing.key_id == "inventory-runtime"

    def test_oauth2_nested_config_requires_oauth2_auth_type(self):
        with pytest.raises(ValueError, match="oauth2"):
            AuthConfig(
                type=AuthType.bearer,
                runtime_secret_ref="legacy-bearer-token",
                oauth2=OAuth2ClientCredentialsConfig(
                    token_url="https://auth.example.com/oauth/token",
                    client_id_ref="oauth-client-id",
                    client_secret_ref="oauth-client-secret",
                ),
            )


# ── Hypothesis Property-Based Tests ───────────────────────────────────────

param_strategy = st.builds(
    Param,
    name=st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
    ),
    type=st.sampled_from(["string", "integer", "number", "boolean", "array", "object"]),
    required=st.booleans(),
    description=st.text(max_size=200),
    source=st.just(SourceType.extractor),
    confidence=st.floats(min_value=0.8, max_value=1.0),
)


@given(param=param_strategy)
@settings(max_examples=50)
def test_param_round_trip_property(param: Param) -> None:
    """Any valid Param should survive JSON round-trip."""
    json_str = param.model_dump_json()
    restored = Param.model_validate_json(json_str)
    assert restored.name == param.name
    assert restored.type == param.type
    assert restored.required == param.required


# ── ToolIntent and ToolGroup Tests ──────────────────────────────────────

class TestToolIntent:
    def test_operation_accepts_discovery_intent(self) -> None:
        op = make_operation()
        updated = op.model_copy(update={"tool_intent": ToolIntent.discovery})
        assert updated.tool_intent == ToolIntent.discovery

    def test_operation_accepts_action_intent(self) -> None:
        op = make_operation()
        updated = op.model_copy(update={"tool_intent": ToolIntent.action})
        assert updated.tool_intent == ToolIntent.action

    def test_operation_defaults_to_none_intent(self) -> None:
        op = make_operation()
        assert op.tool_intent is None


class TestToolGroup:
    def test_valid_tool_group(self) -> None:
        ir = make_service_ir()
        op_ids = [op.id for op in ir.operations]
        group = ToolGroup(
            id="user-management",
            label="User Management",
            intent="CRUD operations for user accounts",
            operation_ids=op_ids,
        )
        updated = ir.model_copy(update={"tool_grouping": [group]})
        assert len(updated.tool_grouping) == 1
        assert updated.tool_grouping[0].label == "User Management"

    def test_tool_group_rejects_unknown_operations(self) -> None:
        ir = make_service_ir()
        group = ToolGroup(
            id="bad-group",
            label="Bad Group",
            operation_ids=["nonexistent_op"],
        )
        data = ir.model_dump()
        data["tool_grouping"] = [group.model_dump()]
        with pytest.raises(ValidationError, match="unknown operations"):
            ServiceIR.model_validate(data)

    def test_empty_tool_grouping_is_valid(self) -> None:
        ir = make_service_ir()
        assert ir.tool_grouping == []

    def test_tool_group_round_trip(self) -> None:
        ir = make_service_ir()
        op_ids = [op.id for op in ir.operations]
        group = ToolGroup(
            id="test-group",
            label="Test Group",
            intent="Testing",
            operation_ids=op_ids,
            source=SourceType.llm,
            confidence=0.85,
        )
        updated = ir.model_copy(update={"tool_grouping": [group]})
        serialized = serialize_ir(updated)
        restored = deserialize_ir(serialized)
        assert len(restored.tool_grouping) == 1
        assert restored.tool_grouping[0].id == "test-group"
        assert restored.tool_grouping[0].confidence == 0.85


# ── ErrorSchema / ResponseExample (DEP-001) ──────────────────────────────


class TestErrorResponse:
    def test_defaults(self) -> None:
        er = ErrorResponse()
        assert er.status_code is None
        assert er.error_code is None
        assert er.description == ""
        assert er.error_body_schema is None

    def test_http_error_response(self) -> None:
        er = ErrorResponse(
            status_code=404,
            error_code="NOT_FOUND",
            description="Resource not found",
            error_body_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        )
        assert er.status_code == 404
        assert er.error_code == "NOT_FOUND"

    def test_non_http_error_response(self) -> None:
        er = ErrorResponse(error_code="INVALID_ARGUMENT", description="Bad field value")
        assert er.status_code is None
        assert er.error_code == "INVALID_ARGUMENT"

    def test_round_trip(self) -> None:
        er = ErrorResponse(status_code=500, error_code="INTERNAL", description="Server error")
        data = er.model_dump(mode="json")
        restored = ErrorResponse.model_validate(data)
        assert restored == er


class TestErrorSchema:
    def test_defaults(self) -> None:
        es = ErrorSchema()
        assert es.responses == []
        assert es.default_error_schema is None

    def test_with_responses(self) -> None:
        es = ErrorSchema(
            responses=[
                ErrorResponse(status_code=400, description="Bad request"),
                ErrorResponse(status_code=500, description="Internal error"),
            ],
            default_error_schema={"type": "object", "properties": {"error": {"type": "string"}}},
        )
        assert len(es.responses) == 2
        assert es.default_error_schema is not None

    def test_round_trip(self) -> None:
        es = ErrorSchema(
            responses=[ErrorResponse(status_code=422, error_code="VALIDATION_ERROR")],
            default_error_schema={"type": "object"},
        )
        data = es.model_dump(mode="json")
        restored = ErrorSchema.model_validate(data)
        assert restored == es


class TestResponseExample:
    def test_minimal(self) -> None:
        ex = ResponseExample(name="success")
        assert ex.name == "success"
        assert ex.description == ""
        assert ex.status_code is None
        assert ex.body is None
        assert ex.source == SourceType.extractor

    def test_full(self) -> None:
        ex = ResponseExample(
            name="user-list",
            description="Example list of users",
            status_code=200,
            body={"users": [{"id": 1, "name": "Alice"}]},
            source=SourceType.llm,
        )
        assert ex.status_code == 200
        assert ex.source == SourceType.llm

    def test_string_body(self) -> None:
        ex = ResponseExample(name="raw", body="plain text response")
        assert ex.body == "plain text response"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResponseExample(name="")

    def test_round_trip(self) -> None:
        ex = ResponseExample(
            name="example-1",
            status_code=200,
            body={"id": 42},
            source=SourceType.extractor,
        )
        data = ex.model_dump(mode="json")
        restored = ResponseExample.model_validate(data)
        assert restored == ex


class TestOperationErrorSchemaAndExamples:
    def test_operation_defaults_empty_error_schema(self) -> None:
        op = Operation(
            id="test-op",
            name="Test",
            risk=RiskMetadata(risk_level=RiskLevel.safe),
        )
        assert op.error_schema == ErrorSchema()
        assert op.response_examples == []

    def test_operation_with_error_schema(self) -> None:
        op = Operation(
            id="test-op",
            name="Test",
            risk=RiskMetadata(risk_level=RiskLevel.safe),
            error_schema=ErrorSchema(
                responses=[ErrorResponse(status_code=404, description="Not found")],
            ),
        )
        assert len(op.error_schema.responses) == 1
        assert op.error_schema.responses[0].status_code == 404

    def test_operation_with_response_examples(self) -> None:
        op = Operation(
            id="test-op",
            name="Test",
            risk=RiskMetadata(risk_level=RiskLevel.safe),
            response_examples=[
                ResponseExample(name="ok", status_code=200, body={"result": "success"}),
            ],
        )
        assert len(op.response_examples) == 1
        assert op.response_examples[0].name == "ok"

    def test_backward_compat_existing_operations_unaffected(self) -> None:
        """Existing operations without error_schema/response_examples still work."""
        op_data = {
            "id": "legacy-op",
            "name": "Legacy",
            "risk": {"risk_level": "safe"},
        }
        op = Operation.model_validate(op_data)
        assert op.error_schema == ErrorSchema()
        assert op.response_examples == []

    def test_ir_round_trip_with_error_schema_and_examples(self) -> None:
        ir = make_service_ir()
        op = ir.operations[0]
        updated_op = op.model_copy(
            update={
                "error_schema": ErrorSchema(
                    responses=[
                        ErrorResponse(status_code=400, description="Bad request"),
                        ErrorResponse(status_code=500, error_code="INTERNAL"),
                    ],
                    default_error_schema={"type": "object"},
                ),
                "response_examples": [
                    ResponseExample(name="success", status_code=200, body={"id": 1}),
                    ResponseExample(name="error", status_code=400, body="bad input"),
                ],
            }
        )
        updated_ir = ir.model_copy(update={"operations": [updated_op]})
        serialized = serialize_ir(updated_ir)
        restored = deserialize_ir(serialized)
        restored_op = restored.operations[0]
        assert len(restored_op.error_schema.responses) == 2
        assert restored_op.error_schema.responses[0].status_code == 400
        assert restored_op.error_schema.default_error_schema == {"type": "object"}
        assert len(restored_op.response_examples) == 2
        assert restored_op.response_examples[0].name == "success"
        assert restored_op.response_examples[1].body == "bad input"
