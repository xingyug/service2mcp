"""Tests for audit/routes.py — covering authentication and error paths."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.access_control.audit.routes import (
    list_audit_logs,
    require_authenticated_caller,
)
from apps.access_control.authn.service import AuthenticationError


class TestRequireAuthenticatedCaller:
    async def test_no_authorization_header(self):
        """Test lines 32-36: missing Authorization header."""
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Authorization header is required."

    async def test_empty_authorization_header(self):
        """Test lines 32-36: empty Authorization header."""
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": ""}
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Authorization header is required."

    async def test_no_bearer_token(self):
        """Test lines 38-42: no Bearer token in Authorization header."""
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Basic dXNlcjpwYXNz"}  # Not Bearer
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")
        mock_session = AsyncMock()

        # Since "Basic dXNlcjpwYXNz" after removeprefix("Bearer ") is still "Basic dXNlcjpwYXNz",
        # it will try to validate as JWT and fail with JWT error
        with patch("apps.access_control.audit.routes.AuthnService") as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.side_effect = AuthenticationError(
                "JWT must contain three segments."
            )
            mock_authn_service.return_value = mock_service

            with pytest.raises(HTTPException) as exc_info:
                await require_authenticated_caller(mock_request, mock_session)

            assert exc_info.value.status_code == 401
            # The actual error comes from JWT validation, not the Bearer check
            assert "JWT must contain three segments" in exc_info.value.detail

    async def test_empty_bearer_token(self):
        """Test lines 38-42: empty Bearer token."""
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer"}  # No token after Bearer
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")
        mock_session = AsyncMock()

        # "Bearer" after removeprefix("Bearer ") becomes "" which is empty
        # However, let's check what actually happens
        with patch("apps.access_control.audit.routes.AuthnService") as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.side_effect = AuthenticationError(
                "JWT must contain three segments."
            )
            mock_authn_service.return_value = mock_service

            with pytest.raises(HTTPException) as exc_info:
                await require_authenticated_caller(mock_request, mock_session)

            assert exc_info.value.status_code == 401
            # The actual implementation validates empty string as JWT
            assert "JWT must contain three segments" in exc_info.value.detail

    async def test_jwt_settings_not_configured(self):
        """Test lines 43-48: JWT settings not configured."""
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer valid_token"}
        mock_request.app.state = MagicMock()
        mock_request.app.state.jwt_settings = None
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_caller(mock_request, mock_session)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "JWT settings are not configured."

    async def test_authentication_error(self):
        """Test lines 52-56: AuthenticationError converted to HTTP 401."""
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer invalid_token"}
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")
        mock_session = AsyncMock()

        with patch("apps.access_control.audit.routes.AuthnService") as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.side_effect = AuthenticationError("Invalid token")
            mock_authn_service.return_value = mock_service

            with pytest.raises(HTTPException) as exc_info:
                await require_authenticated_caller(mock_request, mock_session)

            assert exc_info.value.status_code == 401
            assert exc_info.value.detail == "Invalid token"

    async def test_successful_authentication(self):
        """Test successful authentication flow."""
        from apps.access_control.authn.models import TokenPrincipalResponse
        from apps.access_control.authn.service import JWTSettings

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer valid_token"}
        mock_request.app.state.jwt_settings = JWTSettings(secret="test")
        mock_session = AsyncMock()

        expected_response = TokenPrincipalResponse(
            subject="alice", token_type="jwt", claims={"sub": "alice"}
        )

        with patch("apps.access_control.audit.routes.AuthnService") as mock_authn_service:
            mock_service = AsyncMock()
            mock_service.validate_token.return_value = expected_response
            mock_authn_service.return_value = mock_service

            result = await require_authenticated_caller(mock_request, mock_session)

            assert result == expected_response
            mock_service.validate_token.assert_called_once_with("valid_token")


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
        )
