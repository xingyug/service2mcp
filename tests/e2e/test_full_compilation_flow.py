"""End-to-end compilation flow test using the compiler API and generic runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
from celery.contrib.testing.worker import start_worker
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from apps.compiler_api.dispatcher import CeleryCompilationDispatcher
from apps.compiler_api.main import create_app as create_compiler_api_app
from apps.compiler_api.repository import ArtifactRegistryRepository
from apps.compiler_worker.activities import ActivityRegistry
from apps.compiler_worker.activities.production import _apply_post_enhancement
from apps.compiler_worker.celery_app import create_celery_app
from apps.compiler_worker.executor import (
    CallbackCompilationExecutor,
    configure_compilation_executor,
    reset_compilation_executor,
)
from apps.compiler_worker.models import (
    CompilationContext,
    CompilationRequest,
    CompilationStage,
    StageExecutionResult,
)
from apps.compiler_worker.repository import SQLAlchemyCompilationJobStore
from apps.compiler_worker.workflows import CompilationWorkflow
from apps.mcp_runtime.main import create_app as create_runtime_app
from libs.db_models import Base
from libs.enhancer import EnhancerConfig, IREnhancer, LLMProvider, create_llm_client
from libs.extractors.base import SourceConfig, TypeDetector
from libs.extractors.graphql import GraphQLExtractor
from libs.extractors.grpc import GrpcProtoExtractor
from libs.extractors.openapi import OpenAPIExtractor
from libs.extractors.rest import (
    EndpointClassification,
    EndpointClassifier,
    RESTExtractor,
)
from libs.extractors.jsonrpc import JsonRpcExtractor
from libs.extractors.soap import SOAPWSDLExtractor
from libs.extractors.sql import SQLExtractor
from libs.generator import GenericManifestConfig, generate_generic_manifests
from libs.ir import serialize_ir
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventSupportLevel,
    EventTransport,
    Param,
    ServiceIR,
    SourceType,
    ToolIntent,
)
from libs.registry_client.models import ArtifactRecordPayload, ArtifactVersionCreate
from libs.validator import PostDeployValidator, PreDeployValidator

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "openapi_specs"
PETSTORE_SPEC_PATH = FIXTURES_DIR / "petstore_3_0.yaml"
GRAPHQL_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "graphql_schemas"
GRAPHQL_INTROSPECTION_PATH = GRAPHQL_FIXTURES_DIR / "catalog_introspection.json"
GRPC_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "grpc_protos"
GRPC_INVENTORY_PROTO_PATH = GRPC_FIXTURES_DIR / "inventory.proto"
WSDL_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "wsdl"
ORDER_SERVICE_WSDL_PATH = WSDL_FIXTURES_DIR / "order_service.wsdl"
JSONRPC_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "jsonrpc_specs"
JSONRPC_CALCULATOR_PATH = JSONRPC_FIXTURES_DIR / "openrpc_calculator.json"


def _initialize_sqlite_catalog(tmp_path: Path) -> str:
    database_path = tmp_path / "catalog.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            );

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                total_cents INTEGER NOT NULL,
                notes TEXT,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
            );

            CREATE VIEW order_summaries AS
            SELECT orders.id, customers.name AS customer_name, orders.total_cents
            FROM orders
            JOIN customers ON customers.id = orders.customer_id;

            INSERT INTO customers(name) VALUES ('Acme');
            INSERT INTO orders(customer_id, total_cents, notes) VALUES (1, 1250, 'rush');
            """
        )
        connection.commit()
    finally:
        connection.close()

    return f"sqlite:///{database_path}"


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
    runtime_client: httpx.AsyncClient


class RuntimeDeploymentHarness:
    """Deploy runtime revisions into in-memory ASGI apps for end-to-end validation."""

    def __init__(
        self,
        *,
        tmp_path: Path,
        upstream_handler: Callable[[httpx.Request], Awaitable[httpx.Response]],
        app_overrides_factory: Callable[[ServiceIR], dict[str, Any]] | None = None,
    ) -> None:
        self._tmp_path = tmp_path
        self._upstream_handler = upstream_handler
        self._app_overrides_factory = app_overrides_factory
        self._deployments: dict[str, DeployedRuntime] = {}
        self._active_revision: str | None = None

    async def deploy(self, service_ir: ServiceIR) -> str:
        revision = f"rev-{service_ir.service_name}-{len(self._deployments) + 1}"
        ir_path = self._tmp_path / f"{revision}.json"
        ir_path.write_text(serialize_ir(service_ir), encoding="utf-8")

        upstream_client = httpx.AsyncClient(
            transport=httpx.MockTransport(cast(Any, self._upstream_handler))
        )
        app_kwargs: dict[str, Any] = {
            "service_ir_path": ir_path,
            "upstream_client": upstream_client,
        }
        if self._app_overrides_factory is not None:
            app_kwargs.update(self._app_overrides_factory(service_ir))
        app = create_runtime_app(**app_kwargs)
        runtime_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://runtime",
        )
        self._deployments[revision] = DeployedRuntime(
            revision=revision,
            app=app,
            upstream_client=upstream_client,
            runtime_client=runtime_client,
        )
        self._active_revision = revision
        return revision

    def current(self) -> DeployedRuntime:
        if self._active_revision is None:
            raise RuntimeError("No runtime revision has been deployed.")
        return self._deployments[self._active_revision]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        _, structured = await self.current().app.state.runtime_state.mcp_server.call_tool(
            tool_name,
            arguments,
        )
        return cast(dict[str, Any], structured)

    async def rollback(self, revision: str) -> None:
        deployment = self._deployments.pop(revision, None)
        if deployment is None:
            return
        await deployment.runtime_client.aclose()
        await deployment.upstream_client.aclose()
        if self._active_revision == revision:
            self._active_revision = None

    async def aclose(self) -> None:
        for revision in list(self._deployments):
            await self.rollback(revision)


def _prioritize_safe_operations(service_ir: ServiceIR) -> ServiceIR:
    operations = sorted(
        service_ir.operations,
        key=lambda operation: (
            0 if (operation.method or "").upper() == "GET" and operation.enabled else 1,
            len(operation.params),
            operation.id,
        ),
    )
    return service_ir.model_copy(update={"operations": operations})


def _sample_value(param: Param) -> Any:
    if param.default is not None:
        return param.default
    lowered_name = param.name.lower()
    if lowered_name == "status":
        return "available"
    if param.type == "integer":
        return 1
    if param.type == "number":
        return 1.0
    if param.type == "boolean":
        return True
    if param.type == "array":
        return ["sample"]
    if param.type == "object":
        return {"name": "sample"}
    if lowered_name.endswith("id"):
        return "1"
    return "sample"


def _build_sample_invocations(service_ir: ServiceIR) -> dict[str, dict[str, Any]]:
    samples: dict[str, dict[str, Any]] = {}
    for operation in service_ir.operations:
        if not operation.enabled:
            continue
        samples[operation.id] = {param.name: _sample_value(param) for param in operation.params}
    return samples


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _optional_real_deepseek_config() -> EnhancerConfig | None:
    if not _env_flag("ENABLE_REAL_DEEPSEEK_E2E"):
        return None

    api_key = (os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ENABLE_REAL_DEEPSEEK_E2E requires DEEPSEEK_API_KEY or LLM_API_KEY.")

    model = (os.getenv("DEEPSEEK_MODEL") or os.getenv("LLM_MODEL") or "deepseek-chat").strip()
    api_base_url = (
        os.getenv("DEEPSEEK_API_BASE_URL")
        or os.getenv("LLM_API_BASE_URL")
        or "https://api.deepseek.com"
    ).strip()
    return EnhancerConfig(
        provider=LLMProvider.deepseek,
        model=model,
        api_key=api_key,
        api_base_url=api_base_url,
        skip_if_description_exists=False,
    )


async def _run_optional_real_deepseek_enhancer(
    context: CompilationContext,
    *,
    stub_model: str,
    stub_input_tokens: int,
    stub_output_tokens: int,
) -> StageExecutionResult:
    config = _optional_real_deepseek_config()
    if config is None:
        # Apply deterministic post-enhancement (intent derivation, description
        # bifurcation) even when no real LLM is available, mirroring production
        # passthrough behaviour.
        service_ir_payload = context.payload.get("service_ir")
        stub_updates: dict[str, Any] = {
            "token_usage": {
                "model": stub_model,
                "input_tokens": stub_input_tokens,
                "output_tokens": stub_output_tokens,
            }
        }
        if isinstance(service_ir_payload, dict):
            ir = ServiceIR.model_validate(service_ir_payload)
            ir = _apply_post_enhancement(ir)
            stub_updates["service_ir"] = ir.model_dump(mode="json")
        return _stage_result(
            context_updates=stub_updates,
            event_detail={"model": stub_model},
        )

    service_ir_payload = context.payload.get("service_ir")
    if not isinstance(service_ir_payload, dict):
        raise RuntimeError("Enhance stage requires an extracted ServiceIR payload.")

    service_ir = ServiceIR.model_validate(service_ir_payload)
    enhancer = IREnhancer(create_llm_client(config), config=config)
    result = await asyncio.to_thread(enhancer.enhance, service_ir)
    if result.token_usage.total_calls < 1 or result.operations_enhanced < 1:
        raise RuntimeError(
            "ENABLE_REAL_DEEPSEEK_E2E requested a real DeepSeek enhancement, "
            "but no enhancement result was recorded."
        )
    return _stage_result(
        context_updates={
            "service_ir": result.enhanced_ir.model_dump(mode="json"),
            "token_usage": {
                "model": result.token_usage.model,
                "input_tokens": result.token_usage.input_tokens,
                "output_tokens": result.token_usage.output_tokens,
            },
        },
        event_detail={
            "model": result.token_usage.model,
            "operations_enhanced": result.operations_enhanced,
            "real_provider": True,
        },
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


class _LLMRESTClassifier(EndpointClassifier):
    """Classifier double that simulates an LLM-normalized REST operation."""

    def classify(
        self,
        *,
        base_url: str,
        endpoints: list[Any],
    ) -> list[EndpointClassification]:
        assert base_url == "https://catalog.example.test/rest/catalog"
        assert any(
            getattr(endpoint, "path", None) == "/rest/catalog/items/{item_id}?view=detail"
            for endpoint in endpoints
        )
        return [
            EndpointClassification(
                path="/items/{item_id}?view=detail",
                method="GET",
                name="Get Catalog Item",
                description="Fetch one discovered catalog item with detail view.",
                confidence=0.96,
                tags=("rest", "catalog", "read"),
            )
        ]


@pytest.mark.asyncio
async def test_openapi_spec_compiles_to_running_runtime_and_tool_invocation(
    postgres_container: PostgresContainer,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    petstore_spec = PETSTORE_SPEC_PATH.read_text(encoding="utf-8")
    worker_database_url = _to_asyncpg_url(postgres_container.get_connection_url())

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "doggie", "status": "available"}],
                request=request,
            )
        return httpx.Response(
            200,
            json={"id": 1, "status": "ok"},
            request=request,
        )

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )
    detector = TypeDetector([OpenAPIExtractor()])

    async def detect_stage(context: CompilationContext) -> StageExecutionResult:
        detection = detector.detect(
            SourceConfig(
                url=context.request.source_url,
                file_content=context.request.source_content,
            )
        )
        return _stage_result(
            context_updates={"detection_confidence": detection.confidence},
            event_detail={"confidence": detection.confidence},
            protocol=detection.protocol_name,
        )

    async def extract_stage(context: CompilationContext) -> StageExecutionResult:
        extractor = OpenAPIExtractor()
        service_ir = _prioritize_safe_operations(
            extractor.extract(
                SourceConfig(
                    url=context.request.source_url,
                    file_content=context.request.source_content,
                )
            )
        )
        if service_ir.auth.type is not AuthType.none:
            service_ir = service_ir.model_copy(update={"auth": AuthConfig(type=AuthType.none)})
        service_id = context.request.service_name or service_ir.service_name
        return _stage_result(
            context_updates={
                "service_id": service_id,
                "service_ir": service_ir.model_dump(mode="json"),
                "version_number": 1,
            },
            event_detail={"operation_count": len(service_ir.operations)},
            protocol="openapi",
            service_name=service_id,
        )

    async def enhance_stage(context: CompilationContext) -> StageExecutionResult:
        return await _run_optional_real_deepseek_enhancer(
            context,
            stub_model="stub-enhancer",
            stub_input_tokens=12,
            stub_output_tokens=8,
        )

    async def validate_ir_stage(context: CompilationContext) -> StageExecutionResult:
        async with PreDeployValidator() as validator:
            report = await validator.validate(context.payload["service_ir"])
        if not report.overall_passed:
            raise RuntimeError("Pre-deploy validation failed.")
        return _stage_result(
            context_updates={"pre_validation_report": report.model_dump(mode="json")},
            event_detail={"overall_passed": report.overall_passed},
        )

    async def generate_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        manifest_set = generate_generic_manifests(
            service_ir,
            config=GenericManifestConfig(
                runtime_image="tool-compiler/mcp-runtime:latest",
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
            ),
        )
        return _stage_result(
            context_updates={
                "manifest_yaml": manifest_set.yaml,
                "route_config": manifest_set.route_config,
            },
            event_detail={"deployment_name": manifest_set.deployment["metadata"]["name"]},
            rollback_payload={"deployment_name": manifest_set.deployment["metadata"]["name"]},
        )

    async def deploy_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        revision = await deployment_harness.deploy(service_ir)
        return _stage_result(
            context_updates={"deployment_revision": revision},
            event_detail={"deployment_revision": revision},
            rollback_payload={"deployment_revision": revision},
        )

    async def validate_runtime_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        sample_invocations = _build_sample_invocations(service_ir)
        deployed = deployment_harness.current()
        async with PostDeployValidator(
            client=deployed.runtime_client,
            tool_invoker=deployment_harness.call_tool,
        ) as validator:
            report = await validator.validate(
                "http://runtime",
                service_ir,
                sample_invocations=sample_invocations,
            )
        if not report.overall_passed:
            raise RuntimeError("Post-deploy validation failed.")
        return _stage_result(
            context_updates={
                "post_validation_report": report.model_dump(mode="json"),
                "sample_invocations": sample_invocations,
            },
            event_detail={"overall_passed": report.overall_passed},
        )

    async def route_stage(context: CompilationContext) -> StageExecutionResult:
        route_config = context.payload["route_config"]
        return _stage_result(
            event_detail={"route_id": route_config["default_route"]["route_id"]},
            rollback_payload={"route_id": route_config["default_route"]["route_id"]},
        )

    async def deploy_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        del context
        assert result.rollback_payload is not None
        deployment_revision = result.rollback_payload["deployment_revision"]
        await deployment_harness.rollback(str(deployment_revision))

    worker_celery_app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )

    async def execute_workflow_for_task(
        request: CompilationRequest,
    ) -> None:
        worker_engine = create_async_engine(worker_database_url)
        worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

        async def register_stage(context: CompilationContext) -> StageExecutionResult:
            manifest_yaml = str(context.payload["manifest_yaml"])
            artifact_payload = ArtifactVersionCreate(
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
                ir_json=context.payload["service_ir"],
                deployment_revision=str(context.payload["deployment_revision"]),
                route_config=context.payload["route_config"],
                validation_report=context.payload["post_validation_report"],
                is_active=True,
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash=hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest(),
                        storage_path="memory://manifests/petstore.yaml",
                    )
                ],
            )
            async with worker_session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                created = await repository.create_version(artifact_payload)
            return _stage_result(
                context_updates={"registered_version": created.version_number},
                event_detail={
                    "service_id": created.service_id,
                    "version_number": created.version_number,
                },
            )

        activities = ActivityRegistry(
            stage_handlers={
                CompilationStage.DETECT: detect_stage,
                CompilationStage.EXTRACT: extract_stage,
                CompilationStage.ENHANCE: enhance_stage,
                CompilationStage.VALIDATE_IR: validate_ir_stage,
                CompilationStage.GENERATE: generate_stage,
                CompilationStage.DEPLOY: deploy_stage,
                CompilationStage.VALIDATE_RUNTIME: validate_runtime_stage,
                CompilationStage.ROUTE: route_stage,
                CompilationStage.REGISTER: register_stage,
            },
            rollback_handlers={
                CompilationStage.DEPLOY: deploy_rollback,
            },
        )
        workflow = CompilationWorkflow(
            store=SQLAlchemyCompilationJobStore(worker_session_factory),
            activities=activities,
        )
        try:
            await workflow.run(request)
        finally:
            await worker_engine.dispose()

    configure_compilation_executor(CallbackCompilationExecutor(callback=execute_workflow_for_task))

    compiler_api_app = create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=CeleryCompilationDispatcher(celery_app=worker_celery_app),
    )
    transport = httpx.ASGITransport(app=compiler_api_app)

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
            ) as http_client:
                submission = await http_client.post(
                    "/api/v1/compilations",
                    json={
                        "source_url": "https://example.com/petstore.yaml",
                        "source_content": petstore_spec,
                        "created_by": "e2e-user",
                        "service_name": "petstore-api",
                    },
                )
                if submission.status_code != 202:
                    raise AssertionError(
                        f"Unexpected submit response {submission.status_code}: {submission.text}"
                    )
                assert submission.status_code == 202
                job_id = submission.json()["id"]

                job = await _wait_for_job_status(http_client, job_id, expected_status="succeeded")
                assert job["protocol"] == "openapi"

                services = await http_client.get("/api/v1/services")
                assert services.status_code == 200
                listed_services = services.json()["services"]
                assert listed_services[0]["service_id"] == "petstore-api"
                assert listed_services[0]["active_version"] == 1
                assert (
                    listed_services[0]["deployment_revision"]
                    == deployment_harness.current().revision
                )

                async with http_client.stream(
                    "GET",
                    f"/api/v1/compilations/{job_id}/events",
                ) as response:
                    body = ""
                    async for chunk in response.aiter_text():
                        body += chunk

                assert response.status_code == 200
                assert "event: job.succeeded" in body
                assert "event: stage.succeeded" in body

                active_runtime = deployment_harness.current()
                service_ir = active_runtime.app.state.runtime_state.service_ir
                assert service_ir is not None
                # Verify tool_intent is populated after enhancement.
                for op in service_ir.operations:
                    assert op.tool_intent is not None, f"tool_intent not set on {op.id}"
                    assert op.description.startswith("[DISCOVERY] ") or op.description.startswith(
                        "[ACTION] "
                    ), f"description not bifurcated on {op.id}"
                # GET operations should be discovery, POST should be action.
                get_ops = [op for op in service_ir.operations if op.method == "GET"]
                post_ops = [op for op in service_ir.operations if op.method == "POST"]
                for op in get_ops:
                    assert op.tool_intent == ToolIntent.discovery, op.id
                for op in post_ops:
                    assert op.tool_intent == ToolIntent.action, op.id
                sample_invocations = _build_sample_invocations(service_ir)
                target_operation = next(
                    operation for operation in service_ir.operations if operation.enabled
                )
                tool_result = await deployment_harness.call_tool(
                    target_operation.id,
                    sample_invocations[target_operation.id],
                )

        assert tool_result["status"] == "ok"
        assert tool_result["result"][0]["name"] == "doggie"
    finally:
        reset_compilation_executor()
        await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_rest_discovery_compiles_to_running_runtime_and_tool_invocation(
    postgres_container: PostgresContainer,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    worker_database_url = _to_asyncpg_url(postgres_container.get_connection_url())

    def discovery_handler(request: httpx.Request) -> httpx.Response:
        routes: dict[tuple[str, str], httpx.Response] = {
            (
                "GET",
                "https://catalog.example.test/rest/catalog",
            ): httpx.Response(
                200,
                text=(
                    "<html><body>"
                    '<a href="/rest/catalog/items/{item_id}?view=detail">Item Detail</a>'
                    "</body></html>"
                ),
                headers={"content-type": "text/html"},
                request=request,
            ),
            (
                "GET",
                "https://catalog.example.test/rest/catalog/items/%7Bitem_id%7D?view=detail",
            ): httpx.Response(200, json={"ok": True}, request=request),
            (
                "OPTIONS",
                "https://catalog.example.test/rest/catalog/items/%7Bitem_id%7D?view=detail",
            ): httpx.Response(200, headers={"allow": "GET"}, request=request),
        }
        return routes.get((request.method, str(request.url)), httpx.Response(404, request=request))

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        item_id = request.url.path.rstrip("/").split("/")[-1]
        return httpx.Response(
            200,
            json={
                "item_id": item_id,
                "view": request.url.params.get("view"),
                "name": "Puzzle Box",
                "status": "active",
            },
            request=request,
        )

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )
    discovery_client = httpx.Client(
        transport=httpx.MockTransport(discovery_handler),
        follow_redirects=True,
    )
    rest_extractor = RESTExtractor(
        client=discovery_client,
        classifier=_LLMRESTClassifier(),
    )

    async def detect_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(url=context.request.source_url, hints=hints)
        detection = TypeDetector([rest_extractor]).detect(source)
        return _stage_result(
            context_updates={"detection_confidence": detection.confidence},
            event_detail={"confidence": detection.confidence},
            protocol=detection.protocol_name,
        )

    async def extract_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(url=context.request.source_url, hints=hints)
        service_ir = _prioritize_safe_operations(rest_extractor.extract(source))
        if context.request.service_name:
            service_ir = service_ir.model_copy(
                update={"service_name": context.request.service_name}
            )
        if service_ir.auth.type is not AuthType.none:
            service_ir = service_ir.model_copy(update={"auth": AuthConfig(type=AuthType.none)})
        service_id = context.request.service_name or service_ir.service_name
        return _stage_result(
            context_updates={
                "service_id": service_id,
                "service_ir": service_ir.model_dump(mode="json"),
                "version_number": 1,
            },
            event_detail={"operation_count": len(service_ir.operations)},
            protocol="rest",
            service_name=service_id,
        )

    async def enhance_stage(context: CompilationContext) -> StageExecutionResult:
        return await _run_optional_real_deepseek_enhancer(
            context,
            stub_model="stub-rest-classifier",
            stub_input_tokens=9,
            stub_output_tokens=7,
        )

    async def validate_ir_stage(context: CompilationContext) -> StageExecutionResult:
        async with PreDeployValidator() as validator:
            report = await validator.validate(context.payload["service_ir"])
        if not report.overall_passed:
            raise RuntimeError("Pre-deploy validation failed.")
        return _stage_result(
            context_updates={"pre_validation_report": report.model_dump(mode="json")},
            event_detail={"overall_passed": report.overall_passed},
        )

    async def generate_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        manifest_set = generate_generic_manifests(
            service_ir,
            config=GenericManifestConfig(
                runtime_image="tool-compiler/mcp-runtime:latest",
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
            ),
        )
        return _stage_result(
            context_updates={
                "manifest_yaml": manifest_set.yaml,
                "route_config": manifest_set.route_config,
            },
            event_detail={"deployment_name": manifest_set.deployment["metadata"]["name"]},
            rollback_payload={"deployment_name": manifest_set.deployment["metadata"]["name"]},
        )

    async def deploy_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        revision = await deployment_harness.deploy(service_ir)
        return _stage_result(
            context_updates={"deployment_revision": revision},
            event_detail={"deployment_revision": revision},
            rollback_payload={"deployment_revision": revision},
        )

    async def validate_runtime_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        sample_invocations = _build_sample_invocations(service_ir)
        deployed = deployment_harness.current()
        async with PostDeployValidator(
            client=deployed.runtime_client,
            tool_invoker=deployment_harness.call_tool,
        ) as validator:
            report = await validator.validate(
                "http://runtime",
                service_ir,
                sample_invocations=sample_invocations,
            )
        if not report.overall_passed:
            raise RuntimeError("Post-deploy validation failed.")
        return _stage_result(
            context_updates={
                "post_validation_report": report.model_dump(mode="json"),
                "sample_invocations": sample_invocations,
            },
            event_detail={"overall_passed": report.overall_passed},
        )

    async def route_stage(context: CompilationContext) -> StageExecutionResult:
        route_config = context.payload["route_config"]
        return _stage_result(
            event_detail={"route_id": route_config["default_route"]["route_id"]},
            rollback_payload={"route_id": route_config["default_route"]["route_id"]},
        )

    async def deploy_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        del context
        assert result.rollback_payload is not None
        deployment_revision = result.rollback_payload["deployment_revision"]
        await deployment_harness.rollback(str(deployment_revision))

    worker_celery_app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )

    async def execute_workflow_for_task(
        request: CompilationRequest,
    ) -> None:
        worker_engine = create_async_engine(worker_database_url)
        worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

        async def register_stage(context: CompilationContext) -> StageExecutionResult:
            manifest_yaml = str(context.payload["manifest_yaml"])
            artifact_payload = ArtifactVersionCreate(
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
                ir_json=context.payload["service_ir"],
                deployment_revision=str(context.payload["deployment_revision"]),
                route_config=context.payload["route_config"],
                validation_report=context.payload["post_validation_report"],
                is_active=True,
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash=hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest(),
                        storage_path="memory://manifests/rest-catalog.yaml",
                    )
                ],
            )
            async with worker_session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                created = await repository.create_version(artifact_payload)
            return _stage_result(
                context_updates={"registered_version": created.version_number},
                event_detail={
                    "service_id": created.service_id,
                    "version_number": created.version_number,
                },
            )

        activities = ActivityRegistry(
            stage_handlers={
                CompilationStage.DETECT: detect_stage,
                CompilationStage.EXTRACT: extract_stage,
                CompilationStage.ENHANCE: enhance_stage,
                CompilationStage.VALIDATE_IR: validate_ir_stage,
                CompilationStage.GENERATE: generate_stage,
                CompilationStage.DEPLOY: deploy_stage,
                CompilationStage.VALIDATE_RUNTIME: validate_runtime_stage,
                CompilationStage.ROUTE: route_stage,
                CompilationStage.REGISTER: register_stage,
            },
            rollback_handlers={
                CompilationStage.DEPLOY: deploy_rollback,
            },
        )
        workflow = CompilationWorkflow(
            store=SQLAlchemyCompilationJobStore(worker_session_factory),
            activities=activities,
        )
        try:
            await workflow.run(request)
        finally:
            await worker_engine.dispose()

    configure_compilation_executor(CallbackCompilationExecutor(callback=execute_workflow_for_task))

    compiler_api_app = create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=CeleryCompilationDispatcher(celery_app=worker_celery_app),
    )
    transport = httpx.ASGITransport(app=compiler_api_app)

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
            ) as http_client:
                submission = await http_client.post(
                    "/api/v1/compilations",
                    json={
                        "source_url": "https://catalog.example.test/rest/catalog",
                        "created_by": "e2e-user",
                        "service_name": "rest-catalog-api",
                        "options": {"protocol": "rest"},
                    },
                )
                if submission.status_code != 202:
                    raise AssertionError(
                        f"Unexpected submit response {submission.status_code}: {submission.text}"
                    )
                assert submission.status_code == 202
                job_id = submission.json()["id"]

                job = await _wait_for_job_status(http_client, job_id, expected_status="succeeded")
                assert job["protocol"] == "rest"

                services = await http_client.get("/api/v1/services")
                assert services.status_code == 200
                listed_services = services.json()["services"]
                rest_service = next(
                    (s for s in listed_services if s["service_id"] == "rest-catalog-api"),
                    None,
                )
                assert rest_service is not None
                assert rest_service["active_version"] == 1

                async with http_client.stream(
                    "GET",
                    f"/api/v1/compilations/{job_id}/events",
                ) as response:
                    body = ""
                    async for chunk in response.aiter_text():
                        body += chunk

                assert response.status_code == 200
                assert "event: job.succeeded" in body
                assert "event: stage.succeeded" in body

                active_runtime = deployment_harness.current()
                service_ir = active_runtime.app.state.runtime_state.service_ir
                assert service_ir is not None
                assert service_ir.protocol == "rest"
                assert service_ir.base_url == "https://catalog.example.test/rest/catalog"
                item_op = next(
                    (op for op in service_ir.operations if op.id == "get_items_item_id"),
                    None,
                )
                assert item_op is not None
                assert item_op.source is SourceType.llm
                assert item_op.path == "/items/{item_id}"
                # Verify tool_intent populated by pipeline.
                assert item_op.tool_intent is not None
                for op in service_ir.operations:
                    assert op.tool_intent is not None, f"tool_intent not set on {op.id}"

                tool_result = await deployment_harness.call_tool(
                    "get_items_item_id",
                    {"item_id": "sku-123", "view": "detail"},
                )

        assert tool_result["status"] == "ok"
        assert tool_result["result"]["item_id"] == "sku-123"
        assert tool_result["result"]["view"] == "detail"
        assert tool_result["result"]["name"] == "Puzzle Box"
    finally:
        reset_compilation_executor()
        rest_extractor.close()
        discovery_client.close()
        await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_grpc_proto_compiles_to_running_runtime_and_tool_invocation(
    postgres_container: PostgresContainer,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    grpc_proto = GRPC_INVENTORY_PROTO_PATH.read_text(encoding="utf-8")
    worker_database_url = _to_asyncpg_url(postgres_container.get_connection_url())
    unary_invocations: list[dict[str, Any]] = []
    stream_invocations: list[dict[str, Any]] = []

    async def unused_upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    class FakeGrpcUnaryExecutor:
        async def invoke(
            self,
            *,
            operation: Any,
            arguments: dict[str, object],
            config: Any,
        ) -> dict[str, Any]:
            unary_invocations.append(
                {
                    "operation_id": operation.id,
                    "arguments": dict(arguments),
                    "rpc_path": config.rpc_path,
                }
            )
            if operation.id == "ListItems":
                location_id = str(arguments.get("location_id", "warehouse-1"))
                return {
                    "items": [
                        {
                            "sku": f"{location_id}-sku",
                            "title": "Puzzle Box",
                        }
                    ],
                    "next_page_token": "",
                }
            if operation.id == "AdjustInventory":
                sku = str(arguments.get("sku", "sku-1"))
                raw_delta = arguments.get("delta", 0)
                delta = raw_delta if isinstance(raw_delta, int) else 0
                return {"operation_id": f"adj-{sku}-{delta}"}
            raise AssertionError(f"Unexpected unary operation {operation.id}")

    class FakeGrpcStreamExecutor:
        async def invoke(
            self,
            *,
            operation: Any,
            arguments: dict[str, object],
            descriptor: Any,
            config: Any,
        ) -> dict[str, Any]:
            stream_invocations.append(
                {
                    "operation_id": operation.id,
                    "arguments": dict(arguments),
                    "descriptor_id": descriptor.id,
                    "rpc_path": config.rpc_path,
                }
            )
            sku = str(arguments.get("sku", "sku-live"))
            return {
                "events": [
                    {
                        "message_type": "protobuf",
                        "parsed_data": {"sku": sku, "status": "ready"},
                    }
                ],
                "lifecycle": {
                    "termination_reason": "max_messages",
                    "messages_collected": 1,
                    "rpc_path": config.rpc_path,
                    "mode": "server",
                },
            }

    def build_runtime_overrides(service_ir: ServiceIR) -> dict[str, Any]:
        assert service_ir.protocol == "grpc"
        return {
            "grpc_unary_executor": FakeGrpcUnaryExecutor(),
            "grpc_stream_executor": FakeGrpcStreamExecutor(),
        }

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=unused_upstream_handler,
        app_overrides_factory=build_runtime_overrides,
    )
    grpc_extractor = GrpcProtoExtractor()

    async def detect_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(
            url=context.request.source_url,
            file_content=context.request.source_content,
            hints=hints,
        )
        detection = TypeDetector([grpc_extractor]).detect(source)
        return _stage_result(
            context_updates={"detection_confidence": detection.confidence},
            event_detail={"confidence": detection.confidence},
            protocol=detection.protocol_name,
        )

    async def extract_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(
            url=context.request.source_url,
            file_content=context.request.source_content,
            hints=hints,
        )
        service_ir = grpc_extractor.extract(source)
        if context.request.service_name:
            service_ir = service_ir.model_copy(
                update={"service_name": context.request.service_name}
            )
        if service_ir.auth.type is not AuthType.none:
            service_ir = service_ir.model_copy(update={"auth": AuthConfig(type=AuthType.none)})
        service_id = context.request.service_name or service_ir.service_name
        return _stage_result(
            context_updates={
                "service_id": service_id,
                "service_ir": service_ir.model_dump(mode="json"),
                "version_number": 1,
            },
            event_detail={"operation_count": len(service_ir.operations)},
            protocol="grpc",
            service_name=service_id,
        )

    async def enhance_stage(context: CompilationContext) -> StageExecutionResult:
        return await _run_optional_real_deepseek_enhancer(
            context,
            stub_model="stub-grpc-enhancer",
            stub_input_tokens=14,
            stub_output_tokens=9,
        )

    async def validate_ir_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        async with PreDeployValidator(
            allow_native_grpc_stream=any(
                descriptor.transport is EventTransport.grpc_stream
                and descriptor.support is EventSupportLevel.supported
                for descriptor in service_ir.event_descriptors
            ),
            allow_native_grpc_unary=any(
                operation.enabled and operation.grpc_unary is not None
                for operation in service_ir.operations
            ),
        ) as validator:
            report = await validator.validate(service_ir)
        if not report.overall_passed:
            raise RuntimeError("Pre-deploy validation failed.")
        return _stage_result(
            context_updates={"pre_validation_report": report.model_dump(mode="json")},
            event_detail={"overall_passed": report.overall_passed},
        )

    async def generate_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        manifest_set = generate_generic_manifests(
            service_ir,
            config=GenericManifestConfig(
                runtime_image="tool-compiler/mcp-runtime:latest",
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
            ),
        )
        return _stage_result(
            context_updates={
                "manifest_yaml": manifest_set.yaml,
                "route_config": manifest_set.route_config,
            },
            event_detail={"deployment_name": manifest_set.deployment["metadata"]["name"]},
            rollback_payload={"deployment_name": manifest_set.deployment["metadata"]["name"]},
        )

    async def deploy_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        revision = await deployment_harness.deploy(service_ir)
        return _stage_result(
            context_updates={"deployment_revision": revision},
            event_detail={"deployment_revision": revision},
            rollback_payload={"deployment_revision": revision},
        )

    async def validate_runtime_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        sample_invocations = _build_sample_invocations(service_ir)
        deployed = deployment_harness.current()
        async with PostDeployValidator(
            client=deployed.runtime_client,
            tool_invoker=deployment_harness.call_tool,
        ) as validator:
            report = await validator.validate(
                "http://runtime",
                service_ir,
                sample_invocations=sample_invocations,
            )
        if not report.overall_passed:
            raise RuntimeError("Post-deploy validation failed.")
        return _stage_result(
            context_updates={
                "post_validation_report": report.model_dump(mode="json"),
                "sample_invocations": sample_invocations,
            },
            event_detail={"overall_passed": report.overall_passed},
        )

    async def route_stage(context: CompilationContext) -> StageExecutionResult:
        route_config = context.payload["route_config"]
        return _stage_result(
            event_detail={"route_id": route_config["default_route"]["route_id"]},
            rollback_payload={"route_id": route_config["default_route"]["route_id"]},
        )

    async def deploy_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        del context
        assert result.rollback_payload is not None
        deployment_revision = result.rollback_payload["deployment_revision"]
        await deployment_harness.rollback(str(deployment_revision))

    worker_celery_app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )

    async def execute_workflow_for_task(
        request: CompilationRequest,
    ) -> None:
        worker_engine = create_async_engine(worker_database_url)
        worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

        async def register_stage(context: CompilationContext) -> StageExecutionResult:
            manifest_yaml = str(context.payload["manifest_yaml"])
            artifact_payload = ArtifactVersionCreate(
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
                ir_json=context.payload["service_ir"],
                deployment_revision=str(context.payload["deployment_revision"]),
                route_config=context.payload["route_config"],
                validation_report=context.payload["post_validation_report"],
                is_active=True,
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash=hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest(),
                        storage_path="memory://manifests/grpc-inventory.yaml",
                    )
                ],
            )
            async with worker_session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                created = await repository.create_version(artifact_payload)
            return _stage_result(
                context_updates={"registered_version": created.version_number},
                event_detail={
                    "service_id": created.service_id,
                    "version_number": created.version_number,
                },
            )

        activities = ActivityRegistry(
            stage_handlers={
                CompilationStage.DETECT: detect_stage,
                CompilationStage.EXTRACT: extract_stage,
                CompilationStage.ENHANCE: enhance_stage,
                CompilationStage.VALIDATE_IR: validate_ir_stage,
                CompilationStage.GENERATE: generate_stage,
                CompilationStage.DEPLOY: deploy_stage,
                CompilationStage.VALIDATE_RUNTIME: validate_runtime_stage,
                CompilationStage.ROUTE: route_stage,
                CompilationStage.REGISTER: register_stage,
            },
            rollback_handlers={
                CompilationStage.DEPLOY: deploy_rollback,
            },
        )
        workflow = CompilationWorkflow(
            store=SQLAlchemyCompilationJobStore(worker_session_factory),
            activities=activities,
        )
        try:
            await workflow.run(request)
        finally:
            await worker_engine.dispose()

    configure_compilation_executor(CallbackCompilationExecutor(callback=execute_workflow_for_task))

    compiler_api_app = create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=CeleryCompilationDispatcher(celery_app=worker_celery_app),
    )
    transport = httpx.ASGITransport(app=compiler_api_app)

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
            ) as http_client:
                submission = await http_client.post(
                    "/api/v1/compilations",
                    json={
                        "source_url": "grpc://inventory.example.test:443",
                        "source_content": grpc_proto,
                        "created_by": "e2e-user",
                        "service_name": "grpc-inventory-api",
                        "options": {
                            "protocol": "grpc",
                            "hints": {"enable_native_grpc_stream": "true"},
                        },
                    },
                )
                if submission.status_code != 202:
                    raise AssertionError(
                        f"Unexpected submit response {submission.status_code}: {submission.text}"
                    )
                assert submission.status_code == 202
                job_id = submission.json()["id"]

                job = await _wait_for_job_status(http_client, job_id, expected_status="succeeded")
                assert job["protocol"] == "grpc"

                services = await http_client.get("/api/v1/services")
                assert services.status_code == 200
                listed_services = services.json()["services"]
                grpc_service = next(
                    (s for s in listed_services if s["service_id"] == "grpc-inventory-api"),
                    None,
                )
                assert grpc_service is not None
                assert grpc_service["active_version"] == 1

                async with http_client.stream(
                    "GET",
                    f"/api/v1/compilations/{job_id}/events",
                ) as response:
                    body = ""
                    async for chunk in response.aiter_text():
                        body += chunk

                assert response.status_code == 200
                assert "event: job.succeeded" in body
                assert "event: stage.succeeded" in body

                active_runtime = deployment_harness.current()
                service_ir = active_runtime.app.state.runtime_state.service_ir
                assert service_ir is not None
                assert service_ir.protocol == "grpc"
                list_items = next(
                    (op for op in service_ir.operations if op.id == "ListItems"),
                    None,
                )
                watch_inventory = next(
                    (op for op in service_ir.operations if op.id == "WatchInventory"),
                    None,
                )
                assert list_items is not None
                assert list_items.grpc_unary is not None
                assert watch_inventory is not None
                watch_descriptor = next(
                    (
                        descriptor
                        for descriptor in service_ir.event_descriptors
                        if descriptor.operation_id == "WatchInventory"
                    ),
                    None,
                )
                assert watch_descriptor is not None
                assert watch_descriptor.transport is EventTransport.grpc_stream
                assert watch_descriptor.support is EventSupportLevel.supported

                unary_result = await deployment_harness.call_tool(
                    "ListItems",
                    {"location_id": "warehouse-1", "page_size": 1},
                )
                stream_result = await deployment_harness.call_tool(
                    "WatchInventory",
                    {"sku": "sku-live"},
                )

        assert unary_result["status"] == "ok"
        assert unary_result["result"]["items"][0]["sku"] == "warehouse-1-sku"
        assert unary_result["result"]["items"][0]["title"] == "Puzzle Box"
        assert stream_result["status"] == "ok"
        assert stream_result["transport"] == "grpc_stream"
        assert stream_result["result"]["events"] == [
            {
                "message_type": "protobuf",
                "parsed_data": {"sku": "sku-live", "status": "ready"},
            }
        ]
        assert any(
            invocation["operation_id"] == "ListItems"
            and invocation["rpc_path"] == "/catalog.v1.InventoryService/ListItems"
            for invocation in unary_invocations
        )
        assert any(
            invocation["operation_id"] == "WatchInventory"
            and invocation["rpc_path"] == "/catalog.v1.InventoryService/WatchInventory"
            for invocation in stream_invocations
        )
    finally:
        reset_compilation_executor()
        await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_soap_wsdl_compiles_to_running_runtime_and_tool_invocation(
    postgres_container: PostgresContainer,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    wsdl_spec = ORDER_SERVICE_WSDL_PATH.read_text(encoding="utf-8")
    worker_database_url = _to_asyncpg_url(postgres_container.get_connection_url())
    soap_extractor = SOAPWSDLExtractor()

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        soap_action = str(request.headers.get("SOAPAction", "")).strip('"')
        body = request.read().decode("utf-8")
        if soap_action.endswith("/GetOrderStatus") or "GetOrderStatusRequest" in body:
            return httpx.Response(
                200,
                text=(
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
                    "<soapenv:Body>"
                    '<tns:GetOrderStatusResponse xmlns:tns="http://example.com/orders/wsdl">'
                    "<status>SHIPPED</status>"
                    "<estimatedShipDate>2026-03-26T10:00:00Z</estimatedShipDate>"
                    "</tns:GetOrderStatusResponse>"
                    "</soapenv:Body>"
                    "</soapenv:Envelope>"
                ),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                request=request,
            )
        if soap_action.endswith("/SubmitOrder") or "SubmitOrderRequest" in body:
            return httpx.Response(
                200,
                text=(
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
                    "<soapenv:Body>"
                    '<tns:SubmitOrderResponse xmlns:tns="http://example.com/orders/wsdl">'
                    "<confirmationId>CONF-12345</confirmationId>"
                    "</tns:SubmitOrderResponse>"
                    "</soapenv:Body>"
                    "</soapenv:Envelope>"
                ),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                request=request,
            )
        return httpx.Response(
            500,
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
                "<soapenv:Body>"
                "<soapenv:Fault>"
                "<faultcode>soapenv:Client</faultcode>"
                "<faultstring>Unsupported SOAP action.</faultstring>"
                "</soapenv:Fault>"
                "</soapenv:Body>"
                "</soapenv:Envelope>"
            ),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            request=request,
        )

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )

    async def detect_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(
            url=context.request.source_url,
            file_content=context.request.source_content,
            hints=hints,
        )
        detection = TypeDetector([soap_extractor]).detect(source)
        return _stage_result(
            context_updates={"detection_confidence": detection.confidence},
            event_detail={"confidence": detection.confidence},
            protocol=detection.protocol_name,
        )

    async def extract_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(
            url=context.request.source_url,
            file_content=context.request.source_content,
            hints=hints,
        )
        service_ir = soap_extractor.extract(source)
        if context.request.service_name:
            service_ir = service_ir.model_copy(
                update={"service_name": context.request.service_name}
            )
        if service_ir.auth.type is not AuthType.none:
            service_ir = service_ir.model_copy(update={"auth": AuthConfig(type=AuthType.none)})
        service_id = context.request.service_name or service_ir.service_name
        return _stage_result(
            context_updates={
                "service_id": service_id,
                "service_ir": service_ir.model_dump(mode="json"),
                "version_number": 1,
            },
            event_detail={"operation_count": len(service_ir.operations)},
            protocol="soap",
            service_name=service_id,
        )

    async def enhance_stage(context: CompilationContext) -> StageExecutionResult:
        return await _run_optional_real_deepseek_enhancer(
            context,
            stub_model="stub-soap-enhancer",
            stub_input_tokens=11,
            stub_output_tokens=8,
        )

    async def validate_ir_stage(context: CompilationContext) -> StageExecutionResult:
        async with PreDeployValidator() as validator:
            report = await validator.validate(context.payload["service_ir"])
        if not report.overall_passed:
            raise RuntimeError("Pre-deploy validation failed.")
        return _stage_result(
            context_updates={"pre_validation_report": report.model_dump(mode="json")},
            event_detail={"overall_passed": report.overall_passed},
        )

    async def generate_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        manifest_set = generate_generic_manifests(
            service_ir,
            config=GenericManifestConfig(
                runtime_image="tool-compiler/mcp-runtime:latest",
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
            ),
        )
        return _stage_result(
            context_updates={
                "manifest_yaml": manifest_set.yaml,
                "route_config": manifest_set.route_config,
            },
            event_detail={"deployment_name": manifest_set.deployment["metadata"]["name"]},
            rollback_payload={"deployment_name": manifest_set.deployment["metadata"]["name"]},
        )

    async def deploy_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        revision = await deployment_harness.deploy(service_ir)
        return _stage_result(
            context_updates={"deployment_revision": revision},
            event_detail={"deployment_revision": revision},
            rollback_payload={"deployment_revision": revision},
        )

    async def validate_runtime_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        sample_invocations = _build_sample_invocations(service_ir)
        deployed = deployment_harness.current()
        async with PostDeployValidator(
            client=deployed.runtime_client,
            tool_invoker=deployment_harness.call_tool,
        ) as validator:
            report = await validator.validate(
                "http://runtime",
                service_ir,
                sample_invocations=sample_invocations,
            )
        if not report.overall_passed:
            raise RuntimeError("Post-deploy validation failed.")
        return _stage_result(
            context_updates={
                "post_validation_report": report.model_dump(mode="json"),
                "sample_invocations": sample_invocations,
            },
            event_detail={"overall_passed": report.overall_passed},
        )

    async def route_stage(context: CompilationContext) -> StageExecutionResult:
        route_config = context.payload["route_config"]
        return _stage_result(
            event_detail={"route_id": route_config["default_route"]["route_id"]},
            rollback_payload={"route_id": route_config["default_route"]["route_id"]},
        )

    async def deploy_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        del context
        assert result.rollback_payload is not None
        deployment_revision = result.rollback_payload["deployment_revision"]
        await deployment_harness.rollback(str(deployment_revision))

    worker_celery_app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )

    async def execute_workflow_for_task(
        request: CompilationRequest,
    ) -> None:
        worker_engine = create_async_engine(worker_database_url)
        worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

        async def register_stage(context: CompilationContext) -> StageExecutionResult:
            manifest_yaml = str(context.payload["manifest_yaml"])
            artifact_payload = ArtifactVersionCreate(
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
                ir_json=context.payload["service_ir"],
                deployment_revision=str(context.payload["deployment_revision"]),
                route_config=context.payload["route_config"],
                validation_report=context.payload["post_validation_report"],
                is_active=True,
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash=hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest(),
                        storage_path="memory://manifests/soap-order-service.yaml",
                    )
                ],
            )
            async with worker_session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                created = await repository.create_version(artifact_payload)
            return _stage_result(
                context_updates={"registered_version": created.version_number},
                event_detail={
                    "service_id": created.service_id,
                    "version_number": created.version_number,
                },
            )

        activities = ActivityRegistry(
            stage_handlers={
                CompilationStage.DETECT: detect_stage,
                CompilationStage.EXTRACT: extract_stage,
                CompilationStage.ENHANCE: enhance_stage,
                CompilationStage.VALIDATE_IR: validate_ir_stage,
                CompilationStage.GENERATE: generate_stage,
                CompilationStage.DEPLOY: deploy_stage,
                CompilationStage.VALIDATE_RUNTIME: validate_runtime_stage,
                CompilationStage.ROUTE: route_stage,
                CompilationStage.REGISTER: register_stage,
            },
            rollback_handlers={
                CompilationStage.DEPLOY: deploy_rollback,
            },
        )
        workflow = CompilationWorkflow(
            store=SQLAlchemyCompilationJobStore(worker_session_factory),
            activities=activities,
        )
        try:
            await workflow.run(request)
        finally:
            await worker_engine.dispose()

    configure_compilation_executor(CallbackCompilationExecutor(callback=execute_workflow_for_task))

    compiler_api_app = create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=CeleryCompilationDispatcher(celery_app=worker_celery_app),
    )
    transport = httpx.ASGITransport(app=compiler_api_app)

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
            ) as http_client:
                submission = await http_client.post(
                    "/api/v1/compilations",
                    json={
                        "source_content": wsdl_spec,
                        "created_by": "e2e-user",
                        "service_name": "soap-order-api",
                        "options": {"protocol": "soap"},
                    },
                )
                if submission.status_code != 202:
                    raise AssertionError(
                        f"Unexpected submit response {submission.status_code}: {submission.text}"
                    )
                assert submission.status_code == 202
                job_id = submission.json()["id"]

                job = await _wait_for_job_status(http_client, job_id, expected_status="succeeded")
                assert job["protocol"] == "soap"

                services = await http_client.get("/api/v1/services")
                assert services.status_code == 200
                listed_services = services.json()["services"]
                soap_service = next(
                    (s for s in listed_services if s["service_id"] == "soap-order-api"),
                    None,
                )
                assert soap_service is not None
                assert soap_service["active_version"] == 1

                async with http_client.stream(
                    "GET",
                    f"/api/v1/compilations/{job_id}/events",
                ) as response:
                    body = ""
                    async for chunk in response.aiter_text():
                        body += chunk

                assert response.status_code == 200
                assert "event: job.succeeded" in body
                assert "event: stage.succeeded" in body

                active_runtime = deployment_harness.current()
                service_ir = active_runtime.app.state.runtime_state.service_ir
                assert service_ir is not None
                assert service_ir.protocol == "soap"
                assert service_ir.base_url == "https://orders.example.com/soap/order-service"
                get_order_status = next(
                    (op for op in service_ir.operations if op.id == "GetOrderStatus"),
                    None,
                )
                submit_order = next(
                    (op for op in service_ir.operations if op.id == "SubmitOrder"),
                    None,
                )
                assert get_order_status is not None
                assert get_order_status.soap is not None
                assert get_order_status.soap.soap_action == (
                    "http://example.com/orders/GetOrderStatus"
                )
                assert submit_order is not None
                assert submit_order.soap is not None

                tool_result = await deployment_harness.call_tool(
                    "GetOrderStatus",
                    {"orderId": "ORD-100", "includeHistory": True},
                )

        assert tool_result["status"] == "ok"
        assert tool_result["result"] == {
            "status": "SHIPPED",
            "estimatedShipDate": "2026-03-26T10:00:00Z",
        }
    finally:
        reset_compilation_executor()
        await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_graphql_introspection_compiles_to_running_runtime_and_tool_invocation(
    postgres_container: PostgresContainer,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    graphql_spec = GRAPHQL_INTROSPECTION_PATH.read_text(encoding="utf-8")
    worker_database_url = _to_asyncpg_url(postgres_container.get_connection_url())

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/graphql":
            body = json.loads(request.content.decode("utf-8"))
            operation_name = body.get("operationName", "")
            variables = body.get("variables", {})
            if operation_name == "searchProducts":
                term = str(variables.get("term", "sample"))
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "searchProducts": [{"id": f"sku-{term}", "name": f"{term.title()} Kit"}]
                        }
                    },
                    request=request,
                )
            if operation_name == "adjustInventory":
                return httpx.Response(
                    200,
                    json={"data": {"adjustInventory": {"ok": True}}},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"errors": [{"message": f"Unknown operation {operation_name}"}]},
                request=request,
            )
        return httpx.Response(404, request=request)

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )

    graphql_extractor = GraphQLExtractor()

    async def detect_stage(context: CompilationContext) -> StageExecutionResult:
        source = SourceConfig(
            file_content=context.request.source_content,
            hints=dict(context.request.options.get("hints", {})),
        )
        detection = TypeDetector([graphql_extractor]).detect(source)
        return _stage_result(
            context_updates={"detection_confidence": detection.confidence},
            event_detail={"confidence": detection.confidence},
            protocol=detection.protocol_name,
        )

    async def extract_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(
            file_content=context.request.source_content,
            hints=hints,
        )
        service_ir = graphql_extractor.extract(source)
        if context.request.service_name:
            service_ir = service_ir.model_copy(
                update={"service_name": context.request.service_name}
            )
        if service_ir.auth.type is not AuthType.none:
            service_ir = service_ir.model_copy(update={"auth": AuthConfig(type=AuthType.none)})
        service_id = context.request.service_name or service_ir.service_name
        return _stage_result(
            context_updates={
                "service_id": service_id,
                "service_ir": service_ir.model_dump(mode="json"),
                "version_number": 1,
            },
            event_detail={"operation_count": len(service_ir.operations)},
            protocol="graphql",
            service_name=service_id,
        )

    async def enhance_stage(context: CompilationContext) -> StageExecutionResult:
        return await _run_optional_real_deepseek_enhancer(
            context,
            stub_model="stub-enhancer",
            stub_input_tokens=12,
            stub_output_tokens=8,
        )

    async def validate_ir_stage(context: CompilationContext) -> StageExecutionResult:
        async with PreDeployValidator() as validator:
            report = await validator.validate(context.payload["service_ir"])
        if not report.overall_passed:
            raise RuntimeError("Pre-deploy validation failed.")
        return _stage_result(
            context_updates={"pre_validation_report": report.model_dump(mode="json")},
            event_detail={"overall_passed": report.overall_passed},
        )

    async def generate_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        manifest_set = generate_generic_manifests(
            service_ir,
            config=GenericManifestConfig(
                runtime_image="tool-compiler/mcp-runtime:latest",
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
            ),
        )
        return _stage_result(
            context_updates={
                "manifest_yaml": manifest_set.yaml,
                "route_config": manifest_set.route_config,
            },
            event_detail={"deployment_name": manifest_set.deployment["metadata"]["name"]},
            rollback_payload={"deployment_name": manifest_set.deployment["metadata"]["name"]},
        )

    async def deploy_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        revision = await deployment_harness.deploy(service_ir)
        return _stage_result(
            context_updates={"deployment_revision": revision},
            event_detail={"deployment_revision": revision},
            rollback_payload={"deployment_revision": revision},
        )

    async def validate_runtime_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        sample_invocations = _build_sample_invocations(service_ir)
        deployed = deployment_harness.current()
        async with PostDeployValidator(
            client=deployed.runtime_client,
            tool_invoker=deployment_harness.call_tool,
        ) as validator:
            report = await validator.validate(
                "http://runtime",
                service_ir,
                sample_invocations=sample_invocations,
            )
        if not report.overall_passed:
            raise RuntimeError("Post-deploy validation failed.")
        return _stage_result(
            context_updates={
                "post_validation_report": report.model_dump(mode="json"),
                "sample_invocations": sample_invocations,
            },
            event_detail={"overall_passed": report.overall_passed},
        )

    async def route_stage(context: CompilationContext) -> StageExecutionResult:
        route_config = context.payload["route_config"]
        return _stage_result(
            event_detail={"route_id": route_config["default_route"]["route_id"]},
            rollback_payload={"route_id": route_config["default_route"]["route_id"]},
        )

    async def deploy_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        del context
        assert result.rollback_payload is not None
        deployment_revision = result.rollback_payload["deployment_revision"]
        await deployment_harness.rollback(str(deployment_revision))

    worker_celery_app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )

    async def execute_workflow_for_task(
        request: CompilationRequest,
    ) -> None:
        worker_engine = create_async_engine(worker_database_url)
        worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

        async def register_stage(context: CompilationContext) -> StageExecutionResult:
            manifest_yaml = str(context.payload["manifest_yaml"])
            artifact_payload = ArtifactVersionCreate(
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
                ir_json=context.payload["service_ir"],
                deployment_revision=str(context.payload["deployment_revision"]),
                route_config=context.payload["route_config"],
                validation_report=context.payload["post_validation_report"],
                is_active=True,
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash=hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest(),
                        storage_path="memory://manifests/graphql-catalog.yaml",
                    )
                ],
            )
            async with worker_session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                created = await repository.create_version(artifact_payload)
            return _stage_result(
                context_updates={"registered_version": created.version_number},
                event_detail={
                    "service_id": created.service_id,
                    "version_number": created.version_number,
                },
            )

        activities = ActivityRegistry(
            stage_handlers={
                CompilationStage.DETECT: detect_stage,
                CompilationStage.EXTRACT: extract_stage,
                CompilationStage.ENHANCE: enhance_stage,
                CompilationStage.VALIDATE_IR: validate_ir_stage,
                CompilationStage.GENERATE: generate_stage,
                CompilationStage.DEPLOY: deploy_stage,
                CompilationStage.VALIDATE_RUNTIME: validate_runtime_stage,
                CompilationStage.ROUTE: route_stage,
                CompilationStage.REGISTER: register_stage,
            },
            rollback_handlers={
                CompilationStage.DEPLOY: deploy_rollback,
            },
        )
        workflow = CompilationWorkflow(
            store=SQLAlchemyCompilationJobStore(worker_session_factory),
            activities=activities,
        )
        try:
            await workflow.run(request)
        finally:
            await worker_engine.dispose()

    configure_compilation_executor(CallbackCompilationExecutor(callback=execute_workflow_for_task))

    compiler_api_app = create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=CeleryCompilationDispatcher(celery_app=worker_celery_app),
    )
    transport = httpx.ASGITransport(app=compiler_api_app)

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
            ) as http_client:
                submission = await http_client.post(
                    "/api/v1/compilations",
                    json={
                        "source_content": graphql_spec,
                        "created_by": "e2e-user",
                        "service_name": "graphql-catalog-api",
                        "options": {
                            "protocol": "graphql",
                            "hints": {
                                "base_url": "http://upstream",
                                "graphql_path": "/graphql",
                            },
                        },
                    },
                )
                if submission.status_code != 202:
                    raise AssertionError(
                        f"Unexpected submit response {submission.status_code}: {submission.text}"
                    )
                assert submission.status_code == 202
                job_id = submission.json()["id"]

                job = await _wait_for_job_status(http_client, job_id, expected_status="succeeded")
                assert job["protocol"] == "graphql"

                services = await http_client.get("/api/v1/services")
                assert services.status_code == 200
                listed_services = services.json()["services"]
                graphql_service = next(
                    (s for s in listed_services if s["service_id"] == "graphql-catalog-api"),
                    None,
                )
                assert graphql_service is not None
                assert graphql_service["active_version"] == 1

                async with http_client.stream(
                    "GET",
                    f"/api/v1/compilations/{job_id}/events",
                ) as response:
                    body = ""
                    async for chunk in response.aiter_text():
                        body += chunk

                assert response.status_code == 200
                assert "event: job.succeeded" in body
                assert "event: stage.succeeded" in body

                active_runtime = deployment_harness.current()
                service_ir = active_runtime.app.state.runtime_state.service_ir
                assert service_ir is not None
                assert service_ir.protocol == "graphql"
                search_op = next(
                    (op for op in service_ir.operations if op.id == "searchProducts"),
                    None,
                )
                assert search_op is not None
                assert search_op.graphql is not None
                assert search_op.graphql.operation_name == "searchProducts"
                # Verify tool_intent populated by pipeline.
                for op in service_ir.operations:
                    assert op.tool_intent is not None, f"tool_intent not set on {op.id}"

                tool_result = await deployment_harness.call_tool(
                    "searchProducts",
                    {"term": "puzzle", "limit": 1},
                )

        assert tool_result["status"] == "ok"
        result_data = tool_result["result"]
        assert isinstance(result_data, list)
        assert result_data[0]["id"] == "sku-puzzle"
        assert result_data[0]["name"] == "Puzzle Kit"
    finally:
        reset_compilation_executor()
        await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_sql_schema_compiles_to_running_runtime_and_tool_invocation(
    postgres_container: PostgresContainer,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    database_url = _initialize_sqlite_catalog(tmp_path)
    worker_database_url = _to_asyncpg_url(postgres_container.get_connection_url())
    sql_extractor = SQLExtractor()

    async def unexpected_upstream_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            f"SQL runtime should not proxy upstream HTTP traffic: {request.method} {request.url}"
        )

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=unexpected_upstream_handler,
    )

    async def detect_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(url=context.request.source_url, hints=hints)
        detection = TypeDetector([sql_extractor]).detect(source)
        return _stage_result(
            context_updates={"detection_confidence": detection.confidence},
            event_detail={"confidence": detection.confidence},
            protocol=detection.protocol_name,
        )

    async def extract_stage(context: CompilationContext) -> StageExecutionResult:
        hints = dict(context.request.options.get("hints", {}))
        protocol_hint = context.request.options.get("protocol")
        if isinstance(protocol_hint, str) and protocol_hint:
            hints.setdefault("protocol", protocol_hint)

        source = SourceConfig(url=context.request.source_url, hints=hints)
        service_ir = sql_extractor.extract(source)
        if context.request.service_name:
            service_ir = service_ir.model_copy(
                update={"service_name": context.request.service_name}
            )
        if service_ir.auth.type is not AuthType.none:
            service_ir = service_ir.model_copy(update={"auth": AuthConfig(type=AuthType.none)})
        service_id = context.request.service_name or service_ir.service_name
        return _stage_result(
            context_updates={
                "service_id": service_id,
                "service_ir": service_ir.model_dump(mode="json"),
                "version_number": 1,
            },
            event_detail={"operation_count": len(service_ir.operations)},
            protocol="sql",
            service_name=service_id,
        )

    async def enhance_stage(context: CompilationContext) -> StageExecutionResult:
        return await _run_optional_real_deepseek_enhancer(
            context,
            stub_model="stub-sql-enhancer",
            stub_input_tokens=10,
            stub_output_tokens=7,
        )

    async def validate_ir_stage(context: CompilationContext) -> StageExecutionResult:
        async with PreDeployValidator() as validator:
            report = await validator.validate(context.payload["service_ir"])
        if not report.overall_passed:
            raise RuntimeError("Pre-deploy validation failed.")
        return _stage_result(
            context_updates={"pre_validation_report": report.model_dump(mode="json")},
            event_detail={"overall_passed": report.overall_passed},
        )

    async def generate_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        manifest_set = generate_generic_manifests(
            service_ir,
            config=GenericManifestConfig(
                runtime_image="tool-compiler/mcp-runtime:latest",
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
            ),
        )
        return _stage_result(
            context_updates={
                "manifest_yaml": manifest_set.yaml,
                "route_config": manifest_set.route_config,
            },
            event_detail={"deployment_name": manifest_set.deployment["metadata"]["name"]},
            rollback_payload={"deployment_name": manifest_set.deployment["metadata"]["name"]},
        )

    async def deploy_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        revision = await deployment_harness.deploy(service_ir)
        return _stage_result(
            context_updates={"deployment_revision": revision},
            event_detail={"deployment_revision": revision},
            rollback_payload={"deployment_revision": revision},
        )

    async def validate_runtime_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        sample_invocations = _build_sample_invocations(service_ir)
        deployed = deployment_harness.current()
        async with PostDeployValidator(
            client=deployed.runtime_client,
            tool_invoker=deployment_harness.call_tool,
        ) as validator:
            report = await validator.validate(
                "http://runtime",
                service_ir,
                sample_invocations=sample_invocations,
            )
        if not report.overall_passed:
            raise RuntimeError("Post-deploy validation failed.")
        return _stage_result(
            context_updates={
                "post_validation_report": report.model_dump(mode="json"),
                "sample_invocations": sample_invocations,
            },
            event_detail={"overall_passed": report.overall_passed},
        )

    async def route_stage(context: CompilationContext) -> StageExecutionResult:
        route_config = context.payload["route_config"]
        return _stage_result(
            event_detail={"route_id": route_config["default_route"]["route_id"]},
            rollback_payload={"route_id": route_config["default_route"]["route_id"]},
        )

    async def deploy_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        del context
        assert result.rollback_payload is not None
        deployment_revision = result.rollback_payload["deployment_revision"]
        await deployment_harness.rollback(str(deployment_revision))

    worker_celery_app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )

    async def execute_workflow_for_task(
        request: CompilationRequest,
    ) -> None:
        worker_engine = create_async_engine(worker_database_url)
        worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

        async def register_stage(context: CompilationContext) -> StageExecutionResult:
            manifest_yaml = str(context.payload["manifest_yaml"])
            artifact_payload = ArtifactVersionCreate(
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
                ir_json=context.payload["service_ir"],
                deployment_revision=str(context.payload["deployment_revision"]),
                route_config=context.payload["route_config"],
                validation_report=context.payload["post_validation_report"],
                is_active=True,
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash=hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest(),
                        storage_path="memory://manifests/sql-catalog.yaml",
                    )
                ],
            )
            async with worker_session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                created = await repository.create_version(artifact_payload)
            return _stage_result(
                context_updates={"registered_version": created.version_number},
                event_detail={
                    "service_id": created.service_id,
                    "version_number": created.version_number,
                },
            )

        activities = ActivityRegistry(
            stage_handlers={
                CompilationStage.DETECT: detect_stage,
                CompilationStage.EXTRACT: extract_stage,
                CompilationStage.ENHANCE: enhance_stage,
                CompilationStage.VALIDATE_IR: validate_ir_stage,
                CompilationStage.GENERATE: generate_stage,
                CompilationStage.DEPLOY: deploy_stage,
                CompilationStage.VALIDATE_RUNTIME: validate_runtime_stage,
                CompilationStage.ROUTE: route_stage,
                CompilationStage.REGISTER: register_stage,
            },
            rollback_handlers={
                CompilationStage.DEPLOY: deploy_rollback,
            },
        )
        workflow = CompilationWorkflow(
            store=SQLAlchemyCompilationJobStore(worker_session_factory),
            activities=activities,
        )
        try:
            await workflow.run(request)
        finally:
            await worker_engine.dispose()

    configure_compilation_executor(CallbackCompilationExecutor(callback=execute_workflow_for_task))

    compiler_api_app = create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=CeleryCompilationDispatcher(celery_app=worker_celery_app),
    )
    transport = httpx.ASGITransport(app=compiler_api_app)

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
            ) as http_client:
                submission = await http_client.post(
                    "/api/v1/compilations",
                    json={
                        "source_url": database_url,
                        "created_by": "e2e-user",
                        "service_name": "sql-catalog-api",
                        "options": {"protocol": "sql", "hints": {"schema": "main"}},
                    },
                )
                if submission.status_code != 202:
                    raise AssertionError(
                        f"Unexpected submit response {submission.status_code}: {submission.text}"
                    )
                assert submission.status_code == 202
                job_id = submission.json()["id"]

                job = await _wait_for_job_status(http_client, job_id, expected_status="succeeded")
                assert job["protocol"] == "sql"

                services = await http_client.get("/api/v1/services")
                assert services.status_code == 200
                listed_services = services.json()["services"]
                sql_service = next(
                    (s for s in listed_services if s["service_id"] == "sql-catalog-api"),
                    None,
                )
                assert sql_service is not None
                assert sql_service["active_version"] == 1

                async with http_client.stream(
                    "GET",
                    f"/api/v1/compilations/{job_id}/events",
                ) as response:
                    body = ""
                    async for chunk in response.aiter_text():
                        body += chunk

                assert response.status_code == 200
                assert "event: job.succeeded" in body
                assert "event: stage.succeeded" in body

                active_runtime = deployment_harness.current()
                service_ir = active_runtime.app.state.runtime_state.service_ir
                assert service_ir is not None
                assert service_ir.protocol == "sql"
                assert service_ir.base_url == database_url
                assert service_ir.metadata["database_schema"] == "main"
                assert service_ir.metadata["tables"] == ["customers", "orders"]
                assert service_ir.metadata["views"] == ["order_summaries"]
                query_order_summaries = next(
                    (op for op in service_ir.operations if op.id == "query_order_summaries"),
                    None,
                )
                assert query_order_summaries is not None
                assert query_order_summaries.sql is not None
                assert query_order_summaries.sql.relation_name == "order_summaries"

                tool_result = await deployment_harness.call_tool(
                    "query_order_summaries",
                    {"limit": 1},
                )

        assert tool_result["status"] == "ok"
        assert tool_result["result"] == {
            "relation": "order_summaries",
            "action": "query",
            "limit": 1,
            "row_count": 1,
            "rows": [
                {
                    "id": 1,
                    "customer_name": "Acme",
                    "total_cents": 1250,
                }
            ],
        }
    finally:
        reset_compilation_executor()
        await deployment_harness.aclose()


@pytest.mark.asyncio
async def test_jsonrpc_openrpc_compiles_to_running_runtime_and_tool_invocation(
    postgres_container: PostgresContainer,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """End-to-end: OpenRPC calculator spec → detect → extract → enhance → deploy → invoke."""
    jsonrpc_spec = JSONRPC_CALCULATOR_PATH.read_text(encoding="utf-8")
    worker_database_url = _to_asyncpg_url(postgres_container.get_connection_url())

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        """Mock JSON-RPC upstream that dispatches on method name."""
        body = json.loads(request.content.decode())
        method = body.get("method", "")
        params = body.get("params", {})
        request_id = body.get("id", 1)

        if method == "add":
            result = (params.get("a", 0) or 0) + (params.get("b", 0) or 0)
        elif method == "subtract":
            result = (params.get("a", 0) or 0) - (params.get("b", 0) or 0)
        elif method == "get_history":
            result = [{"op": "add", "a": 1, "b": 2, "result": 3}]
        elif method == "delete_history":
            result = True
        else:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": request_id,
                },
                request=request,
            )

        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "result": result, "id": request_id},
            request=request,
        )

    deployment_harness = RuntimeDeploymentHarness(
        tmp_path=tmp_path,
        upstream_handler=upstream_handler,
    )
    jsonrpc_extractor = JsonRpcExtractor()

    async def detect_stage(context: CompilationContext) -> StageExecutionResult:
        source = SourceConfig(
            url=context.request.source_url,
            file_content=context.request.source_content,
        )
        detection = TypeDetector([jsonrpc_extractor]).detect(source)
        return _stage_result(
            context_updates={"detection_confidence": detection.confidence},
            event_detail={"confidence": detection.confidence},
            protocol=detection.protocol_name,
        )

    async def extract_stage(context: CompilationContext) -> StageExecutionResult:
        source = SourceConfig(
            url=context.request.source_url,
            file_content=context.request.source_content,
        )
        service_ir = _prioritize_safe_operations(jsonrpc_extractor.extract(source))
        if service_ir.auth.type is not AuthType.none:
            service_ir = service_ir.model_copy(update={"auth": AuthConfig(type=AuthType.none)})
        service_id = context.request.service_name or service_ir.service_name
        return _stage_result(
            context_updates={
                "service_id": service_id,
                "service_ir": service_ir.model_dump(mode="json"),
                "version_number": 1,
            },
            event_detail={"operation_count": len(service_ir.operations)},
            protocol="jsonrpc",
            service_name=service_id,
        )

    async def enhance_stage(context: CompilationContext) -> StageExecutionResult:
        return await _run_optional_real_deepseek_enhancer(
            context,
            stub_model="stub-jsonrpc-enhancer",
            stub_input_tokens=8,
            stub_output_tokens=6,
        )

    async def validate_ir_stage(context: CompilationContext) -> StageExecutionResult:
        async with PreDeployValidator() as validator:
            report = await validator.validate(context.payload["service_ir"])
        if not report.overall_passed:
            raise RuntimeError("Pre-deploy validation failed.")
        return _stage_result(
            context_updates={"pre_validation_report": report.model_dump(mode="json")},
            event_detail={"overall_passed": report.overall_passed},
        )

    async def generate_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        manifest_set = generate_generic_manifests(
            service_ir,
            config=GenericManifestConfig(
                runtime_image="tool-compiler/mcp-runtime:latest",
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
            ),
        )
        return _stage_result(
            context_updates={
                "manifest_yaml": manifest_set.yaml,
                "route_config": manifest_set.route_config,
            },
            event_detail={"deployment_name": manifest_set.deployment["metadata"]["name"]},
            rollback_payload={"deployment_name": manifest_set.deployment["metadata"]["name"]},
        )

    async def deploy_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        revision = await deployment_harness.deploy(service_ir)
        return _stage_result(
            context_updates={"deployment_revision": revision},
            event_detail={"deployment_revision": revision},
            rollback_payload={"deployment_revision": revision},
        )

    async def validate_runtime_stage(context: CompilationContext) -> StageExecutionResult:
        service_ir = ServiceIR.model_validate(context.payload["service_ir"])
        sample_invocations = _build_sample_invocations(service_ir)
        deployed = deployment_harness.current()
        async with PostDeployValidator(
            client=deployed.runtime_client,
            tool_invoker=deployment_harness.call_tool,
        ) as validator:
            report = await validator.validate(
                "http://runtime",
                service_ir,
                sample_invocations=sample_invocations,
            )
        if not report.overall_passed:
            raise RuntimeError("Post-deploy validation failed.")
        return _stage_result(
            context_updates={
                "post_validation_report": report.model_dump(mode="json"),
                "sample_invocations": sample_invocations,
            },
            event_detail={"overall_passed": report.overall_passed},
        )

    async def route_stage(context: CompilationContext) -> StageExecutionResult:
        route_config = context.payload["route_config"]
        return _stage_result(
            event_detail={"route_id": route_config["default_route"]["route_id"]},
            rollback_payload={"route_id": route_config["default_route"]["route_id"]},
        )

    async def deploy_rollback(
        context: CompilationContext,
        result: StageExecutionResult,
    ) -> None:
        del context
        assert result.rollback_payload is not None
        deployment_revision = result.rollback_payload["deployment_revision"]
        await deployment_harness.rollback(str(deployment_revision))

    worker_celery_app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )

    async def execute_workflow_for_task(
        request: CompilationRequest,
    ) -> None:
        worker_engine = create_async_engine(worker_database_url)
        worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

        async def register_stage(context: CompilationContext) -> StageExecutionResult:
            manifest_yaml = str(context.payload["manifest_yaml"])
            artifact_payload = ArtifactVersionCreate(
                service_id=str(context.payload["service_id"]),
                version_number=int(context.payload["version_number"]),
                ir_json=context.payload["service_ir"],
                deployment_revision=str(context.payload["deployment_revision"]),
                route_config=context.payload["route_config"],
                validation_report=context.payload["post_validation_report"],
                is_active=True,
                artifacts=[
                    ArtifactRecordPayload(
                        artifact_type="manifest",
                        content_hash=hashlib.sha256(manifest_yaml.encode("utf-8")).hexdigest(),
                        storage_path="memory://manifests/calculator-jsonrpc.yaml",
                    )
                ],
            )
            async with worker_session_factory() as session:
                repository = ArtifactRegistryRepository(session)
                created = await repository.create_version(artifact_payload)
            return _stage_result(
                context_updates={"registered_version": created.version_number},
                event_detail={
                    "service_id": created.service_id,
                    "version_number": created.version_number,
                },
            )

        activities = ActivityRegistry(
            stage_handlers={
                CompilationStage.DETECT: detect_stage,
                CompilationStage.EXTRACT: extract_stage,
                CompilationStage.ENHANCE: enhance_stage,
                CompilationStage.VALIDATE_IR: validate_ir_stage,
                CompilationStage.GENERATE: generate_stage,
                CompilationStage.DEPLOY: deploy_stage,
                CompilationStage.VALIDATE_RUNTIME: validate_runtime_stage,
                CompilationStage.ROUTE: route_stage,
                CompilationStage.REGISTER: register_stage,
            },
            rollback_handlers={
                CompilationStage.DEPLOY: deploy_rollback,
            },
        )
        workflow = CompilationWorkflow(
            store=SQLAlchemyCompilationJobStore(worker_session_factory),
            activities=activities,
        )
        try:
            await workflow.run(request)
        finally:
            await worker_engine.dispose()

    configure_compilation_executor(CallbackCompilationExecutor(callback=execute_workflow_for_task))

    compiler_api_app = create_compiler_api_app(
        session_factory=session_factory,
        compilation_dispatcher=CeleryCompilationDispatcher(celery_app=worker_celery_app),
    )
    transport = httpx.ASGITransport(app=compiler_api_app)

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
            ) as http_client:
                submission = await http_client.post(
                    "/api/v1/compilations",
                    json={
                        "source_url": "https://calc.example.com/openrpc.json",
                        "source_content": jsonrpc_spec,
                        "created_by": "e2e-user",
                        "service_name": "calculator-jsonrpc",
                    },
                )
                if submission.status_code != 202:
                    raise AssertionError(
                        f"Unexpected submit response {submission.status_code}: {submission.text}"
                    )
                assert submission.status_code == 202
                job_id = submission.json()["id"]

                job = await _wait_for_job_status(http_client, job_id, expected_status="succeeded")
                assert job["protocol"] == "jsonrpc"

                services = await http_client.get("/api/v1/services")
                assert services.status_code == 200
                listed_services = services.json()["services"]
                jsonrpc_service = next(
                    (s for s in listed_services if s["service_id"] == "calculator-jsonrpc"),
                    None,
                )
                assert jsonrpc_service is not None
                assert jsonrpc_service["active_version"] == 1

                async with http_client.stream(
                    "GET",
                    f"/api/v1/compilations/{job_id}/events",
                ) as response:
                    body = ""
                    async for chunk in response.aiter_text():
                        body += chunk

                assert response.status_code == 200
                assert "event: job.succeeded" in body
                assert "event: stage.succeeded" in body

                active_runtime = deployment_harness.current()
                service_ir = active_runtime.app.state.runtime_state.service_ir
                assert service_ir is not None
                assert service_ir.protocol == "jsonrpc"
                assert len(service_ir.operations) == 4

                expected_methods = {"add", "subtract", "get_history", "delete_history"}
                actual_methods = {op.id for op in service_ir.operations}
                assert expected_methods == actual_methods, (
                    f"Missing methods: {expected_methods - actual_methods}"
                )

                # Verify JSON-RPC config is set on operations
                for op in service_ir.operations:
                    assert op.jsonrpc is not None, f"jsonrpc config not set on {op.id}"
                    assert op.jsonrpc.method_name == op.id

                # Invoke 'add' tool and verify correct JSON-RPC response
                tool_result = await deployment_harness.call_tool(
                    "add",
                    {"a": 3, "b": 7},
                )

        assert tool_result["status"] == "ok"
        assert tool_result["result"] == 10
    finally:
        reset_compilation_executor()
        await deployment_harness.aclose()


def _stage_result(
    *,
    context_updates: dict[str, Any] | None = None,
    event_detail: dict[str, Any] | None = None,
    rollback_payload: dict[str, Any] | None = None,
    protocol: str | None = None,
    service_name: str | None = None,
) -> StageExecutionResult:
    return StageExecutionResult(
        context_updates=context_updates or {},
        event_detail=event_detail,
        rollback_payload=rollback_payload,
        protocol=protocol,
        service_name=service_name,
    )


async def _wait_for_job_status(
    http_client: httpx.AsyncClient,
    job_id: str,
    *,
    expected_status: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    resolved_timeout_seconds = timeout_seconds
    if resolved_timeout_seconds is None:
        resolved_timeout_seconds = 30.0 if _env_flag("ENABLE_REAL_DEEPSEEK_E2E") else 10.0

    deadline = asyncio.get_running_loop().time() + resolved_timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await http_client.get(f"/api/v1/compilations/{job_id}")
        assert response.status_code == 200
        payload = cast(dict[str, Any], response.json())
        if payload["status"] == expected_status:
            return payload
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"Timed out waiting for compilation job {job_id} to reach {expected_status}."
    )
