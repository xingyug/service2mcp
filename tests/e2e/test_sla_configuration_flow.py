"""E2E: SLA configuration and validation pipeline.

Tests the full SLA lifecycle covering:
- ServiceIR creation with SlaConfig (latency_budget_ms, timeout_ms, retry)
- SLA preserved through schema validation
- SLA preserved through IR composition
- SLA drift detection when configs change
- RetryConfig validation (max_retries bounds, backoff_multiplier > 1.0)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from libs.ir.compose import compose_irs
from libs.ir.models import (
    AuthConfig,
    Operation,
    Param,
    RetryConfig,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SlaConfig,
)
from libs.validator.drift import DriftSeverity, detect_drift
from libs.validator.pre_deploy import PreDeployValidator

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _op(
    op_id: str,
    *,
    sla: SlaConfig | None = None,
    risk_level: RiskLevel = RiskLevel.safe,
) -> Operation:
    return Operation(
        id=op_id,
        name=op_id,
        description=f"Op {op_id}",
        method="GET",
        path=f"/{op_id}",
        risk=RiskMetadata(risk_level=risk_level, confidence=0.9),
        params=[Param(name="q", type="string")],
        sla=sla,
    )


def _ir(
    name: str,
    operations: list[Operation],
    *,
    protocol: str = "openapi",
) -> ServiceIR:
    return ServiceIR(
        source_url=f"https://example.com/{name}",
        source_hash="abc123",
        protocol=protocol,
        service_name=name,
        base_url=f"https://api.{name}.example.com",
        auth=AuthConfig(),
        operations=operations,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSlaCreation:
    """SlaConfig and RetryConfig can be set on operations."""

    async def test_sla_on_operation(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=200,
            timeout_ms=1000,
            retry=RetryConfig(max_retries=3, backoff_multiplier=2.0),
        )
        op = _op("fast_op", sla=sla)
        assert op.sla is not None
        assert op.sla.latency_budget_ms == 200
        assert op.sla.timeout_ms == 1000
        assert op.sla.retry.max_retries == 3
        assert op.sla.retry.backoff_multiplier == 2.0

    async def test_sla_defaults(self) -> None:
        sla = SlaConfig()
        assert sla.latency_budget_ms is None
        assert sla.timeout_ms is None
        assert sla.retry.max_retries == 0
        assert sla.retry.backoff_base_ms == 100
        assert sla.retry.backoff_multiplier == 2.0

    async def test_operation_without_sla(self) -> None:
        op = _op("no_sla")
        assert op.sla is None


class TestRetryConfigValidation:
    """RetryConfig boundary validation."""

    async def test_max_retries_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            RetryConfig(max_retries=11)

    async def test_max_retries_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            RetryConfig(max_retries=-1)

    async def test_backoff_multiplier_must_exceed_one(self) -> None:
        with pytest.raises(ValidationError):
            RetryConfig(backoff_multiplier=1.0)

    async def test_backoff_multiplier_below_one_fails(self) -> None:
        with pytest.raises(ValidationError):
            RetryConfig(backoff_multiplier=0.5)

    async def test_valid_retry_config(self) -> None:
        cfg = RetryConfig(
            max_retries=5,
            backoff_base_ms=200,
            backoff_multiplier=1.5,
            retryable_errors=["ECONNRESET", "ETIMEOUT"],
        )
        assert cfg.max_retries == 5
        assert cfg.backoff_base_ms == 200
        assert len(cfg.retryable_errors) == 2


class TestSlaPreservedThroughValidation:
    """SLA config survives PreDeployValidator schema check."""

    async def test_sla_survives_validation(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=150,
            timeout_ms=3000,
            retry=RetryConfig(max_retries=2, backoff_multiplier=1.5),
        )
        ir = _ir("sla-svc", [_op("op1", sla=sla)])

        async with PreDeployValidator() as validator:
            report = await validator.validate(ir)

        assert report.get_result("schema").passed
        # The IR round-trips through validation intact — the SLA is on the model
        assert ir.operations[0].sla is not None
        assert ir.operations[0].sla.latency_budget_ms == 150


class TestSlaPreservedThroughComposition:
    """SLA configs from different source IRs are preserved after compose."""

    async def test_composed_ir_preserves_sla(self) -> None:
        sla_fast = SlaConfig(latency_budget_ms=50, timeout_ms=200)
        sla_slow = SlaConfig(latency_budget_ms=5000, timeout_ms=30000)

        ir1 = _ir("fast-svc", [_op("fast_op", sla=sla_fast)])
        ir2 = _ir("slow-svc", [_op("slow_op", sla=sla_slow)])

        merged = compose_irs([ir1, ir2])

        sla_by_op = {op.id: op.sla for op in merged.operations}
        assert sla_by_op["fast-svc_fast_op"] is not None
        assert sla_by_op["fast-svc_fast_op"].latency_budget_ms == 50
        assert sla_by_op["slow-svc_slow_op"] is not None
        assert sla_by_op["slow-svc_slow_op"].latency_budget_ms == 5000

    async def test_composed_ir_with_mixed_sla_and_no_sla(self) -> None:
        sla = SlaConfig(latency_budget_ms=100)
        ir1 = _ir("sla-svc", [_op("with_sla", sla=sla)])
        ir2 = _ir("nosla-svc", [_op("without_sla")])

        merged = compose_irs([ir1, ir2])

        sla_map = {op.id: op.sla for op in merged.operations}
        assert sla_map["sla-svc_with_sla"] is not None
        assert sla_map["nosla-svc_without_sla"] is None


class TestSlaDrift:
    """Drift detection for SLA config changes."""

    async def test_sla_latency_relaxed_drift(self) -> None:
        deployed = _ir(
            "svc",
            [_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=500))],
        )
        live = _ir(
            "svc",
            [_op("op1", sla=SlaConfig(latency_budget_ms=500, timeout_ms=500))],
        )
        report = detect_drift(deployed, live)
        assert report.has_drift
        assert report.severity == DriftSeverity.breaking

    async def test_sla_timeout_tightened_drift_non_breaking(self) -> None:
        deployed = _ir(
            "svc",
            [_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=1000))],
        )
        live = _ir(
            "svc",
            [_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=500))],
        )
        report = detect_drift(deployed, live)
        assert report.has_drift
        mod = report.modified_operations[0]
        assert any("sla timeout tightened" in c for c in mod.changes)
        assert report.severity == DriftSeverity.non_breaking

    async def test_sla_retry_drift(self) -> None:
        deployed = _ir(
            "svc",
            [
                _op(
                    "op1",
                    sla=SlaConfig(
                        latency_budget_ms=100,
                        retry=RetryConfig(max_retries=1, backoff_multiplier=2.0),
                    ),
                )
            ],
        )
        live = _ir(
            "svc",
            [
                _op(
                    "op1",
                    sla=SlaConfig(
                        latency_budget_ms=100,
                        retry=RetryConfig(max_retries=5, backoff_multiplier=3.0),
                    ),
                )
            ],
        )
        report = detect_drift(deployed, live)
        assert report.has_drift
        mod = report.modified_operations[0]
        assert any("sla retry config changed" in c for c in mod.changes)

    async def test_no_sla_drift_when_identical(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=200,
            timeout_ms=1000,
            retry=RetryConfig(max_retries=2, backoff_multiplier=2.0),
        )
        deployed = _ir("svc", [_op("op1", sla=sla)])
        live = _ir("svc", [_op("op1", sla=sla)])
        report = detect_drift(deployed, live)
        assert report.has_drift is False


class TestSlaEndToEnd:
    """Full lifecycle: create → validate → compose → drift."""

    async def test_full_sla_lifecycle(self) -> None:
        sla_a = SlaConfig(
            latency_budget_ms=100,
            timeout_ms=500,
            retry=RetryConfig(max_retries=2, backoff_multiplier=1.5),
        )
        sla_b = SlaConfig(latency_budget_ms=1000, timeout_ms=5000)

        ir_a = _ir("svc_a", [_op("op_a", sla=sla_a)])
        ir_b = _ir("svc_b", [_op("op_b", sla=sla_b)], protocol="graphql")

        # Validate both individually
        async with PreDeployValidator() as validator:
            for ir in (ir_a, ir_b):
                report = await validator.validate(ir)
                assert report.get_result("schema").passed

        # Compose
        merged = compose_irs([ir_a, ir_b])
        assert merged.protocol == "federated"
        assert len(merged.operations) == 2

        # Validate composed
        async with PreDeployValidator() as validator:
            report = await validator.validate(merged)
            assert report.get_result("schema").passed

        # Simulate drift: tighten SLA on svc_a
        sla_a_updated = SlaConfig(
            latency_budget_ms=50,
            timeout_ms=300,
            retry=RetryConfig(max_retries=2, backoff_multiplier=1.5),
        )
        ir_a_updated = _ir("svc_a", [_op("op_a", sla=sla_a_updated)])
        drift = detect_drift(ir_a, ir_a_updated)
        assert drift.has_drift is True
        # Tightened = non-breaking
        assert drift.severity == DriftSeverity.non_breaking
