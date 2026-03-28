"""Integration tests for the generic MCP runtime."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from apps.mcp_runtime import create_app, load_service_ir

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ir"
VALID_IR_PATH = FIXTURES_DIR / "service_ir_valid.json"
INVALID_IR_PATH = FIXTURES_DIR / "service_ir_invalid.json"


def test_load_service_ir_fixture() -> None:
    service_ir = load_service_ir(VALID_IR_PATH)
    enabled_operation_ids = [
        operation.id for operation in service_ir.operations if operation.enabled
    ]

    assert service_ir.service_name == "billing-runtime"
    assert enabled_operation_ids == ["listAccounts"]


def test_load_invalid_ir_fixture_auto_disables() -> None:
    service_ir = load_service_ir(INVALID_IR_PATH)
    assert service_ir.service_name == "invalid-runtime"
    # unknown risk with enabled=True should be auto-corrected to disabled
    assert all(not op.enabled for op in service_ir.operations)


@pytest.mark.asyncio
async def test_runtime_loads_ir_registers_tools_and_reports_healthy() -> None:
    app = create_app(service_ir_path=VALID_IR_PATH)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health_response = await client.get("/healthz")
        ready_response = await client.get("/readyz")
        tools_response = await client.get("/tools")

    assert health_response.status_code == 200
    assert ready_response.status_code == 200
    assert tools_response.status_code == 200
    assert tools_response.json()["tool_count"] == 1
    assert [tool["name"] for tool in tools_response.json()["tools"]] == ["listAccounts"]

    runtime_tools = await app.state.runtime_state.mcp_server.list_tools()
    assert [tool.name for tool in runtime_tools] == ["listAccounts"]
    assert runtime_tools[0].inputSchema["required"] == ["customer_id"]


@pytest.mark.asyncio
async def test_runtime_loads_ir_with_all_ops_disabled() -> None:
    app = create_app(service_ir_path=INVALID_IR_PATH)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health_response = await client.get("/healthz")
        ready_response = await client.get("/readyz")
        tools_response = await client.get("/tools")

    assert health_response.status_code == 200
    assert ready_response.status_code == 200
    assert tools_response.status_code == 200
    assert tools_response.json()["tool_count"] == 0
