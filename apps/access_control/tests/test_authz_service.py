"""Unit tests for authz service — policy matching and evaluation logic.

Tests the pure matching/specificity/evaluate logic using mock Policy objects,
avoiding real database interactions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from apps.access_control.authz.models import PolicyEvaluationRequest, PolicyResponse
from apps.access_control.authz.service import (
    _DECISION_PRIORITY,
    _RISK_ORDER,
    AuthzService,
    _MatchedPolicy,
)
from libs.db_models import Policy
from libs.ir.models import RiskLevel


def _mock_policy(
    *,
    subject_type: str = "user",
    subject_id: str = "alice",
    resource_id: str = "svc-1",
    action_pattern: str = "*",
    risk_threshold: str = "safe",
    decision: str = "allow",
) -> Any:
    """Create a lightweight mock that satisfies Policy attribute access."""
    return SimpleNamespace(
        id=uuid4(),
        subject_type=subject_type,
        subject_id=subject_id,
        resource_id=resource_id,
        action_pattern=action_pattern,
        risk_threshold=risk_threshold,
        decision=decision,
        created_by="admin",
        created_at=datetime.now(UTC),
    )


def _eval_request(
    *,
    subject_type: str = "user",
    subject_id: str = "alice",
    resource_id: str = "svc-1",
    action: str = "read",
    risk_level: RiskLevel = RiskLevel.safe,
) -> PolicyEvaluationRequest:
    return PolicyEvaluationRequest(
        subject_type=subject_type,
        subject_id=subject_id,
        resource_id=resource_id,
        action=action,
        risk_level=risk_level,
    )


class TestRiskOrder:
    def test_safe_is_lowest(self) -> None:
        assert _RISK_ORDER[RiskLevel.safe] < _RISK_ORDER[RiskLevel.cautious]

    def test_unknown_is_highest(self) -> None:
        assert _RISK_ORDER[RiskLevel.unknown] > _RISK_ORDER[RiskLevel.dangerous]

    def test_all_risk_levels_present(self) -> None:
        for level in RiskLevel:
            assert level in _RISK_ORDER


class TestDecisionPriority:
    def test_deny_highest(self) -> None:
        assert _DECISION_PRIORITY["deny"] > _DECISION_PRIORITY["allow"]

    def test_require_approval_middle(self) -> None:
        assert _DECISION_PRIORITY["deny"] > _DECISION_PRIORITY["require_approval"]
        assert _DECISION_PRIORITY["require_approval"] > _DECISION_PRIORITY["allow"]


class TestMatches:
    def _svc(self) -> AuthzService:

        return AuthzService(AsyncMock())

    def test_exact_match(self) -> None:
        svc = self._svc()
        policy = _mock_policy(resource_id="svc-1", action_pattern="read")
        req = _eval_request(resource_id="svc-1", action="read")
        assert svc._matches(policy, req) is True

    def test_wildcard_resource(self) -> None:
        svc = self._svc()
        policy = _mock_policy(resource_id="*", action_pattern="read")
        req = _eval_request(resource_id="svc-1", action="read")
        assert svc._matches(policy, req) is True

    def test_wildcard_action(self) -> None:
        svc = self._svc()
        policy = _mock_policy(action_pattern="*")
        req = _eval_request(action="anything")
        assert svc._matches(policy, req) is True

    def test_glob_action_pattern(self) -> None:
        svc = self._svc()
        policy = _mock_policy(action_pattern="read_*")
        req = _eval_request(action="read_users")
        assert svc._matches(policy, req) is True

    def test_glob_action_no_match(self) -> None:
        svc = self._svc()
        policy = _mock_policy(action_pattern="read_*")
        req = _eval_request(action="write_users")
        assert svc._matches(policy, req) is False

    def test_resource_no_match(self) -> None:
        svc = self._svc()
        policy = _mock_policy(resource_id="svc-2")
        req = _eval_request(resource_id="svc-1")
        assert svc._matches(policy, req) is False

    def test_risk_threshold_allows_lower(self) -> None:
        svc = self._svc()
        policy = _mock_policy(risk_threshold="cautious")
        req = _eval_request(risk_level=RiskLevel.safe)
        assert svc._matches(policy, req) is True

    def test_risk_threshold_blocks_higher(self) -> None:
        svc = self._svc()
        policy = _mock_policy(risk_threshold="safe")
        req = _eval_request(risk_level=RiskLevel.dangerous)
        assert svc._matches(policy, req) is False

    def test_risk_threshold_exact_match(self) -> None:
        svc = self._svc()
        policy = _mock_policy(risk_threshold="cautious")
        req = _eval_request(risk_level=RiskLevel.cautious)
        assert svc._matches(policy, req) is True


class TestSpecificity:
    def _svc(self) -> AuthzService:

        return AuthzService(AsyncMock())

    def test_all_exact(self) -> None:
        svc = self._svc()
        policy = _mock_policy(subject_id="alice", resource_id="svc-1", action_pattern="read")
        req = _eval_request(subject_id="alice", resource_id="svc-1", action="read")
        assert svc._specificity(policy, req) == 7  # 4 + 2 + 1

    def test_wildcard_subject(self) -> None:
        svc = self._svc()
        policy = _mock_policy(subject_id="*", resource_id="svc-1", action_pattern="read")
        req = _eval_request(subject_id="alice", resource_id="svc-1", action="read")
        assert svc._specificity(policy, req) == 3  # 0 + 2 + 1

    def test_wildcard_resource(self) -> None:
        svc = self._svc()
        policy = _mock_policy(subject_id="alice", resource_id="*", action_pattern="read")
        req = _eval_request(subject_id="alice", resource_id="svc-1", action="read")
        assert svc._specificity(policy, req) == 5  # 4 + 0 + 1

    def test_wildcard_action(self) -> None:
        svc = self._svc()
        policy = _mock_policy(subject_id="alice", resource_id="svc-1", action_pattern="*")
        req = _eval_request(subject_id="alice", resource_id="svc-1", action="read")
        assert svc._specificity(policy, req) == 6  # 4 + 2 + 0

    def test_all_wildcards(self) -> None:
        svc = self._svc()
        policy = _mock_policy(subject_id="*", resource_id="*", action_pattern="*")
        req = _eval_request()
        assert svc._specificity(policy, req) == 0


class TestMatchedPolicy:
    def test_construction(self) -> None:
        policy = _mock_policy()
        mp = _MatchedPolicy(policy=policy, specificity=5)
        assert mp.specificity == 5
        assert mp.policy is policy


class TestToResponse:
    def test_converts_policy(self) -> None:
        policy = _mock_policy(risk_threshold="cautious")
        response = AuthzService._to_response(policy)
        assert isinstance(response, PolicyResponse)
        assert response.risk_threshold == RiskLevel.cautious
        assert response.decision == "allow"


# Additional tests to cover uncovered lines in authz/service.py


class TestCreatePolicy:
    async def test_create_policy_with_commit(self) -> None:
        """Lines 60-63: commit=True triggers session.commit() then session.refresh()."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        # refresh() simulates DB populating id and created_at on the ORM object
        async def _fake_refresh(obj: Any) -> None:
            obj.id = uuid4()
            obj.created_at = datetime.now(UTC)

        mock_session.refresh = AsyncMock(side_effect=_fake_refresh)

        from apps.access_control.authz.models import PolicyCreateRequest

        payload = PolicyCreateRequest(
            subject_type="user",
            subject_id="bob",
            resource_id="svc-2",
            action_pattern="write_*",
            risk_threshold=RiskLevel.cautious,
            decision="deny",
            created_by="admin",
        )

        service = AuthzService(mock_session)
        result = await service.create_policy(payload, commit=True)

        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()
        mock_session.commit.assert_awaited_once()
        mock_session.refresh.assert_awaited_once()
        assert isinstance(result, PolicyResponse)

    async def test_create_policy_without_commit(self) -> None:
        """commit=False should NOT call session.commit(), but still calls flush/refresh."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        async def _fake_refresh(obj: Any) -> None:
            obj.id = uuid4()
            obj.created_at = datetime.now(UTC)

        mock_session.refresh = AsyncMock(side_effect=_fake_refresh)

        from apps.access_control.authz.models import PolicyCreateRequest

        payload = PolicyCreateRequest(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="*",
            decision="allow",
            created_by="admin",
        )

        service = AuthzService(mock_session)
        result = await service.create_policy(payload, commit=False)

        mock_session.flush.assert_awaited_once()
        mock_session.commit.assert_not_awaited()
        mock_session.refresh.assert_awaited_once()
        assert isinstance(result, PolicyResponse)


class TestListPolicies:
    async def test_filter_by_subject_type(self) -> None:
        """Line 74: filter by subject_type only."""
        mock_session = AsyncMock()
        policies = [_mock_policy(subject_type="user")]
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = policies
        mock_session.scalars.return_value = mock_scalars_result

        service = AuthzService(mock_session)
        result = await service.list_policies(subject_type="user")

        assert len(result) == 1
        assert result[0].subject_type == "user"
        mock_session.scalars.assert_awaited_once()

    async def test_filter_by_subject_id(self) -> None:
        """Line 78: filter by subject_id only."""
        mock_session = AsyncMock()
        policies = [_mock_policy(subject_id="bob")]
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = policies
        mock_session.scalars.return_value = mock_scalars_result

        service = AuthzService(mock_session)
        result = await service.list_policies(subject_id="bob")

        assert len(result) == 1
        assert result[0].subject_id == "bob"

    async def test_filter_by_resource_id(self) -> None:
        """Line 81: filter by resource_id only."""
        mock_session = AsyncMock()
        policies = [_mock_policy(resource_id="svc-3")]
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = policies
        mock_session.scalars.return_value = mock_scalars_result

        service = AuthzService(mock_session)
        result = await service.list_policies(resource_id="svc-3")

        assert len(result) == 1
        assert result[0].resource_id == "svc-3"

    async def test_filter_combined(self) -> None:
        """Lines 74, 78, 81: all three filters at once."""
        mock_session = AsyncMock()
        policies = [_mock_policy(subject_type="role", subject_id="editor", resource_id="doc-1")]
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = policies
        mock_session.scalars.return_value = mock_scalars_result

        service = AuthzService(mock_session)
        result = await service.list_policies(
            subject_type="role", subject_id="editor", resource_id="doc-1"
        )

        assert len(result) == 1
        assert result[0].subject_type == "role"
        assert result[0].subject_id == "editor"
        assert result[0].resource_id == "doc-1"

    async def test_no_filters_returns_all(self) -> None:
        """Line 87: no filters, returns everything from scalars."""
        mock_session = AsyncMock()
        policies = [_mock_policy(), _mock_policy(subject_id="bob")]
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = policies
        mock_session.scalars.return_value = mock_scalars_result

        service = AuthzService(mock_session)
        result = await service.list_policies()

        assert len(result) == 2


class TestGetPolicy:
    async def test_policy_not_found(self) -> None:
        """Test lines 80-82: get_policy returns None when not found."""
        mock_session = AsyncMock()
        mock_session.get.return_value = None
        service = AuthzService(mock_session)

        policy_id = uuid4()
        result = await service.get_policy(policy_id)

        assert result is None
        mock_session.get.assert_called_once_with(Policy, policy_id)


class TestUpdatePolicy:
    async def test_policy_not_found_for_update(self) -> None:
        """Test lines 90-91: update_policy returns None when policy not found."""
        mock_session = AsyncMock()
        mock_session.get.return_value = None
        service = AuthzService(mock_session)

        from apps.access_control.authz.models import PolicyUpdateRequest

        payload = PolicyUpdateRequest(resource_id="svc-2")
        policy_id = uuid4()

        result = await service.update_policy(policy_id, payload)

        assert result is None

    async def test_updates_mutable_fields_without_touching_created_by(self) -> None:
        """update_policy should preserve immutable author metadata."""
        mock_session = AsyncMock()
        mock_policy = _mock_policy()
        mock_session.get.return_value = mock_policy
        service = AuthzService(mock_session)

        from apps.access_control.authz.models import PolicyUpdateRequest

        payload = PolicyUpdateRequest(
            resource_id="new-svc",
            action_pattern="write_*",
            risk_threshold=RiskLevel.dangerous,
            decision="deny",
        )
        policy_id = uuid4()

        await service.update_policy(policy_id, payload)

        # Verify all fields were updated
        assert mock_policy.resource_id == "new-svc"
        assert mock_policy.action_pattern == "write_*"
        assert mock_policy.risk_threshold == "dangerous"
        assert mock_policy.decision == "deny"
        assert mock_policy.created_by == "admin"

        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()


class TestDeletePolicy:
    async def test_policy_not_found_for_delete(self) -> None:
        """Test lines 111-112: delete_policy returns None when policy not found."""
        mock_session = AsyncMock()
        mock_session.get.return_value = None
        service = AuthzService(mock_session)

        policy_id = uuid4()
        result = await service.delete_policy(policy_id)

        assert result is None

    async def test_deletes_and_commits(self) -> None:
        """Test lines 114-116: delete_policy deletes and commits."""
        mock_session = AsyncMock()
        mock_policy = _mock_policy()
        mock_session.get.return_value = mock_policy
        service = AuthzService(mock_session)

        policy_id = uuid4()
        result = await service.delete_policy(policy_id)

        assert result == mock_policy
        mock_session.delete.assert_called_once_with(mock_policy)
        mock_session.commit.assert_called_once()


class TestEvaluate:
    async def test_invalid_risk_threshold(self) -> None:
        """Test lines 169-171: _matches handles invalid risk threshold."""
        service = AuthzService(AsyncMock())
        policy = _mock_policy(risk_threshold="invalid_risk")
        req = _eval_request()

        result = service._matches(policy, req)

        assert result is False

    async def test_evaluate_top_match_highest_specificity(self) -> None:
        """Lines 161-165: multiple matches, returns the highest-specificity one."""
        # Policy A: wildcard subject → specificity 2+1=3
        policy_a = _mock_policy(
            subject_type="user",
            subject_id="*",
            resource_id="svc-1",
            action_pattern="read",
            risk_threshold="safe",
            decision="allow",
        )
        # Policy B: exact subject → specificity 4+2+1=7
        policy_b = _mock_policy(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="read",
            risk_threshold="safe",
            decision="deny",
        )

        mock_session = AsyncMock()
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = [policy_a, policy_b]
        mock_session.scalars.return_value = mock_scalars_result

        service = AuthzService(mock_session)
        req = _eval_request(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action="read",
            risk_level=RiskLevel.safe,
        )
        result = await service.evaluate(req)

        assert result.decision == "deny"
        assert result.matched_policy_id == policy_b.id
        assert "alice" in result.reason

    async def test_evaluate_same_specificity_picks_higher_decision_priority(self) -> None:
        """Lines 154-164: tied specificity → higher decision priority wins."""
        policy_allow = _mock_policy(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="read",
            risk_threshold="safe",
            decision="allow",
        )
        policy_deny = _mock_policy(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="read",
            risk_threshold="safe",
            decision="deny",
        )

        mock_session = AsyncMock()
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = [policy_allow, policy_deny]
        mock_session.scalars.return_value = mock_scalars_result

        service = AuthzService(mock_session)
        req = _eval_request(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action="read",
            risk_level=RiskLevel.safe,
        )
        result = await service.evaluate(req)

        assert result.decision == "deny"
        assert result.matched_policy_id == policy_deny.id
