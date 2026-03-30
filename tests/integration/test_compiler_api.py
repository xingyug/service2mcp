"""Integration tests for compilation and service discovery API endpoints."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import httpx
import pytest
import pytest_asyncio
from celery.contrib.testing.worker import start_worker
from fastapi import FastAPI
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

import apps.compiler_api.routes.artifacts as artifact_routes
from apps.access_control.authn.service import JWTSettings, build_service_jwt
from apps.access_control.gateway_binding.client import InMemoryAPISIXAdminClient
from apps.access_control.main import create_app as create_access_control_app
from apps.compiler_api.dispatcher import CeleryCompilationDispatcher
from apps.compiler_api.repository import ArtifactRegistryRepository
from apps.compiler_api.route_publisher import AccessControlArtifactRoutePublisher
from apps.compiler_worker.celery_app import create_celery_app
from apps.compiler_worker.executor import (
    CallbackCompilationExecutor,
    configure_compilation_executor,
    reset_compilation_executor,
)
from apps.compiler_worker.models import (
    CompilationEventType,
    CompilationRequest,
    CompilationStage,
)
from apps.compiler_worker.repository import SQLAlchemyCompilationJobStore
from libs.db_models import Base, ServiceVersion
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

_TEST_COMPILER_API_JWT_SECRET = "integration-test-compiler-api-jwt-secret"

_TEST_COMPILER_API_JWT_SETTINGS = JWTSettings(secret=_TEST_COMPILER_API_JWT_SECRET)

os.environ.setdefault("ACCESS_CONTROL_JWT_SECRET", _TEST_COMPILER_API_JWT_SECRET)


def _create_compiler_api_app(**kwargs: Any) -> FastAPI:
    from apps.compiler_api.main import create_app

    return create_app(**kwargs)


def _compiler_api_auth_headers(
    subject: str = "tool-compiler-control-plane",
) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer "
            f"{build_service_jwt(subject=subject, jwt_settings=_TEST_COMPILER_API_JWT_SETTINGS)}"
        )
    }


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
    service_name: str,
    service_description: str,
    tenant: str,
    environment: str,
) -> dict[str, object]:
    service_ir = ServiceIR(
        source_hash="b" * 64,
        protocol="openapi",
        service_name=service_name,
        service_description=service_description,
        base_url=f"https://{service_name.lower().replace(' ', '-')}.example.com",
        auth=AuthConfig(type=AuthType.bearer, runtime_secret_ref=f"{service_name}-secret"),
        operations=[
            Operation(
                id="listItems",
                name="List Items",
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
        tenant=tenant,
        environment=environment,
    )
    return service_ir.model_dump(mode="json")


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


class RecordingDispatcher:
    """Dispatcher test double used to verify enqueue behavior."""

    def __init__(self) -> None:
        self.requests: list[CompilationRequest] = []

    async def enqueue(self, request: CompilationRequest) -> None:
        self.requests.append(request)


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
def dispatcher() -> RecordingDispatcher:
    return RecordingDispatcher()


@pytest.fixture
def app(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: RecordingDispatcher,
) -> FastAPI:
    return _create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=dispatcher,
        jwt_settings=_TEST_COMPILER_API_JWT_SETTINGS,
    )


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=_compiler_api_auth_headers(),
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_compiler_api_openapi_contract(http_client: httpx.AsyncClient) -> None:
    response = await http_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/api/v1/compilations" in schema["paths"]
    assert "post" in schema["paths"]["/api/v1/compilations"]
    assert "get" in schema["paths"]["/api/v1/compilations"]
    assert "/api/v1/compilations/{job_id}" in schema["paths"]
    assert "/api/v1/compilations/{job_id}/events" in schema["paths"]
    assert "/api/v1/services" in schema["paths"]
    assert "/api/v1/services/{service_id}" in schema["paths"]


@pytest.mark.asyncio
async def test_submit_compilation_creates_job_and_enqueues_request(
    http_client: httpx.AsyncClient,
    dispatcher: RecordingDispatcher,
) -> None:
    response = await http_client.post(
        "/api/v1/compilations",
        json={
            "source_url": "https://example.com/openapi.json",
            "created_by": "alice",
            "options": {"tenant": "team-a", "environment": "prod"},
        },
        headers=_compiler_api_auth_headers(subject="alice"),
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["source_url"] == "https://example.com/openapi.json"
    assert payload["current_stage"] is None

    assert len(dispatcher.requests) == 1
    queued_request = dispatcher.requests[0]
    assert queued_request.job_id is not None
    assert str(queued_request.job_id) == payload["id"]
    assert queued_request.options == {"tenant": "team-a", "environment": "prod"}

    fetched = await http_client.get(f"/api/v1/compilations/{payload['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["created_by"] == "alice"
    assert fetched.json()["tenant"] == "team-a"
    assert fetched.json()["environment"] == "prod"

    listed = await http_client.get("/api/v1/compilations")
    assert listed.status_code == 200
    jobs = listed.json()
    assert [job["id"] for job in jobs] == [payload["id"]]
    assert jobs[0]["tenant"] == "team-a"
    assert jobs[0]["environment"] == "prod"


@pytest.mark.asyncio
async def test_submit_compilation_uses_celery_dispatcher_in_eager_mode(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    worker_celery_app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )
    recorded_requests: list[CompilationRequest] = []

    async def record_request(request: CompilationRequest) -> None:
        recorded_requests.append(request)

    configure_compilation_executor(CallbackCompilationExecutor(callback=record_request))
    app = _create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=CeleryCompilationDispatcher(celery_app=worker_celery_app),
        jwt_settings=_TEST_COMPILER_API_JWT_SETTINGS,
    )
    transport = httpx.ASGITransport(app=app)

    try:
        with start_worker(
            worker_celery_app,
            pool="solo",
            concurrency=1,
            loglevel="INFO",
            perform_ping_check=False,
        ):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers=_compiler_api_auth_headers(subject="queue-user"),
            ) as http_client:
                response = await http_client.post(
                    "/api/v1/compilations",
                    json={
                        "source_url": "https://example.com/asyncapi.yaml",
                        "created_by": "queue-user",
                        "options": {"tenant": "team-b"},
                    },
                )

                assert response.status_code == 202
                payload = response.json()
                await _wait_for(lambda: len(recorded_requests) == 1)
                assert recorded_requests[0].job_id is not None
                assert str(recorded_requests[0].job_id) == payload["id"]
                assert recorded_requests[0].options == {"tenant": "team-b"}
    finally:
        reset_compilation_executor()


@pytest.mark.asyncio
async def test_compilation_events_endpoint_streams_sse_for_terminal_job(
    http_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SQLAlchemyCompilationJobStore(session_factory)
    request = CompilationRequest(source_url="https://example.com/petstore.yaml")
    job_id = await store.create_job(request)
    await store.append_event(job_id, event_type=CompilationEventType.JOB_STARTED)
    await store.mark_job_running(
        job_id,
        CompilationStage.DETECT,
        protocol="openapi",
        service_name="billing-api",
    )
    await store.append_event(
        job_id,
        event_type=CompilationEventType.STAGE_STARTED,
        stage=CompilationStage.DETECT,
        attempt=1,
    )
    await store.append_event(
        job_id,
        event_type=CompilationEventType.STAGE_SUCCEEDED,
        stage=CompilationStage.DETECT,
        attempt=1,
        detail={"detected_protocol": "openapi"},
    )
    await store.mark_job_succeeded(
        job_id,
        CompilationStage.REGISTER,
        protocol="openapi",
        service_name="billing-api",
    )
    await store.append_event(
        job_id,
        event_type=CompilationEventType.JOB_SUCCEEDED,
        stage=CompilationStage.REGISTER,
        detail={"service_name": "billing-api"},
    )

    sse_token = build_service_jwt(jwt_settings=_TEST_COMPILER_API_JWT_SETTINGS)
    async with http_client.stream(
        "GET",
        f"/api/v1/compilations/{job_id}/events",
        params={"token": sse_token},
    ) as response:
        body = ""
        async for chunk in response.aiter_text():
            body += chunk

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: job.started" in body
    assert "event: stage.succeeded" in body
    assert "event: job.succeeded" in body
    assert '"sequence_number":4' in body


@pytest.mark.asyncio
async def test_list_services_returns_active_services_with_filters(
    http_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = ArtifactRegistryRepository(session)
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="billing-api",
                version_number=1,
                ir_json=_build_ir(
                    service_name="Billing API",
                    service_description="Compiled billing service",
                    tenant="team-a",
                    environment="prod",
                ),
                tenant="team-a",
                environment="prod",
                deployment_revision="rev-billing-1",
            )
        )
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="ledger-api",
                version_number=1,
                ir_json=_build_ir(
                    service_name="Ledger API",
                    service_description="Compiled ledger service",
                    tenant="team-b",
                    environment="staging",
                ),
                tenant="team-b",
                environment="staging",
                deployment_revision="rev-ledger-1",
            )
        )
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="billing-api",
                version_number=2,
                ir_json=_build_ir(
                    service_name="Billing API v2",
                    service_description="Inactive draft",
                    tenant="team-a",
                    environment="prod",
                ),
                tenant="team-a",
                environment="prod",
                deployment_revision="rev-billing-2",
                is_active=False,
            )
        )
        await session.execute(
            update(ServiceVersion)
            .where(
                ServiceVersion.service_id == "billing-api",
                ServiceVersion.version_number == 1,
                ServiceVersion.tenant == "team-a",
                ServiceVersion.environment == "prod",
            )
            .values(created_at=datetime(2026, 3, 29, 0, 0, tzinfo=UTC))
        )
        await session.execute(
            update(ServiceVersion)
            .where(
                ServiceVersion.service_id == "billing-api",
                ServiceVersion.version_number == 2,
                ServiceVersion.tenant == "team-a",
                ServiceVersion.environment == "prod",
            )
            .values(created_at=datetime(2026, 3, 29, 1, 0, tzinfo=UTC))
        )
        await session.commit()

    response = await http_client.get("/api/v1/services")
    assert response.status_code == 200
    services = response.json()["services"]
    assert [service["service_id"] for service in services] == ["billing-api", "ledger-api"]
    assert services[0]["active_version"] == 1
    assert services[0]["version_count"] == 2
    assert services[0]["tool_count"] == 1
    assert services[0]["deployment_revision"] == "rev-billing-1"
    assert services[0]["created_at"].startswith("2026-03-29T01:00:00")

    filtered = await http_client.get("/api/v1/services", params={"tenant": "team-a"})
    assert filtered.status_code == 200
    filtered_services = filtered.json()["services"]
    assert [service["service_id"] for service in filtered_services] == ["billing-api"]
    assert filtered_services[0]["version_count"] == 2

    detail = await http_client.get("/api/v1/services/billing-api")
    assert detail.status_code == 200
    service = detail.json()
    assert service["service_id"] == "billing-api"
    assert service["service_name"] == "Billing API"
    assert service["version_count"] == 2
    assert service["created_at"].startswith("2026-03-29T01:00:00")

    missing = await http_client.get("/api/v1/services/missing-service")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_service_detail_returns_conflict_when_scope_is_ambiguous(
    http_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = ArtifactRegistryRepository(session)
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="billing-api",
                version_number=1,
                ir_json=_build_ir(
                    service_name="Billing API",
                    service_description="Compiled billing service",
                    tenant="team-a",
                    environment="prod",
                ),
                tenant="team-a",
                environment="prod",
            )
        )
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="billing-api",
                version_number=1,
                ir_json=_build_ir(
                    service_name="Billing API",
                    service_description="Compiled billing service",
                    tenant="team-b",
                    environment="staging",
                ),
                tenant="team-b",
                environment="staging",
            )
        )

    detail = await http_client.get("/api/v1/services/billing-api")

    assert detail.status_code == 409
    assert "matched multiple service versions" in detail.json()["detail"]


@pytest.mark.asyncio
async def test_unscoped_duplicate_versions_are_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = ArtifactRegistryRepository(session)
        await repository.create_version(
            ArtifactVersionCreate(
                service_id="billing-api",
                version_number=1,
                ir_json=_build_ir(
                    service_name="Billing API",
                    service_description="Compiled billing service",
                    tenant="global",
                    environment="shared",
                ),
            )
        )

        with pytest.raises(IntegrityError):
            await repository.create_version(
                ArtifactVersionCreate(
                    service_id="billing-api",
                    version_number=1,
                    ir_json=_build_ir(
                        service_name="Billing API",
                        service_description="Compiled billing service",
                        tenant="global",
                        environment="shared",
                    ),
                    is_active=False,
                ),
                commit=False,
            )


@pytest.mark.asyncio
async def test_unscoped_multiple_active_versions_are_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                ServiceVersion(
                    service_id="billing-api",
                    version_number=1,
                    is_active=True,
                    ir_json=_build_ir(
                        service_name="Billing API",
                        service_description="Compiled billing service",
                        tenant="global",
                        environment="shared",
                    ),
                ),
                ServiceVersion(
                    service_id="billing-api",
                    version_number=2,
                    is_active=True,
                    ir_json=_build_ir(
                        service_name="Billing API",
                        service_description="Compiled billing service",
                        tenant="global",
                        environment="shared",
                    ),
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_activate_artifact_version_syncs_gateway_routes(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: RecordingDispatcher,
) -> None:
    gateway_admin_client = InMemoryAPISIXAdminClient()
    jwt_settings = JWTSettings(secret="test-secret")
    access_control_app = create_access_control_app(
        session_factory=session_factory,
        jwt_settings=jwt_settings,
        gateway_admin_client=gateway_admin_client,
    )
    access_control_transport = httpx.ASGITransport(app=access_control_app)

    async with httpx.AsyncClient(
        transport=access_control_transport,
        base_url="http://access-control",
    ) as access_control_http_client:
        app = _create_compiler_api_app(
            session_factory=session_factory,
            compilation_dispatcher=dispatcher,
            route_publisher=AccessControlArtifactRoutePublisher(
                base_url="http://access-control",
                client=access_control_http_client,
                auth_token=build_service_jwt(jwt_settings=jwt_settings),
            ),
            jwt_settings=_TEST_COMPILER_API_JWT_SETTINGS,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_compiler_api_auth_headers(),
        ) as client:
            async with session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="billing-api",
                        version_number=1,
                        ir_json=_build_ir(
                            service_name="Billing API",
                            service_description="Compiled billing service",
                            tenant="team-a",
                            environment="prod",
                        ),
                        route_config=_build_route_config(
                            service_id="billing-api",
                            service_name="Billing API",
                            version_number=1,
                        ),
                    )
                )
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="billing-api",
                        version_number=2,
                        ir_json=_build_ir(
                            service_name="Billing API",
                            service_description="Compiled billing service",
                            tenant="team-a",
                            environment="prod",
                        ),
                        route_config=_build_route_config(
                            service_id="billing-api",
                            service_name="Billing API",
                            version_number=2,
                        ),
                        is_active=False,
                    )
                )

            response = await client.post("/api/v1/artifacts/billing-api/versions/2/activate")

            assert response.status_code == 200
            assert response.json()["version_number"] == 2
            assert (
                gateway_admin_client.routes["billing-api-active"].document["target_service"]["name"]
                == "billing-api-v2"
            )
            assert "billing-api-v2" in gateway_admin_client.routes


@pytest.mark.asyncio
async def test_delete_active_artifact_version_syncs_gateway_replacement(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: RecordingDispatcher,
) -> None:
    gateway_admin_client = InMemoryAPISIXAdminClient()
    jwt_settings = JWTSettings(secret="test-secret")
    access_control_app = create_access_control_app(
        session_factory=session_factory,
        jwt_settings=jwt_settings,
        gateway_admin_client=gateway_admin_client,
    )
    access_control_transport = httpx.ASGITransport(app=access_control_app)

    async with httpx.AsyncClient(
        transport=access_control_transport,
        base_url="http://access-control",
    ) as access_control_http_client:
        app = _create_compiler_api_app(
            session_factory=session_factory,
            compilation_dispatcher=dispatcher,
            route_publisher=AccessControlArtifactRoutePublisher(
                base_url="http://access-control",
                client=access_control_http_client,
                auth_token=build_service_jwt(jwt_settings=jwt_settings),
            ),
            jwt_settings=_TEST_COMPILER_API_JWT_SETTINGS,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_compiler_api_auth_headers(),
        ) as client:
            async with session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="ledger-api",
                        version_number=1,
                        ir_json=_build_ir(
                            service_name="Ledger API",
                            service_description="Compiled ledger service",
                            tenant="team-a",
                            environment="prod",
                        ),
                        route_config=_build_route_config(
                            service_id="ledger-api",
                            service_name="Ledger API",
                            version_number=1,
                        ),
                    )
                )
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="ledger-api",
                        version_number=2,
                        ir_json=_build_ir(
                            service_name="Ledger API",
                            service_description="Compiled ledger service",
                            tenant="team-a",
                            environment="prod",
                        ),
                        route_config=_build_route_config(
                            service_id="ledger-api",
                            service_name="Ledger API",
                            version_number=2,
                        ),
                        is_active=False,
                    )
                )

            response = await client.delete("/api/v1/artifacts/ledger-api/versions/1")

            assert response.status_code == 204
            assert (
                gateway_admin_client.routes["ledger-api-active"].document["target_service"]["name"]
                == "ledger-api-v2"
            )
            assert "ledger-api-v1" not in gateway_admin_client.routes
            assert "ledger-api-v2" in gateway_admin_client.routes


@pytest.mark.asyncio
async def test_activate_artifact_version_restores_routes_when_audit_fails(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: RecordingDispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway_admin_client = InMemoryAPISIXAdminClient()
    jwt_settings = JWTSettings(secret="test-secret")
    access_control_app = create_access_control_app(
        session_factory=session_factory,
        jwt_settings=jwt_settings,
        gateway_admin_client=gateway_admin_client,
    )
    access_control_transport = httpx.ASGITransport(app=access_control_app)

    async def _fail_audit(*args: object, **kwargs: object) -> object:
        raise RuntimeError("audit broke")

    monkeypatch.setattr(artifact_routes.AuditLogService, "append_entry", _fail_audit)

    async with httpx.AsyncClient(
        transport=access_control_transport,
        base_url="http://access-control",
    ) as access_control_http_client:
        route_publisher = AccessControlArtifactRoutePublisher(
            base_url="http://access-control",
            client=access_control_http_client,
            auth_token=build_service_jwt(jwt_settings=jwt_settings),
        )
        app = _create_compiler_api_app(
            session_factory=session_factory,
            compilation_dispatcher=dispatcher,
            route_publisher=route_publisher,
            jwt_settings=_TEST_COMPILER_API_JWT_SETTINGS,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_compiler_api_auth_headers(),
        ) as client:
            async with session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="billing-api",
                        version_number=1,
                        ir_json=_build_ir(
                            service_name="Billing API",
                            service_description="Compiled billing service",
                            tenant="team-a",
                            environment="prod",
                        ),
                        route_config=_build_route_config(
                            service_id="billing-api",
                            service_name="Billing API",
                            version_number=1,
                        ),
                    )
                )
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="billing-api",
                        version_number=2,
                        ir_json=_build_ir(
                            service_name="Billing API",
                            service_description="Compiled billing service",
                            tenant="team-a",
                            environment="prod",
                        ),
                        route_config=_build_route_config(
                            service_id="billing-api",
                            service_name="Billing API",
                            version_number=2,
                        ),
                        is_active=False,
                    )
                )

            await route_publisher.sync(
                _build_route_config(
                    service_id="billing-api",
                    service_name="Billing API",
                    version_number=1,
                )
            )

            response = await client.post("/api/v1/artifacts/billing-api/versions/2/activate")

            assert response.status_code == 502
            assert (
                gateway_admin_client.routes["billing-api-active"].document["target_service"]["name"]
                == "billing-api-v1"
            )
            assert "billing-api-v1" in gateway_admin_client.routes
            assert "billing-api-v2" not in gateway_admin_client.routes


@pytest.mark.asyncio
async def test_delete_active_artifact_version_restores_routes_when_audit_fails(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: RecordingDispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway_admin_client = InMemoryAPISIXAdminClient()
    jwt_settings = JWTSettings(secret="test-secret")
    access_control_app = create_access_control_app(
        session_factory=session_factory,
        jwt_settings=jwt_settings,
        gateway_admin_client=gateway_admin_client,
    )
    access_control_transport = httpx.ASGITransport(app=access_control_app)

    async def _fail_audit(*args: object, **kwargs: object) -> object:
        raise RuntimeError("audit broke")

    monkeypatch.setattr(artifact_routes.AuditLogService, "append_entry", _fail_audit)

    async with httpx.AsyncClient(
        transport=access_control_transport,
        base_url="http://access-control",
    ) as access_control_http_client:
        route_publisher = AccessControlArtifactRoutePublisher(
            base_url="http://access-control",
            client=access_control_http_client,
            auth_token=build_service_jwt(jwt_settings=jwt_settings),
        )
        app = _create_compiler_api_app(
            session_factory=session_factory,
            compilation_dispatcher=dispatcher,
            route_publisher=route_publisher,
            jwt_settings=_TEST_COMPILER_API_JWT_SETTINGS,
        )
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_compiler_api_auth_headers(),
        ) as client:
            async with session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="ledger-api",
                        version_number=1,
                        ir_json=_build_ir(
                            service_name="Ledger API",
                            service_description="Compiled ledger service",
                            tenant="team-a",
                            environment="prod",
                        ),
                        route_config=_build_route_config(
                            service_id="ledger-api",
                            service_name="Ledger API",
                            version_number=1,
                        ),
                    )
                )
                await repository.create_version(
                    ArtifactVersionCreate(
                        service_id="ledger-api",
                        version_number=2,
                        ir_json=_build_ir(
                            service_name="Ledger API",
                            service_description="Compiled ledger service",
                            tenant="team-a",
                            environment="prod",
                        ),
                        route_config=_build_route_config(
                            service_id="ledger-api",
                            service_name="Ledger API",
                            version_number=2,
                        ),
                        is_active=False,
                    )
                )

            await route_publisher.sync(
                _build_route_config(
                    service_id="ledger-api",
                    service_name="Ledger API",
                    version_number=1,
                )
            )

            response = await client.delete("/api/v1/artifacts/ledger-api/versions/1")

            assert response.status_code == 502
            assert (
                gateway_admin_client.routes["ledger-api-active"].document["target_service"]["name"]
                == "ledger-api-v1"
            )
            assert "ledger-api-v1" in gateway_admin_client.routes
            assert "ledger-api-v2" not in gateway_admin_client.routes


async def _wait_for(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("Timed out while waiting for background worker progress.")
