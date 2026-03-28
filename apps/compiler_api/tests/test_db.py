"""Unit tests for apps/compiler_api/db.py."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apps.compiler_api.db import (
    build_engine_and_session_factory,
    configure_database,
    dispose_database,
    get_db_session,
    resolve_session_factory,
)


class TestBuildEngineAndSessionFactory:
    def test_creates_engine_and_session_factory(self) -> None:
        with patch("apps.compiler_api.db.create_async_engine") as mock_create_engine, \
             patch("apps.compiler_api.db.async_sessionmaker") as mock_sessionmaker:
            
            mock_engine = MagicMock(spec=AsyncEngine)
            mock_create_engine.return_value = mock_engine
            mock_factory = MagicMock()
            mock_sessionmaker.return_value = mock_factory
            
            engine, factory = build_engine_and_session_factory("sqlite+aiosqlite:///:memory:")
            
            assert engine == mock_engine
            assert factory == mock_factory
            mock_create_engine.assert_called_once_with("sqlite+aiosqlite:///:memory:", pool_pre_ping=True)
            mock_sessionmaker.assert_called_once_with(mock_engine, expire_on_commit=False)


class TestConfigureDatabase:
    def test_with_session_factory(self) -> None:
        app = FastAPI()
        mock_factory = MagicMock()
        
        configure_database(app, session_factory=mock_factory)
        
        assert getattr(app.state, "database_session_factory") == mock_factory
        # Should not set engine when only factory provided
        assert not hasattr(app.state, "database_engine")

    def test_with_database_url(self) -> None:
        app = FastAPI()
        
        with patch("apps.compiler_api.db.build_engine_and_session_factory") as mock_build:
            mock_engine = MagicMock()
            mock_factory = MagicMock()
            mock_build.return_value = (mock_engine, mock_factory)
            
            configure_database(app, database_url="sqlite+aiosqlite:///:memory:")
            
            assert getattr(app.state, "database_engine") == mock_engine
            assert getattr(app.state, "database_session_factory") == mock_factory
            mock_build.assert_called_once_with("sqlite+aiosqlite:///:memory:")

    def test_with_no_params_is_noop(self) -> None:
        app = FastAPI()
        
        configure_database(app)
        
        assert not hasattr(app.state, "database_engine")
        assert not hasattr(app.state, "database_session_factory")


class TestResolveSessionFactory:
    def test_returns_existing_factory(self) -> None:
        app = FastAPI()
        mock_factory = MagicMock()
        setattr(app.state, "database_session_factory", mock_factory)
        
        result = resolve_session_factory(app)
        
        assert result == mock_factory

    def test_lazy_initialization_from_env(self) -> None:
        app = FastAPI()
        
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite+aiosqlite:///:memory:"}), \
             patch("apps.compiler_api.db.build_engine_and_session_factory") as mock_build, \
             patch("apps.compiler_api.db._logger.warning") as mock_warning:
            
            mock_engine = MagicMock()
            mock_factory = MagicMock()
            mock_build.return_value = (mock_engine, mock_factory)
            
            result = resolve_session_factory(app)
            
            assert result == mock_factory
            assert getattr(app.state, "database_engine") == mock_engine
            assert getattr(app.state, "database_session_factory") == mock_factory
            mock_warning.assert_called_once()
            mock_build.assert_called_once_with("sqlite+aiosqlite:///:memory:")

    def test_no_database_url_raises_error(self) -> None:
        app = FastAPI()
        
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            
            with pytest.raises(RuntimeError, match="DATABASE_URL must be configured"):
                resolve_session_factory(app)


class TestGetDbSession:
    async def test_yields_session(self) -> None:
        app = FastAPI()
        mock_factory = MagicMock()
        mock_session = AsyncMock(spec=AsyncSession)
        
        # Mock the async context manager
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
        
        with patch("apps.compiler_api.db.resolve_session_factory", return_value=mock_factory):
            mock_request = MagicMock()
            mock_request.app = app
            
            # get_db_session is an async generator, so we need to use aiter
            session_gen = get_db_session(mock_request)
            session = await session_gen.__anext__()
            
            assert session == mock_session
            mock_factory.assert_called_once()
            mock_factory.return_value.__aenter__.assert_called_once()
            
            # Clean up the generator
            try:
                await session_gen.__anext__()
            except StopAsyncIteration:
                pass


class TestDisposeDatabase:
    async def test_disposes_engine_when_present(self) -> None:
        app = FastAPI()
        mock_engine = AsyncMock(spec=AsyncEngine)
        setattr(app.state, "database_engine", mock_engine)
        
        await dispose_database(app)
        
        mock_engine.dispose.assert_called_once()

    async def test_no_engine_is_noop(self) -> None:
        app = FastAPI()
        
        # Should not raise any exception
        await dispose_database(app)