"""Tests for the black-box validation module (libs/validator/black_box.py)."""

from __future__ import annotations

from libs.ir.models import Operation, RiskLevel, RiskMetadata, ServiceIR
from libs.validator.black_box import (
    evaluate_black_box,
)
from tests.fixtures.ground_truth.jsonplaceholder import EndpointTruth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ir(
    operations: list[Operation] | None = None,
    protocol: str = "rest",
    service_name: str = "test-api",
    base_url: str = "https://example.com",
) -> ServiceIR:
    return ServiceIR(
        source_hash="abc123",
        protocol=protocol,
        service_name=service_name,
        base_url=base_url,
        operations=operations or [],
    )


def _make_op(
    op_id: str,
    method: str = "GET",
    path: str = "/test",
    *,
    writes_state: bool = False,
    destructive: bool = False,
    risk_level: RiskLevel = RiskLevel.safe,
) -> Operation:
    return Operation(
        id=op_id,
        name=op_id,
        method=method,
        path=path,
        risk=RiskMetadata(
            writes_state=writes_state,
            destructive=destructive,
            risk_level=risk_level,
        ),
    )


def _make_truth(
    method: str,
    path: str,
    *,
    writes_state: bool = False,
    destructive: bool = False,
    resource_group: str = "test",
) -> EndpointTruth:
    return EndpointTruth(
        method=method,
        path=path,
        writes_state=writes_state,
        destructive=destructive,
        resource_group=resource_group,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvaluateBlackBox:
    def test_perfect_match(self) -> None:
        """All ground truth endpoints are discovered with correct risk."""
        truth = [
            _make_truth("GET", "/users"),
            _make_truth("GET", "/users/{id}"),
            _make_truth("POST", "/users", writes_state=True),
        ]
        ops = [
            _make_op("list_users", "GET", "/users"),
            _make_op("get_user", "GET", "/users/{user_id}"),
            _make_op("create_user", "POST", "/users", writes_state=True),
        ]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, truth)

        assert report.discovery_coverage == 1.0
        assert report.risk_accuracy == 1.0
        assert report.matched_count == 3
        assert report.unmatched_count == 0
        assert len(report.extra_discovered) == 0

    def test_partial_discovery(self) -> None:
        """Only some ground truth endpoints are discovered."""
        truth = [
            _make_truth("GET", "/users"),
            _make_truth("GET", "/users/{id}"),
            _make_truth("POST", "/users", writes_state=True),
            _make_truth("DELETE", "/users/{id}", destructive=True),
        ]
        ops = [
            _make_op("list_users", "GET", "/users"),
            _make_op("get_user", "GET", "/users/{user_id}"),
        ]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, truth)

        assert report.discovery_coverage == 0.5
        assert report.matched_count == 2
        assert report.unmatched_count == 2
        assert ("POST", "/users") in report.unmatched_ground_truth
        assert ("DELETE", "/users/{id}") in report.unmatched_ground_truth

    def test_extra_discovered_operations(self) -> None:
        """Operations found that aren't in ground truth."""
        truth = [_make_truth("GET", "/users")]
        ops = [
            _make_op("list_users", "GET", "/users"),
            _make_op("get_health", "GET", "/health"),
        ]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, truth)

        assert report.matched_count == 1
        assert len(report.extra_discovered) == 1
        assert ("GET", "/health") in report.extra_discovered

    def test_empty_ir(self) -> None:
        """No operations extracted at all."""
        truth = [
            _make_truth("GET", "/users"),
            _make_truth("GET", "/users/{id}"),
        ]
        ir = _make_ir([])
        report = evaluate_black_box(ir, truth)

        assert report.discovery_coverage == 0.0
        assert report.matched_count == 0
        assert report.unmatched_count == 2
        pattern_names = {p.pattern_name for p in report.failure_patterns}
        assert "no_operations_extracted" in pattern_names

    def test_empty_ground_truth(self) -> None:
        """Ground truth is empty (edge case)."""
        ops = [_make_op("list_users", "GET", "/users")]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, [])

        assert report.discovery_coverage == 0.0
        assert report.ground_truth_count == 0
        assert len(report.extra_discovered) == 1

    def test_path_template_normalization(self) -> None:
        """Different template variable names should still match."""
        truth = [_make_truth("GET", "/pets/{petId}")]
        ops = [_make_op("get_pet", "GET", "/pets/{id}")]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, truth)

        assert report.matched_count == 1
        assert report.discovery_coverage == 1.0

    def test_risk_mismatch_counted(self) -> None:
        """Risk accuracy penalized when classification is wrong."""
        truth = [
            _make_truth("DELETE", "/users/{id}", destructive=True),
        ]
        ops = [
            _make_op(
                "delete_user",
                "DELETE",
                "/users/{user_id}",
                destructive=False,  # Wrong!
            ),
        ]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, truth)

        assert report.matched_count == 1
        assert report.risk_accuracy == 0.0

    def test_disabled_operations_excluded(self) -> None:
        """Disabled operations should not count as discovered."""
        truth = [_make_truth("GET", "/users")]
        disabled_op = Operation(
            id="list_users",
            name="list_users",
            method="GET",
            path="/users",
            risk=RiskMetadata(risk_level=RiskLevel.unknown),
            enabled=False,
        )
        ir = _make_ir([disabled_op])
        report = evaluate_black_box(ir, truth)

        assert report.discovery_coverage == 0.0
        assert report.discovered_operations == 0

    def test_resource_groups_populated(self) -> None:
        truth = [
            _make_truth("GET", "/users", resource_group="users"),
            _make_truth("GET", "/posts", resource_group="posts"),
        ]
        ir = _make_ir([])
        report = evaluate_black_box(ir, truth)

        assert report.resource_groups == ["posts", "users"]

    def test_target_name_defaults(self) -> None:
        ir = _make_ir([], service_name="my-api", base_url="https://my.api")
        report = evaluate_black_box(ir, [])

        assert report.target_name == "my-api"
        assert report.target_base_url == "https://my.api"

    def test_target_name_override(self) -> None:
        ir = _make_ir([], service_name="my-api", base_url="https://my.api")
        report = evaluate_black_box(
            ir,
            [],
            target_name="Custom Name",
            target_base_url="https://custom.url",
        )

        assert report.target_name == "Custom Name"
        assert report.target_base_url == "https://custom.url"


class TestFailurePatterns:
    def test_nested_resource_pattern(self) -> None:
        """Unmatched nested endpoints trigger nested_resource pattern."""
        truth = [
            _make_truth("GET", "/users"),
            _make_truth("GET", "/users/{id}/posts"),
            _make_truth("GET", "/users/{id}/albums"),
        ]
        ops = [_make_op("list_users", "GET", "/users")]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, truth)

        pattern_names = {p.pattern_name for p in report.failure_patterns}
        assert "nested_resource_not_discovered" in pattern_names

    def test_mutation_pattern(self) -> None:
        """Unmatched mutation endpoints trigger mutation pattern."""
        truth = [
            _make_truth("GET", "/users"),
            _make_truth("POST", "/users", writes_state=True),
            _make_truth("DELETE", "/users/{id}", destructive=True),
        ]
        ops = [_make_op("list_users", "GET", "/users")]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, truth)

        pattern_names = {p.pattern_name for p in report.failure_patterns}
        assert "mutation_endpoints_not_discovered" in pattern_names

    def test_no_patterns_on_full_match(self) -> None:
        """No failure patterns when everything matches."""
        truth = [_make_truth("GET", "/users")]
        ops = [_make_op("list_users", "GET", "/users")]
        ir = _make_ir(ops)
        report = evaluate_black_box(ir, truth)

        assert len(report.failure_patterns) == 0
