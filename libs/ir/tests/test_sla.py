"""Unit tests for libs.ir.sla — SLA baseline computation and recommendation."""

from __future__ import annotations

from typing import Any

import pytest

from libs.ir.models import (
    Operation,
    RetryConfig,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SlaConfig,
)
from libs.ir.sla import (
    compute_sla_from_latencies,
    export_sla_report,
    recommend_sla_for_ir,
    validate_sla_feasibility,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_operation(op_id: str = "op1", sla: SlaConfig | None = None) -> Operation:
    return Operation(
        id=op_id,
        name=op_id,
        description=f"Test operation {op_id}",
        method="GET",
        path=f"/{op_id}",
        risk=RiskMetadata(risk_level=RiskLevel.safe),
        sla=sla,
    )


def _make_ir(**overrides: Any) -> ServiceIR:
    defaults: dict[str, Any] = {
        "source_hash": "a" * 64,
        "protocol": "openapi",
        "service_name": "test-svc",
        "base_url": "https://api.example.com",
        "operations": [_make_operation()],
    }
    return ServiceIR(**(defaults | overrides))


# ===================================================================
# compute_sla_from_latencies
# ===================================================================


class TestComputeSlaFromLatencies:
    """Tests for compute_sla_from_latencies."""

    def test_normal_data(self) -> None:
        latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        sla = compute_sla_from_latencies(latencies)
        assert sla.latency_budget_ms == 100
        assert sla.timeout_ms == 200
        assert sla.retry.max_retries == 2
        assert sla.retry.backoff_base_ms == 100

    def test_single_value(self) -> None:
        sla = compute_sla_from_latencies([42.0])
        assert sla.latency_budget_ms == 42
        assert sla.timeout_ms == 84

    def test_all_same_values(self) -> None:
        sla = compute_sla_from_latencies([25.0] * 100)
        assert sla.latency_budget_ms == 25
        assert sla.timeout_ms == 50

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            compute_sla_from_latencies([])

    def test_percentile_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="percentile"):
            compute_sla_from_latencies([10.0], percentile=0.0)
        with pytest.raises(ValueError, match="percentile"):
            compute_sla_from_latencies([10.0], percentile=101.0)

    def test_p50(self) -> None:
        latencies = list(range(1, 101))  # 1..100
        sla = compute_sla_from_latencies([float(x) for x in latencies], percentile=50.0)
        assert sla.latency_budget_ms == 50

    def test_p95(self) -> None:
        latencies = [float(x) for x in range(1, 101)]
        sla = compute_sla_from_latencies(latencies, percentile=95.0)
        assert sla.latency_budget_ms == 95

    def test_fractional_latency_rounds_up(self) -> None:
        sla = compute_sla_from_latencies([0.5])
        assert sla.latency_budget_ms == 1  # ceil(0.5) = 1, min 1

    def test_sub_ms_latency_floor_at_1(self) -> None:
        sla = compute_sla_from_latencies([0.001])
        assert sla.latency_budget_ms >= 1

    def test_large_dataset(self) -> None:
        latencies = [float(i) for i in range(1, 10001)]
        sla = compute_sla_from_latencies(latencies)
        assert sla.latency_budget_ms == 9900

    def test_unsorted_input(self) -> None:
        latencies = [100.0, 10.0, 50.0, 90.0, 20.0]
        sla = compute_sla_from_latencies(latencies)
        assert sla.latency_budget_ms == 100

    def test_p100(self) -> None:
        latencies = [10.0, 20.0, 30.0]
        sla = compute_sla_from_latencies(latencies, percentile=100.0)
        assert sla.latency_budget_ms == 30


# ===================================================================
# recommend_sla_for_ir
# ===================================================================


class TestRecommendSlaForIr:
    """Tests for recommend_sla_for_ir."""

    def test_applies_sla_to_matching_operations(self) -> None:
        ir = _make_ir(
            operations=[_make_operation("a"), _make_operation("b")],
        )
        latency_data = {"a": [100.0, 200.0, 300.0]}
        result = recommend_sla_for_ir(ir, latency_data)
        assert result.operations[0].sla is not None
        assert result.operations[0].sla.latency_budget_ms is not None
        assert result.operations[1].sla is None

    def test_does_not_mutate_original(self) -> None:
        ir = _make_ir(operations=[_make_operation("x")])
        result = recommend_sla_for_ir(ir, {"x": [50.0]})
        assert ir.operations[0].sla is None
        assert result.operations[0].sla is not None

    def test_preserves_existing_sla_when_no_data(self) -> None:
        existing_sla = SlaConfig(latency_budget_ms=999, timeout_ms=1998)
        ir = _make_ir(operations=[_make_operation("a", sla=existing_sla)])
        result = recommend_sla_for_ir(ir, {})
        assert result.operations[0].sla is not None
        assert result.operations[0].sla.latency_budget_ms == 999

    def test_overwrites_existing_sla_when_data_present(self) -> None:
        existing_sla = SlaConfig(latency_budget_ms=999, timeout_ms=1998)
        ir = _make_ir(operations=[_make_operation("a", sla=existing_sla)])
        result = recommend_sla_for_ir(ir, {"a": [50.0, 60.0, 70.0]})
        assert result.operations[0].sla is not None
        assert result.operations[0].sla.latency_budget_ms != 999

    def test_empty_latency_list_preserves_existing(self) -> None:
        ir = _make_ir(operations=[_make_operation("a")])
        result = recommend_sla_for_ir(ir, {"a": []})
        assert result.operations[0].sla is None

    def test_mixed_coverage(self) -> None:
        ops = [_make_operation(f"op{i}") for i in range(5)]
        ir = _make_ir(operations=ops)
        latency_data = {
            "op0": [100.0],
            "op2": [200.0],
            "op4": [300.0],
        }
        result = recommend_sla_for_ir(ir, latency_data)
        with_sla = [op for op in result.operations if op.sla is not None]
        without_sla = [op for op in result.operations if op.sla is None]
        assert len(with_sla) == 3
        assert len(without_sla) == 2

    def test_unknown_operation_ids_ignored(self) -> None:
        ir = _make_ir(operations=[_make_operation("a")])
        result = recommend_sla_for_ir(ir, {"nonexistent": [100.0]})
        assert result.operations[0].sla is None

    def test_preserves_service_metadata(self) -> None:
        ir = _make_ir(service_name="my-service", protocol="graphql")
        result = recommend_sla_for_ir(ir, {"op1": [100.0]})
        assert result.service_name == "my-service"
        assert result.protocol == "graphql"


# ===================================================================
# export_sla_report
# ===================================================================


class TestExportSlaReport:
    """Tests for export_sla_report."""

    def test_report_structure(self) -> None:
        sla = SlaConfig(latency_budget_ms=100, timeout_ms=200)
        ir = _make_ir(
            operations=[_make_operation("a", sla=sla), _make_operation("b")],
        )
        report = export_sla_report(ir)
        assert "operations" in report
        assert "summary" in report
        assert len(report["operations"]) == 2

    def test_summary_counts(self) -> None:
        sla = SlaConfig(latency_budget_ms=100, timeout_ms=200)
        ir = _make_ir(
            operations=[
                _make_operation("a", sla=sla),
                _make_operation("b", sla=sla),
                _make_operation("c"),
            ],
        )
        report = export_sla_report(ir)
        summary = report["summary"]
        assert summary["total_operations"] == 3
        assert summary["with_sla"] == 2
        assert summary["without_sla"] == 1

    def test_operation_with_sla_details(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=150,
            timeout_ms=300,
            retry=RetryConfig(max_retries=3, backoff_base_ms=50),
        )
        ir = _make_ir(operations=[_make_operation("x", sla=sla)])
        report = export_sla_report(ir)
        op_report = report["operations"][0]
        assert op_report["operation_id"] == "x"
        assert op_report["has_sla"] is True
        assert op_report["latency_budget_ms"] == 150
        assert op_report["timeout_ms"] == 300
        assert op_report["retry"]["max_retries"] == 3
        assert op_report["retry"]["backoff_base_ms"] == 50

    def test_operation_without_sla(self) -> None:
        ir = _make_ir(operations=[_make_operation("y")])
        report = export_sla_report(ir)
        op_report = report["operations"][0]
        assert op_report["has_sla"] is False
        assert op_report["latency_budget_ms"] is None
        assert op_report["timeout_ms"] is None
        assert op_report["retry"] is None

    def test_empty_operations(self) -> None:
        ir = _make_ir(operations=[])
        report = export_sla_report(ir)
        assert report["summary"]["total_operations"] == 0
        assert report["summary"]["with_sla"] == 0
        assert report["summary"]["without_sla"] == 0
        assert report["operations"] == []


# ===================================================================
# validate_sla_feasibility
# ===================================================================


class TestValidateSlaFeasibility:
    """Tests for validate_sla_feasibility."""

    def test_clean_config_no_warnings(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=500,
            timeout_ms=5000,
            retry=RetryConfig(max_retries=0),
        )
        assert validate_sla_feasibility(sla) == []

    def test_aggressive_budget_warning(self) -> None:
        sla = SlaConfig(latency_budget_ms=5, timeout_ms=100)
        warnings = validate_sla_feasibility(sla)
        assert any("very aggressive" in w for w in warnings)

    def test_timeout_less_than_budget(self) -> None:
        sla = SlaConfig(latency_budget_ms=200, timeout_ms=100)
        warnings = validate_sla_feasibility(sla)
        assert any("less than latency_budget_ms" in w for w in warnings)

    def test_retries_exceed_timeout(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=100,
            timeout_ms=200,
            retry=RetryConfig(max_retries=5, backoff_base_ms=100),
        )
        warnings = validate_sla_feasibility(sla)
        assert any("may exceed timeout_ms" in w for w in warnings)

    def test_high_retries_warning(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=50,
            timeout_ms=100,
            retry=RetryConfig(max_retries=10, backoff_base_ms=10),
        )
        warnings = validate_sla_feasibility(sla)
        assert any("may exceed timeout_ms" in w for w in warnings)

    def test_no_retries_no_retry_warning(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=500,
            timeout_ms=1000,
            retry=RetryConfig(max_retries=0),
        )
        warnings = validate_sla_feasibility(sla)
        retry_warnings = [w for w in warnings if "retries" in w.lower()]
        assert retry_warnings == []

    def test_budget_exactly_10_no_aggressive_warning(self) -> None:
        sla = SlaConfig(latency_budget_ms=10, timeout_ms=1000)
        warnings = validate_sla_feasibility(sla)
        assert not any("very aggressive" in w for w in warnings)

    def test_multiple_warnings(self) -> None:
        sla = SlaConfig(
            latency_budget_ms=5,
            timeout_ms=3,
            retry=RetryConfig(max_retries=5, backoff_base_ms=100),
        )
        warnings = validate_sla_feasibility(sla)
        assert len(warnings) >= 2

    def test_none_budget_no_aggressive_warning(self) -> None:
        sla = SlaConfig(timeout_ms=1000)
        warnings = validate_sla_feasibility(sla)
        assert not any("very aggressive" in w for w in warnings)

    def test_none_timeout_no_timeout_warning(self) -> None:
        sla = SlaConfig(latency_budget_ms=100)
        warnings = validate_sla_feasibility(sla)
        assert not any("less than" in w for w in warnings)
        assert not any("may exceed" in w for w in warnings)
