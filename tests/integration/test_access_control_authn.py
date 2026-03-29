"""Integration tests for the access control authentication module."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.access_control.authn.service import JWTSettings
from apps.access_control.gateway_binding.client import InMemoryAPISIXAdminClient
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


def _encode_jwt(payload: dict[str, object], secret: str) -> str:
    header: dict[str, object] = {"alg": "HS256", "typ": "JWT"}

    def encode_part(value: dict[str, object]) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode("utf-8"))
            .decode("utf-8")
            .rstrip("=")
        )

    header_segment = encode_part(header)
    payload_segment = encode_part(payload)
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{header_segment}.{payload_segment}".encode(),
        hashlib.sha256,
    ).digest()
    signature_segment = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    return f"{header_segment}.{payload_segment}.{signature_segment}"


def _auth_headers(
    *,
    subject: str,
    roles: list[str] | None = None,
) -> dict[str, str]:
    payload: dict[str, object] = {
        "sub": subject,
        "iss": "https://issuer.example.com",
        "aud": "tool-compiler",
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
    }
    if roles is not None:
        payload["roles"] = roles
    token = _encode_jwt(payload, "test-secret")
    return {"Authorization": f"Bearer {token}"}


class _FailingGatewayAdminClient(InMemoryAPISIXAdminClient):
    def __init__(self) -> None:
        super().__init__()
        self.fail_upsert_consumer = False
        self.fail_delete_consumer = False

    async def upsert_consumer(
        self,
        *,
        consumer_id: str,
        username: str,
        credential: str,
        metadata: dict[str, object],
    ) -> None:
        if self.fail_upsert_consumer:
            raise RuntimeError("gateway down")
        await super().upsert_consumer(
            consumer_id=consumer_id,
            username=username,
            credential=credential,
            metadata=metadata,
        )

    async def delete_consumer(self, consumer_id: str) -> None:
        if self.fail_delete_consumer:
            raise RuntimeError("gateway down")
        await super().delete_consumer(consumer_id)


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
        jwt_settings=JWTSettings(
            secret="test-secret",
            issuer="https://issuer.example.com",
            audience="tool-compiler",
        ),
    )


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_valid_jwt_passes_validation(http_client: httpx.AsyncClient) -> None:
    token = _encode_jwt(
        {
            "sub": "alice",
            "iss": "https://issuer.example.com",
            "aud": "tool-compiler",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        "test-secret",
    )

    response = await http_client.post("/api/v1/authn/validate", json={"token": token})

    assert response.status_code == 200
    assert response.json()["subject"] == "alice"
    assert response.json()["token_type"] == "jwt"


@pytest.mark.asyncio
async def test_expired_jwt_is_rejected(http_client: httpx.AsyncClient) -> None:
    token = _encode_jwt(
        {
            "sub": "alice",
            "iss": "https://issuer.example.com",
            "aud": "tool-compiler",
            "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp()),
        },
        "test-secret",
    )

    response = await http_client.post("/api/v1/authn/validate", json={"token": token})

    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pat_lifecycle_create_list_validate_and_revoke(
    http_client: httpx.AsyncClient,
) -> None:
    created = await http_client.post(
        "/api/v1/authn/pats",
        json={"username": "alice", "name": "CI token", "email": "alice@example.com"},
        headers=_auth_headers(subject="alice"),
    )
    assert created.status_code == 201
    created_payload = created.json()
    assert created_payload["username"] == "alice"
    assert created_payload["token"].startswith("pat_")

    listed = await http_client.get(
        "/api/v1/authn/pats",
        params={"username": "alice"},
        headers=_auth_headers(subject="alice"),
    )
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["name"] == "CI token"

    validated = await http_client.post(
        "/api/v1/authn/validate",
        json={"token": created_payload["token"]},
    )
    assert validated.status_code == 200
    assert validated.json()["subject"] == "alice"
    assert validated.json()["token_type"] == "pat"

    revoked = await http_client.post(
        f"/api/v1/authn/pats/{created_payload['id']}/revoke",
        headers=_auth_headers(subject="alice"),
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    rejected = await http_client.post(
        "/api/v1/authn/validate",
        json={"token": created_payload["token"]},
    )
    assert rejected.status_code == 401
    assert "revoked" in rejected.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pat_routes_require_auth_and_enforce_self_or_admin(
    http_client: httpx.AsyncClient,
) -> None:
    unauthenticated = await http_client.post(
        "/api/v1/authn/pats",
        json={"username": "alice", "name": "CI token"},
    )
    assert unauthenticated.status_code == 401

    created = await http_client.post(
        "/api/v1/authn/pats",
        json={"username": "alice", "name": "CLI token"},
        headers=_auth_headers(subject="alice"),
    )
    assert created.status_code == 201
    pat_id = created.json()["id"]

    forbidden_list = await http_client.get(
        "/api/v1/authn/pats",
        params={"username": "alice"},
        headers=_auth_headers(subject="bob"),
    )
    assert forbidden_list.status_code == 403

    forbidden_revoke = await http_client.post(
        f"/api/v1/authn/pats/{pat_id}/revoke",
        headers=_auth_headers(subject="bob"),
    )
    assert forbidden_revoke.status_code == 403

    admin_list = await http_client.get(
        "/api/v1/authn/pats",
        params={"username": "alice"},
        headers=_auth_headers(subject="admin", roles=["admin"]),
    )
    assert admin_list.status_code == 200


@pytest.mark.asyncio
async def test_pat_creation_rolls_back_when_gateway_sync_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway_client = _FailingGatewayAdminClient()
    gateway_client.fail_upsert_consumer = True
    app = create_app(
        session_factory=session_factory,
        jwt_settings=JWTSettings(
            secret="test-secret",
            issuer="https://issuer.example.com",
            audience="tool-compiler",
        ),
        gateway_admin_client=gateway_client,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/authn/pats",
            json={"username": "alice", "name": "CI token"},
            headers=_auth_headers(subject="alice"),
        )
        assert created.status_code == 502

        listed = await client.get(
            "/api/v1/authn/pats",
            params={"username": "alice"},
            headers=_auth_headers(subject="alice"),
        )
        assert listed.status_code == 200
        assert listed.json()["items"] == []


@pytest.mark.asyncio
async def test_pat_revocation_rolls_back_when_gateway_sync_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway_client = _FailingGatewayAdminClient()
    app = create_app(
        session_factory=session_factory,
        jwt_settings=JWTSettings(
            secret="test-secret",
            issuer="https://issuer.example.com",
            audience="tool-compiler",
        ),
        gateway_admin_client=gateway_client,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/authn/pats",
            json={"username": "alice", "name": "CI token"},
            headers=_auth_headers(subject="alice"),
        )
        assert created.status_code == 201
        pat = created.json()

        gateway_client.fail_delete_consumer = True
        revoked = await client.post(
            f"/api/v1/authn/pats/{pat['id']}/revoke",
            headers=_auth_headers(subject="alice"),
        )
        assert revoked.status_code == 502

        validated = await client.post(
            "/api/v1/authn/validate",
            json={"token": pat["token"]},
        )
        assert validated.status_code == 200
        assert validated.json()["token_type"] == "pat"
