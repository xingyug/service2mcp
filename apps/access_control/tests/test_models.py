"""Unit tests for access control models (authn, authz, audit)."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.access_control.audit.models import (
    AuditLogEntryResponse,
    AuditLogListResponse,
)
from apps.access_control.authn.models import (
    PATCreateRequest,
    PATCreateResponse,
    PATListResponse,
    PATResponse,
    TokenPrincipalResponse,
    TokenValidationRequest,
)
from apps.access_control.authz.models import (
    PolicyCreateRequest,
    PolicyEvaluationRequest,
    PolicyEvaluationResponse,
    PolicyListResponse,
    PolicyResponse,
    PolicyUpdateRequest,
)
from libs.ir.models import RiskLevel

# --- authn models ---


class TestTokenValidationRequest:
    def test_valid(self) -> None:
        req = TokenValidationRequest(token="my-jwt-token")
        assert req.token == "my-jwt-token"

    def test_empty_token_rejected(self) -> None:
        with pytest.raises(ValidationError, match="token"):
            TokenValidationRequest(token="")


class TestTokenPrincipalResponse:
    def test_construction(self) -> None:
        resp = TokenPrincipalResponse(
            subject="user1",
            token_type="jwt",
            claims={"role": "admin"},
        )
        assert resp.subject == "user1"


class TestPATCreateRequest:
    def test_valid(self) -> None:
        req = PATCreateRequest(username="alice", name="dev-token")
        assert req.email is None

    def test_empty_username_rejected(self) -> None:
        with pytest.raises(ValidationError, match="username"):
            PATCreateRequest(username="", name="token")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            PATCreateRequest(username="alice", name="")


class TestPATResponse:
    def test_construction(self) -> None:
        now = datetime.utcnow()
        resp = PATResponse(
            id=uuid4(),
            username="alice",
            name="dev-token",
            created_at=now,
        )
        assert resp.revoked_at is None


class TestPATCreateResponse:
    def test_includes_token(self) -> None:
        now = datetime.utcnow()
        resp = PATCreateResponse(
            id=uuid4(),
            username="alice",
            name="dev-token",
            created_at=now,
            token="pat-plaintext-secret",
        )
        assert resp.token == "pat-plaintext-secret"


class TestPATListResponse:
    def test_empty_list(self) -> None:
        resp = PATListResponse(items=[])
        assert resp.items == []


# --- authz models ---


class TestPolicyCreateRequest:
    def test_valid(self) -> None:
        req = PolicyCreateRequest(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="*",
            decision="allow",
        )
        assert req.risk_threshold == RiskLevel.safe

    def test_invalid_decision_rejected(self) -> None:
        with pytest.raises(ValidationError, match="decision"):
            PolicyCreateRequest(
                subject_type="user",
                subject_id="alice",
                resource_id="svc-1",
                action_pattern="*",
                decision="invalid",
            )

    def test_empty_subject_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="subject_type"):
            PolicyCreateRequest(
                subject_type="",
                subject_id="alice",
                resource_id="svc-1",
                action_pattern="*",
                decision="allow",
            )

    def test_require_approval_decision(self) -> None:
        req = PolicyCreateRequest(
            subject_type="role",
            subject_id="reviewer",
            resource_id="svc-2",
            action_pattern="delete_*",
            risk_threshold=RiskLevel.dangerous,
            decision="require_approval",
        )
        assert req.decision == "require_approval"


class TestPolicyUpdateRequest:
    def test_partial_update(self) -> None:
        req = PolicyUpdateRequest(decision="deny")
        assert req.decision == "deny"
        assert req.resource_id is None

    def test_all_none_allowed(self) -> None:
        # PolicyUpdateRequest does not enforce "at least one field"
        req = PolicyUpdateRequest()
        assert req.decision is None


class TestPolicyResponse:
    def test_construction(self) -> None:
        now = datetime.utcnow()
        resp = PolicyResponse(
            id=uuid4(),
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action_pattern="*",
            risk_threshold=RiskLevel.safe,
            decision="allow",
            created_at=now,
        )
        assert resp.created_by is None


class TestPolicyListResponse:
    def test_empty(self) -> None:
        resp = PolicyListResponse(items=[])
        assert resp.items == []


class TestPolicyEvaluationRequest:
    def test_valid(self) -> None:
        req = PolicyEvaluationRequest(
            subject_type="user",
            subject_id="alice",
            resource_id="svc-1",
            action="read",
            risk_level=RiskLevel.safe,
        )
        assert req.risk_level == RiskLevel.safe

    def test_empty_action_rejected(self) -> None:
        with pytest.raises(ValidationError, match="action"):
            PolicyEvaluationRequest(
                subject_type="user",
                subject_id="alice",
                resource_id="svc-1",
                action="",
                risk_level=RiskLevel.safe,
            )


class TestPolicyEvaluationResponse:
    def test_construction(self) -> None:
        resp = PolicyEvaluationResponse(
            decision="allow",
            matched_policy_id=uuid4(),
            reason="Matched policy for user",
        )
        assert resp.decision == "allow"

    def test_no_matched_policy(self) -> None:
        resp = PolicyEvaluationResponse(
            decision="deny",
            reason="No matching policy",
        )
        assert resp.matched_policy_id is None


# --- audit models ---


class TestAuditLogEntryResponse:
    def test_construction(self) -> None:
        now = datetime.utcnow()
        entry = AuditLogEntryResponse(
            id=uuid4(),
            actor="alice",
            action="policy.create",
            resource="svc-1",
            timestamp=now,
        )
        assert entry.detail is None

    def test_with_detail(self) -> None:
        now = datetime.utcnow()
        entry = AuditLogEntryResponse(
            id=uuid4(),
            actor="system",
            action="consumer.sync",
            detail={"consumer_id": "c1"},
            timestamp=now,
        )
        assert entry.detail == {"consumer_id": "c1"}


class TestAuditLogListResponse:
    def test_empty(self) -> None:
        resp = AuditLogListResponse(items=[])
        assert resp.items == []
