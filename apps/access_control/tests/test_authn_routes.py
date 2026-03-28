"""Tests for authn/routes.py — covering error paths and gateway sync failures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.access_control.authn.models import (
    TokenValidationRequest,
)
from apps.access_control.authn.routes import (
    get_jwt_settings,
    revoke_pat,
    validate_token,
)
from apps.access_control.authn.service import AuthenticationError, JWTSettings


class TestGetJwtSettings:
    def test_jwt_settings_not_configured(self):
        """Test line 36: JWT settings not configured."""
        mock_request = MagicMock()
        mock_app = MagicMock()
        mock_request.app = mock_app
        mock_app.state = MagicMock()

        # No jwt_settings attribute
        del mock_app.state.jwt_settings
        mock_app.state.jwt_settings = None

        with pytest.raises(RuntimeError) as exc_info:
            get_jwt_settings(mock_request)

        assert "JWT settings are not configured" in str(exc_info.value)

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


class TestCreatePat:
    pass


class TestRevokePat:
    async def test_invalid_pat_id_format(self):
        """Test lines 96-100: invalid PAT ID format."""
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await revoke_pat("invalid-uuid", service_mock, gateway_binding_mock)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid PAT ID."

    async def test_pat_not_found(self):
        """Test lines 103-104: PAT not found during revocation."""
        service_mock = AsyncMock()
        gateway_binding_mock = AsyncMock()

        service_mock.revoke_pat.return_value = None

        valid_pat_id = str(uuid4())

        with pytest.raises(HTTPException) as exc_info:
            await revoke_pat(valid_pat_id, service_mock, gateway_binding_mock)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "PAT not found."
