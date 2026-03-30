"""Integration tests for the access control authorization module."""

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

import apps.access_control.authz.routes as authz_routes
from apps.access_control.authn.service import JWTSettings
from apps.access_control.gateway_binding.client import InMemoryAPISIXAdminClient
from apps.access_control.main import create_app
from libs.db_models import Base

_TEST_JWT_SECRET = "test-secret"


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


def _make_test_jwt(
    subject: str = "admin",
    *,
    roles: list[str] | None = None,
    secret: str = _TEST_JWT_SECRET,
) -> str:
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


def _auth_headers(subject: str = "admin", *, roles: list[str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_test_jwt(subject, roles=roles)}"}


class _FailingGatewayAdminClient(InMemoryAPISIXAdminClient):
    def __init__(self) -> None:
        super().__init__()
        self.fail_upsert_policy_binding = False
        self.fail_delete_policy_binding = False

    async def upsert_policy_binding(self, *, binding_id: str, document: dict[str, object]) -> None:
        if self.fail_upsert_policy_binding:
            raise RuntimeError("gateway down")
        await super().upsert_policy_binding(binding_id=binding_id, document=document)

    async def delete_policy_binding(self, binding_id: str) -> None:
        if self.fail_delete_policy_binding:
            raise RuntimeError("gateway down")
        await super().delete_policy_binding(binding_id)


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
def gateway_client() -> InMemoryAPISIXAdminClient:
    return InMemoryAPISIXAdminClient()


@pytest.fixture
def app(
    session_factory: async_sessionmaker[AsyncSession],
    gateway_client: InMemoryAPISIXAdminClient,
) -> FastAPI:
    return create_app(
        session_factory=session_factory,
        jwt_settings=JWTSettings(secret="test-secret"),
        gateway_admin_client=gateway_client,
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
        headers=_auth_headers(roles=["admin"]),
    )
    assert created.status_code == 201
    policy_id = created.json()["id"]

    fetched = await http_client.get(
        f"/api/v1/authz/policies/{policy_id}",
        headers=_auth_headers(),
    )
    assert fetched.status_code == 200
    assert fetched.json()["decision"] == "allow"

    updated = await http_client.put(
        f"/api/v1/authz/policies/{policy_id}",
        json={"decision": "require_approval", "risk_threshold": "cautious"},
        headers=_auth_headers(roles=["admin"]),
    )
    assert updated.status_code == 200
    assert updated.json()["decision"] == "require_approval"

    listed = await http_client.get(
        "/api/v1/authz/policies",
        params={"subject_id": "alice"},
        headers=_auth_headers(),
    )
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    deleted = await http_client.delete(
        f"/api/v1/authz/policies/{policy_id}",
        headers=_auth_headers(roles=["admin"]),
    )
    assert deleted.status_code == 204

    missing = await http_client.get(
        f"/api/v1/authz/policies/{policy_id}",
        headers=_auth_headers(),
    )
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
        headers=_auth_headers(roles=["admin"]),
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
        headers=_auth_headers(subject="alice"),
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
        headers=_auth_headers(subject="bob"),
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
        headers=_auth_headers(roles=["admin"]),
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
        headers=_auth_headers(subject="alice"),
    )
    assert cautious.status_code == 200
    assert cautious.json()["decision"] == "deny"


@pytest.mark.asyncio
async def test_policy_routes_require_auth_and_admin_for_mutations(
    http_client: httpx.AsyncClient,
) -> None:
    unauthenticated_create = await http_client.post(
        "/api/v1/authz/policies",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "resource_id": "billing-api",
            "action_pattern": "*",
            "risk_threshold": "safe",
            "decision": "allow",
        },
    )
    assert unauthenticated_create.status_code == 401

    forbidden_create = await http_client.post(
        "/api/v1/authz/policies",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "resource_id": "billing-api",
            "action_pattern": "*",
            "risk_threshold": "safe",
            "decision": "allow",
        },
        headers=_auth_headers(subject="alice"),
    )
    assert forbidden_create.status_code == 403

    unauthenticated_list = await http_client.get("/api/v1/authz/policies")
    assert unauthenticated_list.status_code == 401


@pytest.mark.asyncio
async def test_policy_creation_rolls_back_when_gateway_sync_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway_client = _FailingGatewayAdminClient()
    gateway_client.fail_upsert_policy_binding = True
    app = create_app(
        session_factory=session_factory,
        jwt_settings=JWTSettings(secret="test-secret"),
        gateway_admin_client=gateway_client,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/authz/policies",
            json={
                "subject_type": "user",
                "subject_id": "alice",
                "resource_id": "billing-api",
                "action_pattern": "*",
                "risk_threshold": "safe",
                "decision": "allow",
            },
            headers=_auth_headers(roles=["admin"]),
        )
        assert created.status_code == 502

        listed = await client.get(
            "/api/v1/authz/policies",
            params={"subject_id": "alice"},
            headers=_auth_headers(subject="alice"),
        )
        assert listed.status_code == 200
        assert listed.json()["items"] == []


@pytest.mark.asyncio
async def test_policy_creation_reconciles_gateway_when_audit_fails(
    app: FastAPI,
    gateway_client: InMemoryAPISIXAdminClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_audit(*args: object, **kwargs: object) -> object:
        raise RuntimeError("audit broke")

    monkeypatch.setattr(authz_routes.AuditLogService, "append_entry", _fail_audit)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/authz/policies",
            json={
                "subject_type": "user",
                "subject_id": "alice",
                "resource_id": "billing-api",
                "action_pattern": "*",
                "risk_threshold": "safe",
                "decision": "allow",
            },
            headers=_auth_headers(roles=["admin"]),
        )

    assert created.status_code == 500
    assert gateway_client.policy_bindings == {}


@pytest.mark.asyncio
async def test_policy_update_rolls_back_when_gateway_sync_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway_client = _FailingGatewayAdminClient()
    app = create_app(
        session_factory=session_factory,
        jwt_settings=JWTSettings(secret="test-secret"),
        gateway_admin_client=gateway_client,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/authz/policies",
            json={
                "subject_type": "user",
                "subject_id": "alice",
                "resource_id": "billing-api",
                "action_pattern": "*",
                "risk_threshold": "safe",
                "decision": "allow",
            },
            headers=_auth_headers(roles=["admin"]),
        )
        assert created.status_code == 201
        policy_id = created.json()["id"]

        gateway_client.fail_upsert_policy_binding = True
        updated = await client.put(
            f"/api/v1/authz/policies/{policy_id}",
            json={"decision": "deny"},
            headers=_auth_headers(roles=["admin"]),
        )
        assert updated.status_code == 502

        fetched = await client.get(
            f"/api/v1/authz/policies/{policy_id}",
            headers=_auth_headers(subject="alice"),
        )
        assert fetched.status_code == 200
        assert fetched.json()["decision"] == "allow"


@pytest.mark.asyncio
async def test_policy_update_reconciles_gateway_when_audit_fails(
    app: FastAPI,
    gateway_client: InMemoryAPISIXAdminClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=base_transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/authz/policies",
            json={
                "subject_type": "user",
                "subject_id": "alice",
                "resource_id": "billing-api",
                "action_pattern": "*",
                "risk_threshold": "safe",
                "decision": "allow",
            },
            headers=_auth_headers(roles=["admin"]),
        )
    assert created.status_code == 201
    policy = created.json()
    binding_id = f"policy-{policy['id']}"
    assert gateway_client.policy_bindings[binding_id].document["decision"] == "allow"

    async def _fail_audit(*args: object, **kwargs: object) -> object:
        raise RuntimeError("audit broke")

    monkeypatch.setattr(authz_routes.AuditLogService, "append_entry", _fail_audit)
    failing_transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=failing_transport, base_url="http://testserver") as client:
        updated = await client.put(
            f"/api/v1/authz/policies/{policy['id']}",
            json={"decision": "deny"},
            headers=_auth_headers(roles=["admin"]),
        )

    assert updated.status_code == 500
    assert gateway_client.policy_bindings[binding_id].document["decision"] == "allow"


@pytest.mark.asyncio
async def test_policy_delete_rolls_back_when_gateway_sync_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway_client = _FailingGatewayAdminClient()
    app = create_app(
        session_factory=session_factory,
        jwt_settings=JWTSettings(secret="test-secret"),
        gateway_admin_client=gateway_client,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/authz/policies",
            json={
                "subject_type": "user",
                "subject_id": "alice",
                "resource_id": "billing-api",
                "action_pattern": "*",
                "risk_threshold": "safe",
                "decision": "allow",
            },
            headers=_auth_headers(roles=["admin"]),
        )
        assert created.status_code == 201
        policy_id = created.json()["id"]

        gateway_client.fail_delete_policy_binding = True
        deleted = await client.delete(
            f"/api/v1/authz/policies/{policy_id}",
            headers=_auth_headers(roles=["admin"]),
        )
        assert deleted.status_code == 502

        fetched = await client.get(
            f"/api/v1/authz/policies/{policy_id}",
            headers=_auth_headers(subject="alice"),
        )
        assert fetched.status_code == 200


@pytest.mark.asyncio
async def test_policy_delete_reconciles_gateway_when_audit_fails(
    app: FastAPI,
    gateway_client: InMemoryAPISIXAdminClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=base_transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/authz/policies",
            json={
                "subject_type": "user",
                "subject_id": "alice",
                "resource_id": "billing-api",
                "action_pattern": "*",
                "risk_threshold": "safe",
                "decision": "allow",
            },
            headers=_auth_headers(roles=["admin"]),
        )
    assert created.status_code == 201
    policy = created.json()
    binding_id = f"policy-{policy['id']}"
    assert binding_id in gateway_client.policy_bindings

    async def _fail_audit(*args: object, **kwargs: object) -> object:
        raise RuntimeError("audit broke")

    monkeypatch.setattr(authz_routes.AuditLogService, "append_entry", _fail_audit)
    failing_transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=failing_transport, base_url="http://testserver") as client:
        deleted = await client.delete(
            f"/api/v1/authz/policies/{policy['id']}",
            headers=_auth_headers(roles=["admin"]),
        )

    assert deleted.status_code == 500
    assert binding_id in gateway_client.policy_bindings
