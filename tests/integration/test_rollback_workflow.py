"""Integration tests for the rollback workflow."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.compiler_api.repository import ArtifactRegistryRepository
from apps.compiler_worker.workflows.rollback_workflow import (
    RollbackRequest,
    RollbackWorkflow,
)
from libs.db_models import Base
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.registry_client.models import (
    ArtifactRecordPayload,
    ArtifactVersionCreate,
    ArtifactVersionResponse,
    ArtifactVersionUpdate,
)


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


def _build_ir(version_label: str) -> dict[str, object]:
    operation_id = "getPet" if version_label == "v1" else "searchPets"
    service_ir = ServiceIR(
        source_hash=(version_label * 32)[:64],
        protocol="openapi",
        service_name="Petstore",
        service_description=f"Petstore {version_label}",
        base_url="https://petstore.example.com",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id=operation_id,
                name=operation_id,
                description=f"{version_label} operation",
                method="GET",
                path="/pets",
                risk=RiskMetadata(
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                ),
                source=SourceType.extractor,
                confidence=1.0,
                enabled=True,
            )
        ],
    )
    return service_ir.model_dump(mode="json")


class RegistryRollbackStore:
    """Adapter exposing the rollback store protocol over the artifact repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_version(
        self,
        service_id: str,
        version_number: int,
    ) -> ArtifactVersionResponse | None:
        async with self._session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            return await repository.get_version(service_id, version_number)

    async def get_active_version(self, service_id: str) -> ArtifactVersionResponse | None:
        async with self._session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            return await repository.get_active_version(service_id)

    async def update_version(
        self,
        service_id: str,
        version_number: int,
        payload: ArtifactVersionUpdate,
    ) -> ArtifactVersionResponse | None:
        async with self._session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            return await repository.update_version(service_id, version_number, payload)

    async def activate_version(
        self,
        service_id: str,
        version_number: int,
    ) -> ArtifactVersionResponse | None:
        async with self._session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            return await repository.activate_version(service_id, version_number)


class FakeRollbackDeployer:
    """Deployment fake that remembers the currently served version."""

    def __init__(self) -> None:
        self.current_version: int | None = None
        self.current_ir: dict[str, object | list[dict[str, object]]] | None = None
        self.rollout_revision: str | None = None

    async def apply_version(self, version: ArtifactVersionResponse) -> str:
        self.current_version = version.version_number
        self.current_ir = version.ir_json
        self.rollout_revision = f"deploy-{version.service_id}-v{version.version_number}"
        return self.rollout_revision

    async def wait_for_rollout(self, deployment_revision: str) -> None:
        assert deployment_revision == self.rollout_revision


class FakeRollbackValidator:
    """Validator fake that checks the deployer now serves the target version."""

    def __init__(self, deployer: FakeRollbackDeployer) -> None:
        self._deployer = deployer

    async def validate(self, version: ArtifactVersionResponse) -> dict[str, object]:
        served_ir = self._deployer.current_ir or {}
        served_entries = served_ir.get("operations", [])
        if not isinstance(served_entries, list):
            served_entries = []
        served_operations = [
            operation["id"]
            for operation in served_entries
            if isinstance(operation, dict)
        ]
        expected_operations = [
            operation["id"]
            for operation in version.ir_json.get("operations", [])
            if isinstance(operation, dict)
        ]
        return {
            "overall_passed": served_operations == expected_operations,
            "served_operations": served_operations,
            "expected_operations": expected_operations,
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


@pytest.mark.asyncio
async def test_rollback_reactivates_previous_version_and_serves_its_tools(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = ArtifactRegistryRepository(session)
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="petstore",
                version_number=1,
                ir_json=_build_ir("v1"),
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash="manifest-v1",
                        storage_path="manifests/petstore-v1.yaml",
                    )
                ],
                is_active=True,
            )
        )
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="petstore",
                version_number=2,
                ir_json=_build_ir("v2"),
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash="manifest-v2",
                        storage_path="manifests/petstore-v2.yaml",
                    )
                ],
                is_active=True,
            )
        )

    deployer = FakeRollbackDeployer()
    validator = FakeRollbackValidator(deployer)
    workflow = RollbackWorkflow(
        store=RegistryRollbackStore(session_factory),
        deployer=deployer,
        validator=validator,
    )

    result = await workflow.run(RollbackRequest(service_id="petstore", target_version=1))

    assert result.previous_active_version == 2
    assert result.target_version == 1
    assert result.deployment_revision == "deploy-petstore-v1"
    assert result.validation_report["overall_passed"] is True

    async with session_factory() as session:
        repository = ArtifactRegistryRepository(session)
        active_version = await repository.get_active_version("petstore")

    assert active_version is not None
    assert active_version.version_number == 1
    assert active_version.deployment_revision == "deploy-petstore-v1"
    assert deployer.current_ir is not None
    operations = deployer.current_ir.get("operations", [])
    if not isinstance(operations, list):
        operations = []
    assert [
        operation["id"]
        for operation in operations
        if isinstance(operation, dict)
    ] == ["getPet"]
