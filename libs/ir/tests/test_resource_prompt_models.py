"""Tests for MCP Resource and Prompt IR models (IRX-001, IRX-002)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from libs.ir.models import (
    Operation,
    Param,
    PromptArgument,
    PromptDefinition,
    ResourceDefinition,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)
from libs.ir.schema import (
    deserialize_ir,
    generate_json_schema,
    ir_from_dict,
    ir_to_dict,
    serialize_ir,
)

# ── Helpers ────────────────────────────────────────────────────────────────


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


def make_operation(id: str = "get_pet", enabled: bool = True, **overrides: Any) -> Operation:
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


def make_resource(**overrides: Any) -> ResourceDefinition:
    defaults: dict[str, Any] = {
        "id": "schema",
        "name": "API Schema",
        "description": "The API schema summary",
        "uri": "service:///petstore/schema",
        "mime_type": "application/json",
        "content_type": "static",
        "content": '{"info": "petstore schema"}',
    }
    values = defaults | overrides
    if values.get("content_type") == "dynamic" and "content" not in overrides:
        values["content"] = None
    return ResourceDefinition(**values)


def make_prompt_argument(**overrides: Any) -> PromptArgument:
    defaults: dict[str, Any] = {
        "name": "entity",
        "description": "The entity to manage",
        "required": True,
    }
    return PromptArgument(**(defaults | overrides))


def make_prompt(**overrides: Any) -> PromptDefinition:
    defaults: dict[str, Any] = {
        "id": "explore_petstore",
        "name": "Explore Petstore",
        "description": "Explore available operations",
        "template": ("List available operations for {service_name} and their risk levels"),
        "arguments": [
            PromptArgument(
                name="service_name",
                description="Service name",
                required=True,
            ),
        ],
        "tool_ids": ["get_pet"],
    }
    return PromptDefinition(**(defaults | overrides))


# ── IRX-001: ResourceDefinition Tests ──────────────────────────────────────


class TestResourceDefinition:
    def test_valid_resource_all_fields(self) -> None:
        r = make_resource()
        assert r.id == "schema"
        assert r.name == "API Schema"
        assert r.description == "The API schema summary"
        assert r.uri == "service:///petstore/schema"
        assert r.mime_type == "application/json"
        assert r.content_type == "static"
        assert r.content == '{"info": "petstore schema"}'
        assert r.tags == []
        assert r.operation_id is None

    def test_resource_static_with_content(self) -> None:
        r = make_resource(content_type="static", content="hello world")
        assert r.content_type == "static"
        assert r.content == "hello world"

    def test_resource_dynamic_with_operation_id(self) -> None:
        r = make_resource(
            content_type="dynamic",
            content=None,
            operation_id="fetch_schema",
        )
        assert r.content_type == "dynamic"
        assert r.operation_id == "fetch_schema"
        assert r.content is None

    def test_resource_dynamic_without_operation_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must define operation_id"):
            make_resource(content_type="dynamic", content=None, operation_id=None)

    def test_resource_dynamic_with_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not define static content"):
            make_resource(
                content_type="dynamic",
                content='{"schema":"inline"}',
                operation_id="fetch_schema",
            )

    def test_resource_with_tags(self) -> None:
        r = make_resource(tags=["metadata", "schema"])
        assert r.tags == ["metadata", "schema"]

    def test_resource_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_resource(id="")

    def test_resource_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_resource(name="")

    def test_resource_empty_uri_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_resource(uri="")

    def test_resource_defaults(self) -> None:
        with pytest.raises(ValidationError, match="must define content"):
            ResourceDefinition(id="r1", name="R1", uri="service:///test/r1")

    def test_resource_static_without_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must define content"):
            make_resource(content_type="static", content=None)

    def test_resource_mime_type_override(self) -> None:
        r = make_resource(mime_type="text/plain")
        assert r.mime_type == "text/plain"


# ── IRX-001: PromptArgument Tests ──────────────────────────────────────────


class TestPromptArgument:
    def test_valid_argument(self) -> None:
        a = make_prompt_argument()
        assert a.name == "entity"
        assert a.description == "The entity to manage"
        assert a.required is True
        assert a.default is None

    def test_argument_with_default(self) -> None:
        a = make_prompt_argument(required=False, default="pet")
        assert a.required is False
        assert a.default == "pet"

    def test_argument_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_prompt_argument(name="")

    def test_argument_defaults(self) -> None:
        a = PromptArgument(name="x")
        assert a.description == ""
        assert a.required is False
        assert a.default is None


# ── IRX-001: PromptDefinition Tests ────────────────────────────────────────


class TestPromptDefinition:
    def test_valid_prompt_with_arguments(self) -> None:
        p = make_prompt()
        assert p.id == "explore_petstore"
        assert p.name == "Explore Petstore"
        assert p.description == "Explore available operations"
        assert "{service_name}" in p.template
        assert len(p.arguments) == 1
        assert p.arguments[0].name == "service_name"
        assert p.tool_ids == ["get_pet"]
        assert p.tags == []

    def test_prompt_with_tags(self) -> None:
        p = make_prompt(tags=["discovery", "safe"])
        assert p.tags == ["discovery", "safe"]

    def test_prompt_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_prompt(id="")

    def test_prompt_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_prompt(name="")

    def test_prompt_empty_template_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_prompt(template="")

    def test_prompt_multiple_arguments(self) -> None:
        args = [
            PromptArgument(name="entity", required=True),
            PromptArgument(name="format", required=False, default="json"),
        ]
        p = make_prompt(arguments=args)
        assert len(p.arguments) == 2
        assert p.arguments[1].default == "json"

    def test_prompt_no_tool_ids(self) -> None:
        p = make_prompt(tool_ids=[])
        assert p.tool_ids == []

    def test_prompt_defaults(self) -> None:
        p = PromptDefinition(id="p1", name="P1", template="Hello {name}")
        assert p.description == ""
        assert p.arguments == []
        assert p.tool_ids == []
        assert p.tags == []


# ── IRX-001: ServiceIR backward compatibility ──────────────────────────────


class TestServiceIRBackwardCompatibility:
    def test_default_factory_produces_empty_lists(self) -> None:
        ir = make_service_ir()
        assert ir.resource_definitions == []
        assert ir.prompt_definitions == []

    def test_existing_ir_without_new_fields_still_works(self) -> None:
        ir = make_service_ir()
        assert ir.service_name == "petstore"
        assert len(ir.operations) == 1

    def test_ir_with_resources_and_prompts(self) -> None:
        ir = make_service_ir(
            resource_definitions=[make_resource()],
            prompt_definitions=[make_prompt()],
        )
        assert len(ir.resource_definitions) == 1
        assert len(ir.prompt_definitions) == 1
        assert ir.resource_definitions[0].id == "schema"
        assert ir.prompt_definitions[0].id == "explore_petstore"


# ── IRX-002: Validator Tests ───────────────────────────────────────────────


class TestServiceIRResourcePromptValidators:
    def test_unique_resource_ids_accepted(self) -> None:
        ir = make_service_ir(
            resource_definitions=[
                make_resource(id="r1", uri="service:///test/r1"),
                make_resource(id="r2", uri="service:///test/r2"),
            ],
        )
        assert len(ir.resource_definitions) == 2

    def test_duplicate_resource_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match="Duplicate resource definition IDs"):
            make_service_ir(
                resource_definitions=[
                    make_resource(id="dup", uri="service:///test/a"),
                    make_resource(id="dup", uri="service:///test/b"),
                ],
            )

    def test_duplicate_resource_uris_rejected(self) -> None:
        with pytest.raises(ValueError, match="Duplicate resource definition URIs"):
            make_service_ir(
                resource_definitions=[
                    make_resource(id="r1", uri="service:///test/dup"),
                    make_resource(id="r2", uri="service:///test/dup"),
                ],
            )

    def test_unique_prompt_ids_accepted(self) -> None:
        ir = make_service_ir(
            prompt_definitions=[
                make_prompt(id="p1", name="Prompt One"),
                make_prompt(id="p2", name="Prompt Two"),
            ],
        )
        assert len(ir.prompt_definitions) == 2

    def test_duplicate_prompt_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match="Duplicate prompt definition IDs"):
            make_service_ir(
                prompt_definitions=[
                    make_prompt(id="dup"),
                    make_prompt(id="dup"),
                ],
            )

    def test_duplicate_prompt_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="Duplicate prompt definition names"):
            make_service_ir(
                prompt_definitions=[
                    make_prompt(id="p1", name="Same Prompt"),
                    make_prompt(id="p2", name="Same Prompt"),
                ],
            )

    def test_prompt_tool_ids_reference_valid_operations(self) -> None:
        ir = make_service_ir(
            operations=[make_operation(id="op1"), make_operation(id="op2")],
            prompt_definitions=[make_prompt(id="p1", tool_ids=["op1", "op2"])],
        )
        assert ir.prompt_definitions[0].tool_ids == ["op1", "op2"]

    def test_prompt_tool_ids_reference_invalid_operations_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown operations"):
            make_service_ir(
                operations=[make_operation(id="op1")],
                prompt_definitions=[make_prompt(id="p1", tool_ids=["op1", "nonexistent"])],
            )

    def test_prompt_empty_tool_ids_accepted(self) -> None:
        ir = make_service_ir(
            prompt_definitions=[make_prompt(id="p1", tool_ids=[])],
        )
        assert ir.prompt_definitions[0].tool_ids == []

    def test_resource_operation_id_references_valid_operation(self) -> None:
        ir = make_service_ir(
            operations=[make_operation(id="fetch_schema")],
            resource_definitions=[
                make_resource(
                    id="dynamic_schema",
                    content_type="dynamic",
                    operation_id="fetch_schema",
                ),
            ],
        )
        assert ir.resource_definitions[0].operation_id == "fetch_schema"

    def test_resource_operation_id_references_invalid_operation_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown operations"):
            make_service_ir(
                resource_definitions=[
                    make_resource(
                        id="dynamic_schema",
                        content_type="dynamic",
                        operation_id="nonexistent",
                    ),
                ],
            )

    def test_static_resource_no_operation_id_accepted(self) -> None:
        ir = make_service_ir(
            resource_definitions=[make_resource(operation_id=None)],
        )
        assert ir.resource_definitions[0].operation_id is None


# ── IRX-005: Schema generation includes new fields ────────────────────────


class TestSchemaResourcePrompt:
    def test_schema_contains_resource_definition(self) -> None:
        schema = generate_json_schema()
        schema_defs = schema.get("$defs", {})
        assert "ResourceDefinition" in schema_defs

    def test_schema_contains_prompt_definition(self) -> None:
        schema = generate_json_schema()
        schema_defs = schema.get("$defs", {})
        assert "PromptDefinition" in schema_defs

    def test_schema_contains_prompt_argument(self) -> None:
        schema = generate_json_schema()
        schema_defs = schema.get("$defs", {})
        assert "PromptArgument" in schema_defs

    def test_schema_service_ir_has_resource_definitions_field(self) -> None:
        schema = generate_json_schema()
        props = schema["properties"]
        assert "resource_definitions" in props

    def test_schema_service_ir_has_prompt_definitions_field(self) -> None:
        schema = generate_json_schema()
        props = schema["properties"]
        assert "prompt_definitions" in props

    def test_resource_definition_schema_has_correct_fields(self) -> None:
        schema = generate_json_schema()
        rd_schema = schema["$defs"]["ResourceDefinition"]
        rd_props = rd_schema["properties"]
        expected_fields = {
            "id",
            "name",
            "description",
            "uri",
            "mime_type",
            "content_type",
            "content",
            "operation_id",
            "tags",
        }
        assert expected_fields <= set(rd_props.keys())

    def test_prompt_definition_schema_has_correct_fields(self) -> None:
        schema = generate_json_schema()
        pd_schema = schema["$defs"]["PromptDefinition"]
        pd_props = pd_schema["properties"]
        expected_fields = {"id", "name", "description", "template", "arguments", "tool_ids", "tags"}
        assert expected_fields <= set(pd_props.keys())


# ── Serialization round-trip with resources/prompts ────────────────────────


class TestSerializationResourcePrompt:
    def test_json_round_trip_with_resources_and_prompts(self) -> None:
        ir = make_service_ir(
            resource_definitions=[make_resource()],
            prompt_definitions=[make_prompt()],
        )
        json_str = serialize_ir(ir)
        restored = deserialize_ir(json_str)

        assert len(restored.resource_definitions) == 1
        assert restored.resource_definitions[0].id == "schema"
        assert restored.resource_definitions[0].uri == "service:///petstore/schema"
        assert restored.resource_definitions[0].content == '{"info": "petstore schema"}'

        assert len(restored.prompt_definitions) == 1
        assert restored.prompt_definitions[0].id == "explore_petstore"
        expected_template = "List available operations for {service_name} and their risk levels"
        assert restored.prompt_definitions[0].template == expected_template
        assert len(restored.prompt_definitions[0].arguments) == 1

    def test_dict_round_trip_with_resources_and_prompts(self) -> None:
        ir = make_service_ir(
            resource_definitions=[make_resource()],
            prompt_definitions=[make_prompt()],
        )
        d = ir_to_dict(ir)
        assert "resource_definitions" in d
        assert "prompt_definitions" in d
        assert len(d["resource_definitions"]) == 1
        assert len(d["prompt_definitions"]) == 1

        restored = ir_from_dict(d)
        assert restored.resource_definitions[0].id == "schema"
        assert restored.prompt_definitions[0].id == "explore_petstore"

    def test_json_round_trip_without_resources_prompts_backward_compat(self) -> None:
        ir = make_service_ir()
        json_str = serialize_ir(ir)
        restored = deserialize_ir(json_str)
        assert restored.resource_definitions == []
        assert restored.prompt_definitions == []

    def test_complex_ir_round_trip_with_resources_and_prompts(self) -> None:
        """Full round-trip with multiple resources, prompts, and operations."""
        ir = make_service_ir(
            operations=[
                make_operation(id="list_pets"),
                make_operation(id="get_pet"),
                make_operation(id="create_pet", method="POST", risk=make_risk(RiskLevel.cautious)),
            ],
            resource_definitions=[
                make_resource(id="schema", uri="service:///petstore/schema"),
                make_resource(
                    id="operations",
                    name="Operations List",
                    uri="service:///petstore/operations",
                    content='["list_pets", "get_pet", "create_pet"]',
                ),
                make_resource(
                    id="auth",
                    name="Auth Requirements",
                    uri="service:///petstore/auth-requirements",
                    content='{"type": "none"}',
                    tags=["auth"],
                ),
            ],
            prompt_definitions=[
                make_prompt(
                    id="explore",
                    tool_ids=["list_pets", "get_pet", "create_pet"],
                ),
                make_prompt(
                    id="safe_discovery",
                    name="Safe Discovery",
                    template="Only use read-only tools: {safe_tools}",
                    arguments=[PromptArgument(name="safe_tools", required=True)],
                    tool_ids=["list_pets", "get_pet"],
                    tags=["safe"],
                ),
            ],
        )

        json_str = serialize_ir(ir)
        restored = deserialize_ir(json_str)

        assert len(restored.resource_definitions) == 3
        assert len(restored.prompt_definitions) == 2
        assert restored.resource_definitions[2].tags == ["auth"]
        assert restored.prompt_definitions[1].tags == ["safe"]
        assert restored.prompt_definitions[1].tool_ids == ["list_pets", "get_pet"]
