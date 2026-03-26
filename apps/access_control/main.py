"""FastAPI app for the access control service."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.access_control.audit.routes import router as audit_router
from apps.access_control.authn.routes import router as authn_router
from apps.access_control.authn.service import JWTSettings, load_jwt_settings
from apps.access_control.authz.routes import router as authz_router
from apps.access_control.db import configure_database, dispose_database
from apps.access_control.gateway_binding.client import GatewayAdminClient
from apps.access_control.gateway_binding.routes import router as gateway_binding_router
from apps.access_control.gateway_binding.service import (
    configure_gateway_binding_service,
    dispose_gateway_binding_service,
)


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
    app.state.jwt_settings = jwt_settings or load_jwt_settings()
    configure_gateway_binding_service(app.state, client=gateway_admin_client)
    app.include_router(audit_router)
    app.include_router(authn_router)
    app.include_router(authz_router)
    app.include_router(gateway_binding_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app(database_url=os.getenv("DATABASE_URL"))
