"""Integration coverage for the production MCP streamable HTTP tool invoker."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import uvicorn

from apps.compiler_worker.activities import build_streamable_http_tool_invoker
from apps.compiler_worker.activities.production import _build_sample_invocations
from apps.mcp_runtime import create_app, load_service_ir
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.ir.schema import serialize_ir
from libs.validator import PostDeployValidator

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "ir"
PROXY_IR_PATH = FIXTURES_DIR / "service_ir_proxy.json"


def _write_service_ir(tmp_path: Path, name: str, service_ir: ServiceIR) -> Path:
    output_path = tmp_path / name
    output_path.write_text(serialize_ir(service_ir), encoding="utf-8")
    return output_path


def _build_grpc_stream_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="f" * 64,
        protocol="grpc",
        service_name="grpc-stream-runtime",
        service_description="gRPC stream HTTP invoker fixture",
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
                    max_messages=1,
                    idle_timeout_seconds=2.0,
                ),
            )
        ],
    )


def _build_grpc_unary_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="e" * 64,
        protocol="grpc",
        service_name="grpc-unary-runtime",
        service_description="gRPC unary HTTP invoker fixture",
        base_url="grpc://inventory.example.test:443",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="ListItems",
                name="List Items",
                description="Execute a native gRPC list-items lookup.",
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
                    rpc_path="/catalog.v1.InventoryService/ListItems",
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


@pytest.mark.asyncio
async def test_streamable_http_tool_invoker_calls_runtime_over_http(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port: int,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "acct-1", "name": "Primary", "secret": "ignore"},
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=unused_tcp_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("Timed out waiting for runtime HTTP server startup.")

        base_url = f"http://127.0.0.1:{unused_tcp_port}"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            validator = PostDeployValidator(
                client=client,
                tool_invoker=build_streamable_http_tool_invoker(base_url),
            )
            report = await validator.validate(
                base_url,
                load_service_ir(PROXY_IR_PATH),
                sample_invocations={"getAccount": {"account_id": "acct-1"}},
            )

        assert report.overall_passed is True
        assert report.get_result("invocation_smoke").passed is True
    finally:
        server.should_exit = True
        await server_task
        await upstream_client.aclose()


@pytest.mark.asyncio
async def test_streamable_http_tool_invoker_accepts_cluster_service_hosts(
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
    app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
    base_url = "http://petstore-live-r11-v1.tool-compiler-gke-test-r11.svc.cluster.local:8003"

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)

        def client_factory(_: str) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=transport,
                base_url=base_url,
                follow_redirects=True,
                timeout=30.0,
            )

        async with httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            validator = PostDeployValidator(
                client=client,
                tool_invoker=build_streamable_http_tool_invoker(
                    base_url,
                    http_client_factory=client_factory,
                ),
            )
            report = await validator.validate(
                base_url,
                load_service_ir(PROXY_IR_PATH),
                sample_invocations={"getAccount": {"account_id": "acct-1"}},
            )

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    await upstream_client.aclose()


@pytest.mark.asyncio
async def test_streamable_http_tool_invoker_supports_native_grpc_stream_validation(
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
    service_ir_path = _write_service_ir(tmp_path, "grpc_stream_http_invoker_ir.json", service_ir)
    app = create_app(
        service_ir_path=service_ir_path,
        grpc_stream_executor=StubGrpcStreamExecutor(),
    )
    base_url = "http://grpc-stream-runtime.test"

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)

        def client_factory(_: str) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=transport,
                base_url=base_url,
                follow_redirects=True,
                timeout=30.0,
            )

        async with httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            validator = PostDeployValidator(
                client=client,
                tool_invoker=build_streamable_http_tool_invoker(
                    base_url,
                    http_client_factory=client_factory,
                ),
            )
            report = await validator.validate(
                base_url,
                service_ir,
                sample_invocations={"watchInventory": {"payload": {"sku": "sku-1"}}},
            )

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "grpc_stream" in report.get_result("invocation_smoke").details


@pytest.mark.asyncio
async def test_streamable_http_tool_invoker_supports_native_grpc_unary_validation(
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
            assert operation.id == "ListItems"
            assert arguments == {
                "location_id": "1",
                "page_size": 1,
                "page_token": "sample",
                "filter": None,
                "reason": None,
            }
            assert config.rpc_path == "/catalog.v1.InventoryService/ListItems"
            return {
                "items": [
                    {
                        "sku": "warehouse-1-sku",
                        "title": "Puzzle Box",
                    }
                ]
            }

    service_ir = _build_grpc_unary_ir()
    service_ir_path = _write_service_ir(tmp_path, "grpc_unary_http_invoker_ir.json", service_ir)
    app = create_app(
        service_ir_path=service_ir_path,
        grpc_unary_executor=StubGrpcUnaryExecutor(),
    )
    base_url = "http://grpc-unary-runtime.test"

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)

        def client_factory(_: str) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=transport,
                base_url=base_url,
                follow_redirects=True,
                timeout=30.0,
            )

        async with httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            validator = PostDeployValidator(
                client=client,
                tool_invoker=build_streamable_http_tool_invoker(
                    base_url,
                    http_client_factory=client_factory,
                ),
            )
            report = await validator.validate(
                base_url,
                service_ir,
                sample_invocations=_build_sample_invocations(service_ir),
            )

    assert report.overall_passed is True
    assert report.get_result("invocation_smoke").passed is True
    assert "ListItems" in report.get_result("invocation_smoke").details
