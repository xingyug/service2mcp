"""Tests for IR transformation pipeline."""

from __future__ import annotations

from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.ir.transform import TransformAction, TransformRule, apply_transforms


def _make_ir(ops: list[tuple[str, list[str]]]) -> ServiceIR:
    """Create a test IR from (id, tags) tuples."""
    operations = []
    for op_id, tags in ops:
        operations.append(
            Operation(
                id=op_id,
                name=op_id,
                description=f"Test op {op_id}",
                method="GET",
                path=f"/{op_id}",
                params=[],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                tags=tags,
                enabled=True,
            )
        )
    return ServiceIR(
        source_hash="a" * 64,
        protocol="openapi",
        service_name="test-service",
        service_description="Test",
        base_url="https://api.test",
        auth=AuthConfig(type=AuthType.none),
        operations=operations,
    )


class TestApplyTransforms:
    """Tests for apply_transforms."""

    def test_empty_rules_returns_unchanged(self) -> None:
        ir = _make_ir([("op1", ["read"])])
        result = apply_transforms(ir, [])
        assert result.service_name == ir.service_name
        assert len(result.operations) == 1
        assert result.operations[0].id == "op1"

    def test_rename_service(self) -> None:
        ir = _make_ir([("op1", [])])
        rules = [TransformRule(action=TransformAction.rename_service, value="new-name")]
        result = apply_transforms(ir, rules)
        assert result.service_name == "new-name"

    def test_set_metadata(self) -> None:
        ir = _make_ir([("op1", [])])
        rules = [
            TransformRule(
                action=TransformAction.set_metadata,
                value={"owner": "team-a", "version": "2"},
            )
        ]
        result = apply_transforms(ir, rules)
        assert result.metadata["owner"] == "team-a"
        assert result.metadata["version"] == "2"

    def test_set_metadata_merges_existing(self) -> None:
        ir = _make_ir([("op1", [])])
        ir = ir.model_copy(update={"metadata": {"existing": "keep"}})
        rules = [TransformRule(action=TransformAction.set_metadata, value={"new": "val"})]
        result = apply_transforms(ir, rules)
        assert result.metadata["existing"] == "keep"
        assert result.metadata["new"] == "val"

    def test_set_metadata_ignores_non_dict(self) -> None:
        ir = _make_ir([("op1", [])])
        rules = [TransformRule(action=TransformAction.set_metadata, value="bad")]
        result = apply_transforms(ir, rules)
        assert result.metadata == ir.metadata

    def test_rename_operation(self) -> None:
        ir = _make_ir([("op1", []), ("op2", [])])
        rules = [
            TransformRule(action=TransformAction.rename_operation, target="op1", value="renamed")
        ]
        result = apply_transforms(ir, rules)
        assert result.operations[0].id == "renamed"
        assert result.operations[0].name == "renamed"
        assert result.operations[1].id == "op2"

    def test_rename_operation_with_dict(self) -> None:
        ir = _make_ir([("op1", [])])
        rules = [
            TransformRule(
                action=TransformAction.rename_operation,
                target="op1",
                value={"id": "new-id", "name": "New Name"},
            )
        ]
        result = apply_transforms(ir, rules)
        assert result.operations[0].id == "new-id"
        assert result.operations[0].name == "New Name"

    def test_add_tag(self) -> None:
        ir = _make_ir([("op1", ["read"]), ("op2", [])])
        rules = [TransformRule(action=TransformAction.add_tag, target="op1", value="important")]
        result = apply_transforms(ir, rules)
        assert "important" in result.operations[0].tags
        assert "read" in result.operations[0].tags
        assert "important" not in result.operations[1].tags

    def test_add_tag_no_duplicate(self) -> None:
        ir = _make_ir([("op1", ["read"])])
        rules = [TransformRule(action=TransformAction.add_tag, target="op1", value="read")]
        result = apply_transforms(ir, rules)
        assert result.operations[0].tags.count("read") == 1

    def test_remove_tag(self) -> None:
        ir = _make_ir([("op1", ["read", "write"])])
        rules = [TransformRule(action=TransformAction.remove_tag, target="op1", value="write")]
        result = apply_transforms(ir, rules)
        assert "write" not in result.operations[0].tags
        assert "read" in result.operations[0].tags

    def test_override_risk(self) -> None:
        ir = _make_ir([("op1", [])])
        rules = [
            TransformRule(
                action=TransformAction.override_risk,
                target="op1",
                value="dangerous",
            )
        ]
        result = apply_transforms(ir, rules)
        assert result.operations[0].risk.risk_level == RiskLevel.dangerous

    def test_disable_operation(self) -> None:
        ir = _make_ir([("op1", []), ("op2", [])])
        rules = [TransformRule(action=TransformAction.disable_operation, target="op1")]
        result = apply_transforms(ir, rules)
        assert result.operations[0].enabled is False
        assert result.operations[1].enabled is True

    def test_enable_operation(self) -> None:
        ir = _make_ir([("op1", [])])
        # Disable first, then re-enable
        rules = [
            TransformRule(action=TransformAction.disable_operation, target="op1"),
            TransformRule(action=TransformAction.enable_operation, target="op1"),
        ]
        result = apply_transforms(ir, rules)
        assert result.operations[0].enabled is True

    def test_filter_by_tag(self) -> None:
        ir = _make_ir([("op1", ["public"]), ("op2", ["internal"]), ("op3", ["public"])])
        rules = [TransformRule(action=TransformAction.filter_by_tag, value="public")]
        result = apply_transforms(ir, rules)
        assert len(result.operations) == 2
        assert {op.id for op in result.operations} == {"op1", "op3"}

    def test_exclude_by_tag(self) -> None:
        ir = _make_ir([("op1", ["public"]), ("op2", ["internal"]), ("op3", ["public"])])
        rules = [TransformRule(action=TransformAction.exclude_by_tag, value="internal")]
        result = apply_transforms(ir, rules)
        assert len(result.operations) == 2
        assert all(op.id != "op2" for op in result.operations)

    def test_wildcard_matching(self) -> None:
        ir = _make_ir([("list-items", []), ("list-users", []), ("get-item", [])])
        rules = [TransformRule(action=TransformAction.add_tag, target="list-*", value="listing")]
        result = apply_transforms(ir, rules)
        assert "listing" in result.operations[0].tags
        assert "listing" in result.operations[1].tags
        assert "listing" not in result.operations[2].tags

    def test_multiple_rules_applied_in_order(self) -> None:
        ir = _make_ir([("op1", ["alpha"]), ("op2", ["beta"])])
        rules = [
            TransformRule(action=TransformAction.add_tag, target="op1", value="beta"),
            TransformRule(action=TransformAction.filter_by_tag, value="beta"),
            TransformRule(action=TransformAction.rename_service, value="filtered-service"),
        ]
        result = apply_transforms(ir, rules)
        assert result.service_name == "filtered-service"
        assert len(result.operations) == 2
        assert {op.id for op in result.operations} == {"op1", "op2"}

    def test_non_matching_target_leaves_ops_unchanged(self) -> None:
        ir = _make_ir([("op1", ["read"]), ("op2", ["write"])])
        rules = [TransformRule(action=TransformAction.add_tag, target="nonexistent", value="new")]
        result = apply_transforms(ir, rules)
        assert result.operations[0].tags == ["read"]
        assert result.operations[1].tags == ["write"]
