"""Tests for the shared audit types, policy, and threshold helpers."""

from __future__ import annotations

from libs.ir.models import (
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    SourceType,
    ToolIntent,
)
from libs.sample_placeholders import (
    PATH_PLACEHOLDER_ID_SAMPLE,
    PATH_PLACEHOLDER_INT_SAMPLE,
    PATH_PLACEHOLDER_STRING_SAMPLE,
)
from libs.validator.audit import (
    AuditPolicy,
    AuditThresholds,
    ToolAuditSummary,
    _contains_synthetic_placeholder_sample,
    _has_synthetic_path_placeholder_samples,
    check_thresholds,
)


def _make_operation(
    *,
    operation_id: str = "get_item",
    writes_state: bool = False,
    destructive: bool = False,
    external_side_effect: bool = False,
    idempotent: bool = True,
    method: str | None = "GET",
    tool_intent: ToolIntent | None = None,
) -> Operation:
    return Operation(
        id=operation_id,
        name=operation_id,
        description="Test operation.",
        method=method,
        path=f"/{operation_id}",
        params=[Param(name="id", type="string", required=True)],
        risk=RiskMetadata(
            risk_level=RiskLevel.safe,
            confidence=1.0,
            source=SourceType.extractor,
            writes_state=writes_state,
            destructive=destructive,
            external_side_effect=external_side_effect,
            idempotent=idempotent,
        ),
        enabled=True,
        tool_intent=tool_intent,
    )


# ---------------------------------------------------------------------------
# AuditPolicy tests
# ---------------------------------------------------------------------------


class TestAuditPolicySkipReason:
    def test_default_policy_allows_safe_read(self) -> None:
        operation = _make_operation()
        policy = AuditPolicy()
        assert policy.skip_reason(operation, {"get_item": {"id": "1"}}) is None

    def test_default_policy_skips_destructive(self) -> None:
        operation = _make_operation(destructive=True, method="POST")
        policy = AuditPolicy()
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "destructive" in reason.lower()

    def test_default_policy_skips_external_side_effect(self) -> None:
        operation = _make_operation(external_side_effect=True, method="POST")
        policy = AuditPolicy()
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "side-effect" in reason.lower()

    def test_default_policy_skips_writes_state(self) -> None:
        operation = _make_operation(writes_state=True, method="POST")
        policy = AuditPolicy()
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "state-mutating" in reason.lower()

    def test_no_sample_invocation_returns_skip_reason(self) -> None:
        operation = _make_operation()
        policy = AuditPolicy()
        reason = policy.skip_reason(operation, {})
        assert reason is not None
        assert "sample invocation" in reason.lower()

    def test_path_placeholder_sample_is_skipped(self) -> None:
        operation = Operation(
            id="get_item",
            name="get_item",
            description="Get item.",
            method="GET",
            path="/items/{id}",
            params=[Param(name="id", type="string", required=True)],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
            enabled=True,
        )
        policy = AuditPolicy()
        reason = policy.skip_reason(operation, {"get_item": {"id": PATH_PLACEHOLDER_ID_SAMPLE}})
        assert reason is not None
        assert "path parameters" in reason.lower()

    def test_real_path_value_sample_is_not_skipped(self) -> None:
        operation = Operation(
            id="get_item",
            name="get_item",
            description="Get item.",
            method="GET",
            path="/items/{id}",
            params=[Param(name="id", type="string", required=True)],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
            enabled=True,
        )
        policy = AuditPolicy()
        assert policy.skip_reason(operation, {"get_item": {"id": "sample"}}) is None

    def test_non_path_sample_is_not_skipped(self) -> None:
        operation = Operation(
            id="search_items",
            name="search_items",
            description="Search items.",
            method="GET",
            path="/items/search",
            params=[Param(name="q", type="string", required=True)],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
            enabled=True,
        )
        policy = AuditPolicy()
        assert policy.skip_reason(operation, {"search_items": {"q": "sample"}}) is None

    def test_path_placeholder_with_default_is_not_skipped(self) -> None:
        operation = Operation(
            id="get_repo",
            name="get_repo",
            description="Get repo.",
            method="GET",
            path="/repos/{owner}",
            params=[Param(name="owner", type="string", required=True, default="sample")],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
            enabled=True,
        )
        policy = AuditPolicy()
        assert policy.skip_reason(operation, {"get_repo": {"owner": "sample"}}) is None

    def test_failure_skip_reason_skips_numeric_path_placeholder(self) -> None:
        operation = Operation(
            id="get_repo",
            name="get_repo",
            description="Get repo.",
            method="GET",
            path="/repos/{id}",
            params=[Param(name="id", type="integer", required=True)],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
            enabled=True,
        )
        policy = AuditPolicy()
        reason = policy.failure_skip_reason(operation, {"id": PATH_PLACEHOLDER_INT_SAMPLE})
        assert reason is not None
        assert "placeholder" in reason.lower()

    def test_failure_skip_reason_keeps_real_numeric_path_value(self) -> None:
        operation = Operation(
            id="get_repo",
            name="get_repo",
            description="Get repo.",
            method="GET",
            path="/repos/{id}",
            params=[Param(name="id", type="integer", required=True)],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
            enabled=True,
        )
        policy = AuditPolicy()
        assert policy.failure_skip_reason(operation, {"id": 1}) is None

    def test_failure_skip_reason_keeps_real_string_path_value(self) -> None:
        operation = Operation(
            id="get_repo",
            name="get_repo",
            description="Get repo.",
            method="GET",
            path="/repos/{owner}",
            params=[Param(name="owner", type="string", required=True)],
            risk=RiskMetadata(
                risk_level=RiskLevel.safe,
                confidence=1.0,
                source=SourceType.extractor,
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
            ),
            enabled=True,
        )
        policy = AuditPolicy()
        assert policy.failure_skip_reason(operation, {"owner": "gitea_admin"}) is None

    def test_allow_idempotent_writes_audits_idempotent_mutation(self) -> None:
        operation = _make_operation(writes_state=True, idempotent=True)
        policy = AuditPolicy(allow_idempotent_writes=True)
        assert policy.skip_reason(operation, {"get_item": {"id": "1"}}) is None

    def test_allow_idempotent_writes_still_skips_non_idempotent(self) -> None:
        operation = _make_operation(writes_state=True, idempotent=False, method="POST")
        policy = AuditPolicy(allow_idempotent_writes=True)
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "state-mutating" in reason.lower()

    def test_allow_idempotent_writes_still_skips_destructive(self) -> None:
        operation = _make_operation(
            writes_state=True,
            destructive=True,
            idempotent=True,
            method="POST",
        )
        policy = AuditPolicy(allow_idempotent_writes=True)
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "destructive" in reason.lower()

    def test_permissive_policy_audits_everything(self) -> None:
        operation = _make_operation(writes_state=True, destructive=True, external_side_effect=True)
        policy = AuditPolicy(
            skip_destructive=False,
            skip_external_side_effect=False,
            skip_writes_state=False,
        )
        assert policy.skip_reason(operation, {"get_item": {"id": "1"}}) is None

    # --- audit_safe_methods tests ---

    def test_audit_safe_methods_overrides_destructive_skip(self) -> None:
        operation = _make_operation(method="GET", destructive=True)
        policy = AuditPolicy()
        assert policy.skip_reason(operation, {"get_item": {"id": "1"}}) is None

    def test_audit_safe_methods_disabled_still_skips_risky_get(
        self,
    ) -> None:
        operation = _make_operation(method="GET", destructive=True)
        policy = AuditPolicy(audit_safe_methods=False)
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "destructive" in reason.lower()

    def test_audit_safe_methods_head_method(self) -> None:
        operation = _make_operation(method="HEAD", destructive=True)
        policy = AuditPolicy()
        assert policy.skip_reason(operation, {"get_item": {"id": "1"}}) is None

    def test_audit_safe_methods_post_no_override(self) -> None:
        operation = _make_operation(method="POST", destructive=True)
        policy = AuditPolicy()
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "destructive" in reason.lower()

    # --- audit_discovery_intent tests ---

    def test_audit_discovery_intent_overrides_writes_state_skip(
        self,
    ) -> None:
        operation = _make_operation(
            writes_state=True,
            tool_intent=ToolIntent.discovery,
            method=None,
        )
        policy = AuditPolicy()
        assert policy.skip_reason(operation, {"get_item": {"id": "1"}}) is None

    def test_audit_discovery_intent_disabled_still_skips(self) -> None:
        operation = _make_operation(
            writes_state=True,
            tool_intent=ToolIntent.discovery,
            method=None,
        )
        policy = AuditPolicy(audit_discovery_intent=False)
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "state-mutating" in reason.lower()

    def test_audit_discovery_intent_action_no_override(self) -> None:
        operation = _make_operation(
            writes_state=True,
            tool_intent=ToolIntent.action,
            method=None,
        )
        policy = AuditPolicy()
        reason = policy.skip_reason(operation, {"get_item": {"id": "1"}})
        assert reason is not None
        assert "state-mutating" in reason.lower()


# ---------------------------------------------------------------------------
# AuditThresholds tests
# ---------------------------------------------------------------------------


def _make_summary(
    *,
    generated: int = 10,
    audited: int = 7,
    passed: int = 7,
    failed: int = 0,
    skipped: int = 3,
) -> ToolAuditSummary:
    return ToolAuditSummary(
        discovered_operations=generated,
        generated_tools=generated,
        audited_tools=audited,
        passed=passed,
        failed=failed,
        skipped=skipped,
        results=[],
    )


class TestCheckThresholds:
    def test_no_thresholds_always_passes(self) -> None:
        summary = _make_summary()
        assert check_thresholds(summary, AuditThresholds()) == []

    def test_min_audited_ratio_passes_when_met(self) -> None:
        summary = _make_summary(generated=10, audited=7)
        thresholds = AuditThresholds(min_audited_ratio=0.5)
        assert check_thresholds(summary, thresholds) == []

    def test_min_audited_ratio_fails_when_not_met(self) -> None:
        summary = _make_summary(generated=10, audited=3)
        thresholds = AuditThresholds(min_audited_ratio=0.5)
        violations = check_thresholds(summary, thresholds)
        assert len(violations) == 1
        assert "audited ratio" in violations[0].lower()

    def test_max_failed_passes_when_zero_failures(self) -> None:
        summary = _make_summary(failed=0)
        thresholds = AuditThresholds(max_failed=0)
        assert check_thresholds(summary, thresholds) == []

    def test_max_failed_fails_when_exceeded(self) -> None:
        summary = _make_summary(passed=5, failed=2)
        thresholds = AuditThresholds(max_failed=1)
        violations = check_thresholds(summary, thresholds)
        assert len(violations) == 1
        assert "failed count" in violations[0].lower()

    def test_min_passed_passes_when_met(self) -> None:
        summary = _make_summary(passed=7)
        thresholds = AuditThresholds(min_passed=5)
        assert check_thresholds(summary, thresholds) == []

    def test_min_passed_fails_when_not_met(self) -> None:
        summary = _make_summary(passed=2)
        thresholds = AuditThresholds(min_passed=5)
        violations = check_thresholds(summary, thresholds)
        assert len(violations) == 1
        assert "passed count" in violations[0].lower()

    def test_multiple_violations_all_reported(self) -> None:
        summary = _make_summary(generated=10, audited=2, passed=1, failed=1)
        thresholds = AuditThresholds(min_audited_ratio=0.5, max_failed=0, min_passed=5)
        violations = check_thresholds(summary, thresholds)
        assert len(violations) == 3

    def test_zero_generated_tools_fails_positive_ratio_threshold(self) -> None:
        summary = _make_summary(generated=0, audited=0, passed=0)
        thresholds = AuditThresholds(min_audited_ratio=0.5)
        violations = check_thresholds(summary, thresholds)
        assert len(violations) == 1
        assert "audited ratio" in violations[0].lower()
        assert "no generated tools" in violations[0].lower()


# ---------------------------------------------------------------------------
# AuditPolicy.skip_reason — line 90 (final return None)
# ---------------------------------------------------------------------------


class TestAuditPolicyFinalReturnNone:
    def test_skip_reason_returns_none_when_no_conditions_match(self) -> None:
        """Line 90: no skip conditions triggered → returns None."""
        operation = _make_operation(
            method=None,
            writes_state=False,
            destructive=False,
            external_side_effect=False,
        )
        policy = AuditPolicy()
        assert policy.skip_reason(operation, {"get_item": {"id": "real_value"}}) is None


# ---------------------------------------------------------------------------
# _has_synthetic_path_placeholder_samples — edge cases
# ---------------------------------------------------------------------------


class TestHasSyntheticPathPlaceholderSamples:
    def test_returns_false_when_path_is_empty(self) -> None:
        """Line 124: empty path → False."""
        operation = Operation(
            id="op",
            name="op",
            description="test",
            method="GET",
            path="",
            params=[Param(name="id", type="string", required=True)],
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=1.0),
        )
        assert _has_synthetic_path_placeholder_samples(operation, {"id": "sample"}) is False

    def test_returns_false_when_path_is_none(self) -> None:
        """Line 124: None path → False."""
        operation = Operation(
            id="op",
            name="op",
            description="test",
            method="GET",
            path=None,
            params=[Param(name="id", type="string", required=True)],
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=1.0),
        )
        assert _has_synthetic_path_placeholder_samples(operation, {"id": "sample"}) is False

    def test_returns_false_when_no_placeholders(self) -> None:
        """Line 133: path without {…} → False."""
        operation = Operation(
            id="op",
            name="op",
            description="test",
            method="GET",
            path="/items/all",
            params=[Param(name="q", type="string", required=True)],
            risk=RiskMetadata(risk_level=RiskLevel.safe, confidence=1.0),
        )
        assert _has_synthetic_path_placeholder_samples(operation, {"q": "sample"}) is False


# ---------------------------------------------------------------------------
# _contains_synthetic_placeholder_sample — recursive and int cases
# ---------------------------------------------------------------------------


class TestContainsSyntheticPlaceholderSample:
    def test_int_1_with_numeric_fallbacks(self) -> None:
        """Real integer 1 should not be treated as a placeholder."""
        assert _contains_synthetic_placeholder_sample(1, include_numeric_fallbacks=True) is False

    def test_int_1_without_numeric_fallbacks(self) -> None:
        """Integer 1 with include_numeric_fallbacks=False → False."""
        assert _contains_synthetic_placeholder_sample(1, include_numeric_fallbacks=False) is False

    def test_int_other_with_numeric_fallbacks(self) -> None:
        """Integer != 1 with include_numeric_fallbacks=True → False."""
        assert _contains_synthetic_placeholder_sample(42, include_numeric_fallbacks=True) is False

    def test_list_recursive_check(self) -> None:
        """Lines 157-164: synthetic value nested in a list → True."""
        assert _contains_synthetic_placeholder_sample(
            ["real", PATH_PLACEHOLDER_STRING_SAMPLE], include_numeric_fallbacks=False
        ) is True

    def test_list_no_synthetic(self) -> None:
        """Lines 157-164: list with no synthetic values → False."""
        assert _contains_synthetic_placeholder_sample(
            ["real", "data"], include_numeric_fallbacks=False
        ) is False

    def test_dict_recursive_check(self) -> None:
        """Lines 165-172: synthetic value nested in a dict → True."""
        assert _contains_synthetic_placeholder_sample(
            {"key": PATH_PLACEHOLDER_STRING_SAMPLE}, include_numeric_fallbacks=False
        ) is True

    def test_dict_no_synthetic(self) -> None:
        """Lines 165-172: dict with no synthetic values → False."""
        assert _contains_synthetic_placeholder_sample(
            {"key": "real"}, include_numeric_fallbacks=False
        ) is False

    def test_list_with_numeric_fallback(self) -> None:
        """Lines 157-164: list containing int 1 with numeric fallbacks → True."""
        assert _contains_synthetic_placeholder_sample(
            [PATH_PLACEHOLDER_INT_SAMPLE], include_numeric_fallbacks=True
        ) is True

    def test_dict_with_numeric_fallback(self) -> None:
        """Lines 165-172: dict containing int 1 with numeric fallbacks → True."""
        assert _contains_synthetic_placeholder_sample(
            {"id": PATH_PLACEHOLDER_INT_SAMPLE}, include_numeric_fallbacks=True
        ) is True
