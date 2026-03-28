"""Black-box validation: compare extracted IR against ground-truth endpoints.

This module provides the core comparison logic for B-005 (Real External
API Black-Box Validation).  Given a ``ServiceIR`` produced by the
extraction pipeline and a ground-truth registry of known endpoints, it
computes discovery coverage, generation accuracy, risk-classification
correctness, and failure-pattern identification.

Typical usage::

    from libs.validator.black_box import (
        BlackBoxReport,
        evaluate_black_box,
    )
    from tests.fixtures.ground_truth.jsonplaceholder import GROUND_TRUTH

    report = evaluate_black_box(service_ir, GROUND_TRUTH)
    assert report.discovery_coverage >= 0.50
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libs.ir.models import Operation, ServiceIR
    from tests.fixtures.ground_truth.jsonplaceholder import EndpointTruth


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointMatch:
    """A single ground-truth endpoint matched to a discovered operation."""

    ground_truth_method: str
    ground_truth_path: str
    matched_operation_id: str | None
    risk_correct: bool | None = None  # None if not matched
    description: str = ""


@dataclass(frozen=True)
class FailurePattern:
    """A pattern of discovery/extraction failure observed across endpoints."""

    pattern_name: str
    affected_endpoints: list[tuple[str, str]]
    description: str


@dataclass(frozen=True)
class BlackBoxReport:
    """Complete report comparing extraction output against ground truth."""

    # Target metadata
    target_name: str
    target_base_url: str
    protocol: str

    # Ground truth stats
    ground_truth_count: int
    resource_groups: list[str]

    # Discovery stats
    discovered_operations: int
    discovered_paths: list[str]

    # Matching results
    matched_endpoints: list[EndpointMatch]
    unmatched_ground_truth: list[tuple[str, str]]
    extra_discovered: list[tuple[str | None, str | None]]

    # Coverage metrics (0.0–1.0)
    discovery_coverage: float
    risk_accuracy: float

    # Failure patterns
    failure_patterns: list[FailurePattern]

    @property
    def matched_count(self) -> int:
        return len([m for m in self.matched_endpoints if m.matched_operation_id])

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched_ground_truth)


# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------

# Pattern to normalize path templates: {id}, {petId}, {user_id} → {_}
_TEMPLATE_RE = re.compile(r"\{[^}]+\}")


def _normalize_template(path: str) -> str:
    """Collapse all path template vars to a single canonical form."""
    return _TEMPLATE_RE.sub("{_}", path.rstrip("/"))


def _match_operation_to_truth(
    op: Operation,
    truth_by_norm: dict[tuple[str, str], EndpointTruth],
) -> EndpointTruth | None:
    """Try to match a single operation to a ground-truth endpoint."""
    if not op.method or not op.path:
        return None

    op_method = op.method.upper()
    op_norm = _normalize_template(op.path)

    return truth_by_norm.get((op_method, op_norm))


# ---------------------------------------------------------------------------
# Risk comparison
# ---------------------------------------------------------------------------


def _risk_matches(op: Operation, truth: EndpointTruth) -> bool:
    """Check if an operation's risk classification matches ground truth."""
    risk = op.risk
    if risk.writes_state is not None and risk.writes_state != truth.writes_state:
        return False
    if risk.destructive is not None and risk.destructive != truth.destructive:
        return False
    return True


# ---------------------------------------------------------------------------
# Failure-pattern identification
# ---------------------------------------------------------------------------


def _identify_failure_patterns(
    unmatched: list[tuple[str, str]],
    ops: list[Operation],
) -> list[FailurePattern]:
    """Analyze unmatched ground-truth endpoints for common failure patterns."""
    patterns: list[FailurePattern] = []

    # Pattern 1: Nested resource endpoints not discovered
    nested = [(m, p) for m, p in unmatched if p.count("/") >= 3]
    if nested:
        patterns.append(
            FailurePattern(
                pattern_name="nested_resource_not_discovered",
                affected_endpoints=nested,
                description=(
                    "Nested resource endpoints (e.g. /users/{id}/posts) were not "
                    "discovered, likely because the crawler did not follow "
                    "sub-resource links."
                ),
            )
        )

    # Pattern 2: Mutation endpoints not discovered
    mutations = [(m, p) for m, p in unmatched if m in ("POST", "PUT", "PATCH", "DELETE")]
    if mutations:
        patterns.append(
            FailurePattern(
                pattern_name="mutation_endpoints_not_discovered",
                affected_endpoints=mutations,
                description=(
                    "Write/delete endpoints were not discovered; the extractor "
                    "may only probe GET/OPTIONS or skip methods not advertised "
                    "in Allow headers."
                ),
            )
        )

    # Pattern 3: Parameterized paths not generalized
    templated_unmatched = [(m, p) for m, p in unmatched if "{" in p]
    if templated_unmatched:
        # Check if we have the non-templated base
        base_paths = set()
        for _, p in templated_unmatched:
            base = "/" + p.split("/")[1]
            base_paths.add(base)

        discovered_paths = {_normalize_template(op.path) for op in ops if op.path}
        missing_bases = base_paths - {_normalize_template(p) for p in discovered_paths}
        if not missing_bases and templated_unmatched:
            patterns.append(
                FailurePattern(
                    pattern_name="item_endpoints_not_generalized",
                    affected_endpoints=templated_unmatched,
                    description=(
                        "Collection endpoints were discovered but item-level "
                        "parameterized paths were not generalized from probing."
                    ),
                )
            )

    # Pattern 4: Rate limiting / auth walls (empty result)
    if not ops:
        patterns.append(
            FailurePattern(
                pattern_name="no_operations_extracted",
                affected_endpoints=unmatched,
                description=(
                    "Zero operations were extracted, suggesting the target may "
                    "require authentication, be rate-limiting, or be unreachable."
                ),
            )
        )

    return patterns


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------


def evaluate_black_box(
    ir: ServiceIR,
    ground_truth: list[EndpointTruth],
    *,
    target_name: str = "",
    target_base_url: str = "",
) -> BlackBoxReport:
    """Compare an extracted IR against ground truth and produce a report.

    Parameters
    ----------
    ir:
        The ServiceIR produced by the extraction pipeline.
    ground_truth:
        List of expected endpoints with risk metadata.
    target_name:
        Human-readable name for the target API.
    target_base_url:
        Base URL of the target API.

    Returns
    -------
    BlackBoxReport with coverage metrics and failure patterns.
    """
    # Build normalized lookup for ground truth
    truth_by_norm: dict[tuple[str, str], EndpointTruth] = {
        (ep.method, _normalize_template(ep.path)): ep for ep in ground_truth
    }

    resource_groups = sorted({ep.resource_group for ep in ground_truth if ep.resource_group})

    # Match each operation to ground truth
    matched_endpoints: list[EndpointMatch] = []
    matched_truth_keys: set[tuple[str, str]] = set()
    matched_op_ids: set[str] = set()

    for op in ir.operations:
        if not op.enabled:
            continue
        truth = _match_operation_to_truth(op, truth_by_norm)
        if truth is not None:
            key = (truth.method, _normalize_template(truth.path))
            if key not in matched_truth_keys:
                matched_truth_keys.add(key)
                matched_op_ids.add(op.id)
                matched_endpoints.append(
                    EndpointMatch(
                        ground_truth_method=truth.method,
                        ground_truth_path=truth.path,
                        matched_operation_id=op.id,
                        risk_correct=_risk_matches(op, truth),
                        description=truth.description,
                    )
                )

    # Unmatched ground truth
    unmatched_gt: list[tuple[str, str]] = []
    for ep in ground_truth:
        key = (ep.method, _normalize_template(ep.path))
        if key not in matched_truth_keys:
            unmatched_gt.append((ep.method, ep.path))

    # Extra discovered (not in ground truth)
    extra: list[tuple[str | None, str | None]] = []
    for op in ir.operations:
        if op.id not in matched_op_ids and op.enabled:
            extra.append((op.method, op.path))

    # Coverage metrics
    gt_count = len(ground_truth)
    discovery_coverage = len(matched_truth_keys) / gt_count if gt_count > 0 else 0.0

    risk_correct_count = sum(1 for m in matched_endpoints if m.risk_correct is True)
    risk_total = sum(1 for m in matched_endpoints if m.risk_correct is not None)
    risk_accuracy = risk_correct_count / risk_total if risk_total > 0 else 0.0

    # Discovered paths
    discovered_paths = [op.path for op in ir.operations if op.path and op.enabled]

    # Failure patterns
    failure_patterns = _identify_failure_patterns(unmatched_gt, list(ir.operations))

    return BlackBoxReport(
        target_name=target_name or ir.service_name,
        target_base_url=target_base_url or ir.base_url,
        protocol=ir.protocol,
        ground_truth_count=gt_count,
        resource_groups=resource_groups,
        discovered_operations=len([op for op in ir.operations if op.enabled]),
        discovered_paths=discovered_paths,
        matched_endpoints=matched_endpoints,
        unmatched_ground_truth=unmatched_gt,
        extra_discovered=extra,
        discovery_coverage=discovery_coverage,
        risk_accuracy=risk_accuracy,
        failure_patterns=failure_patterns,
    )
