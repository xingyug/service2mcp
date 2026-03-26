"""Integration tests for version coexistence support."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.compiler_api.repository import ArtifactRegistryRepository
from libs.db_models import Base
from libs.generator import GenericManifestConfig, generate_generic_manifests
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
from libs.registry_client.models import ArtifactVersionCreate


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


def _build_ir(version_number: int) -> ServiceIR:
    return ServiceIR(
        source_hash=(str(version_number) * 64)[:64],
        protocol="openapi",
        service_name="Billing Runtime",
        service_description=f"Billing runtime version {version_number}",
        base_url="https://billing.internal.example.com",
        auth=AuthConfig(type=AuthType.bearer, runtime_secret_ref="billing-secret"),
        operations=[
            Operation(
                id=f"listItemsV{version_number}",
                name=f"List Items V{version_number}",
                description="List current items.",
                method="GET",
                path="/items",
                params=[Param(name="page", type="integer", required=False, confidence=1.0)],
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
    )


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


@pytest.mark.asyncio
async def test_versioned_deployments_coexist_and_active_switch_is_atomic(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    manifest_v1 = generate_generic_manifests(
        _build_ir(1),
        config=GenericManifestConfig(
            runtime_image="ghcr.io/example/generic-runtime:stable",
            service_id="billing-api",
            version_number=1,
            namespace="runtime-system",
        ),
    )
    manifest_v2 = generate_generic_manifests(
        _build_ir(2),
        config=GenericManifestConfig(
            runtime_image="ghcr.io/example/generic-runtime:stable",
            service_id="billing-api",
            version_number=2,
            namespace="runtime-system",
        ),
    )

    assert manifest_v1.service["metadata"]["name"] == "billing-runtime-v1"
    assert manifest_v2.service["metadata"]["name"] == "billing-runtime-v2"
    assert manifest_v1.route_config["version_route"]["route_id"] == "billing-api-v1"
    assert manifest_v2.route_config["version_route"]["route_id"] == "billing-api-v2"

    async with session_factory() as session:
        repository = ArtifactRegistryRepository(session)
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="billing-api",
                version_number=1,
                ir_json=_build_ir(1).model_dump(mode="json"),
                deployment_revision=manifest_v1.deployment["metadata"]["name"],
                route_config=manifest_v1.route_config,
                is_active=True,
            )
        )
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="billing-api",
                version_number=2,
                ir_json=_build_ir(2).model_dump(mode="json"),
                deployment_revision=manifest_v2.deployment["metadata"]["name"],
                route_config=manifest_v2.route_config,
                is_active=False,
            )
        )

        active_before = await repository.get_active_version("billing-api")
        inactive_before = await repository.get_version("billing-api", 2)
        assert active_before is not None
        assert inactive_before is not None
        assert active_before.version_number == 1
        assert inactive_before.is_active is False
        assert active_before.route_config is not None
        assert inactive_before.route_config is not None
        assert active_before.route_config["default_route"]["target_service"]["name"] == (
            "billing-runtime-v1"
        )
        assert inactive_before.route_config["version_route"]["target_service"]["name"] == (
            "billing-runtime-v2"
        )

        activated = await repository.activate_version("billing-api", 2)
        preserved_old_version = await repository.get_version("billing-api", 1)

    assert activated is not None
    assert preserved_old_version is not None
    assert activated.version_number == 2
    assert activated.route_config is not None
    assert preserved_old_version.route_config is not None
    assert activated.route_config["default_route"]["target_service"]["name"] == (
        "billing-runtime-v2"
    )
    assert preserved_old_version.is_active is False
    assert preserved_old_version.route_config["version_route"]["target_service"]["name"] == (
        "billing-runtime-v1"
    )
