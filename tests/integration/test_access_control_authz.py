"""Integration tests for the access control authorization module."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.access_control.authn.service import JWTSettings
from apps.access_control.main import create_app
from libs.db_models import Base


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
def app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    return create_app(
        session_factory=session_factory,
        jwt_settings=JWTSettings(secret="test-secret"),
    )


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_policy_crud_round_trip(http_client: httpx.AsyncClient) -> None:
    created = await http_client.post(
        "/api/v1/authz/policies",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "resource_id": "billing-api",
            "action_pattern": "get*",
            "risk_threshold": "safe",
            "decision": "allow",
            "created_by": "admin",
        },
    )
    assert created.status_code == 201
    policy_id = created.json()["id"]

    fetched = await http_client.get(f"/api/v1/authz/policies/{policy_id}")
    assert fetched.status_code == 200
    assert fetched.json()["decision"] == "allow"

    updated = await http_client.put(
        f"/api/v1/authz/policies/{policy_id}",
        json={"decision": "require_approval", "risk_threshold": "cautious"},
    )
    assert updated.status_code == 200
    assert updated.json()["decision"] == "require_approval"

    listed = await http_client.get("/api/v1/authz/policies", params={"subject_id": "alice"})
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    deleted = await http_client.delete(f"/api/v1/authz/policies/{policy_id}")
    assert deleted.status_code == 204

    missing = await http_client.get(f"/api/v1/authz/policies/{policy_id}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_explicit_allow_and_wildcards_match_actions(http_client: httpx.AsyncClient) -> None:
    created = await http_client.post(
        "/api/v1/authz/policies",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "resource_id": "billing-api",
            "action_pattern": "*",
            "risk_threshold": "cautious",
            "decision": "allow",
        },
    )
    assert created.status_code == 201

    allowed = await http_client.post(
        "/api/v1/authz/evaluate",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "resource_id": "billing-api",
            "action": "updateInvoice",
            "risk_level": "cautious",
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["decision"] == "allow"

    denied = await http_client.post(
        "/api/v1/authz/evaluate",
        json={
            "subject_type": "user",
            "subject_id": "bob",
            "resource_id": "billing-api",
            "action": "updateInvoice",
            "risk_level": "cautious",
        },
    )
    assert denied.status_code == 200
    assert denied.json()["decision"] == "deny"


@pytest.mark.asyncio
async def test_risk_threshold_blocks_cautious_without_explicit_policy(
    http_client: httpx.AsyncClient,
) -> None:
    created = await http_client.post(
        "/api/v1/authz/policies",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "resource_id": "analytics-api",
            "action_pattern": "*",
            "risk_threshold": "safe",
            "decision": "allow",
        },
    )
    assert created.status_code == 201

    cautious = await http_client.post(
        "/api/v1/authz/evaluate",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "resource_id": "analytics-api",
            "action": "refreshDataset",
            "risk_level": "cautious",
        },
    )
    assert cautious.status_code == 200
    assert cautious.json()["decision"] == "deny"
