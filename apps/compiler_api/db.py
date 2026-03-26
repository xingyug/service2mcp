"""Database wiring for the compiler API."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import cast

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_ENGINE_STATE_KEY = "database_engine"
_SESSION_FACTORY_STATE_KEY = "database_session_factory"


def build_engine_and_session_factory(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create the SQLAlchemy async engine and session factory for the API."""

    engine = create_async_engine(database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def configure_database(
    app: FastAPI,
    *,
    database_url: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Attach database state to the FastAPI app when available."""

    if session_factory is not None:
        setattr(app.state, _SESSION_FACTORY_STATE_KEY, session_factory)
        return
    if database_url is None:
        return

    engine, created_session_factory = build_engine_and_session_factory(database_url)
    setattr(app.state, _ENGINE_STATE_KEY, engine)
    setattr(app.state, _SESSION_FACTORY_STATE_KEY, created_session_factory)


def resolve_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    """Resolve the session factory from app state or environment."""

    existing_factory = getattr(app.state, _SESSION_FACTORY_STATE_KEY, None)
    if existing_factory is not None:
        return cast(async_sessionmaker[AsyncSession], existing_factory)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured for compiler_api database access.")

    engine, created_session_factory = build_engine_and_session_factory(database_url)
    setattr(app.state, _ENGINE_STATE_KEY, engine)
    setattr(app.state, _SESSION_FACTORY_STATE_KEY, created_session_factory)
    return created_session_factory


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a database session for request handlers."""

    session_factory = resolve_session_factory(request.app)
    async with session_factory() as session:
        yield session


async def dispose_database(app: FastAPI) -> None:
    """Dispose the owned database engine when the app shuts down."""

    engine = getattr(app.state, _ENGINE_STATE_KEY, None)
    if engine is not None:
        await cast(AsyncEngine, engine).dispose()
