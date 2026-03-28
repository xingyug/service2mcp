"""Tests for authz/routes.py — covering error paths and gateway sync failures."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from fastapi import HTTPException
import httpx

from apps.access_control.authz.routes import (
    create_policy,
    get_policy,
    update_policy,
    delete_policy,
)
from apps.access_control.authz.models import (
    PolicyCreateRequest,
    PolicyUpdateRequest,
    PolicyResponse,
)
from libs.ir.models import RiskLevel


class TestCreatePolicy:
    pass


class TestGetPolicy:
    async def test_policy_not_found(self):
        """Test lines 82-84: policy not found error."""
        service_mock = AsyncMock()
        service_mock.get_policy.return_value = None
        
        policy_id = uuid4()
        
        with pytest.raises(HTTPException) as exc_info:
            await get_policy(policy_id, service_mock)
            
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Policy not found."


class TestUpdatePolicy:
    async def test_policy_not_found(self):
        """Test lines 96-97: policy not found during update."""
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        
        service_mock.update_policy.return_value = None
        
        payload = PolicyUpdateRequest(
            resource_id="svc-2",
            created_by="admin",
        )
        
        policy_id = uuid4()
        
        with pytest.raises(HTTPException) as exc_info:
            await update_policy(policy_id, payload, service_mock, gateway_binding_mock, audit_log_mock)
            
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Policy not found."


class TestDeletePolicy:
    async def test_policy_not_found(self):
        """Test lines 119-125: policy not found during delete."""
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        
        service_mock.delete_policy.return_value = None
        
        policy_id = uuid4()
        
        with pytest.raises(HTTPException) as exc_info:
            await delete_policy(policy_id, service_mock, gateway_binding_mock, audit_log_mock)
            
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Policy not found."

    async def test_gateway_delete_failure_warning_logged(self):
        """Test lines 121-125: gateway delete exception handling."""
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        
        # Mock policy object (what service.delete_policy returns)
        from types import SimpleNamespace
        deleted_policy = SimpleNamespace(
            id=uuid4(),
            resource_id="svc-1"
        )
        
        # Set up mocks
        service_mock.delete_policy.return_value = deleted_policy
        gateway_binding_mock.delete_policy.side_effect = Exception("Gateway delete error")
        
        policy_id = deleted_policy.id
        
        # Call with mock logger to verify warning
        with patch("apps.access_control.authz.routes._logger") as mock_logger:
            await delete_policy(policy_id, service_mock, gateway_binding_mock, audit_log_mock)
            
            # Verify warning was logged
            mock_logger.warning.assert_called_once()
            assert "Gateway delete failed after policy deletion" in str(mock_logger.warning.call_args)
            assert str(policy_id) in str(mock_logger.warning.call_args)
            
        # Verify audit log still called despite gateway failure
        audit_log_mock.append_entry.assert_called_once_with(
            actor="system",
            action="policy.deleted",
            resource="svc-1",
            detail={"policy_id": str(policy_id)},
        )
        
        # Verify gateway delete was attempted
        gateway_binding_mock.delete_policy.assert_called_once_with(policy_id)