"""Tests for IR Pydantic models — validation, invariants, and round-trip serialization."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    OperationChain,
    Param,
    PaginationConfig,
    ResponseStrategy,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
    TruncationPolicy,
)
from libs.ir.schema import deserialize_ir, generate_json_schema, ir_from_dict, ir_to_dict, serialize_ir


# ── Fixtures ───────────────────────────────────────────────────────────────

def make_param(**overrides) -> Param:
    defaults = {"name": "pet_id", "type": "integer", "required": True}
    return Param(**(defaults | overrides))


def make_risk(level: RiskLevel = RiskLevel.safe) -> RiskMetadata:
    return RiskMetadata(
        writes_state=level != RiskLevel.safe,
        destructive=level == RiskLevel.dangerous,
        risk_level=level,
        confidence=0.9,
    )


def make_operation(id: str = "get_pet", enabled: bool = True, **overrides) -> Operation:
    defaults = {
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


def make_service_ir(**overrides) -> ServiceIR:
    defaults = {
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
                operation_chains=[OperationChain(id="chain1", name="Chain", steps=["step1", "nonexistent"])],
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
        assert restored.operations[1].risk.destructive is True
        assert len(restored.operation_chains) == 1
        assert restored.tenant == "acme-corp"
        assert restored.metadata["openapi_version"] == "3.1.0"


# ── Hypothesis Property-Based Tests ───────────────────────────────────────

param_strategy = st.builds(
    Param,
    name=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "Pd"))),
    type=st.sampled_from(["string", "integer", "number", "boolean", "array", "object"]),
    required=st.booleans(),
    description=st.text(max_size=200),
    source=st.just(SourceType.extractor),
    confidence=st.floats(min_value=0.8, max_value=1.0),
)


@given(param=param_strategy)
@settings(max_examples=50)
def test_param_round_trip_property(param: Param):
    """Any valid Param should survive JSON round-trip."""
    json_str = param.model_dump_json()
    restored = Param.model_validate_json(json_str)
    assert restored.name == param.name
    assert restored.type == param.type
    assert restored.required == param.required
