"""Simple integration tests to hit uncovered lines in authn service."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.access_control.authn.service import (
    AuthenticationError,
    AuthnService,
    JWTConfigurationError,
    JWTSettings,
    UserNotFoundError,
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
            name="test-pat",
        )
        mock_user = SimpleNamespace(username="alice")

        mock_result = MagicMock()
        mock_result.first.return_value = (mock_pat, mock_user)
        session.execute.return_value = mock_result

        svc = AuthnService(session, jwt_settings=JWTSettings(secret="test"))

        with pytest.raises(AuthenticationError, match="PAT has been revoked"):
            await svc._validate_pat("pat_some_token")

    async def test_validate_pat_inactive_user_error(self):
        """Disabled users must not authenticate via PATs."""
        from types import SimpleNamespace

        session = AsyncMock()

        mock_pat = SimpleNamespace(
            id=uuid4(),
            revoked_at=None,
            name="test-pat",
        )
        mock_user = SimpleNamespace(username="alice", is_active=False)

        mock_result = MagicMock()
        mock_result.first.return_value = (mock_pat, mock_user)
        session.execute.return_value = mock_result

        svc = AuthnService(session, jwt_settings=JWTSettings(secret="test"))

        with pytest.raises(AuthenticationError, match="PAT owner is inactive"):
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

    async def test_get_existing_user_returns_existing_without_mutation(self):
        """PAT creation should not mutate stored user profiles."""
        from types import SimpleNamespace

        session = AsyncMock()

        existing_user = SimpleNamespace(id=uuid4(), username="alice", email="old@example.com")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        session.execute.return_value = mock_result

        svc = AuthnService(session, jwt_settings=JWTSettings(secret="test"))

        result = await svc._get_existing_user(username="alice")

        assert existing_user.email == "old@example.com"
        session.commit.assert_not_called()
        session.refresh.assert_not_called()
        assert result == existing_user

    async def test_get_existing_user_raises_when_missing(self):
        """PAT creation should fail for unknown users."""
        session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        svc = AuthnService(session, jwt_settings=JWTSettings(secret="test"))

        with pytest.raises(UserNotFoundError, match="bob"):
            await svc._get_existing_user(username="bob")

        session.add.assert_not_called()
        session.commit.assert_not_called()
        session.refresh.assert_not_called()

    def test_load_jwt_settings_non_dev_environment_no_secret_raises_error(self):
        """Missing JWT secret must raise instead of falling back."""
        with patch.dict(os.environ, {"ENV": "production"}, clear=True):
            with pytest.raises(
                JWTConfigurationError,
                match="ACCESS_CONTROL_JWT_SECRET must be configured",
            ):
                load_jwt_settings()

    def test_load_jwt_settings_dev_environment_raises_error(self):
        """Dev must also fail closed when JWT secret is missing."""
        with patch.dict(os.environ, {"ENV": "dev"}, clear=True):
            with pytest.raises(
                JWTConfigurationError,
                match="ACCESS_CONTROL_JWT_SECRET must be configured",
            ):
                load_jwt_settings()
