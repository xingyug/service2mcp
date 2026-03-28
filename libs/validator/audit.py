"""Shared audit types, skip-policy, and regression thresholds for generated-tool coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from libs.ir.models import Operation, ToolIntent


@dataclass(frozen=True)
class ToolAuditResult:
    """Behavioral audit result for a generated runtime tool."""

    tool_name: str
    outcome: Literal["passed", "failed", "skipped"]
    reason: str
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolAuditSummary:
    """Machine-readable summary for all generated-tool audit outcomes."""

    discovered_operations: int
    generated_tools: int
    audited_tools: int
    passed: int
    failed: int
    skipped: int
    results: list[ToolAuditResult]


@dataclass(frozen=True)
class AuditPolicy:
    """Configurable skip-policy for generated-tool audit runs.

    Default settings match the original conservative policy:
    skip destructive, external-side-effect, and state-mutating tools.

    Setting ``allow_idempotent_writes`` to ``True`` overrides the
    ``skip_writes_state`` rule for operations whose risk metadata
    explicitly marks them as idempotent.
    """

    skip_destructive: bool = True
    skip_external_side_effect: bool = True
    skip_writes_state: bool = True
    allow_idempotent_writes: bool = False
    audit_safe_methods: bool = True
    audit_discovery_intent: bool = True

    def skip_reason(
        self,
        operation: Operation,
        sample_invocations: dict[str, dict[str, Any]],
    ) -> str | None:
        """Return a human-readable skip reason, or ``None`` if the tool should be audited."""

        if operation.id not in sample_invocations:
            return "No sample invocation is available for this tool."

        # Safe-method override — always audit GET/HEAD/OPTIONS
        if self.audit_safe_methods and operation.method:
            if operation.method.upper() in {"GET", "HEAD", "OPTIONS"}:
                return None

        # Discovery-intent override — always audit discovery tools
        if (
            self.audit_discovery_intent
            and operation.tool_intent is not None
            and operation.tool_intent == ToolIntent.discovery
        ):
            return None

        if self.skip_destructive and operation.risk.destructive:
            return "Skipped destructive tool by policy."
        if self.skip_external_side_effect and operation.risk.external_side_effect:
            return "Skipped external side-effect tool by policy."
        if self.skip_writes_state and operation.risk.writes_state:
            if self.allow_idempotent_writes and operation.risk.idempotent:
                return None
            return "Skipped state-mutating tool by policy."
        return None


@dataclass(frozen=True)
class AuditThresholds:
    """Regression thresholds for generated-tool audit coverage.

    Threshold checks return a list of violation messages.  An empty list
    means all thresholds are satisfied.
    """

    min_audited_ratio: float = 0.0
    max_failed: int | None = None
    min_passed: int | None = None


def check_thresholds(
    summary: ToolAuditSummary,
    thresholds: AuditThresholds,
) -> list[str]:
    """Check an audit summary against the given thresholds.

    Returns a list of human-readable violation messages.  An empty list
    means all thresholds are satisfied.
    """

    violations: list[str] = []

    if summary.generated_tools > 0:
        actual_ratio = summary.audited_tools / summary.generated_tools
        if actual_ratio < thresholds.min_audited_ratio:
            violations.append(
                f"Audited ratio {actual_ratio:.2f} is below minimum "
                f"{thresholds.min_audited_ratio:.2f} "
                f"({summary.audited_tools}/{summary.generated_tools})."
            )

    if thresholds.max_failed is not None and summary.failed > thresholds.max_failed:
        violations.append(f"Failed count {summary.failed} exceeds maximum {thresholds.max_failed}.")

    if thresholds.min_passed is not None and summary.passed < thresholds.min_passed:
        violations.append(
            f"Passed count {summary.passed} is below minimum {thresholds.min_passed}."
        )

    return violations


@dataclass(frozen=True)
class LargeSurfacePilotReport:
    """Coverage report for a large-surface black-box pilot run.

    Captures the three B-003 coverage numbers plus unsupported patterns
    encountered during discovery and audit.
    """

    ground_truth_endpoints: int
    discovered_endpoints: int
    generated_tools: int
    audited_tools: int
    passed: int
    failed: int
    skipped: int
    discovery_coverage: float
    generation_coverage: float
    audit_pass_rate: float
    unsupported_patterns: list[str]
