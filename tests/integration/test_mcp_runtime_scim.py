"""Integration tests for SCIM 2.0 runtime proxy behavior."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from apps.mcp_runtime import create_app
from libs.extractors.base import SourceConfig
from libs.extractors.scim import SCIMExtractor
from libs.ir.schema import serialize_ir

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scim_schemas"


def _extract_scim_ir(fixture_name: str):
    """Extract ServiceIR from a SCIM fixture."""
    fixture_path = FIXTURES_DIR / fixture_name
    source = SourceConfig(file_content=fixture_path.read_text(encoding="utf-8"))
    return SCIMExtractor().extract(source)


@pytest.mark.asyncio
async def test_scim_list_users_passes_filter_param(tmp_path: Path) -> None:
    """Call list_users, verify SCIM filter param is passed to upstream."""
    ir = _extract_scim_ir("user_group.json")
    ir_path = tmp_path / "scim_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": 1,
                "Resources": [
                    {
                        "id": "u-001",
                        "userName": "jdoe",
                        "name": {"givenName": "John", "familyName": "Doe"},
                    }
                ],
            },
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "list_users",
            {"filter": 'userName eq "jdoe"', "count": 10},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "GET"
    assert captured["params"].get("filter") == 'userName eq "jdoe"'
    assert captured["params"].get("count") == "10"
    assert structured["status"] == "ok"


@pytest.mark.asyncio
async def test_scim_create_user_sends_post(tmp_path: Path) -> None:
    """Verify create_user sends POST with user attributes."""
    ir = _extract_scim_ir("user_group.json")
    ir_path = tmp_path / "scim_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={
                "id": "u-002",
                "userName": "asmith",
                "name": {"givenName": "Alice", "familyName": "Smith"},
                "active": True,
            },
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "create_user",
            {"userName": "asmith", "active": True},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "POST"
    assert captured["body"]["userName"] == "asmith"
    assert structured["status"] == "ok"


@pytest.mark.asyncio
async def test_scim_get_user_by_id(tmp_path: Path) -> None:
    """Verify GET /Users/{id} path is constructed correctly."""
    ir = _extract_scim_ir("user_group.json")
    ir_path = tmp_path / "scim_ir.json"
    ir_path.write_text(serialize_ir(ir), encoding="utf-8")

    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "id": "u-001",
                "userName": "jdoe",
                "name": {"givenName": "John", "familyName": "Doe"},
            },
            request=request,
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(service_ir_path=str(ir_path), upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "get_user",
            {"id": "u-001"},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "GET"
    assert "u-001" in captured["url"]
    assert structured["status"] == "ok"
