"""Integration tests for runtime metrics and tracing."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime import create_app
from libs.observability.logging import StructuredFormatter

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ir"
PROXY_IR_PATH = FIXTURES_DIR / "service_ir_proxy.json"


@pytest.mark.asyncio
async def test_metrics_endpoint_reports_tool_calls_latency_errors_and_breaker_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"note": "ok"}, request=request)
        return httpx.Response(503, json={"error": "unavailable"}, request=request)

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        await app.state.runtime_state.mcp_server.call_tool(
            "createNote",
            {"account_id": "acct-1", "payload": {"title": "Hello"}},
        )

        for _ in range(5):
            with pytest.raises(ToolError, match="status 503"):
                await app.state.runtime_state.mcp_server.call_tool(
                    "getAccount",
                    {"account_id": "acct-1"},
                )

        with pytest.raises(ToolError, match="Circuit breaker is open"):
            await app.state.runtime_state.mcp_server.call_tool(
                "getAccount",
                {"account_id": "acct-1"},
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            metrics_response = await client.get("/metrics")
    finally:
        await upstream_client.aclose()

    metrics_text = metrics_response.text

    assert metrics_response.status_code == 200
    assert (
        'mcp_runtime_tool_calls_total{operation_id="createNote",outcome="success"} 1.0'
        in metrics_text
    )
    assert (
        'mcp_runtime_tool_calls_total{operation_id="getAccount",outcome="error"} 6.0'
        in metrics_text
    )
    assert (
        'mcp_runtime_upstream_errors_total{error_type="upstream_status",'
        'operation_id="getAccount"} 5.0'
    ) in metrics_text
    assert 'mcp_runtime_circuit_breaker_state{operation_id="getAccount"} 1.0' in metrics_text
    assert 'mcp_runtime_tool_latency_seconds_count{operation_id="createNote"} 1.0' in metrics_text


@pytest.mark.asyncio
async def test_runtime_logs_include_trace_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter(component="mcp-runtime"))

    logger = logging.getLogger("apps.mcp_runtime.proxy")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    async def success_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "acct-1", "name": "Primary"}, request=request)

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(success_handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        await app.state.runtime_state.mcp_server.call_tool(
            "getAccount",
            {"account_id": "acct-1"},
        )
    finally:
        await upstream_client.aclose()
        handler.flush()
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    parsed_logs = [
        json.loads(line)
        for line in stream.getvalue().splitlines()
        if "runtime tool invocation" in line
    ]

    assert parsed_logs
    completed_log = next(
        entry for entry in parsed_logs if entry["message"] == "runtime tool invocation completed"
    )
    assert completed_log["component"] == "mcp-runtime"
    assert completed_log["extra"]["operation_id"] == "getAccount"
    assert completed_log["trace_id"]
    assert completed_log["span_id"]
