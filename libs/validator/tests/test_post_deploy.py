"""Tests for the post-deploy validation harness."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import httpx
import pytest

from apps.mcp_runtime import create_app, load_service_ir
from libs.extractors.base import SourceConfig
from libs.extractors.soap import SOAPWSDLExtractor
from libs.extractors.sql import SQLExtractor
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
    ServiceIR,
    SourceType,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
)
from libs.ir.schema import serialize_ir
from libs.validator.audit import AuditThresholds, check_thresholds
from libs.validator.post_deploy import PostDeployValidator

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "ir"
VALID_IR_PATH = FIXTURES_DIR / "service_ir_valid.json"
PROXY_IR_PATH = FIXTURES_DIR / "service_ir_proxy.json"
WSDL_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "tests"
    / "fixtures"
    / "wsdl"
    / "order_service.wsdl"
)


def _write_service_ir(tmp_path: Path, name: str, service_ir: ServiceIR) -> Path:
    output_path = tmp_path / name
    output_path.write_text(serialize_ir(service_ir), encoding="utf-8")
    return output_path


def _build_grpc_stream_ir(*, base_url: str = "grpc://inventory.example.test:443") -> ServiceIR:
    return ServiceIR(
        source_hash="e" * 64,
        protocol="grpc",
        service_name="grpc-stream-validator",
        service_description="gRPC stream validation fixture",
        base_url=base_url,
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
                    max_messages=1,
                    idle_timeout_seconds=2.0,
                ),
            )
        ],
    )


def _build_grpc_unary_ir(*, base_url: str = "grpc://inventory.example.test:443") -> ServiceIR:
    return ServiceIR(
        source_hash="g" * 64,
        protocol="grpc",
        service_name="grpc-unary-validator",
        service_description="gRPC unary validation fixture",
        base_url=base_url,
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
                    rpc_path="/catalog.v1.InventoryService/LookupInventory",
                    timeout_seconds=2.0,
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


def _build_graphql_ir(*, base_url: str = "https://catalog.example.test") -> ServiceIR:
    return ServiceIR(
        source_hash="f" * 64,
        protocol="graphql",
        service_name="graphql-validator",
        service_description="GraphQL validation fixture",
        base_url=base_url,
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="searchProducts",
                name="Search Products",
                description="Search products by term.",
                method="POST",
                path="/graphql",
                params=[Param(name="term", type="string", required=True)],
                graphql=GraphQLOperationConfig(
                    operation_type=GraphQLOperationType.query,
                    operation_name="searchProducts",
                    document=(
                        "query searchProducts($term: String!) {\n"
                        "  searchProducts(term: $term) { id name }\n"
                        "}"
                    ),
                    variable_names=["term"],
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


def _build_graphql_query_and_mutation_ir(
    *,
    base_url: str = "https://catalog.example.test",
) -> ServiceIR:
    return ServiceIR(
        source_hash="f" * 64,
        protocol="graphql",
        service_name="graphql-validator",
        service_description="GraphQL validation fixture",
        base_url=base_url,
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="searchProducts",
                name="Search Products",
                description="Search products by term.",
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
                        "query searchProducts($term: String!, $limit: Int) {\n"
                        "  searchProducts(term: $term, limit: $limit) { id name }\n"
                        "}"
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
                description="Adjust stock by delta.",
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
                        "mutation adjustInventory($sku: String!, $delta: Int!) {\n"
                        "  adjustInventory(sku: $sku, delta: $delta) { sku }\n"
                        "}"
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


def _build_soap_ir() -> ServiceIR:
    return SOAPWSDLExtractor().extract(SourceConfig(file_path=str(WSDL_FIXTURE_PATH)))


def _initialize_sqlite_catalog(tmp_path: Path) -> str:
    database_path = tmp_path / "validator-catalog.db"
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

            INSERT INTO customers(name) VALUES ('Acme');
            INSERT INTO orders(customer_id, total_cents, notes) VALUES (1, 1250, 'rush');
            """
        )
        connection.commit()
    finally:
        connection.close()

    return f"sqlite:///{database_path}"


def _build_sql_ir(tmp_path: Path) -> ServiceIR:
    database_url = _initialize_sqlite_catalog(tmp_path)
    return SQLExtractor().extract(SourceConfig(url=database_url, hints={"schema": "main"}))


def _build_manual_sql_query_and_insert_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="h" * 64,
        protocol="sql",
        service_name="sql-validator",
        service_description="SQL validation fixture",
        base_url="sqlite:///validator-catalog.db",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="query_orders",
                name="Query Orders",
                description="Query orders.",
                method="GET",
                path="/orders",
                params=[Param(name="limit", type="integer", required=False, default=1)],
                sql=SqlOperationConfig(
                    schema_name="main",
                    relation_name="orders",
                    relation_kind=SqlRelationKind.table,
                    action=SqlOperationType.query,
                    filterable_columns=["customer_id"],
                    default_limit=1,
                    max_limit=50,
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
                description="Insert order.",
                method="POST",
                path="/orders",
                params=[
                    Param(name="customer_id", type="integer", required=True),
                    Param(name="total_cents", type="integer", required=True),
                ],
                sql=SqlOperationConfig(
                    schema_name="main",
                    relation_name="orders",
                    relation_kind=SqlRelationKind.table,
                    action=SqlOperationType.insert,
                    insertable_columns=["customer_id", "total_cents"],
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


@pytest.mark.asyncio
async def test_healthy_runtime_passes_post_deploy_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "acct-1", "name": "Primary", "secret": "ignore"},
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            report = await validator.validate(
                "http://testserver",
                load_service_ir(PROXY_IR_PATH),
                sample_invocations={"getAccount": {"account_id": "acct-1"}},
            )
    finally:
        await upstream_client.aclose()

    assert report.overall_passed is True
    assert report.get_result("health").passed is True
    assert report.get_result("tool_listing").passed is True
    assert report.get_result("invocation_smoke").passed is True


@pytest.mark.asyncio
async def test_runtime_with_wrong_ir_fails_tool_listing_check() -> None:
    app = create_app(service_ir_path=VALID_IR_PATH)
    wrong_ir = load_service_ir(VALID_IR_PATH).model_copy(deep=True)
    wrong_ir.operations[0].id = "wrongOperation"
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        validator = PostDeployValidator(client=client)
        report = await validator.validate("http://testserver", wrong_ir)

    assert report.overall_passed is False
    assert report.get_result("health").passed is True
    assert report.get_result("tool_listing").passed is False
    assert "mismatch" in report.get_result("tool_listing").details.lower()
    assert report.get_result("invocation_smoke").passed is False


@pytest.mark.asyncio
async def test_post_deploy_validator_uses_first_available_sample_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"note": "created", "status": "ok"},
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            report = await validator.validate(
                "http://testserver",
                load_service_ir(PROXY_IR_PATH),
                sample_invocations={
                    "createNote": {
                        "account_id": "acct-1",
                        "payload": {"title": "Quarterly close"},
                    }
                },
            )
    finally:
        await upstream_client.aclose()

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "createNote" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_post_deploy_validator_accepts_graphql_runtime_smoke(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"searchProducts": [{"id": "sku-1", "name": "Widget"}]}},
            request=request,
        )

    service_ir_path = _write_service_ir(
        tmp_path,
        "graphql_post_deploy_ir.json",
        _build_graphql_ir(),
    )
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            report = await validator.validate(
                "http://testserver",
                load_service_ir(service_ir_path),
                sample_invocations={"searchProducts": {"term": "widget"}},
            )
    finally:
        await upstream_client.aclose()

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "searchProducts" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_post_deploy_validator_prefers_graphql_query_over_mutation(
    tmp_path: Path,
) -> None:
    service_ir = _build_graphql_query_and_mutation_ir()
    service_ir_path = _write_service_ir(
        tmp_path,
        "graphql_query_preferred_post_deploy_ir.json",
        service_ir,
    )
    app = create_app(service_ir_path=service_ir_path)
    transport = httpx.ASGITransport(app=app)
    invoked: list[str] = []

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def tool_invoker(
            tool_name: str,
            arguments: dict[str, object],
        ) -> dict[str, object]:
            invoked.append(tool_name)
            if tool_name == "searchProducts":
                assert arguments == {"term": "widget"}
            return {"status": "ok", "result": {"ok": True}}

        validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
        report = await validator.validate(
            "http://testserver",
            service_ir,
            sample_invocations={
                "searchProducts": {"term": "widget"},
                "adjustInventory": {"sku": "sku-1", "delta": 1},
            },
        )

    assert report.overall_passed is True
    assert invoked == ["searchProducts"]


@pytest.mark.asyncio
async def test_post_deploy_validator_accepts_supported_sse_streaming_tool(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='event: update\ndata: {"account_id":"acct-1","status":"ready"}\n\n',
            headers={"Content-Type": "text/event-stream"},
            request=request,
        )

    service_ir = ServiceIR(
        source_hash="d" * 64,
        protocol="openapi",
        service_name="streaming-validator",
        service_description="Streaming validation fixture",
        base_url="https://api.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="streamAccountEvents",
                name="Stream Account Events",
                description="Consume account event updates.",
                method="GET",
                path="/accounts/{account_id}/events",
                params=[Param(name="account_id", type="string", required=True)],
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
                id="streamAccountEvents:sse",
                name="streamAccountEvents",
                transport=EventTransport.sse,
                support=EventSupportLevel.supported,
                operation_id="streamAccountEvents",
                channel="/accounts/{account_id}/events",
                metadata={"max_events": 1},
            )
        ],
    )
    service_ir_path = _write_service_ir(tmp_path, "streaming_post_deploy_ir.json", service_ir)
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            report = await validator.validate(
                "http://testserver",
                service_ir,
                sample_invocations={"streamAccountEvents": {"account_id": "acct-1"}},
            )
    finally:
        await upstream_client.aclose()

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "streamAccountEvents" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_post_deploy_validator_accepts_supported_native_grpc_streaming_tool(
    tmp_path: Path,
) -> None:
    class StubGrpcStreamExecutor:
        async def invoke(
            self,
            *,
            operation: Operation,
            arguments: dict[str, object],
            descriptor: EventDescriptor,
            config: GrpcStreamRuntimeConfig,
        ) -> dict[str, object]:
            assert operation.id == "watchInventory"
            assert arguments == {"payload": {"sku": "sku-1"}}
            assert descriptor.transport is EventTransport.grpc_stream
            assert config.mode is GrpcStreamMode.server
            return {
                "events": [
                    {
                        "message_type": "protobuf",
                        "parsed_data": {"sku": "sku-1", "status": "ready"},
                    }
                ],
                "lifecycle": {
                    "termination_reason": "max_messages",
                    "messages_collected": 1,
                    "rpc_path": config.rpc_path,
                    "mode": config.mode.value,
                },
            }

    service_ir = _build_grpc_stream_ir()
    service_ir_path = _write_service_ir(tmp_path, "grpc_stream_post_deploy_ir.json", service_ir)
    app = create_app(
        service_ir_path=service_ir_path,
        grpc_stream_executor=StubGrpcStreamExecutor(),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def tool_invoker(
            tool_name: str,
            arguments: dict[str, object],
        ) -> dict[str, object]:
            _, structured = await app.state.runtime_state.mcp_server.call_tool(
                tool_name,
                arguments,
            )
            return cast(dict[str, object], structured)

        validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
        report = await validator.validate(
            "http://testserver",
            service_ir,
            sample_invocations={"watchInventory": {"payload": {"sku": "sku-1"}}},
        )

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "grpc_stream" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_post_deploy_validator_accepts_supported_native_grpc_unary_tool(
    tmp_path: Path,
) -> None:
    class StubGrpcUnaryExecutor:
        async def invoke(
            self,
            *,
            operation: Operation,
            arguments: dict[str, object],
            config: GrpcUnaryRuntimeConfig,
        ) -> dict[str, object]:
            assert operation.id == "LookupInventory"
            assert arguments == {"sku": "sku-1"}
            assert config.rpc_path == "/catalog.v1.InventoryService/LookupInventory"
            return {"sku": "sku-1", "count": 3}

    service_ir = _build_grpc_unary_ir()
    service_ir_path = _write_service_ir(tmp_path, "grpc_unary_post_deploy_ir.json", service_ir)
    app = create_app(
        service_ir_path=service_ir_path,
        grpc_unary_executor=StubGrpcUnaryExecutor(),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def tool_invoker(
            tool_name: str,
            arguments: dict[str, object],
        ) -> dict[str, object]:
            _, structured = await app.state.runtime_state.mcp_server.call_tool(
                tool_name,
                arguments,
            )
            return cast(dict[str, object], structured)

        validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
        report = await validator.validate(
            "http://testserver",
            service_ir,
            sample_invocations={"LookupInventory": {"sku": "sku-1"}},
        )

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "LookupInventory" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_post_deploy_validator_accepts_soap_runtime_smoke(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
                'xmlns:tns="http://example.com/orders/wsdl">'
                "<soap:Body>"
                "<tns:GetOrderStatusResponse>"
                "<tns:status>ready</tns:status>"
                "</tns:GetOrderStatusResponse>"
                "</soap:Body>"
                "</soap:Envelope>"
            ),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            request=request,
        )

    service_ir = _build_soap_ir()
    service_ir_path = _write_service_ir(tmp_path, "soap_post_deploy_ir.json", service_ir)
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            report = await validator.validate(
                "http://testserver",
                load_service_ir(service_ir_path),
                sample_invocations={"GetOrderStatus": {"orderId": "ord-1"}},
            )
    finally:
        await upstream_client.aclose()

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "GetOrderStatus" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_post_deploy_validator_accepts_sql_runtime_smoke(
    tmp_path: Path,
) -> None:
    service_ir = _build_sql_ir(tmp_path)
    service_ir_path = _write_service_ir(tmp_path, "sql_post_deploy_ir.json", service_ir)
    app = create_app(service_ir_path=service_ir_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def tool_invoker(
            tool_name: str,
            arguments: dict[str, object],
        ) -> dict[str, object]:
            _, structured = await app.state.runtime_state.mcp_server.call_tool(
                tool_name,
                arguments,
            )
            return cast(dict[str, object], structured)

        validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
        report = await validator.validate(
            "http://testserver",
            load_service_ir(service_ir_path),
            sample_invocations={"query_orders": {"customer_id": 1, "limit": 1}},
        )

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "query_orders" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_post_deploy_validator_prefers_sql_query_over_insert(
    tmp_path: Path,
) -> None:
    service_ir = _build_manual_sql_query_and_insert_ir()
    service_ir_path = _write_service_ir(
        tmp_path,
        "sql_query_preferred_post_deploy_ir.json",
        service_ir,
    )
    app = create_app(service_ir_path=service_ir_path)
    transport = httpx.ASGITransport(app=app)
    invoked: list[str] = []

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def tool_invoker(
            tool_name: str,
            arguments: dict[str, object],
        ) -> dict[str, object]:
            invoked.append(tool_name)
            if tool_name == "query_orders":
                assert arguments == {"limit": 1}
            return {"status": "ok", "result": {"ok": True}}

        validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
        report = await validator.validate(
            "http://testserver",
            service_ir,
            sample_invocations={
                "query_orders": {"limit": 1},
                "insert_orders": {"customer_id": 1, "total_cents": 1250},
            },
        )

    assert report.overall_passed is True
    assert invoked == ["query_orders"]


@pytest.mark.asyncio
async def test_post_deploy_validator_rejects_wrong_grpc_stream_transport_shape(
    tmp_path: Path,
) -> None:
    service_ir = _build_grpc_stream_ir()
    service_ir_path = _write_service_ir(
        tmp_path,
        "grpc_stream_post_deploy_invalid_transport_ir.json",
        service_ir,
    )
    app = create_app(service_ir_path=service_ir_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def tool_invoker(
            tool_name: str,
            arguments: dict[str, object],
        ) -> dict[str, object]:
            del tool_name, arguments
            return {
                "status": "ok",
                "transport": "sse",
                "result": {"events": [], "lifecycle": {}},
            }

        validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
        report = await validator.validate(
            "http://testserver",
            service_ir,
            sample_invocations={"watchInventory": {"payload": {"sku": "sku-1"}}},
        )

    assert report.overall_passed is False
    assert report.get_result("invocation_smoke").passed is False
    assert "expected 'grpc_stream'" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_validate_with_audit_returns_report_and_audit_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_with_audit returns standard report plus audit summary."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "acct-1", "name": "Primary", "secret": "ignore"},
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            report, audit_summary = await validator.validate_with_audit(
                "http://testserver",
                load_service_ir(PROXY_IR_PATH),
                sample_invocations={
                    "getAccount": {"account_id": "acct-1"},
                    "createNote": {
                        "account_id": "acct-1",
                        "payload": {"title": "Test"},
                    },
                },
            )
    finally:
        await upstream_client.aclose()

    assert report.overall_passed is True
    assert report.audit_summary is audit_summary
    assert audit_summary.discovered_operations > 0
    assert audit_summary.passed + audit_summary.failed + audit_summary.skipped == len(
        audit_summary.results
    )
    assert audit_summary.failed == 0
    results_by_tool = {r.tool_name: r for r in audit_summary.results}
    assert results_by_tool["getAccount"].outcome == "passed"


@pytest.mark.asyncio
async def test_validate_with_audit_threshold_violations_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check_thresholds reports violations when audit summary does not meet minimums."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "acct-1", "name": "Primary", "secret": "ignore"},
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def tool_invoker(
                tool_name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                _, structured = await app.state.runtime_state.mcp_server.call_tool(
                    tool_name,
                    arguments,
                )
                return cast(dict[str, object], structured)

            validator = PostDeployValidator(client=client, tool_invoker=tool_invoker)
            _, audit_summary = await validator.validate_with_audit(
                "http://testserver",
                load_service_ir(PROXY_IR_PATH),
                sample_invocations={"getAccount": {"account_id": "acct-1"}},
            )
    finally:
        await upstream_client.aclose()

    # Require more passed tools than actually exist to trigger a threshold violation
    strict_thresholds = AuditThresholds(min_passed=100)
    violations = check_thresholds(audit_summary, strict_thresholds)
    assert len(violations) == 1
    assert "passed count" in violations[0].lower()

    # Zero-tolerance threshold should pass since we have no failures
    clean_thresholds = AuditThresholds(max_failed=0)
    assert check_thresholds(audit_summary, clean_thresholds) == []
