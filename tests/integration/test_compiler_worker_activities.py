"""Integration coverage for production compiler worker activities."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.access_control.authn.service import JWTSettings
from apps.access_control.gateway_binding.client import HTTPGatewayAdminClient
from apps.access_control.main import create_app as create_access_control_app
from apps.compiler_api.repository import ArtifactRegistryRepository
from apps.compiler_worker.activities import (
    AccessControlRoutePublisher,
    DeferredRoutePublisher,
    DeploymentResult,
    KubernetesAPISession,
    KubernetesManifestDeployer,
    ProductionActivitySettings,
    create_default_activity_registry,
)
from apps.compiler_worker.activities.production import (
    _apply_post_enhancement,
    _build_sample_invocations,
)
from apps.compiler_worker.models import (
    CompilationContext,
    CompilationRequest,
    CompilationStage,
    CompilationStatus,
)
from apps.compiler_worker.repository import SQLAlchemyCompilationJobStore
from apps.compiler_worker.workflows import CompilationWorkflow, CompilationWorkflowError
from apps.gateway_admin_mock.main import create_app as create_gateway_admin_mock_app
from apps.mcp_runtime.main import create_app as create_runtime_app
from libs.db_models import Base
from libs.generator import GeneratedManifestSet, GenericManifestConfig, generate_generic_manifests
from libs.ir import ServiceIR, serialize_ir
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    SourceType,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
    ToolIntent,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
OPENAPI_FIXTURES_DIR = FIXTURES_DIR / "openapi_specs"
IR_FIXTURES_DIR = FIXTURES_DIR / "ir"
PETSTORE_SPEC_PATH = OPENAPI_FIXTURES_DIR / "petstore_3_0.yaml"


def _build_supported_grpc_stream_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="1" * 64,
        protocol="grpc",
        service_name="grpc-stream-runtime",
        service_description="gRPC stream worker fixture",
        base_url="grpc://inventory.example.test:443",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="watchInventory",
                name="Watch Inventory",
                description="Consume a native gRPC inventory stream.",
                method="POST",
                path="/catalog.v1.InventoryService/WatchInventory",
                params=[Param(name="payload", type="object", required=False)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        event_descriptors=[
            EventDescriptor(
                id="WatchInventory",
                name="WatchInventory",
                transport=EventTransport.grpc_stream,
                support=EventSupportLevel.supported,
                operation_id="watchInventory",
                channel="/catalog.v1.InventoryService/WatchInventory",
                grpc_stream=GrpcStreamRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/WatchInventory",
                    mode=GrpcStreamMode.server,
                ),
            )
        ],
    )


def _build_native_grpc_unary_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="9" * 64,
        protocol="grpc",
        service_name="grpc-unary-runtime",
        service_description="gRPC unary worker fixture",
        base_url="grpc://inventory.example.test:443",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="LookupInventory",
                name="Lookup Inventory",
                description="Execute a native gRPC inventory lookup.",
                method="POST",
                path="/catalog.v1.InventoryService/LookupInventory",
                params=[Param(name="sku", type="string", required=True)],
                grpc_unary=GrpcUnaryRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/LookupInventory"
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
    )


def _build_native_grpc_unary_ir_with_optional_nested_params() -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol="grpc",
        service_name="grpc-list-items-runtime",
        service_description="gRPC unary worker fixture with nested request fields",
        base_url="grpc://inventory.example.test:443",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="ListItems",
                name="List Items",
                description="List catalog items through a unary gRPC call.",
                method="POST",
                path="/catalog.v1.InventoryService/ListItems",
                params=[
                    Param(name="location_id", type="string", required=False),
                    Param(name="page_size", type="integer", required=False),
                    Param(name="page_token", type="string", required=False),
                    Param(name="filter", type="object", required=False),
                    Param(name="reason", type="string", required=False),
                ],
                grpc_unary=GrpcUnaryRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/ListItems"
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
    )


def _build_rest_discovery_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="2" * 64,
        protocol="rest",
        service_name="rest-discovery-runtime",
        service_description="REST discovery worker fixture",
        base_url="https://catalog.example.test/catalog",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="get_products_product_id",
                name="Get Product",
                description="Fetch one catalog product.",
                method="GET",
                path="/products/{product_id}",
                params=[
                    Param(name="product_id", type="string", required=True),
                    Param(name="view", type="string", required=False, default="detail"),
                ],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        metadata={
            "base_path": "/catalog",
            "discovery_entrypoint": "https://catalog.example.test/catalog",
        },
    )


def _build_graphql_query_and_mutation_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="3" * 64,
        protocol="graphql",
        service_name="graphql-runtime",
        service_description="GraphQL worker fixture",
        base_url="https://catalog.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="searchProducts",
                name="Search Products",
                description="Search the catalog.",
                method="POST",
                path="/graphql",
                params=[
                    Param(name="term", type="string", required=True),
                    Param(name="limit", type="integer", required=False, default=10),
                ],
                graphql=GraphQLOperationConfig(
                    operation_type=GraphQLOperationType.query,
                    operation_name="searchProducts",
                    document=(
                        "query searchProducts($term: String!, $limit: Int) {"
                        " searchProducts(term: $term, limit: $limit) { id name } }"
                    ),
                    variable_names=["term", "limit"],
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
            Operation(
                id="adjustInventory",
                name="Adjust Inventory",
                description="Adjust stock.",
                method="POST",
                path="/graphql",
                params=[
                    Param(name="sku", type="string", required=True),
                    Param(name="delta", type="integer", required=True),
                ],
                graphql=GraphQLOperationConfig(
                    operation_type=GraphQLOperationType.mutation,
                    operation_name="adjustInventory",
                    document=(
                        "mutation adjustInventory($sku: String!, $delta: Int!) {"
                        " adjustInventory(sku: $sku, delta: $delta) { sku } }"
                    ),
                    variable_names=["sku", "delta"],
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=False,
                ),
                enabled=True,
            ),
        ],
    )


def _build_sql_query_and_insert_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="4" * 64,
        protocol="sql",
        service_name="sql-runtime",
        service_description="SQL worker fixture",
        base_url="sqlite:///memory",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="query_orders",
                name="Query Orders",
                description="Query orders.",
                method="GET",
                path="/orders",
                params=[
                    Param(name="customer_id", type="integer", required=False),
                    Param(name="limit", type="integer", required=False, default=50),
                ],
                sql=SqlOperationConfig(
                    schema_name="main",
                    relation_name="orders",
                    relation_kind=SqlRelationKind.table,
                    action=SqlOperationType.query,
                    filterable_columns=["customer_id"],
                    default_limit=50,
                    max_limit=200,
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
            Operation(
                id="insert_orders",
                name="Insert Orders",
                description="Insert one order.",
                method="POST",
                path="/orders",
                params=[
                    Param(name="customer_id", type="integer", required=True),
                    Param(name="total_cents", type="integer", required=True),
                    Param(name="notes", type="string", required=False),
                ],
                sql=SqlOperationConfig(
                    schema_name="main",
                    relation_name="orders",
                    relation_kind=SqlRelationKind.table,
                    action=SqlOperationType.insert,
                    insertable_columns=["customer_id", "total_cents", "notes"],
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=False,
                ),
                enabled=True,
            ),
        ],
    )


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


@dataclass
class DeployedRuntime:
    """In-memory representation of a deployed runtime revision."""

    revision: str
    app: FastAPI
    upstream_client: httpx.AsyncClient


class RuntimeDeploymentHarness:
    """Deploy runtime revisions into in-memory ASGI apps for activity integration tests."""

    def __init__(
        self,
        *,
        tmp_path: Path,
        upstream_handler: Callable[[httpx.Request], Awaitable[httpx.Response]],
    ) -> None:
        self._tmp_path = tmp_path
        self._upstream_handler = upstream_handler
        self._deployments: dict[str, DeployedRuntime] = {}
        self._active_revision: str | None = None

    async def deploy_from_manifest(self, manifest_payload: dict[str, Any]) -> DeploymentResult:
        config_map = cast(dict[str, Any], manifest_payload["config_map"])
        service = cast(dict[str, Any], manifest_payload["service"])
        service_ir = ServiceIR.model_validate(json.loads(config_map["data"]["service-ir.json"]))
        revision = str(service["metadata"]["name"])
        ir_path = self._tmp_path / f"{revision}.json"
        ir_path.write_text(serialize_ir(service_ir), encoding="utf-8")

        upstream_client = httpx.AsyncClient(
            transport=httpx.MockTransport(cast(Any, self._upstream_handler))
        )
        app = create_runtime_app(service_ir_path=ir_path, upstream_client=upstream_client)
        self._deployments[revision] = DeployedRuntime(
            revision=revision,
            app=app,
            upstream_client=upstream_client,
        )
        self._active_revision = revision
        service_port = int(service["spec"]["ports"][0]["port"])
        return DeploymentResult(
            deployment_revision=revision,
            runtime_base_url=f"http://runtime:{service_port}",
            manifest_storage_path=f"memory://manifests/{revision}.yaml",
        )

    def runtime_http_client_factory(self, base_url: str) -> httpx.AsyncClient:
        current = self.current()
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=current.app),
            base_url=base_url,
            follow_redirects=True,
        )

    def tool_invoker_factory(
        self,
        base_url: str,
    ) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
        del base_url

        async def invoke(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            _, structured = await self.current().app.state.runtime_state.mcp_server.call_tool(
                tool_name,
                arguments,
            )
            return cast(dict[str, Any], structured)

        return invoke

    async def rollback(self, revision: str) -> None:
        deployment = self._deployments.pop(revision, None)
        if deployment is None:
            return
        await deployment.upstream_client.aclose()
        if self._active_revision == revision:
            self._active_revision = None

    def current(self) -> DeployedRuntime:
        if self._active_revision is None:
            raise RuntimeError("No runtime revision has been deployed.")
        return self._deployments[self._active_revision]

    async def aclose(self) -> None:
        for revision in list(self._deployments):
            await self.rollback(revision)


@dataclass
class RuntimeStartupLag:
    """Shared mutable state for simulating runtime readiness propagation lag."""

    remaining_failures: int


class FlakyRuntimeTransport(httpx.AsyncBaseTransport):
    """ASGI transport that raises connect errors until readiness lag is exhausted."""

    def __init__(self, app: FastAPI, state: RuntimeStartupLag) -> None:
        self._transport = httpx.ASGITransport(app=app)
        self._state = state

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/healthz", "/readyz"} and self._state.remaining_failures > 0:
            self._state.remaining_failures -= 1
            raise httpx.ConnectError("runtime not reachable yet", request=request)
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


def test_build_sample_invocations_uses_discovery_defaults_for_rest_protocol() -> None:
    sample_invocations = _build_sample_invocations(_build_rest_discovery_ir())

    assert sample_invocations == {
        "get_products_product_id": {
            "product_id": "1",
            "view": "detail",
        }
    }


def test_build_sample_invocations_use_graphql_required_and_default_variables() -> None:
    sample_invocations = _build_sample_invocations(_build_graphql_query_and_mutation_ir())

    assert sample_invocations == {
        "searchProducts": {
            "term": "sample",
            "limit": 10,
        },
        "adjustInventory": {
            "sku": "sample",
            "delta": 1,
        },
    }


def test_build_sample_invocations_use_safe_sql_query_defaults() -> None:
    sample_invocations = _build_sample_invocations(_build_sql_query_and_insert_ir())

    assert sample_invocations == {
        "query_orders": {
            "limit": 50,
        },
        "insert_orders": {
            "customer_id": 1,
            "total_cents": 1,
        },
    }


def test_build_sample_invocations_omit_unsafe_optional_grpc_nested_fields() -> None:
    sample_invocations = _build_sample_invocations(
        _build_native_grpc_unary_ir_with_optional_nested_params()
    )

    assert sample_invocations == {
        "ListItems": {
            "location_id": "1",
            "page_size": 1,
            "page_token": "sample",
        }
    }


@dataclass
class HarnessManifestDeployer:
    """Manifest deployer backed by the in-memory runtime harness."""

    harness: RuntimeDeploymentHarness

    async def deploy(self, manifest_set: GeneratedManifestSet) -> DeploymentResult:
        manifest_payload: dict[str, Any] = {
            "config_map": manifest_set.config_map,
            "deployment": manifest_set.deployment,
            "service": manifest_set.service,
            "network_policy": manifest_set.network_policy,
            "route_config": manifest_set.route_config,
            "yaml": manifest_set.yaml,
        }
        return await self.harness.deploy_from_manifest(manifest_payload)

    async def rollback(
        self,
        manifest_set: GeneratedManifestSet,
        deployment: DeploymentResult,
    ) -> None:
        del manifest_set
        await self.harness.rollback(deployment.deployment_revision)


class FakeKubernetesAPIServer:
    """Minimal in-memory Kubernetes API surface for deployer tests."""

    def __init__(self) -> None:
        self.resources: dict[tuple[str, str], dict[str, Any]] = {}
        self.resource_version = 0

    def create_app(self) -> FastAPI:
        app = FastAPI()

        @app.patch("/{prefix:path}/namespaces/{namespace}/{plural}/{name}")
        async def patch_resource(
            prefix: str,
            namespace: str,
            plural: str,
            name: str,
            request: Request,
        ) -> dict[str, Any]:
            del prefix
            key = (plural, f"{namespace}/{name}")
            if key not in self.resources:
                raise HTTPException(status_code=404)
            payload = cast(dict[str, Any], await request.json())
            self.resources[key] = self._normalize_resource(payload, plural)
            return self.resources[key]

        @app.post("/{prefix:path}/namespaces/{namespace}/{plural}")
        async def create_resource(
            prefix: str,
            namespace: str,
            plural: str,
            request: Request,
        ) -> JSONResponse:
            del prefix
            payload = cast(dict[str, Any], await request.json())
            name = str(payload["metadata"]["name"])
            key = (plural, f"{namespace}/{name}")
            self.resources[key] = self._normalize_resource(payload, plural)
            return JSONResponse(status_code=201, content=self.resources[key])

        @app.get("/{prefix:path}/namespaces/{namespace}/{plural}/{name}")
        async def get_resource(
            prefix: str,
            namespace: str,
            plural: str,
            name: str,
        ) -> dict[str, Any]:
            del prefix
            key = (plural, f"{namespace}/{name}")
            resource = self.resources.get(key)
            if resource is None:
                raise HTTPException(status_code=404)
            return resource

        @app.delete("/{prefix:path}/namespaces/{namespace}/{plural}/{name}")
        async def delete_resource(
            prefix: str,
            namespace: str,
            plural: str,
            name: str,
        ) -> dict[str, str]:
            del prefix
            key = (plural, f"{namespace}/{name}")
            self.resources.pop(key, None)
            return {"status": "deleted"}

        return app

    def _normalize_resource(self, payload: dict[str, Any], plural: str) -> dict[str, Any]:
        self.resource_version += 1
        normalized = cast(dict[str, Any], json.loads(json.dumps(payload)))
        metadata = normalized.setdefault("metadata", {})
        generation = int(metadata.get("generation", 0) or 0) + 1
        metadata["generation"] = generation
        metadata["resourceVersion"] = str(self.resource_version)
        if plural == "deployments":
            replicas = int(normalized["spec"].get("replicas", 1))
            normalized["status"] = {
                "observedGeneration": generation,
                "availableReplicas": replicas,
            }
        return normalized


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
async def test_default_activity_registry_executes_full_pipeline_with_runtime_harness(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    petstore_spec_payload = yaml.safe_load(PETSTORE_SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(petstore_spec_payload, dict)
    petstore_spec_payload.pop("security", None)
    components = petstore_spec_payload.get("components")
    if isinstance(components, dict):
        components.pop("securitySchemes", None)
    petstore_spec = yaml.safe_dump(petstore_spec_payload, sort_keys=False)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "doggie", "status": "available"}],
                request=request,
            )
        return httpx.Response(200, json={"id": 1, "status": "ok"}, request=request)

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )
    deployer = HarnessManifestDeployer(harness=deployment_harness)
    workflow = CompilationWorkflow(
        store=SQLAlchemyCompilationJobStore(session_factory),
        activities=create_default_activity_registry(
            session_factory=session_factory,
            settings=ProductionActivitySettings(
                runtime_image="tool-compiler/mcp-runtime:test",
                namespace="test-ns",
                route_publish_mode="deferred",
            ),
            deployer=deployer,
            route_publisher=DeferredRoutePublisher(),
            runtime_http_client_factory=deployment_harness.runtime_http_client_factory,
            tool_invoker_factory=deployment_harness.tool_invoker_factory,
        ),
    )

    try:
        result = await workflow.run(
            CompilationRequest(
                source_url="https://example.com/petstore.yaml",
                source_content=petstore_spec,
                created_by="integration-user",
                service_name="petstore-api",
            )
        )

        assert result.status is CompilationStatus.SUCCEEDED
        assert result.payload["service_id"] == "petstore-api"
        assert result.payload["version_number"] == 1
        assert result.payload["deployment_revision"] == deployment_harness.current().revision
        assert result.payload["route_publication"]["mode"] == "deferred"

        async with session_factory() as session:
            repository = ArtifactRegistryRepository(session)
            active = await repository.get_active_version("petstore-api")

        assert active is not None
        assert active.version_number == 1
        assert active.deployment_revision == result.payload["deployment_revision"]
        assert active.route_config is not None
        assert active.route_config["default_route"]["route_id"] == "petstore-api-active"
        assert active.validation_report is not None
        assert active.validation_report["overall_passed"] is True

        # Verify tool_intent derivation and description bifurcation ran in pipeline.
        final_ir = ServiceIR.model_validate(result.payload["service_ir"])
        for op in final_ir.operations:
            assert op.tool_intent is not None, f"tool_intent not set on {op.id}"
            assert op.description.startswith("[DISCOVERY] ") or op.description.startswith(
                "[ACTION] "
            ), f"description not bifurcated on {op.id}"
    finally:
        await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_default_activity_registry_allows_supported_native_grpc_stream_ir(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = create_default_activity_registry(session_factory=session_factory)
    service_ir = _build_supported_grpc_stream_ir()
    request = CompilationRequest(service_name="grpc-stream-runtime")
    context = CompilationContext(
        job_id=request.job_id or uuid4(),
        request=request,
        payload={"service_ir": service_ir.model_dump(mode="json")},
        protocol=service_ir.protocol,
        service_name=service_ir.service_name,
    )

    result = await registry.run_stage(CompilationStage.VALIDATE_IR, context)

    assert result.context_updates["pre_validation_report"]["overall_passed"] is True
    assert "grpc_stream" in result.context_updates["pre_validation_report"]["results"][1]["details"]


@pytest.mark.asyncio
async def test_generate_stage_enables_native_grpc_stream_in_runtime_manifest(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = create_default_activity_registry(session_factory=session_factory)
    service_ir = _build_supported_grpc_stream_ir()
    request = CompilationRequest(service_name="grpc-stream-runtime")
    context = CompilationContext(
        job_id=request.job_id or uuid4(),
        request=request,
        payload={
            "service_ir": service_ir.model_dump(mode="json"),
            "service_id": "grpc-stream-runtime",
            "version_number": 1,
        },
        protocol=service_ir.protocol,
        service_name=service_ir.service_name,
    )

    result = await registry.run_stage(CompilationStage.GENERATE, context)

    manifest_set = cast(dict[str, Any], result.context_updates["generated_manifest_set"])
    env_entries = {
        item["name"]: item["value"]
        for item in manifest_set["deployment"]["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env_entries["ENABLE_NATIVE_GRPC_STREAM"] == "true"


@pytest.mark.asyncio
async def test_default_activity_registry_allows_native_grpc_unary_ir(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = create_default_activity_registry(session_factory=session_factory)
    service_ir = _build_native_grpc_unary_ir()
    request = CompilationRequest(service_name="grpc-unary-runtime")
    context = CompilationContext(
        job_id=request.job_id or uuid4(),
        request=request,
        payload={"service_ir": service_ir.model_dump(mode="json")},
        protocol=service_ir.protocol,
        service_name=service_ir.service_name,
    )

    result = await registry.run_stage(CompilationStage.VALIDATE_IR, context)

    assert result.context_updates["pre_validation_report"]["overall_passed"] is True
    assert "grpc_unary" in result.context_updates["pre_validation_report"]["results"][1]["details"]


@pytest.mark.asyncio
async def test_generate_stage_enables_native_grpc_unary_in_runtime_manifest(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = create_default_activity_registry(session_factory=session_factory)
    service_ir = _build_native_grpc_unary_ir()
    request = CompilationRequest(service_name="grpc-unary-runtime")
    context = CompilationContext(
        job_id=request.job_id or uuid4(),
        request=request,
        payload={
            "service_ir": service_ir.model_dump(mode="json"),
            "service_id": "grpc-unary-runtime",
            "version_number": 1,
        },
        protocol=service_ir.protocol,
        service_name=service_ir.service_name,
    )

    result = await registry.run_stage(CompilationStage.GENERATE, context)

    manifest_set = cast(dict[str, Any], result.context_updates["generated_manifest_set"])
    env_entries = {
        item["name"]: item["value"]
        for item in manifest_set["deployment"]["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env_entries["ENABLE_NATIVE_GRPC_UNARY"] == "true"


@pytest.mark.asyncio
async def test_default_activity_registry_waits_for_runtime_readiness_before_validation(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    petstore_spec_payload = yaml.safe_load(PETSTORE_SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(petstore_spec_payload, dict)
    petstore_spec_payload.pop("security", None)
    components = petstore_spec_payload.get("components")
    if isinstance(components, dict):
        components.pop("securitySchemes", None)
    petstore_spec = yaml.safe_dump(petstore_spec_payload, sort_keys=False)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "doggie", "status": "available"}],
                request=request,
            )
        return httpx.Response(200, json={"id": 1, "status": "ok"}, request=request)

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )
    deployer = HarnessManifestDeployer(harness=deployment_harness)
    startup_lag = RuntimeStartupLag(remaining_failures=5)

    def flaky_runtime_http_client_factory(base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=FlakyRuntimeTransport(deployment_harness.current().app, startup_lag),
            base_url=base_url,
            follow_redirects=True,
        )

    workflow = CompilationWorkflow(
        store=SQLAlchemyCompilationJobStore(session_factory),
        activities=create_default_activity_registry(
            session_factory=session_factory,
            settings=ProductionActivitySettings(
                runtime_image="tool-compiler/mcp-runtime:test",
                namespace="test-ns",
                route_publish_mode="deferred",
                runtime_startup_timeout_seconds=1.0,
                runtime_startup_poll_seconds=0.01,
            ),
            deployer=deployer,
            route_publisher=DeferredRoutePublisher(),
            runtime_http_client_factory=flaky_runtime_http_client_factory,
            tool_invoker_factory=deployment_harness.tool_invoker_factory,
        ),
    )

    try:
        result = await workflow.run(
            CompilationRequest(
                source_url="https://example.com/petstore.yaml",
                source_content=petstore_spec,
                created_by="integration-user",
                service_name="petstore-api",
            )
        )
        assert result.status is CompilationStatus.SUCCEEDED
        assert startup_lag.remaining_failures == 0
    finally:
        await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_default_activity_registry_publishes_routes_via_access_control(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    petstore_spec_payload = yaml.safe_load(PETSTORE_SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(petstore_spec_payload, dict)
    petstore_spec_payload.pop("security", None)
    components = petstore_spec_payload.get("components")
    if isinstance(components, dict):
        components.pop("securitySchemes", None)
    petstore_spec = yaml.safe_dump(petstore_spec_payload, sort_keys=False)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "doggie", "status": "available"}],
                request=request,
            )
        return httpx.Response(200, json={"id": 1, "status": "ok"}, request=request)

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )
    deployer = HarnessManifestDeployer(harness=deployment_harness)
    gateway_admin_app = create_gateway_admin_mock_app()
    gateway_admin_transport = httpx.ASGITransport(app=gateway_admin_app)
    async with httpx.AsyncClient(
        transport=gateway_admin_transport,
        base_url="http://gateway-admin",
    ) as gateway_admin_http_client:
        access_control_app = create_access_control_app(
            session_factory=session_factory,
            jwt_settings=JWTSettings(secret="test-secret"),
            gateway_admin_client=HTTPGatewayAdminClient(
                base_url="http://gateway-admin",
                client=gateway_admin_http_client,
            ),
        )
        access_control_transport = httpx.ASGITransport(app=access_control_app)
        async with httpx.AsyncClient(
            transport=access_control_transport,
            base_url="http://access-control",
        ) as access_control_http_client:
            workflow = CompilationWorkflow(
                store=SQLAlchemyCompilationJobStore(session_factory),
                activities=create_default_activity_registry(
                    session_factory=session_factory,
                    settings=ProductionActivitySettings(
                        runtime_image="tool-compiler/mcp-runtime:test",
                        namespace="test-ns",
                        route_publish_mode="access-control",
                        access_control_url="http://access-control",
                    ),
                    deployer=deployer,
                    route_publisher=AccessControlRoutePublisher(
                        base_url="http://access-control",
                        client=access_control_http_client,
                    ),
                    runtime_http_client_factory=deployment_harness.runtime_http_client_factory,
                    tool_invoker_factory=deployment_harness.tool_invoker_factory,
                ),
            )

            try:
                result = await workflow.run(
                    CompilationRequest(
                        source_url="https://example.com/petstore.yaml",
                        source_content=petstore_spec,
                        created_by="integration-user",
                        service_name="petstore-api",
                    )
                )

                assert result.status is CompilationStatus.SUCCEEDED
                assert result.payload["route_publication"]["mode"] == "access-control"
                assert result.payload["route_publication"]["service_routes_synced"] == 2

                listed_routes = await gateway_admin_http_client.get("/admin/routes")
                assert listed_routes.status_code == 200
                routes = {item["route_id"]: item for item in listed_routes.json()["items"]}
                assert "petstore-api-active" in routes
                assert "petstore-api-v1" in routes
                assert routes["petstore-api-active"]["document"]["target_service"]["name"] == (
                    "petstore-api-v1"
                )
            finally:
                await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_route_rollback_restores_previous_active_route_when_register_fails(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    petstore_spec_payload = yaml.safe_load(PETSTORE_SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(petstore_spec_payload, dict)
    petstore_spec_payload.pop("security", None)
    components = petstore_spec_payload.get("components")
    if isinstance(components, dict):
        components.pop("securitySchemes", None)
    petstore_spec = yaml.safe_dump(petstore_spec_payload, sort_keys=False)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "doggie", "status": "available"}],
                request=request,
            )
        return httpx.Response(200, json={"id": 1, "status": "ok"}, request=request)

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )
    deployer = HarnessManifestDeployer(harness=deployment_harness)
    gateway_admin_app = create_gateway_admin_mock_app()
    gateway_admin_transport = httpx.ASGITransport(app=gateway_admin_app)
    async with httpx.AsyncClient(
        transport=gateway_admin_transport,
        base_url="http://gateway-admin",
    ) as gateway_admin_http_client:
        access_control_app = create_access_control_app(
            session_factory=session_factory,
            jwt_settings=JWTSettings(secret="test-secret"),
            gateway_admin_client=HTTPGatewayAdminClient(
                base_url="http://gateway-admin",
                client=gateway_admin_http_client,
            ),
        )
        access_control_transport = httpx.ASGITransport(app=access_control_app)
        async with httpx.AsyncClient(
            transport=access_control_transport,
            base_url="http://access-control",
        ) as access_control_http_client:
            settings = ProductionActivitySettings(
                runtime_image="tool-compiler/mcp-runtime:test",
                namespace="test-ns",
                route_publish_mode="access-control",
                access_control_url="http://access-control",
            )

            success_workflow = CompilationWorkflow(
                store=SQLAlchemyCompilationJobStore(session_factory),
                activities=create_default_activity_registry(
                    session_factory=session_factory,
                    settings=settings,
                    deployer=deployer,
                    route_publisher=AccessControlRoutePublisher(
                        base_url="http://access-control",
                        client=access_control_http_client,
                    ),
                    runtime_http_client_factory=deployment_harness.runtime_http_client_factory,
                    tool_invoker_factory=deployment_harness.tool_invoker_factory,
                ),
            )

            failing_activities = create_default_activity_registry(
                session_factory=session_factory,
                settings=settings,
                deployer=deployer,
                route_publisher=AccessControlRoutePublisher(
                    base_url="http://access-control",
                    client=access_control_http_client,
                ),
                runtime_http_client_factory=deployment_harness.runtime_http_client_factory,
                tool_invoker_factory=deployment_harness.tool_invoker_factory,
            )

            async def fail_register(*_: Any) -> Any:
                raise RuntimeError("forced register failure")

            failing_activities.stage_handlers[CompilationStage.REGISTER] = fail_register
            failing_store = SQLAlchemyCompilationJobStore(session_factory)
            failing_workflow = CompilationWorkflow(
                store=failing_store,
                activities=failing_activities,
            )

            try:
                initial = await success_workflow.run(
                    CompilationRequest(
                        source_url="https://example.com/petstore.yaml",
                        source_content=petstore_spec,
                        created_by="integration-user",
                        service_name="petstore-api",
                    )
                )
                assert initial.status is CompilationStatus.SUCCEEDED

                with pytest.raises(CompilationWorkflowError) as exc_info:
                    await failing_workflow.run(
                        CompilationRequest(
                            source_url="https://example.com/petstore.yaml",
                            source_content=petstore_spec,
                            created_by="integration-user",
                            service_name="petstore-api",
                        )
                    )
                assert exc_info.value.failed_stage is CompilationStage.REGISTER
                assert exc_info.value.final_status is CompilationStatus.ROLLED_BACK
                failed_job = await failing_store.get_job(exc_info.value.job_id)
                assert failed_job is not None
                assert failed_job.status is CompilationStatus.ROLLED_BACK

                listed_routes = await gateway_admin_http_client.get("/admin/routes")
                assert listed_routes.status_code == 200
                routes = {item["route_id"]: item for item in listed_routes.json()["items"]}
                assert routes["petstore-api-active"]["document"]["target_service"]["name"] == (
                    "petstore-api-v1"
                )
                assert "petstore-api-v2" not in routes
            finally:
                await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_kubernetes_manifest_deployer_applies_and_rolls_back_resources() -> None:
    fake_api = FakeKubernetesAPIServer()
    app = fake_api.create_app()
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://kubernetes.test",
    )
    api = KubernetesAPISession(client=http_client, namespace="test-ns")
    deployer = KubernetesManifestDeployer(
        api=api,
        owns_api_client=False,
        rollout_poll_seconds=0.01,
        rollout_timeout_seconds=1.0,
    )

    service_ir = ServiceIR.model_validate_json(
        (IR_FIXTURES_DIR / "service_ir_valid.json").read_text(encoding="utf-8")
    )
    manifest_set = generate_generic_manifests(
        service_ir,
        config=GenericManifestConfig(
            runtime_image="tool-compiler/mcp-runtime:test",
            service_id="billing-api",
            version_number=2,
            namespace="test-ns",
        ),
    )

    try:
        deployment = await deployer.deploy(manifest_set)

        assert deployment.deployment_revision.startswith("billing-runtime-v2@")
        assert (
            deployment.runtime_base_url
            == "http://billing-runtime-v2.test-ns.svc.cluster.local:8003"
        )
        assert ("configmaps", "test-ns/billing-runtime-v2-ir") in fake_api.resources
        assert ("deployments", "test-ns/billing-runtime-v2") in fake_api.resources
        assert ("services", "test-ns/billing-runtime-v2") in fake_api.resources
        assert ("networkpolicies", "test-ns/billing-runtime-v2") in fake_api.resources

        await deployer.rollback(manifest_set, deployment)

        assert fake_api.resources == {}
    finally:
        await http_client.aclose()


def test_apply_post_enhancement_sets_tool_intent_and_bifurcates_descriptions() -> None:
    """Verify _apply_post_enhancement tags intents and bifurcates descriptions."""
    ir = ServiceIR(
        source_hash="a" * 64,
        protocol="rest",
        service_name="intent-test",
        service_description="Fixture for intent derivation test",
        base_url="https://example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="listItems",
                name="List Items",
                description="List all items.",
                method="GET",
                path="/items",
                params=[],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
            Operation(
                id="createItem",
                name="Create Item",
                description="Create a new item.",
                method="POST",
                path="/items",
                params=[Param(name="name", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=False,
                ),
                enabled=True,
            ),
        ],
    )

    result = _apply_post_enhancement(ir)

    get_op = next(op for op in result.operations if op.id == "listItems")
    post_op = next(op for op in result.operations if op.id == "createItem")

    assert get_op.tool_intent == ToolIntent.discovery
    assert post_op.tool_intent == ToolIntent.action
    assert get_op.description.startswith("[DISCOVERY] ")
    assert post_op.description.startswith("[ACTION] ")

    # Without grouping env var, tool_grouping should remain empty.
    assert result.tool_grouping == []


def test_apply_post_enhancement_normalizes_errors() -> None:
    """Verify _apply_post_enhancement populates error_schema on every operation."""
    ir = ServiceIR(
        source_hash="a" * 64,
        protocol="rest",
        service_name="error-norm-test",
        service_description="Fixture for error normalization test",
        base_url="https://example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="getWidget",
                name="Get Widget",
                description="Return a single widget.",
                method="GET",
                path="/widgets/{id}",
                params=[Param(name="id", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
            Operation(
                id="deleteWidget",
                name="Delete Widget",
                description="Remove a widget.",
                method="DELETE",
                path="/widgets/{id}",
                params=[Param(name="id", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.dangerous,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=True,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
        ],
    )

    result = _apply_post_enhancement(ir)

    for op in result.operations:
        assert op.error_schema.responses, (
            f"Operation {op.id!r} should have non-empty error_schema.responses"
        )


def test_apply_post_enhancement_generates_examples_with_llm() -> None:
    """Verify _apply_post_enhancement generates examples when LLM client is available."""
    from unittest.mock import MagicMock

    example_json = json.dumps(
        [
            {
                "name": "success",
                "description": "Successful response",
                "body": {"id": "w-1", "label": "Widget"},
            }
        ]
    )
    mock_response = MagicMock()
    mock_response.content = example_json
    mock_llm = MagicMock()
    mock_llm.complete.return_value = mock_response

    ir = ServiceIR(
        source_hash="a" * 64,
        protocol="rest",
        service_name="examples-test",
        service_description="Fixture for examples generation test",
        base_url="https://example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="getWidget",
                name="Get Widget",
                description="Return a single widget.",
                method="GET",
                path="/widgets/{id}",
                params=[Param(name="id", type="string", required=True)],
                response_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string"},
                    },
                },
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            ),
        ],
    )

    result = _apply_post_enhancement(ir, llm_client_factory=lambda: mock_llm)

    get_op = next(op for op in result.operations if op.id == "getWidget")
    assert get_op.response_examples, "Expected response_examples to be populated"
    assert get_op.error_schema.responses, "Error schema should also be populated"
