"""Integration tests for OData v4 runtime proxy behavior."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from apps.mcp_runtime import create_app
from libs.extractors.base import SourceConfig
from libs.extractors.odata import ODataExtractor
from libs.ir.models import ServiceIR
from libs.ir.schema import serialize_ir

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "odata_metadata"


def _extract_odata_ir(fixture_name: str) -> ServiceIR:
    """Extract ServiceIR from an OData fixture."""
    fixture_path = FIXTURES_DIR / fixture_name
    source = SourceConfig(file_path=str(fixture_path))
    return ODataExtractor().extract(source)


@pytest.mark.asyncio
async def test_odata_list_operation_passes_query_params(tmp_path: Path) -> None:
    """Full path: OData $metadata → extract → register → call list → verify $filter/$select."""
    ir = _extract_odata_ir("simple_entity.xml")
    ir_path = tmp_path / "odata_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"value": [{"Id": 1, "Name": "Widget", "Price": 9.99}]},
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        # MCP strips $ prefix from param names; proxy restores them in the upstream request
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "list_products",
            {"filter": "Price gt 5", "select": "Id,Name", "top": 10},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "GET"
    params: dict[str, str] = captured["params"]  # type: ignore[assignment]
    assert params.get("$filter") == "Price gt 5"
    assert params.get("$select") == "Id,Name"
    assert params.get("$top") == "10"
    assert structured["status"] == "ok"


@pytest.mark.asyncio
async def test_odata_get_by_key_constructs_correct_path(tmp_path: Path) -> None:
    """Verify GET by key uses OData key syntax in path — /Products({Id})."""
    ir = _extract_odata_ir("simple_entity.xml")
    ir_path = tmp_path / "odata_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"Id": 1, "Name": "Widget", "Price": 9.99},
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "get_products_by_key",
            {"Id": 1},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "GET"
    assert "1" in str(captured["url"])
    assert structured["status"] == "ok"


@pytest.mark.asyncio
async def test_odata_create_sends_post_with_entity_body(tmp_path: Path) -> None:
    """Verify create operation sends POST with entity properties as body."""
    ir = _extract_odata_ir("simple_entity.xml")
    ir_path = tmp_path / "odata_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={"Id": 42, "Name": "New Product", "Price": 19.99},
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "create_products",
            {"Name": "New Product", "Price": 19.99, "Category": "Gadgets"},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "POST"
    body: dict[str, object] = captured["body"]  # type: ignore[assignment]
    assert body["Name"] == "New Product"
    assert body["Price"] == 19.99
    assert body["Category"] == "Gadgets"
    assert structured["status"] == "ok"


@pytest.mark.asyncio
async def test_odata_collection_response_unwrapped(tmp_path: Path) -> None:
    """Verify OData collection response ``value`` array is unwrapped."""
    ir = _extract_odata_ir("simple_entity.xml")
    ir_path = tmp_path / "odata_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {"Id": 1, "Name": "A"},
                    {"Id": 2, "Name": "B"},
                ],
                "@odata.count": 42,
                "@odata.nextLink": "http://example.com/Products?$skip=2",
            },
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "list_products",
            {"top": 2},
        )
    finally:
        await upstream_client.aclose()

    assert structured["status"] == "ok"
    result = structured["result"]
    assert isinstance(result, dict)
    assert result["items"] == [{"Id": 1, "Name": "A"}, {"Id": 2, "Name": "B"}]
    assert result["total_count"] == 42
    assert result["next_link"] == "http://example.com/Products?$skip=2"


@pytest.mark.asyncio
async def test_odata_error_response_raises_tool_error(tmp_path: Path) -> None:
    """Verify OData JSON error responses are detected and raised as ToolError."""
    ir = _extract_odata_ir("simple_entity.xml")
    ir_path = tmp_path / "odata_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": {
                    "code": "InvalidFilter",
                    "message": "The filter expression is not valid.",
                }
            },
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        with pytest.raises(Exception, match="OData error.*InvalidFilter"):
            await app.state.runtime_state.mcp_server.call_tool(
                "list_products",
                {"filter": "bad expression"},
            )
    finally:
        await upstream_client.aclose()
