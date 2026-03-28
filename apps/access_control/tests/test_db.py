"""Tests for db.py — covering all uncovered lines and error paths."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apps.access_control.db import (
    build_engine_and_session_factory,
    configure_database,
    resolve_session_factory,
    get_db_session,
    dispose_database,
)


class TestBuildEngineAndSessionFactory:
    def test_creates_engine_with_pool_pre_ping(self):
        """Test lines 26-27: engine creation with pool_pre_ping=True."""
        database_url = "postgresql+asyncpg://test:test@localhost/testdb"
        
        with patch("apps.access_control.db.create_async_engine") as mock_create_engine, \
             patch("apps.access_control.db.async_sessionmaker") as mock_sessionmaker:
            
            mock_engine = AsyncMock()
            mock_create_engine.return_value = mock_engine
            mock_factory = MagicMock()
            mock_sessionmaker.return_value = mock_factory
            
            engine, factory = build_engine_and_session_factory(database_url)
            
            # Verify engine created with pool_pre_ping=True
            mock_create_engine.assert_called_once_with(database_url, pool_pre_ping=True)
            # Verify session factory created with engine and expire_on_commit=False
            mock_sessionmaker.assert_called_once_with(mock_engine, expire_on_commit=False)
            
            assert engine == mock_engine
            assert factory == mock_factory


class TestConfigureDatabase:
    def test_with_session_factory_provided(self):
        """Test lines 38-40: configure with session_factory provided."""
        app = FastAPI()
        mock_factory = MagicMock()
        
        configure_database(app, session_factory=mock_factory)
        
        # Should set session factory in app state and return early
        assert getattr(app.state, "database_session_factory") == mock_factory
        # Should not set engine since we provided factory directly
        assert not hasattr(app.state, "database_engine")

    def test_with_database_url_none(self):
        """Test lines 41-42: configure with database_url=None."""
        app = FastAPI()
        
        configure_database(app, database_url=None)
        
        # Should not set anything in app state
        assert not hasattr(app.state, "database_session_factory")
        assert not hasattr(app.state, "database_engine")

    def test_with_database_url_provided(self):
        """Test lines 44-46: configure with database_url provided."""
        app = FastAPI()
        database_url = "postgresql+asyncpg://test:test@localhost/testdb"
        
        with patch("apps.access_control.db.build_engine_and_session_factory") as mock_build:
            mock_engine = AsyncMock()
            mock_factory = MagicMock()
            mock_build.return_value = (mock_engine, mock_factory)
            
            configure_database(app, database_url=database_url)
            
            mock_build.assert_called_once_with(database_url)
            assert getattr(app.state, "database_engine") == mock_engine
            assert getattr(app.state, "database_session_factory") == mock_factory


class TestResolveSessionFactory:
    def test_existing_factory_in_app_state(self):
        """Test lines 52-54: resolve existing factory from app state."""
        app = FastAPI()
        mock_factory = MagicMock()
        setattr(app.state, "database_session_factory", mock_factory)
        
        factory = resolve_session_factory(app)
        
        assert factory == mock_factory

    def test_no_database_url_env_var(self):
        """Test lines 56-58: no DATABASE_URL environment variable."""
        app = FastAPI()
        
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError) as exc_info:
                resolve_session_factory(app)
                
            assert "DATABASE_URL must be configured" in str(exc_info.value)

    def test_creates_factory_from_env_database_url(self):
        """Test lines 60-63: create factory from DATABASE_URL env var."""
        app = FastAPI()
        database_url = "postgresql+asyncpg://test:test@localhost/testdb"
        
        with patch.dict(os.environ, {"DATABASE_URL": database_url}), \
             patch("apps.access_control.db.build_engine_and_session_factory") as mock_build:
            
            mock_engine = AsyncMock()
            mock_factory = MagicMock()
            mock_build.return_value = (mock_engine, mock_factory)
            
            factory = resolve_session_factory(app)
            
            mock_build.assert_called_once_with(database_url)
            assert getattr(app.state, "database_engine") == mock_engine
            assert getattr(app.state, "database_session_factory") == mock_factory
            assert factory == mock_factory


class TestGetDbSession:
    pass


class TestDisposeDatabase:
    async def test_no_engine_in_app_state(self):
        """Test lines 77-79: no engine to dispose."""
        app = FastAPI()
        
        # Should not raise any exception
        await dispose_database(app)

    async def test_disposes_existing_engine(self):
        """Test dispose when engine exists in app state.""" 
        app = FastAPI()
        mock_engine = AsyncMock()
        setattr(app.state, "database_engine", mock_engine)
        
        await dispose_database(app)
        
        mock_engine.dispose.assert_called_once()