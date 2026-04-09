"""E2E: IR transformation pipeline — apply operator-defined rewrites to a ServiceIR.

Tests the full apply_transforms() pipeline covering:
- rename_operation, filter_by_tag, exclude_by_tag, add_tag, remove_tag
- override_risk, disable_operation, enable_operation, set_metadata, rename_service
- Wildcard pattern matching for operation targets
- Transformed IR still passes schema validation
- ir_tool.py CLI entry point via subprocess
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from libs.ir.models import (
    AuthConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)
from libs.ir.transform import TransformAction, TransformRule, apply_transforms
from libs.validator.pre_deploy import PreDeployValidator

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_operation(
    op_id: str,
    *,
    risk_level: RiskLevel = RiskLevel.safe,
    method: str = "GET",
    path: str | None = None,
    tags: list[str] | None = None,
    enabled: bool | None = None,
) -> Operation:
    op = Operation(
        id=op_id,
        name=op_id,
        description=f"Operation {op_id}",
        method=method,
        path=path or f"/{op_id}",
        risk=RiskMetadata(risk_level=risk_level, confidence=0.9),
        tags=tags or [],
        params=[Param(name="q", type="string", required=False)],
    )
    # Override enabled after model validation (unknown risk auto-disables)
    if enabled is not None and op.enabled != enabled:
        op = op.model_copy(update={"enabled": enabled})
    return op


def _make_ir(operations: list[Operation]) -> ServiceIR:
    return ServiceIR(
        source_url="https://example.com/api",
        source_hash="abc123",
        protocol="openapi",
        service_name="test-svc",
        service_description="Test service",
        base_url="https://api.example.com",
        auth=AuthConfig(),
        operations=operations,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRenameOperation:
    """rename_operation changes both id and name."""

    async def test_rename_single(self) -> None:
        ir = _make_ir([_make_operation("old_id")])
        rules = [
            TransformRule(
                action=TransformAction.rename_operation,
                target="old_id",
                value={"id": "new_id", "name": "new_name"},
            )
        ]
        result = apply_transforms(ir, rules)

        assert result.operations[0].id == "new_id"
        assert result.operations[0].name == "new_name"

    async def test_rename_with_wildcard(self) -> None:
        ir = _make_ir(
            [
                _make_operation("user_list"),
                _make_operation("user_get"),
                _make_operation("order_get"),
            ]
        )
        rules = [
            TransformRule(
                action=TransformAction.rename_operation,
                target="user_*",
                value="prefixed",
            )
        ]
        result = apply_transforms(ir, rules)

        # Wildcard matches both user_ ops; rename sets id=name="prefixed"
        # but since IDs must be unique, only the behaviour of the transform is tested
        renamed_names = [op.name for op in result.operations if op.name == "prefixed"]
        assert len(renamed_names) == 2
        # order_get should be untouched
        assert any(op.id == "order_get" for op in result.operations)


class TestTagTransforms:
    """filter_by_tag, exclude_by_tag, add_tag, remove_tag."""

    async def test_filter_by_tag(self) -> None:
        ops = [
            _make_operation("op_a", tags=["public", "v1"]),
            _make_operation("op_b", tags=["internal"]),
            _make_operation("op_c", tags=["public"]),
        ]
        ir = _make_ir(ops)
        rules = [TransformRule(action=TransformAction.filter_by_tag, value="public")]
        result = apply_transforms(ir, rules)

        assert len(result.operations) == 2
        assert all("public" in op.tags for op in result.operations)

    async def test_exclude_by_tag(self) -> None:
        ops = [
            _make_operation("op_a", tags=["public"]),
            _make_operation("op_b", tags=["deprecated"]),
        ]
        ir = _make_ir(ops)
        rules = [TransformRule(action=TransformAction.exclude_by_tag, value="deprecated")]
        result = apply_transforms(ir, rules)

        assert len(result.operations) == 1
        assert result.operations[0].id == "op_a"

    async def test_add_tag(self) -> None:
        ir = _make_ir([_make_operation("op")])
        rules = [TransformRule(action=TransformAction.add_tag, target="op", value="new_tag")]
        result = apply_transforms(ir, rules)
        assert "new_tag" in result.operations[0].tags

    async def test_remove_tag(self) -> None:
        ir = _make_ir([_make_operation("op", tags=["keep", "remove_me"])])
        rules = [TransformRule(action=TransformAction.remove_tag, target="op", value="remove_me")]
        result = apply_transforms(ir, rules)
        assert "remove_me" not in result.operations[0].tags
        assert "keep" in result.operations[0].tags

    async def test_add_tag_idempotent(self) -> None:
        ir = _make_ir([_make_operation("op", tags=["existing"])])
        rules = [TransformRule(action=TransformAction.add_tag, target="op", value="existing")]
        result = apply_transforms(ir, rules)
        assert result.operations[0].tags.count("existing") == 1


class TestRiskOverride:
    """override_risk changes the risk level on matched operations."""

    async def test_override_risk(self) -> None:
        ir = _make_ir([_make_operation("op", risk_level=RiskLevel.safe)])
        rules = [
            TransformRule(
                action=TransformAction.override_risk,
                target="op",
                value="dangerous",
            )
        ]
        result = apply_transforms(ir, rules)
        assert result.operations[0].risk.risk_level == RiskLevel.dangerous


class TestEnableDisable:
    """disable_operation and enable_operation."""

    async def test_disable_and_reenable(self) -> None:
        ir = _make_ir([_make_operation("op", risk_level=RiskLevel.safe)])
        assert ir.operations[0].enabled is True

        disabled = apply_transforms(
            ir, [TransformRule(action=TransformAction.disable_operation, target="op")]
        )
        assert disabled.operations[0].enabled is False

        reenabled = apply_transforms(
            disabled, [TransformRule(action=TransformAction.enable_operation, target="op")]
        )
        assert reenabled.operations[0].enabled is True


class TestSetMetadata:
    """set_metadata merges new keys into the IR metadata."""

    async def test_set_metadata(self) -> None:
        ir = _make_ir([_make_operation("op")])
        rules = [
            TransformRule(
                action=TransformAction.set_metadata,
                value={"env": "staging", "team": "platform"},
            )
        ]
        result = apply_transforms(ir, rules)
        assert result.metadata["env"] == "staging"
        assert result.metadata["team"] == "platform"

    async def test_set_metadata_merges(self) -> None:
        ir = _make_ir([_make_operation("op")])
        ir = ir.model_copy(update={"metadata": {"existing": True}})
        rules = [TransformRule(action=TransformAction.set_metadata, value={"new_key": 42})]
        result = apply_transforms(ir, rules)
        assert result.metadata["existing"] is True
        assert result.metadata["new_key"] == 42


class TestRenameService:
    """rename_service changes the IR service_name."""

    async def test_rename_service(self) -> None:
        ir = _make_ir([_make_operation("op")])
        rules = [TransformRule(action=TransformAction.rename_service, value="renamed-svc")]
        result = apply_transforms(ir, rules)
        assert result.service_name == "renamed-svc"


class TestChainedTransforms:
    """Apply a sequence of transforms end-to-end."""

    async def test_chain_multiple_rules(self) -> None:
        ops = [
            _make_operation("get_users", tags=["public"]),
            _make_operation("delete_user", risk_level=RiskLevel.safe, tags=["admin"]),
            _make_operation("list_orders", tags=["public"]),
        ]
        ir = _make_ir(ops)

        rules = [
            TransformRule(action=TransformAction.add_tag, target="*", value="v2"),
            TransformRule(
                action=TransformAction.override_risk,
                target="delete_user",
                value="dangerous",
            ),
            TransformRule(action=TransformAction.exclude_by_tag, value="admin"),
            TransformRule(action=TransformAction.rename_service, value="orders-api"),
            TransformRule(
                action=TransformAction.set_metadata,
                value={"version": "2.0"},
            ),
        ]
        result = apply_transforms(ir, rules)

        assert result.service_name == "orders-api"
        assert len(result.operations) == 2
        assert all("v2" in op.tags for op in result.operations)
        assert result.metadata["version"] == "2.0"


class TestTransformedIRValidates:
    """Transformed IR must still pass schema validation."""

    async def test_transformed_ir_validates(self) -> None:
        ir = _make_ir(
            [_make_operation("op1", tags=["keep"]), _make_operation("op2", tags=["drop"])]
        )
        rules = [TransformRule(action=TransformAction.exclude_by_tag, value="drop")]
        result = apply_transforms(ir, rules)

        async with PreDeployValidator() as validator:
            report = await validator.validate(result)

        assert report.get_result("schema").passed


class TestIRToolCLI:
    """Test the scripts/ir_tool.py CLI entry point via subprocess."""

    async def test_cli_transform(self) -> None:
        ir = _make_ir(
            [_make_operation("op_a", tags=["public"]), _make_operation("op_b", tags=["internal"])]
        )

        ir_path = _PROJECT_ROOT / "tests" / "e2e" / "_test_ir_input.json"
        rules_path = _PROJECT_ROOT / "tests" / "e2e" / "_test_rules.json"
        out_path = _PROJECT_ROOT / "tests" / "e2e" / "_test_ir_output.json"

        try:
            ir_path.write_text(ir.model_dump_json(indent=2))
            rules_data = [{"action": "filter_by_tag", "value": "public"}]
            rules_path.write_text(json.dumps(rules_data))

            result = subprocess.run(
                [
                    sys.executable,
                    str(_PROJECT_ROOT / "scripts" / "ir_tool.py"),
                    "transform",
                    str(ir_path),
                    "-r",
                    str(rules_path),
                    "-o",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                cwd=str(_PROJECT_ROOT),
                timeout=30,
            )
            assert result.returncode == 0, f"ir_tool.py failed: {result.stderr}"

            output_ir = ServiceIR.model_validate_json(out_path.read_text())
            assert len(output_ir.operations) == 1
            assert output_ir.operations[0].id == "op_a"
        finally:
            for p in (ir_path, rules_path, out_path):
                p.unlink(missing_ok=True)
