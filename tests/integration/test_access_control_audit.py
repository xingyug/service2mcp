"""Integration tests for audit logging."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.access_control.authn.service import JWTSettings
from apps.access_control.gateway_binding.client import InMemoryAPISIXAdminClient
from apps.access_control.main import create_app as create_access_control_app
from apps.compiler_api.dispatcher import InMemoryCompilationDispatcher
from apps.compiler_api.main import create_app as create_compiler_api_app
from libs.db_models import Base

_TEST_JWT_SECRET = "test-secret"


def _make_test_jwt(
    subject: str = "test-user",
    secret: str = _TEST_JWT_SECRET,
    *,
    roles: list[str] | None = None,
) -> str:
    """Create a minimal HS256 JWT for integration tests."""

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload_body: dict[str, object] = {"sub": subject, "iat": now, "exp": now + 3600}
    if roles is not None:
        payload_body["roles"] = roles
    payload = _b64(json.dumps(payload_body).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture
async def session_factory(
    postgres_container: PostgresContainer,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_to_asyncpg_url(postgres_container.get_connection_url()))

    async with engine.begin() as connection:
        for schema_name in ("compiler", "registry", "auth"):
            await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
def access_control_app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    return create_access_control_app(
        session_factory=session_factory,
        jwt_settings=JWTSettings(secret="test-secret"),
        gateway_admin_client=InMemoryAPISIXAdminClient(),
    )


@pytest.fixture
def compiler_api_app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    return create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=InMemoryCompilationDispatcher(),
        jwt_settings=JWTSettings(secret="test-secret"),
    )


@pytest_asyncio.fixture
async def access_control_client(access_control_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=access_control_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://access-control") as client:
        yield client


@pytest_asyncio.fixture
async def compiler_api_client(compiler_api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=compiler_api_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://compiler-api",
        headers={"Authorization": f"Bearer {_make_test_jwt('compiler-user')}"},
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_policy_change_creates_audit_log_entry(
    access_control_client: httpx.AsyncClient,
) -> None:
    created = await access_control_client.post(
        "/api/v1/authz/policies",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "resource_id": "billing-api",
            "action_pattern": "*",
            "risk_threshold": "cautious",
            "decision": "allow",
            "created_by": "admin-user",
        },
        headers={"Authorization": f"Bearer {_make_test_jwt('admin-user', roles=['admin'])}"},
    )
    assert created.status_code == 201

    logs = await access_control_client.get(
        "/api/v1/audit/logs",
        params={"actor": "admin-user"},
        headers={"Authorization": f"Bearer {_make_test_jwt('admin-user')}"},
    )
    assert logs.status_code == 200
    assert logs.json()["items"][0]["action"] == "policy.created"
    assert logs.json()["items"][0]["resource"] == "billing-api"


@pytest.mark.asyncio
async def test_compilation_submission_is_audited(
    access_control_client: httpx.AsyncClient,
    compiler_api_client: httpx.AsyncClient,
) -> None:
    created = await compiler_api_client.post(
        "/api/v1/compilations",
        json={
            "source_url": "https://example.com/openapi.json",
            "created_by": "compiler-user",
            "service_name": "billing-api",
        },
    )
    assert created.status_code == 202

    logs = await access_control_client.get(
        "/api/v1/audit/logs",
        params={"actor": "compiler-user"},
        headers={"Authorization": f"Bearer {_make_test_jwt()}"},
    )
    assert logs.status_code == 200
    assert logs.json()["items"][0]["action"] == "compilation.triggered"
    assert logs.json()["items"][0]["resource"] == "billing-api"
