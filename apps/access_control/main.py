"""FastAPI app for the access control service."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.access_control.audit.routes import router as audit_router
from apps.access_control.authn.routes import router as authn_router
from apps.access_control.authn.service import JWTConfigurationError, JWTSettings, load_jwt_settings
from apps.access_control.authz.routes import router as authz_router
from apps.access_control.db import configure_database, dispose_database, get_db_session
from apps.access_control.gateway_binding.client import GatewayAdminClient
from apps.access_control.gateway_binding.routes import router as gateway_binding_router
from apps.access_control.gateway_binding.service import (
    configure_gateway_binding_service,
    dispose_gateway_binding_service,
)

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_gateway_binding_service(app.state)
    await dispose_database(app)


def create_app(
    *,
    database_url: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    jwt_settings: JWTSettings | None = None,
    gateway_admin_client: GatewayAdminClient | None = None,
) -> FastAPI:
    """Create the access control application."""

    app = FastAPI(title="Access Control Service", version="0.1.0", lifespan=app_lifespan)
    configure_database(app, database_url=database_url, session_factory=session_factory)
    app.state.jwt_settings = None
    app.state.jwt_settings_error = None
    if jwt_settings is not None:
        app.state.jwt_settings = jwt_settings
    else:
        try:
            app.state.jwt_settings = load_jwt_settings()
        except JWTConfigurationError as exc:
            app.state.jwt_settings_error = str(exc)
    configure_gateway_binding_service(app.state, client=gateway_admin_client)
    app.include_router(audit_router)
    app.include_router(authn_router)
    app.include_router(authz_router)
    app.include_router(gateway_binding_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", response_model=None)
    async def readyz(
        session: AsyncSession = Depends(get_db_session),
    ) -> dict[str, str] | JSONResponse:
        jwt_settings_error = getattr(app.state, "jwt_settings_error", None)
        if isinstance(jwt_settings_error, str) and jwt_settings_error:
            _logger.warning(
                "Readiness check failed: JWT configuration invalid: %s",
                jwt_settings_error,
            )
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "detail": jwt_settings_error},
            )
        gateway_binding_error = getattr(app.state, "gateway_binding_error", None)
        if isinstance(gateway_binding_error, str) and gateway_binding_error:
            _logger.warning(
                "Readiness check failed: gateway binding misconfigured: %s",
                gateway_binding_error,
            )
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "detail": gateway_binding_error},
            )
        try:
            await session.execute(sa_text("SELECT 1"))
            return {"status": "ok"}
        except Exception:
            _logger.warning("Readiness check failed: database unreachable", exc_info=True)
            return JSONResponse(status_code=503, content={"status": "not_ready"})

    return app


app = create_app(database_url=os.getenv("DATABASE_URL"))
