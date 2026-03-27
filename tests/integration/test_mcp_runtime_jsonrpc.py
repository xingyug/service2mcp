"""Integration tests for JSON-RPC 2.0 runtime proxy behavior."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from apps.mcp_runtime import create_app
from libs.extractors.base import SourceConfig
from libs.extractors.jsonrpc import JsonRpcExtractor
from libs.ir.schema import serialize_ir

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "jsonrpc_specs"


def _extract_jsonrpc_ir(fixture_name: str):
    """Extract ServiceIR from a JSON-RPC fixture."""
    fixture_path = FIXTURES_DIR / fixture_name
    source = SourceConfig(file_content=fixture_path.read_text(encoding="utf-8"))
    return JsonRpcExtractor().extract(source)


@pytest.mark.asyncio
async def test_jsonrpc_method_produces_post_request(tmp_path: Path) -> None:
    """Call a JSON-RPC tool ('add'), verify POST is sent to the upstream endpoint."""
    ir = _extract_jsonrpc_ir("openrpc_calculator.json")
    ir_path = tmp_path / "jsonrpc_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "result": 5, "id": 1},
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "add",
            {"a": 2, "b": 3},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "POST"
    assert structured["status"] == "ok"


@pytest.mark.asyncio
async def test_jsonrpc_all_methods_registered(tmp_path: Path) -> None:
    """Verify all discovered JSON-RPC methods are registered as MCP tools."""
    ir = _extract_jsonrpc_ir("openrpc_calculator.json")
    ir_path = tmp_path / "jsonrpc_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, request=request)

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        tools = app.state.runtime_state.mcp_server._tool_manager._tools
        expected = {"add", "subtract", "get_history", "delete_history"}
        assert expected.issubset(set(tools.keys())), (
            f"Missing tools: {expected - set(tools.keys())}"
        )
    finally:
        await upstream_client.aclose()
