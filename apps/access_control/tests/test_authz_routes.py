"""Tests for authz/routes.py — covering error paths and gateway sync failures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.access_control.authz.models import (
    PolicyCreateRequest,
    PolicyEvaluationRequest,
    PolicyUpdateRequest,
)
from apps.access_control.authz.routes import (
    create_policy,
    delete_policy,
    evaluate_policy,
    get_policy,
    list_policies,
    update_policy,
)


class TestCreatePolicy:
    async def test_create_policy_requires_admin(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_policy(
                PolicyCreateRequest(
                    subject_type="user",
                    subject_id="alice",
                    resource_id="svc-1",
                    action_pattern="get*",
                    decision="allow",
                ),
                session,
                service_mock,
                gateway_binding_mock,
                audit_log_mock,
                caller,
            )

        assert exc_info.value.status_code == 403

    async def test_create_policy_gateway_failure_rolls_back(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="admin",
            token_type="jwt",
            claims={"sub": "admin", "roles": ["admin"]},
        )
        created = MagicMock(id=uuid4(), resource_id="svc-1")
        created.model_dump.return_value = {"id": str(created.id)}
        service_mock.create_policy.return_value = created
        gateway_binding_mock.sync_policy.side_effect = RuntimeError("gateway down")

        with pytest.raises(HTTPException) as exc_info:
            await create_policy(
                PolicyCreateRequest(
                    subject_type="user",
                    subject_id="alice",
                    resource_id="svc-1",
                    action_pattern="get*",
                    decision="allow",
                ),
                session,
                service_mock,
                gateway_binding_mock,
                audit_log_mock,
                caller,
            )

        assert exc_info.value.status_code == 502
        assert "gateway down" in exc_info.value.detail
        session.rollback.assert_awaited_once()


class TestGetPolicy:
    async def test_policy_not_found(self):
        """Test lines 82-84: policy not found error."""
        service_mock = AsyncMock()
        service_mock.get_policy.return_value = None
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        policy_id = uuid4()

        with pytest.raises(HTTPException) as exc_info:
            await get_policy(policy_id, service_mock, caller)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Policy not found."


class TestUpdatePolicy:
    async def test_policy_not_found(self):
        """Test lines 96-97: policy not found during update."""
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="admin",
            token_type="jwt",
            claims={"sub": "admin", "roles": ["admin"]},
        )

        service_mock.update_policy.return_value = None

        payload = PolicyUpdateRequest(
            resource_id="svc-2",
        )

        policy_id = uuid4()

        with pytest.raises(HTTPException) as exc_info:
            await update_policy(
                policy_id,
                payload,
                session,
                service_mock,
                gateway_binding_mock,
                audit_log_mock,
                caller,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Policy not found."

    async def test_policy_update_gateway_failure_rolls_back(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="admin",
            token_type="jwt",
            claims={"sub": "admin", "roles": ["admin"]},
        )
        updated = MagicMock(resource_id="svc-1")
        updated.model_dump.return_value = {"id": "policy-1"}
        service_mock.update_policy.return_value = updated
        gateway_binding_mock.sync_policy.side_effect = RuntimeError("gateway down")

        with pytest.raises(HTTPException) as exc_info:
            await update_policy(
                uuid4(),
                PolicyUpdateRequest(decision="allow"),
                session,
                service_mock,
                gateway_binding_mock,
                audit_log_mock,
                caller,
            )

        assert exc_info.value.status_code == 502
        assert "gateway down" in exc_info.value.detail
        session.rollback.assert_awaited_once()


class TestDeletePolicy:
    async def test_policy_not_found(self):
        """Test lines 119-125: policy not found during delete."""
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="admin",
            token_type="jwt",
            claims={"sub": "admin", "roles": ["admin"]},
        )

        service_mock.delete_policy.return_value = None

        policy_id = uuid4()

        with pytest.raises(HTTPException) as exc_info:
            await delete_policy(
                policy_id,
                session,
                service_mock,
                gateway_binding_mock,
                audit_log_mock,
                caller,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Policy not found."

    async def test_gateway_delete_failure_rolls_back(self):
        service_mock = AsyncMock()
        session = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="admin",
            token_type="jwt",
            claims={"sub": "admin", "roles": ["admin"]},
        )

        # Mock policy object (what service.delete_policy returns)
        from types import SimpleNamespace

        deleted_policy = SimpleNamespace(id=uuid4(), resource_id="svc-1")

        # Set up mocks
        service_mock.delete_policy.return_value = deleted_policy
        gateway_binding_mock.delete_policy.side_effect = Exception("Gateway delete error")

        policy_id = deleted_policy.id

        with pytest.raises(HTTPException) as exc_info:
            await delete_policy(
                policy_id,
                session,
                service_mock,
                gateway_binding_mock,
                audit_log_mock,
                caller,
            )

        assert exc_info.value.status_code == 502
        assert "Gateway delete error" in exc_info.value.detail
        gateway_binding_mock.delete_policy.assert_called_once_with(policy_id)
        audit_log_mock.append_entry.assert_not_called()
        session.rollback.assert_awaited_once()


class TestListPolicies:
    async def test_list_policies_requires_authenticated_caller(self):
        service_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        await list_policies(service=service_mock, _caller=caller)

        service_mock.list_policies.assert_awaited_once()


class TestEvaluatePolicy:
    async def test_evaluate_policy_requires_authenticated_caller(self):
        service_mock = AsyncMock()
        service_mock.evaluate.return_value = MagicMock()
        audit_log_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        payload = PolicyEvaluationRequest(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action="getItem",
            risk_level="safe",
        )

        await evaluate_policy(payload, service_mock, caller, audit_log_mock)

        service_mock.evaluate.assert_awaited_once_with(payload)
        audit_log_mock.append_entry.assert_awaited_once()

    async def test_evaluate_policy_audits_real_caller_and_resource(self):
        service_mock = AsyncMock()
        matched_policy_id = uuid4()
        result = MagicMock(
            decision="allow",
            matched_policy_id=matched_policy_id,
            reason="Matched policy",
        )
        service_mock.evaluate.return_value = result
        audit_log_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        payload = PolicyEvaluationRequest(
            subject_type="user",
            subject_id="bob",
            resource_id="svc-1",
            action="getItem",
            risk_level="dangerous",
        )

        response = await evaluate_policy(payload, service_mock, caller, audit_log_mock)

        assert response is result
        audit_log_mock.append_entry.assert_awaited_once()
        call_kwargs = audit_log_mock.append_entry.call_args.kwargs
        assert call_kwargs["actor"] == "alice"
        assert call_kwargs["resource"] == "svc-1"
        assert call_kwargs["detail"] == {
            "subject_type": "user",
            "subject_id": "bob",
            "action": "getItem",
            "resource_id": "svc-1",
            "risk_level": "dangerous",
            "decision": "allow",
            "matched_policy_id": str(matched_policy_id),
            "reason": "Matched policy",
        }


# ---------- Additional tests to cover uncovered lines ----------


def _admin_caller() -> TokenPrincipalResponse:
    return TokenPrincipalResponse(
        subject="admin",
        token_type="jwt",
        claims={"sub": "admin", "roles": ["admin"]},
    )


class TestCreatePolicySuccess:
    """Lines 56-63: successful create → gateway sync → audit → commit → return."""

    async def test_create_policy_success(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = _admin_caller()

        created = MagicMock(id=uuid4(), resource_id="svc-1")
        created.model_dump.return_value = {"id": str(created.id)}
        service_mock.create_policy.return_value = created

        payload = PolicyCreateRequest(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="get*",
            decision="allow",
        )

        result = await create_policy(
            payload, session, service_mock, gateway_binding_mock, audit_log_mock, caller
        )

        assert result is created
        service_mock.create_policy.assert_awaited_once()
        request_payload = service_mock.create_policy.call_args.args[0]
        assert request_payload.created_by == caller.subject
        gateway_binding_mock.sync_policy.assert_awaited_once_with(created)
        audit_log_mock.append_entry.assert_awaited_once()
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_create_policy_audit_failure_rolls_back(self):
        """When a local step fails after sync, rollback and preserve the real error."""
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = _admin_caller()

        created = MagicMock(id=uuid4(), resource_id="svc-1")
        created.model_dump.return_value = {"id": str(created.id)}
        service_mock.create_policy.return_value = created
        audit_log_mock.append_entry.side_effect = RuntimeError("audit failed")

        with pytest.raises(RuntimeError, match="audit failed"):
            await create_policy(
                PolicyCreateRequest(
                    subject_type="user",
                    subject_id="alice",
                    resource_id="svc-1",
                    action_pattern="*",
                    decision="allow",
                ),
                session,
                service_mock,
                gateway_binding_mock,
                audit_log_mock,
                caller,
            )

        gateway_binding_mock.reconcile.assert_awaited_once_with(session)
        session.rollback.assert_awaited_once()


class TestUpdatePolicySuccess:
    """Line 99: successful update path — gateway sync + commit."""

    async def test_update_policy_success(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = _admin_caller()

        updated = MagicMock(resource_id="svc-1")
        updated.model_dump.return_value = {"id": "policy-1"}
        service_mock.update_policy.return_value = updated

        policy_id = uuid4()
        payload = PolicyUpdateRequest(decision="deny")

        result = await update_policy(
            policy_id, payload, session, service_mock, gateway_binding_mock, audit_log_mock, caller
        )

        assert result is updated
        request_payload = service_mock.update_policy.call_args.args[1]
        assert request_payload.model_dump(exclude_none=True) == {"decision": "deny"}
        gateway_binding_mock.sync_policy.assert_awaited_once_with(updated)
        audit_log_mock.append_entry.assert_awaited_once()
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_update_policy_audit_failure_rolls_back(self):
        """When a local step fails after sync, rollback and preserve the real error."""
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = _admin_caller()

        updated = MagicMock(resource_id="svc-1")
        updated.model_dump.return_value = {"id": "policy-1"}
        service_mock.update_policy.return_value = updated
        audit_log_mock.append_entry.side_effect = RuntimeError("audit broke")

        with pytest.raises(RuntimeError, match="audit broke"):
            await update_policy(
                uuid4(),
                PolicyUpdateRequest(decision="allow"),
                session,
                service_mock,
                gateway_binding_mock,
                audit_log_mock,
                caller,
            )

        gateway_binding_mock.reconcile.assert_awaited_once_with(session)
        session.rollback.assert_awaited_once()


class TestDeletePolicySuccess:
    """Lines 119-126, 133, 151-158: successful delete → gateway delete → audit → commit → 204."""

    async def test_delete_policy_success(self):
        from types import SimpleNamespace

        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = _admin_caller()

        policy_id = uuid4()
        deleted_policy = SimpleNamespace(id=policy_id, resource_id="svc-1")
        service_mock.delete_policy.return_value = deleted_policy

        result = await delete_policy(
            policy_id, session, service_mock, gateway_binding_mock, audit_log_mock, caller
        )

        assert result is None  # 204 returns None
        gateway_binding_mock.delete_policy.assert_awaited_once_with(policy_id)
        audit_log_mock.append_entry.assert_awaited_once()
        # Verify audit entry details
        call_kwargs = audit_log_mock.append_entry.call_args.kwargs
        assert call_kwargs["action"] == "policy.deleted"
        assert call_kwargs["detail"] == {"policy_id": str(policy_id)}
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_delete_policy_audit_failure_rolls_back(self):
        """When a local step fails after sync, rollback and preserve the real error."""
        from types import SimpleNamespace

        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        audit_log_mock = AsyncMock()
        caller = _admin_caller()

        policy_id = uuid4()
        deleted_policy = SimpleNamespace(id=policy_id, resource_id="svc-1")
        service_mock.delete_policy.return_value = deleted_policy
        audit_log_mock.append_entry.side_effect = RuntimeError("audit exploded")

        with pytest.raises(RuntimeError, match="audit exploded"):
            await delete_policy(
                policy_id, session, service_mock, gateway_binding_mock, audit_log_mock, caller
            )

        gateway_binding_mock.reconcile.assert_awaited_once_with(session)
        session.rollback.assert_awaited_once()
