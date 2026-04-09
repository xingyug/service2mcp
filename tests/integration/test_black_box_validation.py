"""Integration tests: full black-box pipeline against mock real-world APIs.

These tests exercise the complete discovery → extraction → black-box evaluation
pipeline using realistic mock HTTP transports that simulate well-known public
APIs (JSONPlaceholder for REST discovery, PetStore v3 for OpenAPI spec-first).

Each test follows the pilot pattern:
1. Build mock transport
2. Run extractor against mock
3. Evaluate IR against ground truth
4. Assert coverage thresholds and failure-pattern expectations
"""

from __future__ import annotations

import json

import httpx
import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.openapi import OpenAPIExtractor
from libs.extractors.rest import RESTExtractor
from libs.ir.models import ServiceIR
from libs.validator.black_box import BlackBoxReport, evaluate_black_box
from tests.fixtures.ground_truth.jsonplaceholder import (
    BASE_URL as JP_BASE_URL,
)
from tests.fixtures.ground_truth.jsonplaceholder import (
    GROUND_TRUTH as JP_GROUND_TRUTH,
)
from tests.fixtures.ground_truth.jsonplaceholder import (
    build_jsonplaceholder_transport,
)
from tests.fixtures.ground_truth.petstore_v3 import (
    BASE_URL as PS_BASE_URL,
)
from tests.fixtures.ground_truth.petstore_v3 import (
    GROUND_TRUTH as PS_GROUND_TRUTH,
)
from tests.fixtures.ground_truth.petstore_v3 import (
    get_petstore_spec_json,
)

# ---------------------------------------------------------------------------
# Thresholds — intentionally lenient for the initial baseline
# foundation.  As the extractors improve, tighten them.
# ---------------------------------------------------------------------------

# JSONPlaceholder REST discovery thresholds
JP_MIN_DISCOVERY_COVERAGE = 0.25  # ≥25% of ground truth discovered
JP_MIN_DISCOVERED_OPS = 4  # at least 4 operations extracted
JP_MAX_FAILURE_PATTERNS = 4  # at most 4 failure pattern categories

# PetStore OpenAPI spec-first thresholds
PS_MIN_DISCOVERY_COVERAGE = 0.80  # spec-first should be very accurate
PS_MIN_DISCOVERED_OPS = 15  # at least 15 of 19 operations
PS_MIN_RISK_ACCURACY = 0.50  # ≥50% risk classification accuracy


# ---------------------------------------------------------------------------
# JSONPlaceholder — REST discovery
# ---------------------------------------------------------------------------


class TestJSONPlaceholderBlackBox:
    """Black-box validation against JSONPlaceholder (REST discovery)."""

    @pytest.fixture
    def ir(self) -> ServiceIR:
        """Extract IR from mock JSONPlaceholder via REST discovery."""
        transport = build_jsonplaceholder_transport()
        client = httpx.Client(transport=transport, follow_redirects=True)
        extractor = RESTExtractor(client=client, max_pages=20)
        try:
            return extractor.extract(
                SourceConfig(
                    url=JP_BASE_URL,
                    hints={
                        "protocol": "rest",
                        "service_name": "jsonplaceholder",
                    },
                )
            )
        finally:
            extractor.close()

    @pytest.fixture
    def report(self, ir: ServiceIR) -> BlackBoxReport:
        return evaluate_black_box(
            ir,
            JP_GROUND_TRUTH,
            target_name="JSONPlaceholder",
            target_base_url=JP_BASE_URL,
        )

    def test_extraction_succeeds(self, ir: ServiceIR) -> None:
        """REST discovery produces a valid IR with operations."""
        assert ir.protocol == "rest"
        assert ir.service_name == "jsonplaceholder"
        assert len(ir.operations) > 0

    def test_minimum_discovered_operations(self, report: BlackBoxReport) -> None:
        """At least JP_MIN_DISCOVERED_OPS operations extracted."""
        assert report.discovered_operations >= JP_MIN_DISCOVERED_OPS, (
            f"Expected ≥{JP_MIN_DISCOVERED_OPS} discovered ops, got {report.discovered_operations}"
        )

    def test_discovery_coverage_threshold(self, report: BlackBoxReport) -> None:
        """Discovery coverage meets minimum threshold."""
        assert report.discovery_coverage >= JP_MIN_DISCOVERY_COVERAGE, (
            f"Discovery coverage {report.discovery_coverage:.2%} below "
            f"threshold {JP_MIN_DISCOVERY_COVERAGE:.2%}; "
            f"matched {report.matched_count}/{report.ground_truth_count}"
        )

    def test_failure_patterns_bounded(self, report: BlackBoxReport) -> None:
        """Failure patterns don't exceed maximum expected count."""
        assert len(report.failure_patterns) <= JP_MAX_FAILURE_PATTERNS, (
            f"Too many failure patterns ({len(report.failure_patterns)}): "
            + ", ".join(p.pattern_name for p in report.failure_patterns)
        )

    def test_ground_truth_metadata(self, report: BlackBoxReport) -> None:
        """Report carries correct ground truth metadata."""
        assert report.target_name == "JSONPlaceholder"
        assert report.ground_truth_count == len(JP_GROUND_TRUTH)
        assert report.protocol == "rest"

    def test_no_zero_operations(self, report: BlackBoxReport) -> None:
        """The 'no_operations_extracted' pattern must NOT fire."""
        pattern_names = {p.pattern_name for p in report.failure_patterns}
        assert "no_operations_extracted" not in pattern_names

    def test_report_summary_printable(self, report: BlackBoxReport) -> None:
        """Report can be serialized for operator review."""
        summary = {
            "target": report.target_name,
            "ground_truth": report.ground_truth_count,
            "discovered": report.discovered_operations,
            "matched": report.matched_count,
            "coverage": f"{report.discovery_coverage:.2%}",
            "risk_accuracy": f"{report.risk_accuracy:.2%}",
            "failure_patterns": [p.pattern_name for p in report.failure_patterns],
            "unmatched": report.unmatched_ground_truth,
        }
        serialized = json.dumps(summary, indent=2)
        assert "JSONPlaceholder" in serialized


# ---------------------------------------------------------------------------
# PetStore v3 — OpenAPI spec-first
# ---------------------------------------------------------------------------


class TestPetStoreBlackBox:
    """Black-box validation against PetStore v3 (OpenAPI spec-first)."""

    @pytest.fixture
    def ir(self) -> ServiceIR:
        """Extract IR from inline PetStore OpenAPI spec."""
        spec_json = get_petstore_spec_json()
        extractor = OpenAPIExtractor()
        return extractor.extract(
            SourceConfig(
                file_content=spec_json,
                hints={"service_name": "petstore-v3"},
            )
        )

    @pytest.fixture
    def report(self, ir: ServiceIR) -> BlackBoxReport:
        return evaluate_black_box(
            ir,
            PS_GROUND_TRUTH,
            target_name="PetStore v3",
            target_base_url=PS_BASE_URL,
        )

    def test_extraction_succeeds(self, ir: ServiceIR) -> None:
        """OpenAPI extraction produces a valid IR."""
        assert ir.protocol == "openapi"
        assert len(ir.operations) > 0

    def test_minimum_discovered_operations(self, report: BlackBoxReport) -> None:
        """At least PS_MIN_DISCOVERED_OPS operations extracted."""
        assert report.discovered_operations >= PS_MIN_DISCOVERED_OPS, (
            f"Expected ≥{PS_MIN_DISCOVERED_OPS} discovered ops, got {report.discovered_operations}"
        )

    def test_discovery_coverage_threshold(self, report: BlackBoxReport) -> None:
        """Spec-first coverage should be very high."""
        assert report.discovery_coverage >= PS_MIN_DISCOVERY_COVERAGE, (
            f"Discovery coverage {report.discovery_coverage:.2%} below "
            f"threshold {PS_MIN_DISCOVERY_COVERAGE:.2%}; "
            f"matched {report.matched_count}/{report.ground_truth_count}"
        )

    def test_risk_accuracy_threshold(self, report: BlackBoxReport) -> None:
        """Risk classification accuracy meets minimum."""
        assert report.risk_accuracy >= PS_MIN_RISK_ACCURACY, (
            f"Risk accuracy {report.risk_accuracy:.2%} below threshold {PS_MIN_RISK_ACCURACY:.2%}"
        )

    def test_no_zero_operations(self, report: BlackBoxReport) -> None:
        """The 'no_operations_extracted' pattern must NOT fire."""
        pattern_names = {p.pattern_name for p in report.failure_patterns}
        assert "no_operations_extracted" not in pattern_names

    def test_all_resource_groups_represented(self, report: BlackBoxReport) -> None:
        """At least one operation from each resource group is matched."""
        matched_groups: set[str] = set()
        for m in report.matched_endpoints:
            if m.matched_operation_id:
                truth = next(
                    (
                        ep
                        for ep in PS_GROUND_TRUTH
                        if ep.method == m.ground_truth_method and ep.path == m.ground_truth_path
                    ),
                    None,
                )
                if truth:
                    matched_groups.add(truth.resource_group)

        expected_groups = {"pet", "store", "user"}
        assert expected_groups.issubset(matched_groups), (
            f"Missing resource groups: {expected_groups - matched_groups}"
        )

    def test_report_summary_printable(self, report: BlackBoxReport) -> None:
        """Report can be serialized for operator review."""
        summary = {
            "target": report.target_name,
            "ground_truth": report.ground_truth_count,
            "discovered": report.discovered_operations,
            "matched": report.matched_count,
            "coverage": f"{report.discovery_coverage:.2%}",
            "risk_accuracy": f"{report.risk_accuracy:.2%}",
            "failure_patterns": [p.pattern_name for p in report.failure_patterns],
        }
        serialized = json.dumps(summary, indent=2)
        assert "PetStore v3" in serialized
