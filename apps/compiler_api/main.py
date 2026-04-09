"""FastAPI app for the compiler API."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.access_control.authn.service import JWTSettings, load_jwt_settings
from apps.compiler_api.db import configure_database, dispose_database, resolve_session_factory
from apps.compiler_api.dispatcher import CompilationDispatcher, configure_compilation_dispatcher
from apps.compiler_api.middleware import (
    RequestIdMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from apps.compiler_api.repository import AmbiguousServiceVersionError
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

_logger = logging.getLogger(__name__)


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
    jwt_settings: JWTSettings | None = None,
) -> FastAPI:
    """Create the compiler API application."""

    app = FastAPI(title="service2mcp API", version="0.1.0", lifespan=app_lifespan)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.state.jwt_settings = jwt_settings or load_jwt_settings()
    configure_database(app, database_url=database_url, session_factory=session_factory)
    configure_compilation_dispatcher(app, dispatcher=compilation_dispatcher)
    configure_route_publisher(app, route_publisher=route_publisher)
    app.include_router(artifact_registry_router)
    app.include_router(compilations_router)
    app.include_router(services_router)
    app.include_router(workflows_router)

    @app.exception_handler(AmbiguousServiceVersionError)
    async def handle_ambiguous_service_version(
        _request: Request,
        exc: AmbiguousServiceVersionError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Dependency-aware readiness: verifies database connectivity."""
        try:
            session_factory = resolve_session_factory(app)
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            return JSONResponse(content={"status": "ok"}, status_code=200)
        except Exception:
            _logger.warning("Readiness check failed", exc_info=True)
            return JSONResponse(
                content={"status": "not_ready"},
                status_code=503,
            )

    return app


app = create_app(database_url=os.getenv("DATABASE_URL"))
