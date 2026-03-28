"""Tests for auto-generated MCP prompts (IRX-004)."""

from __future__ import annotations

from typing import Any

from libs.enhancer.prompt_generator import generate_prompts
from libs.ir.models import (
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    ToolIntent,
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


class TestGeneratePrompts:
    def test_minimal_service_generates_explore_and_safe_discovery(
        self,
    ) -> None:
        ir = _make_ir()
        prompts = generate_prompts(ir)
        ids = {p.id for p in prompts}
        assert "explore_petstore" in ids
        assert "safe_discovery_petstore" in ids

    def test_explore_prompt_references_all_enabled_ops(self) -> None:
        ir = _make_ir(
            operations=[
                _make_op("list_pets"),
                _make_op("get_pet"),
                _make_op("disabled_op", enabled=False),
            ],
        )
        prompts = generate_prompts(ir)
        explore = next(p for p in prompts if p.id == "explore_petstore")
        assert "list_pets" in explore.tool_ids
        assert "get_pet" in explore.tool_ids
        assert "disabled_op" not in explore.tool_ids

    def test_explore_prompt_has_service_name_argument(self) -> None:
        ir = _make_ir()
        prompts = generate_prompts(ir)
        explore = next(p for p in prompts if p.id == "explore_petstore")
        assert len(explore.arguments) == 1
        assert explore.arguments[0].name == "service_name"
        assert explore.arguments[0].default == "petstore"

    def test_explore_prompt_has_discovery_tag(self) -> None:
        ir = _make_ir()
        prompts = generate_prompts(ir)
        explore = next(p for p in prompts if p.id == "explore_petstore")
        assert "discovery" in explore.tags


class TestSafeDiscoveryPrompt:
    def test_safe_discovery_only_includes_safe_ops(self) -> None:
        ir = _make_ir(
            operations=[
                _make_op("list_pets", method="GET"),
                _make_op(
                    "delete_pet",
                    method="DELETE",
                    risk=_make_risk(RiskLevel.dangerous),
                ),
            ],
        )
        prompts = generate_prompts(ir)
        safe = next(p for p in prompts if p.id == "safe_discovery_petstore")
        assert "list_pets" in safe.tool_ids
        assert "delete_pet" not in safe.tool_ids

    def test_safe_discovery_includes_discovery_intent_ops(self) -> None:
        ir = _make_ir(
            operations=[
                _make_op(
                    "search",
                    method="POST",
                    risk=_make_risk(RiskLevel.cautious),
                    tool_intent=ToolIntent.discovery,
                ),
            ],
        )
        prompts = generate_prompts(ir)
        safe = next(p for p in prompts if p.id == "safe_discovery_petstore")
        assert "search" in safe.tool_ids

    def test_safe_discovery_has_safe_tag(self) -> None:
        ir = _make_ir()
        prompts = generate_prompts(ir)
        safe = next(p for p in prompts if p.id == "safe_discovery_petstore")
        assert "safe" in safe.tags
        assert "discovery" in safe.tags


class TestCrudPrompts:
    def test_crud_prompt_generated_for_entity_with_multiple_ops(
        self,
    ) -> None:
        ir = _make_ir(
            operations=[
                _make_op("list_pets", method="GET", path="/pets"),
                _make_op(
                    "create_pet", method="POST", path="/pets", risk=_make_risk(RiskLevel.cautious)
                ),
                _make_op("get_pet", method="GET", path="/pets/{id}"),
            ],
        )
        prompts = generate_prompts(ir)
        crud = [p for p in prompts if p.id.startswith("manage_")]
        assert len(crud) >= 1
        pets_prompt = next(
            (p for p in crud if p.id == "manage_pets"),
            None,
        )
        assert pets_prompt is not None
        assert "list_pets" in pets_prompt.tool_ids
        assert "create_pet" in pets_prompt.tool_ids

    def test_no_crud_prompt_for_single_op_entity(self) -> None:
        ir = _make_ir(
            operations=[
                _make_op("list_pets", method="GET", path="/pets"),
            ],
        )
        prompts = generate_prompts(ir)
        crud = [p for p in prompts if p.id.startswith("manage_")]
        assert len(crud) == 0

    def test_crud_prompt_has_entity_tag(self) -> None:
        ir = _make_ir(
            operations=[
                _make_op("list_pets", method="GET", path="/pets"),
                _make_op(
                    "create_pet", method="POST", path="/pets", risk=_make_risk(RiskLevel.cautious)
                ),
            ],
        )
        prompts = generate_prompts(ir)
        crud = [p for p in prompts if p.id.startswith("manage_")]
        assert len(crud) >= 1
        assert "crud" in crud[0].tags
        assert "pets" in crud[0].tags


class TestGeneratePromptsIntegration:
    def test_prompts_can_be_added_to_service_ir(self) -> None:
        """Generated prompts can be set on ServiceIR without error."""
        ir = _make_ir(
            operations=[
                _make_op("list_pets", method="GET", path="/pets"),
                _make_op("get_pet", method="GET", path="/pets/{id}"),
            ],
        )
        prompts = generate_prompts(ir)
        ir_with_prompts = ir.model_copy(
            update={"prompt_definitions": prompts},
        )
        assert len(ir_with_prompts.prompt_definitions) >= 2

    def test_prompt_tool_ids_are_valid_operation_refs(self) -> None:
        """All tool_ids in generated prompts reference real operations."""
        ir = _make_ir(
            operations=[
                _make_op("list_pets"),
                _make_op("get_pet"),
            ],
        )
        prompts = generate_prompts(ir)
        op_ids = {op.id for op in ir.operations}
        for prompt in prompts:
            for tid in prompt.tool_ids:
                assert tid in op_ids, f"Prompt {prompt.id} references unknown op {tid}"


def test_is_safe_operation_returns_false_for_non_operation():
    """Test _is_safe_operation returns False for non-Operation objects."""
    from libs.enhancer.prompt_generator import _is_safe_operation

    assert _is_safe_operation("not an operation") is False
    assert _is_safe_operation(None) is False
    assert _is_safe_operation(42) is False


def test_crud_prompts_still_creates_prompt_with_disabled_operations():
    """Test _crud_prompts still creates prompt even when some operations are disabled."""
    from libs.enhancer.prompt_generator import _crud_prompts

    ir = _make_ir(
        operations=[
            _make_op("list_pets", method="GET", path="/pets", enabled=True),
            _make_op("create_pet", method="POST", path="/pets", enabled=False),
            _make_op("get_pet", method="GET", path="/pets/{id}", enabled=True),
        ]
    )

    prompts = _crud_prompts(ir)
    # Should create a CRUD prompt for enabled operations
    assert len(prompts) == 1
    assert "list_pets" in prompts[0].tool_ids
    assert "get_pet" in prompts[0].tool_ids
    assert "create_pet" not in prompts[0].tool_ids


def test_crud_prompts_skips_operations_without_path():
    """Test _crud_prompts skips operations without path."""
    from libs.enhancer.prompt_generator import _crud_prompts

    ir = _make_ir(
        operations=[
            _make_op("list_pets", method="GET", path="/pets"),
            _make_op("create_pet", method="POST", path=None),  # No path
        ]
    )

    prompts = _crud_prompts(ir)
    assert len(prompts) == 0


def test_crud_prompts_skips_operations_without_method():
    """Test _crud_prompts skips operations without method."""
    from libs.enhancer.prompt_generator import _crud_prompts

    ir = _make_ir(
        operations=[
            _make_op("list_pets", method="GET", path="/pets"),
            _make_op("create_pet", method=None, path="/pets"),  # No method
        ]
    )

    prompts = _crud_prompts(ir)
    assert len(prompts) == 0


def test_crud_prompts_skips_non_crud_methods():
    """Test _crud_prompts skips operations with non-CRUD methods."""
    from libs.enhancer.prompt_generator import _crud_prompts

    ir = _make_ir(
        operations=[
            _make_op("list_pets", method="GET", path="/pets"),
            _make_op("trace_pets", method="TRACE", path="/pets"),  # Non-CRUD method
        ]
    )

    prompts = _crud_prompts(ir)
    assert len(prompts) == 0


def test_extract_entity_from_path_returns_none_for_empty_meaningful_segments():
    """Test _extract_entity_from_path returns None when no meaningful segments."""
    from libs.enhancer.prompt_generator import _extract_entity_from_path

    # Path with only skip-worthy segments
    result = _extract_entity_from_path("/api/v1/v2")
    assert result is None

    # Path with only parameter segments
    result = _extract_entity_from_path("/{id}/{name}")
    assert result is None

    # Empty path
    result = _extract_entity_from_path("")
    assert result is None
