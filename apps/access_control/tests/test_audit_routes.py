"""Tests for audit/routes.py — covering authentication and error paths."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.access_control.audit.routes import get_audit_log, list_audit_logs
from apps.access_control.authn.service import AuthenticationError
from apps.access_control.security import require_authenticated_caller, require_sse_caller

# The correct patch target: AuthnService is imported inside security.py
_AUTHN_PATCH = "apps.access_control.security.AuthnService"


class TestRequireAuthenticatedCaller:
    async def test_no_authorization_header(self):
        """Missing Authorization header returns 401."""
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Authorization header is required."

    async def test_empty_authorization_header(self):
        """Empty Authorization header returns 401."""
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": ""}
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Authorization header is required."

    async def test_no_bearer_token(self):
        """Non-Bearer Authorization header is rejected before token validation."""
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Basic dXNlcjpwYXNz"}
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Bearer token is required."

    async def test_empty_bearer_token(self):
        """'Bearer ' with empty token value returns 401."""
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer "}
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Bearer token is required."

    async def test_jwt_settings_not_configured(self):
        """Missing jwt_settings returns 503."""
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer valid_token"}
        mock_request.app.state = MagicMock()
        mock_request.app.state.jwt_settings = None
        mock_request.app.state.jwt_settings_error = None
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "JWT settings are not configured."

    async def test_authentication_error(self):
        """AuthenticationError is converted to HTTP 401."""
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer invalid_token"}
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")
        mock_session = AsyncMock()

        with patch(_AUTHN_PATCH) as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.side_effect = AuthenticationError("Invalid token")
            mock_authn_service.return_value = mock_service

            with pytest.raises(HTTPException) as exc_info:
                await require_authenticated_caller(mock_request, mock_session)

            assert exc_info.value.status_code == 401
            assert exc_info.value.detail == "Invalid token"

    async def test_successful_authentication(self):
        """Successful authentication returns the principal."""
        from apps.access_control.authn.models import TokenPrincipalResponse
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer valid_token"}
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")
        mock_session = AsyncMock()

        expected_response = TokenPrincipalResponse(
            subject="alice", token_type="jwt", claims={"sub": "alice"}
        )

        with patch(_AUTHN_PATCH) as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.return_value = expected_response
            mock_authn_service.return_value = mock_service

            result = await require_authenticated_caller(mock_request, mock_session)

            assert result == expected_response
            mock_service.validate_token.assert_called_once_with("valid_token")

    async def test_raw_authorization_token_without_bearer_is_rejected(self):
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "valid_token"}
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Bearer token is required."

    async def test_lowercase_bearer_scheme_is_accepted(self):
        from apps.access_control.authn.models import TokenPrincipalResponse
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "bearer valid_token"}
        mock_session = AsyncMock()
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")

        expected_response = TokenPrincipalResponse(
            subject="alice", token_type="jwt", claims={"sub": "alice"}
        )

        with patch(_AUTHN_PATCH) as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.return_value = expected_response
            mock_authn_service.return_value = mock_service

            result = await require_authenticated_caller(mock_request, mock_session)

        assert result == expected_response
        mock_service.validate_token.assert_called_once_with("valid_token")

    async def test_sse_caller_accepts_authorization_header(self):
        from apps.access_control.authn.models import TokenPrincipalResponse
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer valid_token"}
        mock_request.query_params = {}
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")
        mock_session = AsyncMock()

        expected_response = TokenPrincipalResponse(
            subject="alice", token_type="jwt", claims={"sub": "alice"}
        )

        with patch(_AUTHN_PATCH) as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.return_value = expected_response
            mock_authn_service.return_value = mock_service

            result = await require_sse_caller(mock_request, mock_session)

        assert result == expected_response
        mock_service.validate_token.assert_called_once_with("valid_token")

    async def test_sse_caller_accepts_query_param_token(self):
        from apps.access_control.authn.models import TokenPrincipalResponse
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.query_params = {"token": "query-token"}
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")
        mock_session = AsyncMock()

        expected_response = TokenPrincipalResponse(
            subject="alice", token_type="jwt", claims={"sub": "alice"}
        )

        with patch(_AUTHN_PATCH) as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.return_value = expected_response
            mock_authn_service.return_value = mock_service

            result = await require_sse_caller(mock_request, mock_session)

        assert result == expected_response
        mock_service.validate_token.assert_called_once_with("query-token")


class TestListAuditLogs:
    async def test_list_audit_logs_with_filters(self):
        """Test audit log listing with all filters."""
        from apps.access_control.audit.models import AuditLogEntryResponse, AuditLogListResponse
        from apps.access_control.authn.models import TokenPrincipalResponse

        service_mock = AsyncMock()
        caller_mock = TokenPrincipalResponse(
            subject="admin", token_type="jwt", claims={"sub": "admin"}
        )

        # Mock audit entries
        expected_entries = [
            AuditLogEntryResponse(
                id=uuid4(),  # Use proper UUID
                actor="admin",
                action="policy.created",
                resource="svc-1",
                detail={"policy_id": "123"},
                timestamp=datetime.now(),
            )
        ]

        service_mock.list_entries.return_value = expected_entries

        result = await list_audit_logs(
            actor="admin",
            action="policy.created",
            resource="svc-1",
            start_at=datetime(2023, 1, 1),
            end_at=datetime(2023, 12, 31),
            service=service_mock,
            _caller=caller_mock,
        )

        assert isinstance(result, AuditLogListResponse)
        assert result.items == expected_entries

        # Verify service was called with all filters
        service_mock.list_entries.assert_called_once_with(
            actor="admin",
            action="policy.created",
            resource="svc-1",
            start_at=datetime(2023, 1, 1),
            end_at=datetime(2023, 12, 31),
            limit=1000,
        )

    async def test_list_audit_logs_can_request_full_export_dataset(self):
        from apps.access_control.authn.models import TokenPrincipalResponse

        service_mock = AsyncMock()
        caller_mock = TokenPrincipalResponse(
            subject="admin", token_type="jwt", claims={"sub": "admin"}
        )
        service_mock.list_entries.return_value = []

        await list_audit_logs(
            include_all=True,
            service=service_mock,
            _caller=caller_mock,
        )

        service_mock.list_entries.assert_called_once_with(
            actor=None,
            action=None,
            resource=None,
            start_at=None,
            end_at=None,
            limit=None,
        )


class TestGetAuditLog:
    async def test_returns_entry_when_found(self):
        from apps.access_control.audit.models import AuditLogEntryResponse
        from apps.access_control.authn.models import TokenPrincipalResponse

        entry_id = uuid4()
        service_mock = AsyncMock()
        service_mock.get_entry.return_value = AuditLogEntryResponse(
            id=entry_id,
            actor="admin",
            action="policy.created",
            resource="svc-1",
            detail={"policy_id": "123"},
            timestamp=datetime.now(),
        )
        caller_mock = TokenPrincipalResponse(
            subject="admin", token_type="jwt", claims={"sub": "admin"}
        )

        result = await get_audit_log(
            entry_id=entry_id,
            service=service_mock,
            _caller=caller_mock,
        )

        assert result.id == entry_id
        service_mock.get_entry.assert_called_once_with(entry_id)

    async def test_raises_404_when_missing(self):
        from apps.access_control.authn.models import TokenPrincipalResponse

        entry_id = uuid4()
        service_mock = AsyncMock()
        service_mock.get_entry.return_value = None
        caller_mock = TokenPrincipalResponse(
            subject="admin", token_type="jwt", claims={"sub": "admin"}
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_audit_log(
                entry_id=entry_id,
                service=service_mock,
                _caller=caller_mock,
            )

        assert exc_info.value.status_code == 404
        assert str(entry_id) in exc_info.value.detail
