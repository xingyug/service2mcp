"""FastAPI app for the compiler API."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.compiler_api.db import configure_database, dispose_database
from apps.compiler_api.dispatcher import CompilationDispatcher, configure_compilation_dispatcher
from apps.compiler_api.route_publisher import (
    ArtifactRoutePublisher,
    configure_route_publisher,
    dispose_route_publisher,
)
from apps.compiler_api.routes import (
    artifact_registry_router,
    compilations_router,
    services_router,
    workflows_router,
)


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_route_publisher(app)
    await dispose_database(app)


def create_app(
    *,
    database_url: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    compilation_dispatcher: CompilationDispatcher | None = None,
    route_publisher: ArtifactRoutePublisher | None = None,
) -> FastAPI:
    """Create the compiler API application."""

    app = FastAPI(title="Tool Compiler API", version="0.1.0", lifespan=app_lifespan)
    configure_database(app, database_url=database_url, session_factory=session_factory)
    configure_compilation_dispatcher(app, dispatcher=compilation_dispatcher)
    configure_route_publisher(app, route_publisher=route_publisher)
    app.include_router(artifact_registry_router)
    app.include_router(compilations_router)
    app.include_router(services_router)
    app.include_router(workflows_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app(database_url=os.getenv("DATABASE_URL"))
