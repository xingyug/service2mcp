"""Integration tests for the artifact registry API and client."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.access_control.authn.service import JWTSettings, build_service_jwt
from apps.compiler_api.route_publisher import NoopArtifactRoutePublisher
from libs.db_models import Base
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    Param,
    ResponseStrategy,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.registry_client import (
    ArtifactRecordPayload,
    ArtifactVersionCreate,
    ArtifactVersionUpdate,
    RegistryClient,
)

_TEST_ARTIFACT_REGISTRY_JWT_SETTINGS = JWTSettings(secret="integration-test-artifact-registry-jwt")

os.environ.setdefault(
    "ACCESS_CONTROL_JWT_SECRET",
    "integration-test-artifact-registry-jwt",
)


def _create_compiler_api_app(**kwargs: Any) -> FastAPI:
    from apps.compiler_api.main import create_app

    return create_app(**kwargs)


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


def _build_ir(
    *,
    description: str,
    include_verbose_param: bool,
    tenant: str,
    environment: str,
) -> dict[str, object]:
    params = [
        Param(name="account_id", type="string", required=True, confidence=1.0),
    ]
    if include_verbose_param:
        params.append(Param(name="verbose", type="boolean", required=False, confidence=1.0))

    service_ir = ServiceIR(
        source_hash="a" * 64,
        protocol="openapi",
        service_name="Billing API",
        service_description="Compiled billing service",
        base_url="https://billing.internal.example.com",
        auth=AuthConfig(type=AuthType.bearer, runtime_secret_ref="billing-secret"),
        operations=[
            Operation(
                id="getAccount",
                name="Get Account",
                description=description,
                method="GET",
                path="/accounts/{account_id}",
                params=params,
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                response_strategy=ResponseStrategy(max_response_bytes=4096),
                enabled=True,
            )
        ],
        tenant=tenant,
        environment=environment,
    )
    return service_ir.model_dump(mode="json")


def _artifact_registry_auth_headers(subject: str = "registry-user") -> dict[str, str]:
    token = build_service_jwt(
        subject=subject,
        jwt_settings=_TEST_ARTIFACT_REGISTRY_JWT_SETTINGS,
    )
    return {"Authorization": f"Bearer {token}"}


def _build_route_config(
    *,
    service_id: str,
    service_name: str,
    version_number: int,
) -> dict[str, object]:
    return {
        "service_id": service_id,
        "service_name": service_name,
        "namespace": "test-ns",
        "version_number": version_number,
        "default_route": {
            "route_id": f"{service_id}-active",
            "match": {"prefix": f"/{service_id}"},
            "switch_strategy": "immediate",
            "target_service": {"name": f"{service_id}-v{version_number}", "port": 8000},
        },
        "version_route": {
            "route_id": f"{service_id}-v{version_number}",
            "match": {"prefix": f"/{service_id}/versions/v{version_number}"},
            "target_service": {"name": f"{service_id}-v{version_number}", "port": 8000},
        },
    }


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
    return _create_compiler_api_app(
        session_factory=session_factory,
        jwt_settings=_TEST_ARTIFACT_REGISTRY_JWT_SETTINGS,
        route_publisher=NoopArtifactRoutePublisher(),
    )


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=_artifact_registry_auth_headers(),
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_artifact_registry_crud_filters_and_diff(http_client: httpx.AsyncClient) -> None:
    version_one_payload = {
        "service_id": "billing-api",
        "version_number": 1,
        "ir_json": _build_ir(
            description="Fetch account information.",
            include_verbose_param=False,
            tenant="team-a",
            environment="prod",
        ),
        "raw_ir_json": _build_ir(
            description="Fetch account information.",
            include_verbose_param=False,
            tenant="team-a",
            environment="prod",
        ),
        "tenant": "team-a",
        "environment": "prod",
        "artifacts": [
            {
                "artifact_type": "ir",
                "content_hash": "irhash-v1",
                "storage_path": "s3://registry/billing-api/v1/service-ir.json",
            }
        ],
    }
    create_v1 = await http_client.post("/api/v1/artifacts", json=version_one_payload)
    assert create_v1.status_code == 201
    assert create_v1.json()["is_active"] is True
    assert create_v1.json()["ir_json"]["service_name"] == "Billing API"

    duplicate_v1 = await http_client.post("/api/v1/artifacts", json=version_one_payload)
    assert duplicate_v1.status_code == 409

    version_two_payload = {
        "service_id": "billing-api",
        "version_number": 2,
        "ir_json": _build_ir(
            description="Fetch account information with optional verbosity.",
            include_verbose_param=True,
            tenant="team-a",
            environment="prod",
        ),
        "tenant": "team-a",
        "environment": "prod",
        "validation_report": {"status": "passed"},
    }
    create_v2 = await http_client.post("/api/v1/artifacts", json=version_two_payload)
    assert create_v2.status_code == 201
    assert create_v2.json()["is_active"] is False

    listed = await http_client.get("/api/v1/artifacts/billing-api/versions")
    assert listed.status_code == 200
    assert [version["version_number"] for version in listed.json()["versions"]] == [2, 1]

    filtered = await http_client.get(
        "/api/v1/artifacts/billing-api/versions",
        params={"tenant": "team-a", "environment": "prod"},
    )
    assert filtered.status_code == 200
    assert [version["version_number"] for version in filtered.json()["versions"]] == [2, 1]

    fetched_v1 = await http_client.get(
        "/api/v1/artifacts/billing-api/versions/1",
        params={"tenant": "team-a", "environment": "prod"},
    )
    assert fetched_v1.status_code == 200
    assert fetched_v1.json()["artifacts"][0]["artifact_type"] == "ir"

    missing_due_to_filter = await http_client.get(
        "/api/v1/artifacts/billing-api/versions/1",
        params={"tenant": "team-a", "environment": "staging"},
    )
    assert missing_due_to_filter.status_code == 404

    updated_v2 = await http_client.put(
        "/api/v1/artifacts/billing-api/versions/2",
        params={"tenant": "team-a", "environment": "prod"},
        json={
            "deployment_revision": "rev-2",
            "route_config": _build_route_config(
                service_id="billing-api",
                service_name="Billing API",
                version_number=2,
            ),
            "artifacts": [
                {
                    "artifact_type": "manifest",
                    "content_hash": "manifest-v2",
                    "storage_path": "gs://registry/billing-api/v2/manifest.yaml",
                }
            ],
        },
    )
    assert updated_v2.status_code == 200
    assert updated_v2.json()["deployment_revision"] == "rev-2"
    assert updated_v2.json()["artifacts"][0]["artifact_type"] == "manifest"

    activated_v2 = await http_client.post(
        "/api/v1/artifacts/billing-api/versions/2/activate",
        params={"tenant": "team-a", "environment": "prod"},
    )
    assert activated_v2.status_code == 200
    assert activated_v2.json()["is_active"] is True

    listed_after_activation = await http_client.get("/api/v1/artifacts/billing-api/versions")
    active_flags = {
        version["version_number"]: version["is_active"]
        for version in listed_after_activation.json()["versions"]
    }
    assert active_flags == {2: True, 1: False}

    diff_response = await http_client.get(
        "/api/v1/artifacts/billing-api/diff",
        params={"from": 1, "to": 2},
    )
    assert diff_response.status_code == 200
    assert diff_response.json()["summary"] != "no changes"
    assert diff_response.json()["changed_operations"][0]["operation_id"] == "getAccount"

    deleted_v1 = await http_client.delete(
        "/api/v1/artifacts/billing-api/versions/1",
        params={"tenant": "team-a", "environment": "prod"},
    )
    assert deleted_v1.status_code == 204

    fetch_deleted = await http_client.get("/api/v1/artifacts/billing-api/versions/1")
    assert fetch_deleted.status_code == 404


@pytest.mark.asyncio
async def test_registry_client_round_trip(http_client: httpx.AsyncClient) -> None:
    async with RegistryClient("http://testserver", client=http_client) as registry_client:
        created = await registry_client.create_version(
            ArtifactVersionCreate(
                service_id="ledger-api",
                version_number=1,
                ir_json=_build_ir(
                    description="Get ledger details.",
                    include_verbose_param=False,
                    tenant="team-b",
                    environment="prod",
                ),
                tenant="team-b",
                environment="prod",
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="ir",
                        content_hash="ledger-ir",
                        storage_path="s3://registry/ledger-api/v1/service-ir.json",
                    )
                ],
            )
        )
        assert created.service_id == "ledger-api"
        assert created.is_active is True

        updated = await registry_client.update_version(
            "ledger-api",
            1,
            ArtifactVersionUpdate(validation_report={"status": "passed"}),
            tenant="team-b",
            environment="prod",
        )
        assert updated.validation_report == {"status": "passed"}

        listed = await registry_client.list_versions(
            "ledger-api",
            tenant="team-b",
            environment="prod",
        )
        assert [version.version_number for version in listed.versions] == [1]

        fetched = await registry_client.get_version(
            "ledger-api",
            1,
            tenant="team-b",
            environment="prod",
        )
        assert fetched.artifacts[0].content_hash == "ledger-ir"

        diff = await registry_client.diff_versions("ledger-api", from_version=1, to_version=1)
        assert diff.is_empty is True

        await registry_client.delete_version(
            "ledger-api",
            1,
            tenant="team-b",
            environment="prod",
        )

    fetch_deleted = await http_client.get("/api/v1/artifacts/ledger-api/versions/1")
    assert fetch_deleted.status_code == 404


@pytest.mark.asyncio
async def test_create_version_re_reads_created_scope(http_client: httpx.AsyncClient) -> None:
    team_a_payload = {
        "service_id": "billing-api",
        "version_number": 1,
        "ir_json": _build_ir(
            description="Team A billing.",
            include_verbose_param=False,
            tenant="team-a",
            environment="prod",
        ),
        "tenant": "team-a",
        "environment": "prod",
    }
    team_b_payload = {
        "service_id": "billing-api",
        "version_number": 1,
        "ir_json": _build_ir(
            description="Team B billing.",
            include_verbose_param=False,
            tenant="team-b",
            environment="staging",
        ),
        "tenant": "team-b",
        "environment": "staging",
    }

    created_team_a = await http_client.post("/api/v1/artifacts", json=team_a_payload)
    assert created_team_a.status_code == 201
    assert created_team_a.json()["tenant"] == "team-a"
    assert created_team_a.json()["environment"] == "prod"

    created_team_b = await http_client.post("/api/v1/artifacts", json=team_b_payload)
    assert created_team_b.status_code == 201
    assert created_team_b.json()["tenant"] == "team-b"
    assert created_team_b.json()["environment"] == "staging"
    assert created_team_b.json()["ir_json"]["tenant"] == "team-b"
    assert created_team_b.json()["ir_json"]["environment"] == "staging"
