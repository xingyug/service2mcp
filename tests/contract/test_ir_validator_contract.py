"""Contract: the validator MUST accept any well-formed ServiceIR and return a consistent report.

Tests that PreDeployValidator.validate() produces a ValidationReport with
expected stages for various IR shapes: empty ops, many ops, all protocols,
mixed source fields, and dict payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from libs.ir.models import (
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.validator.pre_deploy import PreDeployValidator, ValidationReport

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

_VALID_IR_PATH = FIXTURES / "ir" / "service_ir_valid.json"

# ── helpers ────────────────────────────────────────────────────────────────


def _minimal_ir(
    *,
    protocol: str = "openapi",
    ops: list[Operation] | None = None,
    service_name: str = "test-svc",
) -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol=protocol,
        service_name=service_name,
        base_url="https://example.com",
        operations=ops or [],
    )


def _safe_operation(op_id: str, *, source: SourceType = SourceType.extractor) -> Operation:
    return Operation(
        id=op_id,
        name=op_id.replace("_", " ").title(),
        method="GET",
        path=f"/{op_id}",
        risk=RiskMetadata(
            writes_state=False,
            destructive=False,
            external_side_effect=False,
            idempotent=True,
            risk_level=RiskLevel.safe,
            confidence=1.0,
            source=source,
        ),
        source=source,
        confidence=1.0,
    )


def _load_valid_ir() -> ServiceIR:
    return ServiceIR.model_validate(json.loads(_VALID_IR_PATH.read_text()))


_EXPECTED_STAGES = {"schema", "event_support", "auth_smoke"}

# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.contract
class TestValidatorAcceptsWellFormedIR:
    """PreDeployValidator MUST accept any well-formed ServiceIR."""

    async def test_fixture_ir_passes_validation(self) -> None:
        ir = _load_valid_ir()
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.overall_passed
        assert report.get_result("schema").passed

    async def test_empty_operations_ir_passes_schema(self) -> None:
        ir = _minimal_ir(ops=[])
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed

    async def test_many_operations_ir_passes_schema(self) -> None:
        ops = [_safe_operation(f"op_{i}") for i in range(50)]
        ir = _minimal_ir(ops=ops)
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed

    @pytest.mark.parametrize(
        "protocol",
        ["openapi", "graphql", "grpc", "soap", "sql", "jsonrpc", "odata", "scim", "rest"],
    )
    async def test_all_protocol_values_pass_schema(self, protocol: str) -> None:
        ir = _minimal_ir(protocol=protocol)
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed

    async def test_report_contains_expected_stages(self) -> None:
        ir = _load_valid_ir()
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        actual_stages = {r.stage for r in report.results}
        assert _EXPECTED_STAGES.issubset(actual_stages)

    async def test_report_is_valid_pydantic_model(self) -> None:
        ir = _load_valid_ir()
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert isinstance(report, ValidationReport)
        dumped = report.model_dump(mode="json")
        restored = ValidationReport.model_validate(dumped)
        assert restored.overall_passed == report.overall_passed

    async def test_dict_payload_accepted(self) -> None:
        ir_dict: dict[str, Any] = json.loads(_VALID_IR_PATH.read_text())
        async with PreDeployValidator() as v:
            report = await v.validate(ir_dict)
        assert report.get_result("schema").passed

    async def test_invalid_dict_fails_schema_stage(self) -> None:
        async with PreDeployValidator() as v:
            report = await v.validate({"bad": "data"})
        schema_result = report.get_result("schema")
        assert not schema_result.passed
        assert not report.overall_passed

    async def test_mixed_source_operations_pass_schema(self) -> None:
        ops = [
            _safe_operation("ext_op", source=SourceType.extractor),
            _safe_operation("llm_op", source=SourceType.llm),
            _safe_operation("user_op", source=SourceType.user_override),
        ]
        ir = _minimal_ir(ops=ops)
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        assert report.get_result("schema").passed

    async def test_validation_result_has_duration(self) -> None:
        ir = _load_valid_ir()
        async with PreDeployValidator() as v:
            report = await v.validate(ir)
        for result in report.results:
            assert isinstance(result.duration_ms, int)
            assert result.duration_ms >= 0
