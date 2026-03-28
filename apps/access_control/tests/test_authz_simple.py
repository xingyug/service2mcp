"""Simple integration tests to hit uncovered lines in authz service."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from apps.access_control.authz.service import AuthzService


class TestAuthzServiceUncoveredLines:
    def test_invalid_risk_threshold_in_matches(self):
        """Test line 170-171: _matches returns False for invalid risk threshold."""
        from types import SimpleNamespace
        from apps.access_control.authz.models import PolicyEvaluationRequest
        from libs.ir.models import RiskLevel
        
        service = AuthzService(AsyncMock())
        
        # Mock policy with invalid risk threshold
        policy = SimpleNamespace(
            resource_id="svc-1",
            action_pattern="read",
            risk_threshold="invalid_value"  # This should trigger ValueError
        )
        
        req = PolicyEvaluationRequest(
            subject_type="user",
            subject_id="alice", 
            resource_id="svc-1",
            action="read",
            risk_level=RiskLevel.safe
        )
        
        result = service._matches(policy, req)
        assert result is False

    async def test_get_policy_returns_none(self):
        """Test lines 80-82: get_policy returns None when policy not found."""
        mock_session = AsyncMock()
        mock_session.get.return_value = None
        
        service = AuthzService(mock_session)
        result = await service.get_policy(uuid4())
        
        assert result is None

    async def test_update_policy_returns_none(self):
        """Test lines 90-91: update_policy returns None when policy not found."""
        from apps.access_control.authz.models import PolicyUpdateRequest
        
        mock_session = AsyncMock()
        mock_session.get.return_value = None
        
        service = AuthzService(mock_session)
        result = await service.update_policy(uuid4(), PolicyUpdateRequest())
        
        assert result is None

    async def test_delete_policy_returns_none(self):
        """Test lines 111-112: delete_policy returns None when policy not found."""
        mock_session = AsyncMock()
        mock_session.get.return_value = None
        
        service = AuthzService(mock_session)
        result = await service.delete_policy(uuid4())
        
        assert result is None

    async def test_evaluate_no_matches_default_deny(self):
        """Test lines 135-140: evaluate returns default deny when no policies match."""
        from apps.access_control.authz.models import PolicyEvaluationRequest
        from libs.ir.models import RiskLevel
        
        mock_session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []  # No policies
        mock_session.scalars.return_value = mock_scalars
        
        service = AuthzService(mock_session)
        req = PolicyEvaluationRequest(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1", 
            action="read",
            risk_level=RiskLevel.safe
        )
        
        result = await service.evaluate(req)
        
        assert result.decision == "deny"
        assert result.matched_policy_id is None
        assert "No matching policy. Default deny applied." in result.reason