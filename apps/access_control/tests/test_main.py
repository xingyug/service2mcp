"""Tests for main.py — covering error paths and lifespan events."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from apps.access_control.main import (
    app_lifespan,
    create_app,
    app,
)


class TestAppLifespan:
    async def test_lifespan_disposes_resources(self):
        """Test lines 31-33: app_lifespan disposes gateway binding and database."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()
        
        with patch("apps.access_control.main.dispose_gateway_binding_service") as mock_dispose_gateway, \
             patch("apps.access_control.main.dispose_database") as mock_dispose_db:
            
            # Test the async context manager
            async with app_lifespan(mock_app):
                pass  # Yield happens here
            
            # Verify cleanup was called
            mock_dispose_gateway.assert_called_once_with(mock_app.state)
            mock_dispose_db.assert_called_once_with(mock_app)


class TestCreateApp:
    def test_creates_app_with_defaults(self):
        """Test successful app creation with default settings."""
        with patch("apps.access_control.main.load_jwt_settings") as mock_load_jwt, \
             patch("apps.access_control.main.configure_database") as mock_config_db, \
             patch("apps.access_control.main.configure_gateway_binding_service") as mock_config_gateway:
            
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

    def test_creates_app_with_custom_settings(self):
        """Test app creation with custom settings."""
        from apps.access_control.authn.service import JWTSettings
        from sqlalchemy.ext.asyncio import async_sessionmaker
        
        custom_jwt = JWTSettings(secret="custom-secret")
        custom_session_factory = MagicMock()
        custom_client = MagicMock()
        
        with patch("apps.access_control.main.configure_database") as mock_config_db, \
             patch("apps.access_control.main.configure_gateway_binding_service") as mock_config_gateway:
            
            result = create_app(
                database_url="postgresql://custom:url",
                session_factory=custom_session_factory,
                jwt_settings=custom_jwt,
                gateway_admin_client=custom_client,
            )
            
            assert result.state.jwt_settings == custom_jwt
            mock_config_db.assert_called_once_with(
                result,
                database_url="postgresql://custom:url", 
                session_factory=custom_session_factory
            )
            mock_config_gateway.assert_called_once_with(result.state, client=custom_client)


class TestHealthEndpoints:

    async def test_readyz_database_success(self):
        """Test lines 62-64: readyz endpoint with successful database check."""
        from apps.access_control.main import create_app
        
        app_instance = create_app()
        
        # Mock successful database session
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        
        # Get the readyz endpoint function
        for route in app_instance.routes:
            if hasattr(route, 'path') and route.path == "/readyz" and hasattr(route, 'endpoint'):
                result = await route.endpoint(session=mock_session)
                assert result == {"status": "ok"}
                mock_session.execute.assert_called_once()
                break

    async def test_readyz_database_failure(self):
        """Test lines 65-67: readyz endpoint with database failure."""
        from apps.access_control.main import create_app
        
        app_instance = create_app()
        
        # Mock failing database session
        mock_session = AsyncMock()
        mock_session.execute.side_effect = Exception("Database connection failed")
        
        with patch("apps.access_control.main._logger") as mock_logger:
            # Get the readyz endpoint function
            for route in app_instance.routes:
                if hasattr(route, 'path') and route.path == "/readyz" and hasattr(route, 'endpoint'):
                    result = await route.endpoint(session=mock_session)
                    assert result == {"status": "not_ready"}
                    
                    # Verify warning was logged
                    mock_logger.warning.assert_called_once()
                    assert "Readiness check failed" in str(mock_logger.warning.call_args)
                    break


class TestModuleLevelApp:
    pass
