"""Generic MCP runtime app."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Response, status
from mcp.server.fastmcp import FastMCP
from prometheus_client import CONTENT_TYPE_LATEST

from apps.mcp_runtime.event_bridge import EventBridgeClient, get_event_bridge_client
from apps.mcp_runtime.grpc_stream import ReflectionGrpcStreamExecutor
from apps.mcp_runtime.grpc_unary import ReflectionGrpcUnaryExecutor
from apps.mcp_runtime.loader import (
    RuntimeLoadError,
    create_runtime_server,
    load_service_ir,
    register_ir_prompts,
    register_ir_resources,
    register_ir_tools,
    register_runtime_resources,
)
from apps.mcp_runtime.observability import RuntimeObservability
from apps.mcp_runtime.proxy import (
    GrpcStreamExecutor,
    GrpcUnaryExecutor,
    RuntimeProxy,
    SqlExecutor,
)
from apps.mcp_runtime.sql import SQLRuntimeExecutor
from apps.mcp_runtime.webhook_adapter import WebhookAdapter
from libs.ir.models import EventSupportLevel, EventTransport, Operation, ServiceIR
from libs.observability.tracing import setup_tracer
from libs.secret_refs import (
    SecretReferenceCollisionError,
    ensure_no_secret_ref_name_collisions,
)


@dataclass
class RuntimeState:
    """In-memory runtime state for the loaded service IR."""

    mcp_server: FastMCP = field(default_factory=create_runtime_server)
    service_ir: ServiceIR | None = None
    registered_operations: dict[str, Operation] = field(default_factory=dict)
    service_ir_path: Path | None = None
    load_error: str | None = None
    proxy: RuntimeProxy | None = None
    observability: RuntimeObservability = field(default_factory=RuntimeObservability)

    @property
    def is_loaded(self) -> bool:
        return self.service_ir is not None and self.load_error is None

    async def aclose(self) -> None:
        if self.proxy is not None:
            await self.proxy.aclose()


def build_runtime_state(
    service_ir_path: str | Path | None = None,
    *,
    upstream_client: httpx.AsyncClient | None = None,
    sql_executor: SqlExecutor | None = None,
    grpc_unary_executor: GrpcUnaryExecutor | None = None,
    grpc_stream_executor: GrpcStreamExecutor | None = None,
    proxy_timeout: float = 10.0,
    failure_threshold: int = 5,
) -> RuntimeState:
    """Create runtime state and eagerly load the configured IR when present."""

    runtime_state = RuntimeState()
    if service_ir_path is None:
        runtime_state.load_error = "SERVICE_IR_PATH is not configured."
        return runtime_state

    runtime_state.service_ir_path = Path(service_ir_path)
    try:
        service_ir = load_service_ir(runtime_state.service_ir_path)
    except RuntimeLoadError as exc:
        runtime_state.load_error = str(exc)
        return runtime_state

    runtime_state.service_ir = service_ir
    try:
        ensure_no_secret_ref_name_collisions(
            [
                secret_ref
                for secret_ref in (
                    service_ir.auth.runtime_secret_ref,
                    service_ir.auth.basic_password_ref,
                    service_ir.auth.oauth2.client_id_ref
                    if service_ir.auth.oauth2 is not None
                    else None,
                    service_ir.auth.oauth2.client_secret_ref
                    if service_ir.auth.oauth2 is not None
                    else None,
                    service_ir.auth.mtls.cert_ref if service_ir.auth.mtls is not None else None,
                    service_ir.auth.mtls.key_ref if service_ir.auth.mtls is not None else None,
                    service_ir.auth.mtls.ca_ref if service_ir.auth.mtls is not None else None,
                    service_ir.auth.request_signing.secret_ref
                    if service_ir.auth.request_signing is not None
                    else None,
                )
                if secret_ref
            ],
            context="runtime auth secret resolution",
        )
    except SecretReferenceCollisionError as exc:
        runtime_state.load_error = str(exc)
        return runtime_state
    setup_tracer(service_ir.service_name, enable_local=True)
    runtime_state.mcp_server = create_runtime_server(name=service_ir.service_name)
    resolved_sql_executor = sql_executor
    if resolved_sql_executor is None and _native_sql_runtime_enabled(service_ir):
        resolved_sql_executor = SQLRuntimeExecutor(service_ir)
    resolved_grpc_unary_executor = grpc_unary_executor
    if resolved_grpc_unary_executor is None and _native_grpc_unary_runtime_enabled(service_ir):
        resolved_grpc_unary_executor = ReflectionGrpcUnaryExecutor(service_ir)
    resolved_grpc_stream_executor = grpc_stream_executor
    if resolved_grpc_stream_executor is None and _native_grpc_stream_runtime_enabled(service_ir):
        resolved_grpc_stream_executor = ReflectionGrpcStreamExecutor(service_ir)
    event_bridge_client_resolved: EventBridgeClient | None = None
    webhook_adapter_resolved: WebhookAdapter | None = None
    if _native_event_bridge_enabled(service_ir):
        transport = _dominant_event_transport(service_ir)
        event_bridge_client_resolved = get_event_bridge_client(transport)
    if _native_webhook_enabled(service_ir):
        callback_url = os.getenv("WEBHOOK_CALLBACK_BASE_URL")
        webhook_adapter_resolved = WebhookAdapter(callback_base_url=callback_url)
    runtime_state.proxy = RuntimeProxy(
        service_ir,
        observability=runtime_state.observability,
        client=upstream_client,
        sql_executor=resolved_sql_executor,
        grpc_unary_executor=resolved_grpc_unary_executor,
        grpc_stream_executor=resolved_grpc_stream_executor,
        event_bridge_client=event_bridge_client_resolved,
        webhook_adapter=webhook_adapter_resolved,
        timeout=proxy_timeout,
        failure_threshold=failure_threshold,
    )
    if _native_cli_runtime_enabled(service_ir):
        runtime_state.observability.logger.info(
            "CLI runtime enabled — %d CLI-backed operations detected",
            sum(1 for op in service_ir.operations if op.enabled and op.cli is not None),
        )
    runtime_state.registered_operations = register_ir_tools(
        runtime_state.mcp_server,
        service_ir,
        tool_handler=runtime_state.proxy.invoke,
    )
    register_ir_resources(
        runtime_state.mcp_server,
        service_ir,
        tool_handler=runtime_state.proxy.invoke,
    )
    register_ir_prompts(runtime_state.mcp_server, service_ir)
    register_runtime_resources(
        runtime_state.mcp_server,
        service_ir,
        observability=runtime_state.observability,
    )
    for operation_id, op in runtime_state.registered_operations.items():
        runtime_state.observability.register_operation(operation_id)
        if op.sla and op.sla.latency_budget_ms:
            runtime_state.observability.register_sla_budget(operation_id, op.sla.latency_budget_ms)
    return runtime_state


def create_app(
    service_ir_path: str | Path | None = None,
    *,
    upstream_client: httpx.AsyncClient | None = None,
    sql_executor: SqlExecutor | None = None,
    grpc_unary_executor: GrpcUnaryExecutor | None = None,
    grpc_stream_executor: GrpcStreamExecutor | None = None,
    proxy_timeout: float = 10.0,
    failure_threshold: int = 5,
) -> FastAPI:
    """Create the generic runtime application."""

    resolved_ir_path = service_ir_path or os.getenv("SERVICE_IR_PATH")
    runtime_state = build_runtime_state(
        resolved_ir_path,
        upstream_client=upstream_client,
        sql_executor=sql_executor,
        grpc_unary_executor=grpc_unary_executor,
        grpc_stream_executor=grpc_stream_executor,
        proxy_timeout=proxy_timeout,
        failure_threshold=failure_threshold,
    )
    mcp_http_app = runtime_state.mcp_server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with runtime_state.mcp_server.session_manager.run():
            try:
                yield
            finally:
                await runtime_state.aclose()

    app = FastAPI(title="Generic MCP Runtime", version="0.1.0", lifespan=lifespan)
    app.state.runtime_state = runtime_state
    app.mount("/", mcp_http_app)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness probe — indicates process is alive."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict[str, Any]:
        """Readiness probe — indicates runtime is loaded and ready."""
        return await _runtime_status(runtime_state, response)

    @app.get("/tools")
    async def list_tools(response: Response) -> dict[str, Any]:
        if not runtime_state.is_loaded:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {
                "status": "not_ready",
                "error": runtime_state.load_error,
                "tool_count": 0,
                "tools": [],
            }

        tools = await runtime_state.mcp_server.list_tools()
        service_name = runtime_state.service_ir.service_name if runtime_state.service_ir else None
        return {
            "status": "ready",
            "service_name": service_name,
            "tool_count": len(tools),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
                for tool in tools
            ],
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            content=runtime_state.observability.render_metrics(),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app


async def _runtime_status(runtime_state: RuntimeState, response: Response) -> dict[str, Any]:
    service_ir_path = str(runtime_state.service_ir_path) if runtime_state.service_ir_path else None
    if not runtime_state.is_loaded:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "error": runtime_state.load_error,
            "service_ir_path": service_ir_path,
        }

    service_name = runtime_state.service_ir.service_name if runtime_state.service_ir else None
    upstream_problems: list[str] = []

    # Check upstream connectivity for HTTP-based protocols (informational only)
    if runtime_state.proxy is not None and runtime_state.service_ir is not None:
        base_url = runtime_state.service_ir.base_url
        protocol = runtime_state.service_ir.protocol
        if protocol not in {"sql", "grpc"} and base_url:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.head(base_url)
                    if resp.status_code >= 500:
                        upstream_problems.append(f"upstream {base_url} returned {resp.status_code}")
            except httpx.HTTPError:
                upstream_problems.append(f"upstream {base_url} is unreachable")

    result: dict[str, Any] = {
        "status": "ok",
        "service_name": service_name,
        "tool_count": len(runtime_state.registered_operations),
        "service_ir_path": service_ir_path,
    }
    if upstream_problems:
        result["upstream_problems"] = upstream_problems
    return result


def _native_grpc_stream_runtime_enabled(service_ir: ServiceIR) -> bool:
    configured = os.getenv("ENABLE_NATIVE_GRPC_STREAM", "").strip().lower()
    if configured not in {"1", "true", "yes", "on"}:
        return False

    return any(
        descriptor.transport is EventTransport.grpc_stream
        and descriptor.support is EventSupportLevel.supported
        for descriptor in service_ir.event_descriptors
    )


def _native_grpc_unary_runtime_enabled(service_ir: ServiceIR) -> bool:
    configured = os.getenv("ENABLE_NATIVE_GRPC_UNARY", "").strip().lower()
    if configured not in {"1", "true", "yes", "on"}:
        return False

    return any(
        operation.enabled and operation.grpc_unary is not None
        for operation in service_ir.operations
    )


def _native_sql_runtime_enabled(service_ir: ServiceIR) -> bool:
    return any(
        operation.enabled and operation.sql is not None for operation in service_ir.operations
    )


def _native_cli_runtime_enabled(service_ir: ServiceIR) -> bool:
    return any(
        operation.enabled and operation.cli is not None for operation in service_ir.operations
    )


def _native_event_bridge_enabled(service_ir: ServiceIR) -> bool:
    broker_transports = {
        EventTransport.kafka,
        EventTransport.rabbitmq,
        EventTransport.mqtt,
        EventTransport.amqp,
        EventTransport.pulsar,
        EventTransport.async_event,
    }
    return any(
        d.support is not EventSupportLevel.unsupported and d.transport in broker_transports
        for d in service_ir.event_descriptors
    )


def _dominant_event_transport(service_ir: ServiceIR) -> EventTransport:
    for d in service_ir.event_descriptors:
        if d.support is not EventSupportLevel.unsupported:
            return d.transport
    return EventTransport.async_event


def _native_webhook_enabled(service_ir: ServiceIR) -> bool:
    wh_transports = {EventTransport.webhook, EventTransport.callback}
    return any(
        d.support is not EventSupportLevel.unsupported and d.transport in wh_transports
        for d in service_ir.event_descriptors
    )


app = create_app()
