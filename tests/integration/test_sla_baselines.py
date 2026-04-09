"""Integration tests for SLA baseline tooling.

Full pipeline: generate latency data → compute baselines → apply to IR →
validate feasibility → drift detection.
"""

from __future__ import annotations

import random

import pytest

from libs.ir.models import (
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SlaConfig,
)
from libs.ir.sla import (
    export_sla_report,
    recommend_sla_for_ir,
    validate_sla_feasibility,
)
from libs.validator.drift import detect_drift

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_operation(op_id: str, sla: SlaConfig | None = None) -> Operation:
    return Operation(
        id=op_id,
        name=op_id,
        description=f"Test {op_id}",
        method="GET",
        path=f"/{op_id}",
        risk=RiskMetadata(risk_level=RiskLevel.safe),
        sla=sla,
    )


def _make_ir(ops: list[Operation]) -> ServiceIR:
    return ServiceIR(
        source_hash="b" * 64,
        protocol="openapi",
        service_name="sla-baseline-test",
        base_url="https://api.example.com",
        operations=ops,
    )


def _normal_latencies(mean: float, stddev: float, n: int = 200) -> list[float]:
    """Generate normally-distributed latencies, clamped to positive values."""
    rng = random.Random(42)
    return [max(0.1, rng.gauss(mean, stddev)) for _ in range(n)]


def _bimodal_latencies(
    mean1: float,
    mean2: float,
    stddev: float = 10.0,
    n: int = 200,
) -> list[float]:
    """Generate bimodal latencies (two peaks)."""
    rng = random.Random(99)
    half = n // 2
    fast = [max(0.1, rng.gauss(mean1, stddev)) for _ in range(half)]
    slow = [max(0.1, rng.gauss(mean2, stddev)) for _ in range(n - half)]
    return fast + slow


def _latencies_with_outliers(
    base_mean: float,
    stddev: float = 5.0,
    n: int = 200,
    outlier_pct: float = 0.02,
    outlier_multiplier: float = 10.0,
) -> list[float]:
    """Generate latencies with a small fraction of outliers."""
    rng = random.Random(77)
    result: list[float] = []
    for _ in range(n):
        val = max(0.1, rng.gauss(base_mean, stddev))
        if rng.random() < outlier_pct:
            val *= outlier_multiplier
        result.append(val)
    return result


# ===================================================================
# Full pipeline tests
# ===================================================================


@pytest.mark.integration
class TestSlaBaselinePipeline:
    """End-to-end: latency data → baselines → IR → validate → drift."""

    def test_full_pipeline_normal_distribution(self) -> None:
        ops = [_make_operation("list-items"), _make_operation("get-item")]
        ir = _make_ir(ops)

        latency_data = {
            "list-items": _normal_latencies(mean=50.0, stddev=10.0),
            "get-item": _normal_latencies(mean=20.0, stddev=5.0),
        }

        # Step 1: Recommend SLA
        updated_ir = recommend_sla_for_ir(ir, latency_data)
        for op in updated_ir.operations:
            assert op.sla is not None
            assert op.sla.latency_budget_ms is not None
            assert op.sla.timeout_ms is not None

        # Step 2: Validate feasibility
        for op in updated_ir.operations:
            assert op.sla is not None
            warnings = validate_sla_feasibility(op.sla)
            # Normal distributions with reasonable means should not trigger
            # aggressive-budget warnings
            aggressive = [w for w in warnings if "very aggressive" in w]
            assert aggressive == [], f"Unexpected aggressive warning for {op.id}"

        # Step 3: Export report
        report = export_sla_report(updated_ir)
        assert report["summary"]["with_sla"] == 2
        assert report["summary"]["without_sla"] == 0

        # Step 4: Drift detection — identical IR should show no drift
        drift = detect_drift(updated_ir, updated_ir)
        assert not drift.has_drift

    def test_full_pipeline_bimodal_distribution(self) -> None:
        ops = [_make_operation("search")]
        ir = _make_ir(ops)

        latency_data = {"search": _bimodal_latencies(mean1=30.0, mean2=150.0)}
        updated_ir = recommend_sla_for_ir(ir, latency_data)

        sla = updated_ir.operations[0].sla
        assert sla is not None
        # p99 of bimodal should capture the slower peak
        assert sla.latency_budget_ms is not None
        assert sla.latency_budget_ms > 100

    def test_full_pipeline_with_outliers(self) -> None:
        ops = [_make_operation("upload")]
        ir = _make_ir(ops)

        latency_data = {
            "upload": _latencies_with_outliers(base_mean=80.0, outlier_multiplier=10.0),
        }
        updated_ir = recommend_sla_for_ir(ir, latency_data)

        sla = updated_ir.operations[0].sla
        assert sla is not None
        assert sla.latency_budget_ms is not None
        # p99 should capture outlier influence but not be wildly larger
        # than 10× base (outlier multiplier)
        assert sla.latency_budget_ms > 80


# ===================================================================
# Drift detection with SLA changes
# ===================================================================


@pytest.mark.integration
class TestSlaDriftDetection:
    """Verify that modifying SLA baselines triggers correct drift classification."""

    def test_tightened_budget_detected(self) -> None:
        sla_original = SlaConfig(latency_budget_ms=200, timeout_ms=400)
        sla_tighter = SlaConfig(latency_budget_ms=100, timeout_ms=200)

        ir_before = _make_ir([_make_operation("op1", sla=sla_original)])
        ir_after = _make_ir([_make_operation("op1", sla=sla_tighter)])

        drift = detect_drift(ir_before, ir_after)
        assert drift.has_drift
        changes = [d.changes for d in drift.modified_operations]
        flat = [c for sublist in changes for c in sublist]
        assert any("tightened" in c for c in flat)

    def test_relaxed_budget_is_breaking(self) -> None:
        sla_original = SlaConfig(latency_budget_ms=100, timeout_ms=200)
        sla_relaxed = SlaConfig(latency_budget_ms=500, timeout_ms=1000)

        ir_before = _make_ir([_make_operation("op1", sla=sla_original)])
        ir_after = _make_ir([_make_operation("op1", sla=sla_relaxed)])

        drift = detect_drift(ir_before, ir_after)
        assert drift.has_drift
        # Relaxed SLA is classified as breaking per _BREAKING_PATTERNS
        from libs.validator.drift import DriftSeverity

        assert drift.severity == DriftSeverity.breaking

    def test_sla_removed_is_breaking(self) -> None:
        sla = SlaConfig(latency_budget_ms=100, timeout_ms=200)

        ir_before = _make_ir([_make_operation("op1", sla=sla)])
        ir_after = _make_ir([_make_operation("op1", sla=None)])

        drift = detect_drift(ir_before, ir_after)
        assert drift.has_drift
        from libs.validator.drift import DriftSeverity

        assert drift.severity == DriftSeverity.breaking

    def test_sla_added_non_breaking(self) -> None:
        sla = SlaConfig(latency_budget_ms=200, timeout_ms=400)

        ir_before = _make_ir([_make_operation("op1", sla=None)])
        ir_after = _make_ir([_make_operation("op1", sla=sla)])

        drift = detect_drift(ir_before, ir_after)
        assert drift.has_drift
        from libs.validator.drift import DriftSeverity

        # Adding SLA is not in _BREAKING_PATTERNS → non-breaking
        assert drift.severity == DriftSeverity.non_breaking

    def test_retry_config_change_detected(self) -> None:
        sla1 = SlaConfig(
            latency_budget_ms=100,
            timeout_ms=200,
            retry=__import__("libs.ir.models", fromlist=["RetryConfig"]).RetryConfig(max_retries=2),
        )
        from libs.ir.models import RetryConfig

        sla2 = SlaConfig(
            latency_budget_ms=100,
            timeout_ms=200,
            retry=RetryConfig(max_retries=5),
        )

        ir_before = _make_ir([_make_operation("op1", sla=sla1)])
        ir_after = _make_ir([_make_operation("op1", sla=sla2)])

        drift = detect_drift(ir_before, ir_after)
        assert drift.has_drift
        changes = [d.changes for d in drift.modified_operations]
        flat = [c for sublist in changes for c in sublist]
        assert any("retry" in c for c in flat)


# ===================================================================
# Realistic scenario: apply baselines then re-compute with new data
# ===================================================================


@pytest.mark.integration
class TestSlaBaselineEvolution:
    """Test that SLA baselines evolve correctly as latency data changes."""

    def test_recompute_with_improved_latency(self) -> None:
        """When latency improves, recomputed SLA should tighten."""
        ops = [_make_operation("fast-endpoint")]
        ir = _make_ir(ops)

        # Initial: slower latencies
        initial_data = {"fast-endpoint": _normal_latencies(mean=100.0, stddev=15.0)}
        ir_v1 = recommend_sla_for_ir(ir, initial_data)

        # Later: faster latencies
        improved_data = {"fast-endpoint": _normal_latencies(mean=40.0, stddev=5.0)}
        ir_v2 = recommend_sla_for_ir(ir, improved_data)

        sla_v1 = ir_v1.operations[0].sla
        sla_v2 = ir_v2.operations[0].sla
        assert sla_v1 is not None and sla_v2 is not None
        assert sla_v1.latency_budget_ms is not None
        assert sla_v2.latency_budget_ms is not None
        assert sla_v2.latency_budget_ms < sla_v1.latency_budget_ms

        # Drift should show tightened budget
        drift = detect_drift(ir_v1, ir_v2)
        assert drift.has_drift

    def test_partial_coverage_then_full(self) -> None:
        """Adding SLA data for previously-uncovered operations."""
        ops = [_make_operation("a"), _make_operation("b"), _make_operation("c")]
        ir = _make_ir(ops)

        # Phase 1: only 'a' has data
        ir_v1 = recommend_sla_for_ir(ir, {"a": [50.0, 60.0, 70.0]})
        assert ir_v1.operations[0].sla is not None
        assert ir_v1.operations[1].sla is None
        assert ir_v1.operations[2].sla is None

        report_v1 = export_sla_report(ir_v1)
        assert report_v1["summary"]["with_sla"] == 1

        # Phase 2: all operations have data
        full_data = {
            "a": [50.0, 60.0, 70.0],
            "b": [100.0, 110.0, 120.0],
            "c": [200.0, 210.0, 220.0],
        }
        ir_v2 = recommend_sla_for_ir(ir, full_data)
        report_v2 = export_sla_report(ir_v2)
        assert report_v2["summary"]["with_sla"] == 3
