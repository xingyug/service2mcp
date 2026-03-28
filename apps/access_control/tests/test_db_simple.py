"""Simple integration tests to hit uncovered lines in db.py."""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import FastAPI

from apps.access_control.db import (
    build_engine_and_session_factory,
    configure_database,
    resolve_session_factory,
    dispose_database,
)


class TestDbUncoveredLines:
    def test_build_engine_with_pool_pre_ping(self):
        """Test lines 26-27: build_engine_and_session_factory creates engine with pool_pre_ping=True."""
        database_url = "postgresql+asyncpg://test:test@localhost/testdb"
        
        with patch("apps.access_control.db.create_async_engine") as mock_create_engine, \
             patch("apps.access_control.db.async_sessionmaker") as mock_sessionmaker:
            
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            mock_factory = MagicMock()
            mock_sessionmaker.return_value = mock_factory
            
            engine, factory = build_engine_and_session_factory(database_url)
            
            # Verify engine created with pool_pre_ping=True
            mock_create_engine.assert_called_once_with(database_url, pool_pre_ping=True)
            assert engine == mock_engine
            assert factory == mock_factory

    def test_configure_database_with_session_factory_early_return(self):
        """Test lines 38-40: configure_database returns early when session_factory provided."""
        app = FastAPI()
        mock_factory = MagicMock()
        
        configure_database(app, session_factory=mock_factory)
        
        # Should set session factory in app state
        assert getattr(app.state, "database_session_factory") == mock_factory
        # Should not set engine since we provided factory directly
        assert not hasattr(app.state, "database_engine")

    def test_configure_database_with_no_database_url(self):
        """Test lines 41-42: configure_database returns early when no database_url."""
        app = FastAPI()
        
        configure_database(app, database_url=None)
        
        # Should not set anything in app state
        assert not hasattr(app.state, "database_session_factory")
        assert not hasattr(app.state, "database_engine")

    def test_configure_database_builds_engine_and_factory(self):
        """Test lines 44-46: configure_database builds engine and factory from URL."""
        app = FastAPI()
        database_url = "postgresql+asyncpg://test:test@localhost/testdb"
        
        with patch("apps.access_control.db.build_engine_and_session_factory") as mock_build:
            mock_engine = MagicMock()
            mock_factory = MagicMock()
            mock_build.return_value = (mock_engine, mock_factory)
            
            configure_database(app, database_url=database_url)
            
            mock_build.assert_called_once_with(database_url)
            assert getattr(app.state, "database_engine") == mock_engine
            assert getattr(app.state, "database_session_factory") == mock_factory

    def test_resolve_session_factory_from_env(self):
        """Test lines 56-63: resolve_session_factory creates factory from DATABASE_URL env."""
        app = FastAPI()
        database_url = "postgresql+asyncpg://test:test@localhost/testdb"
        
        with patch.dict(os.environ, {"DATABASE_URL": database_url}), \
             patch("apps.access_control.db.build_engine_and_session_factory") as mock_build:
            
            mock_engine = MagicMock()
            mock_factory = MagicMock()
            mock_build.return_value = (mock_engine, mock_factory)
            
            factory = resolve_session_factory(app)
            
            mock_build.assert_called_once_with(database_url)
            assert getattr(app.state, "database_engine") == mock_engine
            assert getattr(app.state, "database_session_factory") == mock_factory
            assert factory == mock_factory

    def test_resolve_session_factory_no_database_url_raises_error(self):
        """Test lines 57-58: resolve_session_factory raises error when no DATABASE_URL."""
        app = FastAPI()
        
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError) as exc_info:
                resolve_session_factory(app)
                
            assert "DATABASE_URL must be configured" in str(exc_info.value)

    async def test_dispose_database_no_engine(self):
        """Test lines 77-79: dispose_database when no engine exists."""
        app = FastAPI()
        
        # Should not raise any exception
        await dispose_database(app)

    async def test_dispose_database_with_engine(self):
        """Test dispose_database when engine exists."""
        app = FastAPI()
        mock_engine = AsyncMock()
        setattr(app.state, "database_engine", mock_engine)
        
        await dispose_database(app)
        
        mock_engine.dispose.assert_called_once()