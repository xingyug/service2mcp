"""Tests for capability manifest generation (IRX-006)."""

from __future__ import annotations

from typing import Any

from libs.generator.generic_mode import build_capability_manifest
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


def _make_param(**overrides: Any) -> Param:
    defaults: dict[str, Any] = {
        "name": "id",
        "type": "integer",
        "required": True,
    }
    return Param(**(defaults | overrides))


def _make_risk(level: RiskLevel = RiskLevel.safe) -> RiskMetadata:
    return RiskMetadata(
        writes_state=level != RiskLevel.safe,
        destructive=level == RiskLevel.dangerous,
        risk_level=level,
        confidence=0.9,
    )


def _make_op(id: str = "list_pets", **overrides: Any) -> Operation:
    defaults: dict[str, Any] = {
        "id": id,
        "name": f"Op {id}",
        "description": f"Desc {id}",
        "method": "GET",
        "path": f"/{id}",
        "params": [_make_param()],
        "risk": _make_risk(),
        "enabled": True,
    }
    return Operation(**(defaults | overrides))


def _make_ir(**overrides: Any) -> ServiceIR:
    defaults: dict[str, Any] = {
        "source_hash": "abc123",
        "protocol": "openapi",
        "service_name": "petstore",
        "base_url": "https://petstore.example.com/v1",
        "operations": [_make_op()],
    }
    return ServiceIR(**(defaults | overrides))


class TestBuildCapabilityManifest:
    def test_empty_ir_has_three_sections(self) -> None:
        ir = _make_ir(operations=[])
        manifest = build_capability_manifest(ir)
        assert "tools" in manifest
        assert "resources" in manifest
        assert "prompts" in manifest

    def test_tools_from_enabled_operations(self) -> None:
        ir = _make_ir(
            operations=[
                _make_op("list_pets"),
                _make_op("get_pet"),
                _make_op("disabled", enabled=False),
            ],
        )
        manifest = build_capability_manifest(ir)
        assert len(manifest["tools"]) == 2
        tool_ids = {t["id"] for t in manifest["tools"]}
        assert tool_ids == {"list_pets", "get_pet"}

    def test_tool_structure(self) -> None:
        ir = _make_ir()
        manifest = build_capability_manifest(ir)
        tool = manifest["tools"][0]
        assert tool["id"] == "list_pets"
        assert tool["name"] == "Op list_pets"
        assert "description" in tool
        assert "method" in tool
        assert "path" in tool

    def test_resources_included(self) -> None:
        ir = _make_ir(
            resource_definitions=[
                ResourceDefinition(
                    id="schema",
                    name="Schema",
                    description="API schema",
                    uri="service:///petstore/schema",
                    mime_type="application/json",
                ),
            ],
        )
        manifest = build_capability_manifest(ir)
        assert len(manifest["resources"]) == 1
        r = manifest["resources"][0]
        assert r["uri"] == "service:///petstore/schema"
        assert r["name"] == "Schema"
        assert r["mime_type"] == "application/json"

    def test_prompts_included(self) -> None:
        ir = _make_ir(
            prompt_definitions=[
                PromptDefinition(
                    id="explore",
                    name="Explore",
                    description="Explore the service",
                    template="Explore {service}",
                    arguments=[
                        PromptArgument(
                            name="service",
                            description="Service name",
                            required=True,
                        ),
                    ],
                    tool_ids=["list_pets"],
                ),
            ],
        )
        manifest = build_capability_manifest(ir)
        assert len(manifest["prompts"]) == 1
        p = manifest["prompts"][0]
        assert p["name"] == "Explore"
        assert len(p["arguments"]) == 1
        assert p["arguments"][0]["name"] == "service"
        assert p["arguments"][0]["required"] is True

    def test_no_resources_or_prompts_returns_empty_lists(self) -> None:
        ir = _make_ir()
        manifest = build_capability_manifest(ir)
        assert manifest["resources"] == []
        assert manifest["prompts"] == []

    def test_full_manifest_with_all_sections(self) -> None:
        ir = _make_ir(
            operations=[_make_op("op1"), _make_op("op2")],
            resource_definitions=[
                ResourceDefinition(
                    id="r1",
                    name="R1",
                    uri="service:///test/r1",
                ),
                ResourceDefinition(
                    id="r2",
                    name="R2",
                    uri="service:///test/r2",
                    mime_type="text/plain",
                ),
            ],
            prompt_definitions=[
                PromptDefinition(
                    id="p1",
                    name="P1",
                    template="Do {thing}",
                    tool_ids=["op1"],
                ),
            ],
        )
        manifest = build_capability_manifest(ir)
        assert len(manifest["tools"]) == 2
        assert len(manifest["resources"]) == 2
        assert len(manifest["prompts"]) == 1
