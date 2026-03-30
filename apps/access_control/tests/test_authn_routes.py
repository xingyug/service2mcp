"""Tests for authn/routes.py — covering error paths and gateway sync failures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.access_control.authn.models import (
    PATCreateRequest,
    TokenPrincipalResponse,
    TokenValidationRequest,
)
from apps.access_control.authn.routes import (
    create_pat,
    get_jwt_settings,
    list_pats,
    revoke_pat,
    validate_token,
)
from apps.access_control.authn.service import AuthenticationError, JWTSettings, UserNotFoundError


@pytest.fixture(autouse=True)
def _mock_audit_log():
    """Patch AuditLogService so tests don't need a real DB for audit entries."""
    from unittest.mock import patch

    with patch("apps.access_control.authn.routes.AuditLogService") as mock_cls:
        mock_cls.return_value = AsyncMock()
        yield mock_cls


class TestGetJwtSettings:
    def test_jwt_settings_not_configured(self):
        """Test line 36: JWT settings not configured."""
        mock_request = MagicMock()
        mock_app = MagicMock()
        mock_request.app = mock_app
        mock_app.state = MagicMock()

        mock_app.state.jwt_settings = None
        mock_app.state.jwt_settings_error = None

        with pytest.raises(HTTPException) as exc_info:
            get_jwt_settings(mock_request)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "JWT settings are not configured."

    def test_returns_configured_settings(self):
        """Test successful JWT settings retrieval."""
        mock_request = MagicMock()
        mock_app = MagicMock()
        mock_request.app = mock_app

        jwt_settings = JWTSettings(secret="test-secret")
        mock_app.state.jwt_settings = jwt_settings

        result = get_jwt_settings(mock_request)

        assert result == jwt_settings


class TestValidateToken:
    async def test_authentication_error_raises_http_exception(self):
        """Test lines 56-57: AuthenticationError converted to HTTP 401."""
        service_mock = AsyncMock()
        service_mock.validate_token.side_effect = AuthenticationError("Invalid token")

        payload = TokenValidationRequest(token="invalid_token")

        with pytest.raises(HTTPException) as exc_info:
            await validate_token(payload, service_mock)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid token"

    async def test_jwt_validation_syncs_existing_user_roles(self):
        service_mock = AsyncMock()
        principal = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice", "roles": ["admin"]},
        )
        service_mock.validate_token.return_value = principal

        result = await validate_token(TokenValidationRequest(token="jwt_token"), service_mock)

        assert result == principal
        service_mock.sync_jwt_user_roles.assert_awaited_once_with(principal)


class TestCreatePat:
    async def test_create_pat_for_other_user_requires_admin(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_pat(
                PATCreateRequest(username="bob", name="CI token"),
                session,
                service_mock,
                gateway_binding_mock,
                caller,
            )

        assert exc_info.value.status_code == 403

    async def test_gateway_sync_failure_rolls_back_pat_creation(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )
        created = MagicMock(id=uuid4(), token="pat_token")
        service_mock.create_pat.return_value = created
        gateway_binding_mock.sync_pat_creation.side_effect = RuntimeError("gateway down")

        with pytest.raises(HTTPException) as exc_info:
            await create_pat(
                PATCreateRequest(username="alice", name="CI token"),
                session,
                service_mock,
                gateway_binding_mock,
                caller,
            )

        assert exc_info.value.status_code == 502
        assert "gateway down" in exc_info.value.detail
        service_mock.create_pat.assert_awaited_once_with(
            username="alice",
            name="CI token",
            commit=False,
        )
        session.rollback.assert_awaited_once()

    async def test_audit_failure_rolls_back_pat_creation_without_gateway_502(
        self, _mock_audit_log
    ):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )
        created = MagicMock(id=uuid4(), token="pat_token")
        service_mock.create_pat.return_value = created
        _mock_audit_log.return_value.append_entry.side_effect = RuntimeError("audit broke")

        with pytest.raises(RuntimeError, match="audit broke"):
            await create_pat(
                PATCreateRequest(username="alice", name="CI token"),
                session,
                service_mock,
                gateway_binding_mock,
                caller,
            )

        gateway_binding_mock.sync_pat_creation.assert_awaited_once_with(created, created.token)
        gateway_binding_mock.reconcile.assert_awaited_once_with(session)
        session.rollback.assert_awaited_once()

    async def test_unknown_user_returns_not_found(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        service_mock.create_pat.side_effect = UserNotFoundError("User 'ghost' not found.")
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="ghost",
            token_type="jwt",
            claims={"sub": "ghost"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_pat(
                PATCreateRequest(username="ghost", name="CI token"),
                session,
                service_mock,
                gateway_binding_mock,
                caller,
            )

        assert exc_info.value.status_code == 404
        assert "ghost" in exc_info.value.detail
        gateway_binding_mock.sync_pat_creation.assert_not_awaited()
        session.rollback.assert_awaited_once()

    async def test_list_pats_for_other_user_requires_admin(self):
        service_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await list_pats("bob", service_mock, caller)

        assert exc_info.value.status_code == 403


class TestRevokePat:
    async def test_invalid_pat_id_format(self):
        """Test lines 96-100: invalid PAT ID format."""
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await revoke_pat("invalid-uuid", session, service_mock, gateway_binding_mock, caller)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid PAT ID."

    async def test_pat_not_found(self):
        """Test lines 103-104: PAT not found during revocation."""
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )

        service_mock.get_pat.return_value = None

        valid_pat_id = str(uuid4())

        with pytest.raises(HTTPException) as exc_info:
            await revoke_pat(valid_pat_id, session, service_mock, gateway_binding_mock, caller)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "PAT not found."

    async def test_revoke_pat_for_other_user_requires_admin(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )
        pat_id = str(uuid4())
        service_mock.get_pat.return_value = MagicMock(username="bob")

        with pytest.raises(HTTPException) as exc_info:
            await revoke_pat(pat_id, session, service_mock, gateway_binding_mock, caller)

        assert exc_info.value.status_code == 403

    async def test_gateway_sync_failure_rolls_back_pat_revocation(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )
        pat_id = str(uuid4())
        service_mock.get_pat.return_value = MagicMock(username="alice")
        revoked = MagicMock(id=pat_id)
        service_mock.revoke_pat.return_value = revoked
        gateway_binding_mock.sync_pat_revocation.side_effect = RuntimeError("gateway down")

        with pytest.raises(HTTPException) as exc_info:
            await revoke_pat(pat_id, session, service_mock, gateway_binding_mock, caller)

        assert exc_info.value.status_code == 502
        assert "gateway down" in exc_info.value.detail
        service_mock.revoke_pat.assert_awaited_once()
        session.rollback.assert_awaited_once()

    async def test_audit_failure_rolls_back_pat_revocation_without_gateway_502(
        self, _mock_audit_log
    ):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )
        pat_id = str(uuid4())
        service_mock.get_pat.return_value = MagicMock(username="alice")
        revoked = MagicMock(id=pat_id)
        service_mock.revoke_pat.return_value = revoked
        _mock_audit_log.return_value.append_entry.side_effect = RuntimeError("audit broke")

        with pytest.raises(RuntimeError, match="audit broke"):
            await revoke_pat(pat_id, session, service_mock, gateway_binding_mock, caller)

        gateway_binding_mock.sync_pat_revocation.assert_awaited_once_with(revoked.id)
        gateway_binding_mock.reconcile.assert_awaited_once_with(session)
        session.rollback.assert_awaited_once()


class TestCreatePatHappyPath:
    async def test_create_pat_success_commits_and_returns(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )
        created = MagicMock(id=uuid4(), token="pat_abc123")
        service_mock.create_pat.return_value = created

        result = await create_pat(
            PATCreateRequest(username="alice", name="CI token"),
            session,
            service_mock,
            gateway_binding_mock,
            caller,
        )

        assert result is created
        service_mock.sync_jwt_user_roles.assert_awaited_once_with(caller, commit=False)
        service_mock.create_pat.assert_awaited_once_with(
            username="alice",
            name="CI token",
            commit=False,
        )
        gateway_binding_mock.sync_pat_creation.assert_awaited_once_with(
            created, created.token,
        )
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()


class TestRevokePatHappyPath:
    async def test_revoke_pat_success_commits_and_returns(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )
        pat_id = uuid4()
        existing = MagicMock(username="alice")
        revoked = MagicMock(id=pat_id)
        service_mock.get_pat.return_value = existing
        service_mock.revoke_pat.return_value = revoked

        result = await revoke_pat(
            str(pat_id), session, service_mock, gateway_binding_mock, caller,
        )

        assert result is revoked
        gateway_binding_mock.sync_pat_revocation.assert_awaited_once_with(pat_id)
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_revoke_pat_returns_none_after_revoke(self):
        session = AsyncMock()
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()
        caller = TokenPrincipalResponse(
            subject="alice",
            token_type="jwt",
            claims={"sub": "alice"},
        )
        pat_id = uuid4()
        service_mock.get_pat.return_value = MagicMock(username="alice")
        service_mock.revoke_pat.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await revoke_pat(
                str(pat_id), session, service_mock, gateway_binding_mock, caller,
            )

        assert exc_info.value.status_code == 404
