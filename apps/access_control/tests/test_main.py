"""Tests for main.py — covering error paths and lifespan events."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from apps.access_control.authn.service import JWTConfigurationError, JWTSettings
from apps.access_control.main import (
    app_lifespan,
    create_app,
)


class TestAppLifespan:
    async def test_lifespan_disposes_resources(self):
        """Test lines 31-33: app_lifespan disposes gateway binding and database."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch(
                "apps.access_control.main.dispose_gateway_binding_service"
            ) as mock_dispose_gateway,
            patch("apps.access_control.main.dispose_database") as mock_dispose_db,
        ):
            # Test the async context manager
            async with app_lifespan(mock_app):
                pass  # Yield happens here

            # Verify cleanup was called
            mock_dispose_gateway.assert_called_once_with(mock_app.state)
            mock_dispose_db.assert_called_once_with(mock_app)


class TestCreateApp:
    def test_creates_app_with_defaults(self):
        """Test successful app creation with default settings."""
        with (
            patch("apps.access_control.main.load_jwt_settings") as mock_load_jwt,
            patch("apps.access_control.main.configure_database") as mock_config_db,
            patch(
                "apps.access_control.main.configure_gateway_binding_service"
            ) as mock_config_gateway,
        ):
            mock_jwt_settings = MagicMock()
            mock_load_jwt.return_value = mock_jwt_settings

            result = create_app()

            assert isinstance(result, FastAPI)
            assert result.title == "Access Control Service"
            assert result.version == "0.1.0"

            # Verify configuration was called
            mock_config_db.assert_called_once()
            mock_config_gateway.assert_called_once()
            assert result.state.jwt_settings == mock_jwt_settings
            assert result.state.jwt_settings_error is None

    def test_creates_app_with_custom_settings(self):
        """Test app creation with custom settings."""

        from apps.access_control.authn.service import JWTSettings

        custom_jwt = JWTSettings(secret="custom-secret")
        custom_session_factory = MagicMock()
        custom_client = MagicMock()

        with (
            patch("apps.access_control.main.configure_database") as mock_config_db,
            patch(
                "apps.access_control.main.configure_gateway_binding_service"
            ) as mock_config_gateway,
        ):
            result = create_app(
                database_url="postgresql://custom:url",
                session_factory=custom_session_factory,
                jwt_settings=custom_jwt,
                gateway_admin_client=custom_client,
            )

            assert result.state.jwt_settings == custom_jwt
            assert result.state.jwt_settings_error is None
            mock_config_db.assert_called_once_with(
                result,
                database_url="postgresql://custom:url",
                session_factory=custom_session_factory,
            )
            mock_config_gateway.assert_called_once_with(result.state, client=custom_client)

    def test_records_jwt_configuration_error_without_crashing(self):
        with (
            patch(
                "apps.access_control.main.load_jwt_settings",
                side_effect=JWTConfigurationError("ACCESS_CONTROL_JWT_SECRET must be configured."),
            ),
            patch("apps.access_control.main.configure_database") as mock_config_db,
            patch(
                "apps.access_control.main.configure_gateway_binding_service"
            ) as mock_config_gateway,
        ):
            result = create_app()

        assert isinstance(result, FastAPI)
        assert result.state.jwt_settings is None
        assert result.state.jwt_settings_error == "ACCESS_CONTROL_JWT_SECRET must be configured."
        mock_config_db.assert_called_once()
        mock_config_gateway.assert_called_once()


class TestHealthEndpoints:
    async def test_readyz_database_success(self):
        """Test lines 62-64: readyz endpoint with successful database check."""
        from apps.access_control.main import create_app
        from apps.access_control.gateway_binding.client import InMemoryAPISIXAdminClient

        app_instance = create_app(
            gateway_admin_client=InMemoryAPISIXAdminClient(),
            jwt_settings=JWTSettings(secret="test-secret"),
        )

        # Mock successful database session
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        # Get the readyz endpoint function
        for route in app_instance.routes:
            if hasattr(route, "path") and route.path == "/readyz" and hasattr(route, "endpoint"):
                result = await route.endpoint(session=mock_session)
                assert result == {"status": "ok"}
                mock_session.execute.assert_called_once()
                break

    async def test_readyz_database_failure(self):
        """Test lines 65-67: readyz endpoint with database failure."""
        from apps.access_control.main import create_app
        from apps.access_control.gateway_binding.client import InMemoryAPISIXAdminClient

        app_instance = create_app(
            gateway_admin_client=InMemoryAPISIXAdminClient(),
            jwt_settings=JWTSettings(secret="test-secret"),
        )

        # Mock failing database session
        mock_session = AsyncMock()
        mock_session.execute.side_effect = Exception("Database connection failed")

        with patch("apps.access_control.main._logger") as mock_logger:
            # Get the readyz endpoint function
            for route in app_instance.routes:
                if (
                    hasattr(route, "path")
                    and route.path == "/readyz"
                    and hasattr(route, "endpoint")
                ):
                    result = await route.endpoint(session=mock_session)
                    assert isinstance(result, JSONResponse)
                    assert result.status_code == 503
                    assert result.body == b'{"status":"not_ready"}'

                    # Verify warning was logged
                    mock_logger.warning.assert_called_once()
                    assert "Readiness check failed" in str(mock_logger.warning.call_args)
                    break

    async def test_readyz_reports_gateway_binding_configuration_error(self):
        from apps.access_control.main import create_app

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GATEWAY_ADMIN_URL", None)
            app_instance = create_app(jwt_settings=JWTSettings(secret="test-secret"))
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        with patch("apps.access_control.main._logger") as mock_logger:
            for route in app_instance.routes:
                if hasattr(route, "path") and route.path == "/readyz" and hasattr(route, "endpoint"):
                    result = await route.endpoint(session=mock_session)
                    assert isinstance(result, JSONResponse)
                    assert result.status_code == 503
                    assert b"GATEWAY_ADMIN_URL must be configured" in result.body
                    mock_logger.warning.assert_called_once()
                    break

    async def test_readyz_reports_jwt_configuration_error(self):
        app_instance = create_app(
            gateway_admin_client=MagicMock(),
        )
        app_instance.state.jwt_settings = None
        app_instance.state.jwt_settings_error = "ACCESS_CONTROL_JWT_SECRET must be configured."
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        with patch("apps.access_control.main._logger") as mock_logger:
            for route in app_instance.routes:
                if hasattr(route, "path") and route.path == "/readyz" and hasattr(route, "endpoint"):
                    result = await route.endpoint(session=mock_session)
                    assert isinstance(result, JSONResponse)
                    assert result.status_code == 503
                    assert b"ACCESS_CONTROL_JWT_SECRET must be configured" in result.body
                    mock_logger.warning.assert_called_once()
                    break


class TestModuleLevelApp:
    pass
