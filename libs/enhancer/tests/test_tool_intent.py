"""Tests for Discovery vs Action tool intent derivation and description bifurcation."""

from __future__ import annotations

from libs.enhancer.tool_intent import (
    bifurcate_descriptions,
    derive_tool_intent,
    derive_tool_intents,
)
from libs.ir.models import (
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
    ToolIntent,
)


def _make_op(
    *,
    method: str = "GET",
    risk_level: RiskLevel = RiskLevel.safe,
    writes_state: bool | None = None,
    destructive: bool | None = None,
    external_side_effect: bool | None = None,
    tool_intent: ToolIntent | None = None,
    description: str = "Test operation.",
    op_id: str = "test_op",
) -> Operation:
    return Operation(
        id=op_id,
        name="Test Op",
        description=description,
        method=method,
        path="/test",
        risk=RiskMetadata(
            risk_level=risk_level,
            writes_state=writes_state,
            destructive=destructive,
            external_side_effect=external_side_effect,
            confidence=0.9,
        ),
        tool_intent=tool_intent,
        source=SourceType.extractor,
        confidence=0.9,
    )


def _make_ir(operations: list[Operation]) -> ServiceIR:
    return ServiceIR(
        source_hash="test_hash",
        protocol="rest",
        service_name="test-service",
        base_url="https://api.example.com",
        operations=operations,
    )


class TestDeriveToolIntent:
    def test_safe_get_is_discovery(self) -> None:
        op = _make_op(method="GET", risk_level=RiskLevel.safe)
        assert derive_tool_intent(op) == ToolIntent.discovery

    def test_safe_head_is_discovery(self) -> None:
        op = _make_op(method="HEAD", risk_level=RiskLevel.safe)
        assert derive_tool_intent(op) == ToolIntent.discovery

    def test_safe_options_is_discovery(self) -> None:
        op = _make_op(method="OPTIONS", risk_level=RiskLevel.safe)
        assert derive_tool_intent(op) == ToolIntent.discovery

    def test_post_is_action(self) -> None:
        op = _make_op(method="POST", risk_level=RiskLevel.cautious, writes_state=True)
        assert derive_tool_intent(op) == ToolIntent.action

    def test_delete_is_action(self) -> None:
        op = _make_op(method="DELETE", risk_level=RiskLevel.dangerous, destructive=True)
        assert derive_tool_intent(op) == ToolIntent.action

    def test_writes_state_forces_action(self) -> None:
        op = _make_op(method="GET", risk_level=RiskLevel.safe, writes_state=True)
        assert derive_tool_intent(op) == ToolIntent.action

    def test_destructive_forces_action(self) -> None:
        op = _make_op(method="GET", risk_level=RiskLevel.safe, destructive=True)
        assert derive_tool_intent(op) == ToolIntent.action

    def test_external_side_effect_forces_action(self) -> None:
        op = _make_op(method="GET", risk_level=RiskLevel.safe, external_side_effect=True)
        assert derive_tool_intent(op) == ToolIntent.action

    def test_cautious_risk_forces_action(self) -> None:
        op = _make_op(method="GET", risk_level=RiskLevel.cautious)
        assert derive_tool_intent(op) == ToolIntent.action

    def test_no_method_defaults_to_action(self) -> None:
        op = Operation(
            id="no_method", name="No Method", description="test",
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9),
            source=SourceType.extractor, confidence=0.9,
        )
        assert derive_tool_intent(op) == ToolIntent.action


class TestDeriveToolIntents:
    def test_sets_intents_on_all_operations(self) -> None:
        ops = [
            _make_op(op_id="get_users", method="GET", risk_level=RiskLevel.safe),
            _make_op(
                op_id="create_user", method="POST",
                risk_level=RiskLevel.cautious, writes_state=True,
            ),
        ]
        ir = _make_ir(ops)
        updated = derive_tool_intents(ir)

        assert updated.operations[0].tool_intent == ToolIntent.discovery
        assert updated.operations[1].tool_intent == ToolIntent.action

    def test_preserves_existing_intent(self) -> None:
        op = _make_op(
            op_id="forced_discovery",
            method="POST",
            risk_level=RiskLevel.cautious,
            writes_state=True,
            tool_intent=ToolIntent.discovery,
        )
        ir = _make_ir([op])
        updated = derive_tool_intents(ir)

        # Should preserve the explicit override
        assert updated.operations[0].tool_intent == ToolIntent.discovery

    def test_returns_same_ir_if_all_set(self) -> None:
        op = _make_op(tool_intent=ToolIntent.discovery)
        ir = _make_ir([op])
        updated = derive_tool_intents(ir)
        assert updated is ir


class TestBifurcateDescriptions:
    def test_adds_discovery_prefix(self) -> None:
        op = _make_op(tool_intent=ToolIntent.discovery, description="List all users.")
        ir = _make_ir([op])
        updated = bifurcate_descriptions(ir)
        assert updated.operations[0].description == "[DISCOVERY] List all users."

    def test_adds_action_prefix(self) -> None:
        op = _make_op(
            op_id="create_user",
            tool_intent=ToolIntent.action,
            description="Create a new user.",
            method="POST",
            risk_level=RiskLevel.cautious,
            writes_state=True,
        )
        ir = _make_ir([op])
        updated = bifurcate_descriptions(ir)
        assert updated.operations[0].description == "[ACTION] Create a new user."

    def test_skips_already_prefixed(self) -> None:
        op = _make_op(
            tool_intent=ToolIntent.discovery,
            description="[DISCOVERY] Already prefixed.",
        )
        ir = _make_ir([op])
        updated = bifurcate_descriptions(ir)
        assert updated.operations[0].description == "[DISCOVERY] Already prefixed."
        # Should return same IR since no changes
        assert updated is ir

    def test_skips_operations_without_intent(self) -> None:
        op = _make_op(tool_intent=None, description="No intent set.")
        ir = _make_ir([op])
        updated = bifurcate_descriptions(ir)
        assert updated.operations[0].description == "No intent set."
        assert updated is ir

    def test_replaces_wrong_prefix(self) -> None:
        op = _make_op(
            op_id="reclassified",
            tool_intent=ToolIntent.discovery,
            description="[ACTION] Was action, now discovery.",
            method="GET",
            risk_level=RiskLevel.safe,
        )
        ir = _make_ir([op])
        updated = bifurcate_descriptions(ir)
        assert updated.operations[0].description == "[DISCOVERY] Was action, now discovery."

    def test_full_pipeline_derive_then_bifurcate(self) -> None:
        ops = [
            _make_op(
                op_id="list_items", method="GET", risk_level=RiskLevel.safe,
                description="List all items.",
            ),
            _make_op(
                op_id="delete_item", method="DELETE", risk_level=RiskLevel.dangerous,
                destructive=True, description="Delete an item.",
            ),
        ]
        ir = _make_ir(ops)
        ir = derive_tool_intents(ir)
        ir = bifurcate_descriptions(ir)

        assert ir.operations[0].description == "[DISCOVERY] List all items."
        assert ir.operations[1].description == "[ACTION] Delete an item."
