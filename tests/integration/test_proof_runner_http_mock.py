"""Integration tests for the live-proof HTTP mock service."""

from __future__ import annotations

import httpx
import pytest

from apps.proof_runner.http_mock import app


@pytest.mark.asyncio
async def test_rest_catalog_mock_exposes_discovery_page_and_item_detail() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        discovery_response = await client.get("/rest/catalog")
        detail_response = await client.get("/rest/catalog/items/sku-123", params={"view": "detail"})

    assert discovery_response.status_code == 200
    assert "items/{item_id}?view=detail" in discovery_response.text
    assert detail_response.status_code == 200
    assert detail_response.json()["item_id"] == "sku-123"


@pytest.mark.asyncio
async def test_graphql_mock_handles_supported_operations() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/graphql",
            json={
                "operationName": "searchProducts",
                "query": "query searchProducts { searchProducts { id name } }",
                "variables": {"term": "puzzle"},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["searchProducts"][0]["id"] == "sku-puzzle"


@pytest.mark.asyncio
async def test_soap_mock_returns_xml_response_for_known_action() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/soap/order-service",
            headers={"SOAPAction": '"http://example.com/orders/GetOrderStatus"'},
            content="<Envelope><Body><GetOrderStatusRequest/></Body></Envelope>",
        )

    assert response.status_code == 200
    assert "GetOrderStatusResponse" in response.text
    assert "SHIPPED" in response.text
