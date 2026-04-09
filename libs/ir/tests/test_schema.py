"""Tests for libs.ir.schema — serialization, deserialization, and JSON Schema generation."""

from __future__ import annotations

import json

import pytest

from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.ir.schema import (
    deserialize_ir,
    generate_json_schema,
    generate_json_schema_string,
    ir_from_dict,
    ir_to_dict,
    serialize_ir,
)


def _minimal_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol="rest",
        service_name="schema-test",
        service_description="Fixture for schema tests",
        base_url="https://example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="listItems",
                name="List Items",
                description="List all items.",
                method="GET",
                path="/items",
                params=[Param(name="limit", type="integer", required=False, default=10)],
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
            ),
        ],
    )


class TestSerializeDeserialize:
    def test_round_trip_via_json_string(self) -> None:
        ir = _minimal_ir()
        json_str = serialize_ir(ir)
        restored = deserialize_ir(json_str)
        assert restored.service_name == ir.service_name
        assert restored.protocol == ir.protocol
        assert len(restored.operations) == 1
        assert restored.operations[0].id == "listItems"

    def test_round_trip_via_dict(self) -> None:
        ir = _minimal_ir()
        d = ir_to_dict(ir)
        assert isinstance(d, dict)
        assert d["service_name"] == "schema-test"
        restored = ir_from_dict(d)
        assert restored.service_name == ir.service_name
        assert restored.operations[0].params[0].default == 10

    def test_serialize_produces_valid_json(self) -> None:
        json_str = serialize_ir(_minimal_ir())
        parsed = json.loads(json_str)
        assert parsed["protocol"] == "rest"

    def test_deserialize_rejects_invalid_json(self) -> None:
        with pytest.raises(Exception):
            deserialize_ir("{not valid json}")

    def test_ir_from_dict_rejects_missing_required_fields(self) -> None:
        with pytest.raises(Exception):
            ir_from_dict({"protocol": "rest"})


class TestJsonSchemaGeneration:
    def test_generate_json_schema_returns_dict(self) -> None:
        schema = generate_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "service_name" in schema["properties"]

    def test_generate_json_schema_string_returns_formatted_json(self) -> None:
        schema_str = generate_json_schema_string(indent=4)
        parsed = json.loads(schema_str)
        assert "properties" in parsed

    def test_schema_contains_operation_definitions(self) -> None:
        schema = generate_json_schema()
        # Operations should be defined somewhere in the schema (top-level or $defs)
        schema_json = json.dumps(schema)
        assert "Operation" in schema_json

    def test_schema_string_default_indent(self) -> None:
        schema_str = generate_json_schema_string()
        # Default indent=2, so lines should have 2-space indentation
        lines = schema_str.split("\n")
        indented_lines = [line for line in lines if line.startswith("  ")]
        assert len(indented_lines) > 0
