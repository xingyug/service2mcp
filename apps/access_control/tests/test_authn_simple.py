"""Simple integration tests to hit uncovered lines in authn service."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, UTC
from uuid import uuid4

from apps.access_control.authn.service import (
    AuthnService,
    JWTSettings,
    AuthenticationError,
    load_jwt_settings,
)


class TestAuthnServiceUncoveredLines:
    async def test_validate_pat_revoked_error(self):
        """Test lines 135-137: _validate_pat raises error for revoked PAT."""
        from types import SimpleNamespace
        
        session = AsyncMock()
        
        # Mock revoked PAT
        mock_pat = SimpleNamespace(
            id=uuid4(),
            revoked_at=datetime.now(UTC),  # Revoked
            name="test-pat"
        )
        mock_user = SimpleNamespace(username="alice")
        
        mock_result = MagicMock()
        mock_result.first.return_value = (mock_pat, mock_user)
        session.execute.return_value = mock_result
        
        svc = AuthnService(session, jwt_settings=JWTSettings(secret="test"))
        
        with pytest.raises(AuthenticationError, match="PAT has been revoked"):
            await svc._validate_pat("pat_some_token")

    async def test_revoke_pat_not_found(self):
        """Test lines 106-108: revoke_pat returns None when PAT not found."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        session.execute.return_value = mock_result
        
        svc = AuthnService(session, jwt_settings=JWTSettings(secret="test"))
        
        result = await svc.revoke_pat(uuid4())
        assert result is None

    async def test_get_or_create_user_updates_email(self):
        """Test lines 209-221: _get_or_create_user updates email when different."""
        from types import SimpleNamespace
        
        session = AsyncMock()
        
        # Mock existing user with different email
        existing_user = SimpleNamespace(
            id=uuid4(),
            username="alice",
            email="old@example.com"
        )
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        session.execute.return_value = mock_result
        
        svc = AuthnService(session, jwt_settings=JWTSettings(secret="test"))
        
        result = await svc._get_or_create_user(username="alice", email="new@example.com")
        
        # Should update email and commit/refresh
        assert existing_user.email == "new@example.com"
        session.commit.assert_called_once()
        session.refresh.assert_called_once()
        assert result == existing_user

    async def test_get_or_create_user_creates_new_user(self):
        """Test lines 217-221: _get_or_create_user creates new user."""
        session = AsyncMock()
        
        # Mock no existing user
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        
        svc = AuthnService(session, jwt_settings=JWTSettings(secret="test"))
        
        await svc._get_or_create_user(username="bob", email="bob@example.com")
        
        # Should add new user and commit/refresh
        session.add.assert_called_once()
        session.commit.assert_called_once()
        session.refresh.assert_called_once()

    def test_load_jwt_settings_non_dev_environment_no_secret_raises_error(self):
        """Test lines 231-233: load_jwt_settings raises error in non-dev env without secret."""
        with patch.dict(os.environ, {"ENV": "production"}, clear=True):
            with pytest.raises(RuntimeError, match="ACCESS_CONTROL_JWT_SECRET must be set"):
                load_jwt_settings()

    def test_load_jwt_settings_dev_environment_uses_default(self):
        """Test lines 229-234: load_jwt_settings uses dev secret in dev environment."""
        with patch.dict(os.environ, {"ENV": "dev"}, clear=True):
            settings = load_jwt_settings()
            assert settings.secret == "dev-secret"