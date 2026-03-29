"""Integration tests for gateway binding behavior."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.access_control.authn.service import JWTSettings
from apps.access_control.gateway_binding.client import (
    HTTPGatewayAdminClient,
    InMemoryAPISIXAdminClient,
)
from apps.access_control.main import create_app
from apps.compiler_api.repository import ArtifactRegistryRepository
from apps.gateway_admin_mock.main import create_app as create_gateway_admin_mock_app
from libs.db_models import Base
from libs.ir import ServiceIR
from libs.registry_client.models import ArtifactVersionCreate

IR_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "ir"
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
    subject: str,
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


def _auth_headers(subject: str, *, roles: list[str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_test_jwt(subject, roles=roles)}"}


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


def _route_config(
    *,
    service_name: str = "billing-runtime-v2",
    version_number: int = 2,
) -> dict[str, object]:
    return {
        "service_id": "billing-api",
        "service_name": service_name,
        "namespace": "runtime-system",
        "version_number": version_number,
        "default_route": {
            "route_id": "billing-api-active",
            "target_service": {
                "name": service_name,
                "namespace": "runtime-system",
                "port": 8003,
            },
            "switch_strategy": "atomic-upstream-swap",
        },
        "version_route": {
            "route_id": f"billing-api-v{version_number}",
            "match": {"headers": {"x-tool-compiler-version": str(version_number)}},
            "target_service": {
                "name": service_name,
                "namespace": "runtime-system",
                "port": 8003,
            },
        },
    }


def _service_ir_payload(service_name: str = "billing-runtime-v2") -> dict[str, object]:
    service_ir = ServiceIR.model_validate_json(
        (IR_FIXTURES_DIR / "service_ir_valid.json").read_text(encoding="utf-8")
    )
    return service_ir.model_copy(update={"service_name": service_name}).model_dump(mode="json")


def _runtime_app(service_name: str) -> FastAPI:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def handle(path: str, request: Request) -> dict[str, object]:
        return {
            "service_name": service_name,
            "path": "/" + path.lstrip("/") if path else "/",
            "query": dict(request.query_params),
            "version_header": request.headers.get("x-tool-compiler-version"),
        }

    return app


def _attach_gateway_upstream_overrides(gateway_admin_app: FastAPI) -> None:
    gateway_admin_app.state.upstream_overrides = {
        "billing-runtime-v1.runtime-system:8003": {
            "base_url": "http://billing-runtime-v1",
            "transport": httpx.ASGITransport(app=_runtime_app("billing-runtime-v1")),
        },
        "billing-runtime-v2.runtime-system:8003": {
            "base_url": "http://billing-runtime-v2",
            "transport": httpx.ASGITransport(app=_runtime_app("billing-runtime-v2")),
        },
    }


@pytest.mark.asyncio
async def test_create_pat_creates_gateway_consumer(
    http_client: httpx.AsyncClient,
    gateway_client: InMemoryAPISIXAdminClient,
) -> None:
    created = await http_client.post(
        "/api/v1/authn/pats",
        json={"username": "alice", "name": "CLI token"},
        headers=_auth_headers("alice"),
    )
    assert created.status_code == 201
    pat_id = created.json()["id"]

    assert f"pat-{pat_id}" in gateway_client.consumers


@pytest.mark.asyncio
async def test_revoke_pat_deletes_gateway_consumer(
    http_client: httpx.AsyncClient,
    gateway_client: InMemoryAPISIXAdminClient,
) -> None:
    created = await http_client.post(
        "/api/v1/authn/pats",
        json={"username": "bob", "name": "CI token"},
        headers=_auth_headers("bob"),
    )
    assert created.status_code == 201
    pat_id = created.json()["id"]
    consumer_id = f"pat-{pat_id}"
    assert consumer_id in gateway_client.consumers

    revoked = await http_client.post(
        f"/api/v1/authn/pats/{pat_id}/revoke",
        headers=_auth_headers("bob"),
    )
    assert revoked.status_code == 200
    assert consumer_id not in gateway_client.consumers


@pytest.mark.asyncio
async def test_reconcile_restores_drifted_consumer_and_policy_binding(
    http_client: httpx.AsyncClient,
    gateway_client: InMemoryAPISIXAdminClient,
) -> None:
    created_pat = await http_client.post(
        "/api/v1/authn/pats",
        json={"username": "carol", "name": "Ops token"},
        headers=_auth_headers("carol"),
    )
    assert created_pat.status_code == 201
    pat_id = created_pat.json()["id"]
    consumer_id = f"pat-{pat_id}"

    created_policy = await http_client.post(
        "/api/v1/authz/policies",
        json={
            "subject_type": "user",
            "subject_id": "carol",
            "resource_id": "billing-api",
            "action_pattern": "*",
            "risk_threshold": "cautious",
            "decision": "allow",
        },
        headers=_auth_headers("admin", roles=["admin"]),
    )
    assert created_policy.status_code == 201
    policy_id = created_policy.json()["id"]
    binding_id = f"policy-{policy_id}"
    assert binding_id in gateway_client.policy_bindings

    gateway_client.consumers.pop(consumer_id, None)
    gateway_client.policy_bindings.pop(binding_id, None)

    reconciled = await http_client.post(
        "/api/v1/gateway-binding/reconcile",
        headers=_auth_headers("admin", roles=["admin"]),
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["consumers_synced"] >= 1
    assert reconciled.json()["policy_bindings_synced"] >= 1
    assert consumer_id in gateway_client.consumers
    assert binding_id in gateway_client.policy_bindings


@pytest.mark.asyncio
async def test_sync_service_routes_and_reconcile_restores_drifted_gateway_route(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway_admin_app = create_gateway_admin_mock_app()
    _attach_gateway_upstream_overrides(gateway_admin_app)
    gateway_admin_transport = httpx.ASGITransport(app=gateway_admin_app)
    async with httpx.AsyncClient(
        transport=gateway_admin_transport,
        base_url="http://gateway-admin",
    ) as gateway_admin_http_client:
        app = create_app(
            session_factory=session_factory,
            jwt_settings=JWTSettings(secret="test-secret"),
            gateway_admin_client=HTTPGatewayAdminClient(
                base_url="http://gateway-admin",
                client=gateway_admin_http_client,
            ),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as access_control_client:
            sync_response = await access_control_client.post(
                "/api/v1/gateway-binding/service-routes/sync",
                json={"route_config": _route_config()},
                headers=_auth_headers("admin", roles=["admin"]),
            )
            assert sync_response.status_code == 200
            assert sync_response.json()["service_routes_synced"] == 2

            initial_gateway_call = await gateway_admin_http_client.get("/gateway/billing-api/tools")
            assert initial_gateway_call.status_code == 200
            assert initial_gateway_call.json()["service_name"] == "billing-runtime-v2"
            assert initial_gateway_call.json()["path"] == "/tools"

            async with session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="billing-api",
                        version_number=2,
                        ir_json=_service_ir_payload(),
                        compiler_version="0.1.0",
                        route_config=_route_config(),
                        is_active=True,
                    )
                )

            deleted = await gateway_admin_http_client.delete("/admin/routes/billing-api-active")
            assert deleted.status_code == 200

            missing_gateway_call = await gateway_admin_http_client.get("/gateway/billing-api/tools")
            assert missing_gateway_call.status_code == 404

            pinned_gateway_call = await gateway_admin_http_client.get(
                "/gateway/billing-api/tools",
                headers={"x-tool-compiler-version": "2"},
            )
            assert pinned_gateway_call.status_code == 200
            assert pinned_gateway_call.json()["service_name"] == "billing-runtime-v2"

            reconciled = await access_control_client.post(
                "/api/v1/gateway-binding/reconcile",
                headers=_auth_headers("admin", roles=["admin"]),
            )
            assert reconciled.status_code == 200
            assert reconciled.json()["service_routes_synced"] >= 1

            listed_routes = await gateway_admin_http_client.get("/admin/routes")
            assert listed_routes.status_code == 200
            routes = {item["route_id"]: item for item in listed_routes.json()["items"]}
            assert "billing-api-active" in routes
            assert "billing-api-v2" in routes
            assert routes["billing-api-active"]["document"]["target_service"]["name"] == (
                "billing-runtime-v2"
            )

            restored_gateway_call = await gateway_admin_http_client.get(
                "/gateway/billing-api/tools",
                params={"view": "restored"},
            )
            assert restored_gateway_call.status_code == 200
            assert restored_gateway_call.json()["service_name"] == "billing-runtime-v2"
            assert restored_gateway_call.json()["query"] == {"view": "restored"}


@pytest.mark.asyncio
async def test_reconcile_updates_stable_route_target_across_rollout_and_rollback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway_admin_app = create_gateway_admin_mock_app()
    _attach_gateway_upstream_overrides(gateway_admin_app)
    gateway_admin_transport = httpx.ASGITransport(app=gateway_admin_app)
    async with httpx.AsyncClient(
        transport=gateway_admin_transport,
        base_url="http://gateway-admin",
    ) as gateway_admin_http_client:
        app = create_app(
            session_factory=session_factory,
            jwt_settings=JWTSettings(secret="test-secret"),
            gateway_admin_client=HTTPGatewayAdminClient(
                base_url="http://gateway-admin",
                client=gateway_admin_http_client,
            ),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as access_control_client:
            route_v1 = _route_config(service_name="billing-runtime-v1", version_number=1)
            route_v2 = _route_config(service_name="billing-runtime-v2", version_number=2)

            async with session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="billing-api",
                        version_number=1,
                        ir_json=_service_ir_payload("billing-runtime-v1"),
                        compiler_version="0.1.0",
                        route_config=route_v1,
                        is_active=True,
                    )
                )

            synced_v1 = await access_control_client.post(
                "/api/v1/gateway-binding/service-routes/sync",
                json={"route_config": route_v1},
                headers=_auth_headers("admin", roles=["admin"]),
            )
            assert synced_v1.status_code == 200

            initial_active = await gateway_admin_http_client.get("/gateway/billing-api/status")
            assert initial_active.status_code == 200
            assert initial_active.json()["service_name"] == "billing-runtime-v1"

            initial_pinned_v1 = await gateway_admin_http_client.get(
                "/gateway/billing-api/status",
                headers={"x-tool-compiler-version": "1"},
            )
            assert initial_pinned_v1.status_code == 200
            assert initial_pinned_v1.json()["service_name"] == "billing-runtime-v1"

            async with session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="billing-api",
                        version_number=2,
                        ir_json=_service_ir_payload("billing-runtime-v2"),
                        compiler_version="0.1.0",
                        route_config=route_v2,
                        is_active=True,
                    )
                )

            rolled_forward = await access_control_client.post(
                "/api/v1/gateway-binding/reconcile",
                headers=_auth_headers("admin", roles=["admin"]),
            )
            assert rolled_forward.status_code == 200
            assert rolled_forward.json()["service_routes_synced"] >= 1

            listed_after_forward = await gateway_admin_http_client.get("/admin/routes")
            assert listed_after_forward.status_code == 200
            routes_after_forward = {
                item["route_id"]: item for item in listed_after_forward.json()["items"]
            }
            assert "billing-api-active" in routes_after_forward
            assert "billing-api-v1" in routes_after_forward
            assert "billing-api-v2" in routes_after_forward
            assert (
                routes_after_forward["billing-api-active"]["document"]["target_service"]["name"]
                == "billing-runtime-v2"
            )

            active_after_forward = await gateway_admin_http_client.get(
                "/gateway/billing-api/status"
            )
            assert active_after_forward.status_code == 200
            assert active_after_forward.json()["service_name"] == "billing-runtime-v2"

            pinned_v1_after_forward = await gateway_admin_http_client.get(
                "/gateway/billing-api/status",
                headers={"x-tool-compiler-version": "1"},
            )
            assert pinned_v1_after_forward.status_code == 200
            assert pinned_v1_after_forward.json()["service_name"] == "billing-runtime-v1"

            pinned_v2_after_forward = await gateway_admin_http_client.get(
                "/gateway/billing-api/status",
                headers={"x-tool-compiler-version": "2"},
            )
            assert pinned_v2_after_forward.status_code == 200
            assert pinned_v2_after_forward.json()["service_name"] == "billing-runtime-v2"

            async with session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                rolled_back = await repository.activate_version("billing-api", 1)
                assert rolled_back is not None

            rollback_reconciled = await access_control_client.post(
                "/api/v1/gateway-binding/reconcile",
                headers=_auth_headers("admin", roles=["admin"]),
            )
            assert rollback_reconciled.status_code == 200
            assert rollback_reconciled.json()["service_routes_synced"] >= 1

            listed_after_rollback = await gateway_admin_http_client.get("/admin/routes")
            assert listed_after_rollback.status_code == 200
            routes_after_rollback = {
                item["route_id"]: item for item in listed_after_rollback.json()["items"]
            }
            assert (
                routes_after_rollback["billing-api-active"]["document"]["target_service"]["name"]
                == "billing-runtime-v1"
            )
            assert "billing-api-v1" in routes_after_rollback
            assert "billing-api-v2" in routes_after_rollback

            active_after_rollback = await gateway_admin_http_client.get(
                "/gateway/billing-api/status"
            )
            assert active_after_rollback.status_code == 200
            assert active_after_rollback.json()["service_name"] == "billing-runtime-v1"

            pinned_v2_after_rollback = await gateway_admin_http_client.get(
                "/gateway/billing-api/status",
                headers={"x-tool-compiler-version": "2"},
            )
            assert pinned_v2_after_rollback.status_code == 200
            assert pinned_v2_after_rollback.json()["service_name"] == "billing-runtime-v2"


@pytest.mark.asyncio
async def test_gateway_binding_routes_require_admin(
    http_client: httpx.AsyncClient,
) -> None:
    unauthenticated = await http_client.post("/api/v1/gateway-binding/reconcile")
    assert unauthenticated.status_code == 401

    non_admin = await http_client.post(
        "/api/v1/gateway-binding/reconcile",
        headers=_auth_headers("alice"),
    )
    assert non_admin.status_code == 403
