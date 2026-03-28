"""Tests for apps/proof_runner/http_mock.py — HTTP mock service endpoints."""

from __future__ import annotations

import httpx
import pytest

from apps.proof_runner.http_mock import (
    _SOAP_NS,
    _allow_response,
    _rewrite_wsdl_endpoint,
    _soap_fault,
    _soap_success,
    app,
)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class TestHealthz:
    async def test_healthz_returns_ok(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestRestCatalog:
    async def test_get_catalog_root(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/rest/catalog")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "/rest/catalog/items/{item_id}" in resp.text

    async def test_get_catalog_root_trailing_slash(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/rest/catalog/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_options_catalog_root(self, client: httpx.AsyncClient) -> None:
        resp = await client.options("/rest/catalog")
        assert resp.status_code == 200
        assert "GET" in resp.headers["allow"]
        assert "OPTIONS" in resp.headers["allow"]

    async def test_get_item_detail(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/rest/catalog/items/sku-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["item_id"] == "sku-123"
        assert data["view"] == "detail"
        assert data["name"] == "Puzzle Box"

    async def test_get_item_detail_with_view(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/rest/catalog/items/sku-456?view=summary")
        assert resp.status_code == 200
        assert resp.json()["view"] == "summary"

    async def test_options_item_detail(self, client: httpx.AsyncClient) -> None:
        resp = await client.options("/rest/catalog/items/sku-123")
        assert resp.status_code == 200
        assert "GET" in resp.headers["allow"]


class TestGraphQL:
    async def test_introspection_query(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            json={"query": "query IntrospectionQuery { __schema { types { name } } }"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data or "__schema" in str(data)

    async def test_search_products(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            json={
                "query": (
                    "query searchProducts($term: String) "
                    "{ searchProducts(term: $term) { id name } }"
                ),
                "operationName": "searchProducts",
                "variables": {"term": "puzzle"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        products = data["data"]["searchProducts"]
        assert len(products) == 1
        assert products[0]["id"] == "sku-puzzle"
        assert "Puzzle" in products[0]["name"]

    async def test_search_products_empty_term(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            json={
                "query": "query",
                "operationName": "searchProducts",
                "variables": {"term": "  "},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["searchProducts"][0]["id"] == "sku-sample"

    async def test_adjust_inventory(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            json={
                "query": (
                    "mutation adjustInventory($sku: String!, $delta: Int!) "
                    "{ adjustInventory(sku: $sku, delta: $delta) { operation_id } }"
                ),
                "operationName": "adjustInventory",
                "variables": {"sku": "sku-100", "delta": 5},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["adjustInventory"]["operation_id"] == "adj-sku-100-5"

    async def test_adjust_inventory_defaults(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            json={
                "query": "mutation",
                "operationName": "adjustInventory",
                "variables": {},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["adjustInventory"]["operation_id"] == "adj-sku-1-0"

    async def test_unsupported_operation(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            json={
                "query": "query getUser { user { id } }",
                "operationName": "getUser",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "errors" in data
        assert "getUser" in data["errors"][0]["message"]

    async def test_unsupported_operation_no_name(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            json={"query": "query { user { id } }"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "errors" in data
        assert "unknown" in data["errors"][0]["message"]

    async def test_non_dict_payload(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            content=b'"just a string"',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "errors" in data
        assert "must be an object" in data["errors"][0]["message"]

    async def test_variables_not_dict(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/graphql",
            json={
                "query": "query searchProducts",
                "operationName": "searchProducts",
                "variables": "not-a-dict",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["searchProducts"][0]["id"] == "sku-sample"


class TestSOAP:
    async def test_get_wsdl(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/soap/order-service.wsdl")
        assert resp.status_code == 200
        assert "text/xml" in resp.headers["content-type"]
        assert "location=" in resp.text
        assert "testserver" in resp.text

    async def test_get_order_status_by_action(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/soap/order-service",
            content=b"<Envelope><Body><GetOrderStatusRequest/></Body></Envelope>",
            headers={"SOAPAction": '"http://example.com/orders/GetOrderStatus"'},
        )
        assert resp.status_code == 200
        assert "text/xml" in resp.headers["content-type"]
        assert "GetOrderStatusResponse" in resp.text
        assert "SHIPPED" in resp.text

    async def test_get_order_status_by_body(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/soap/order-service",
            content=b"<Envelope><Body><GetOrderStatusRequest><orderId>1</orderId></GetOrderStatusRequest></Body></Envelope>",
            headers={"SOAPAction": '""'},
        )
        assert resp.status_code == 200
        assert "GetOrderStatusResponse" in resp.text

    async def test_submit_order_by_action(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/soap/order-service",
            content=b"<Envelope><Body></Body></Envelope>",
            headers={"SOAPAction": '"http://example.com/orders/SubmitOrder"'},
        )
        assert resp.status_code == 200
        assert "SubmitOrderResponse" in resp.text
        assert "CONF-12345" in resp.text

    async def test_submit_order_by_body(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/soap/order-service",
            content=b"<Envelope><Body><SubmitOrderRequest/></Body></Envelope>",
        )
        assert resp.status_code == 200
        assert "SubmitOrderResponse" in resp.text

    async def test_unsupported_soap_action(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/soap/order-service",
            content=b"<Envelope><Body><UnknownRequest/></Body></Envelope>",
            headers={"SOAPAction": '"http://example.com/orders/Unknown"'},
        )
        assert resp.status_code == 500
        assert "text/xml" in resp.headers["content-type"]
        assert "Fault" in resp.text
        assert "Unsupported SOAP action" in resp.text


class TestHelpers:
    def test_allow_response(self) -> None:
        resp = _allow_response("GET, POST, OPTIONS")
        assert resp.status_code == 200
        assert resp.headers["allow"] == "GET, POST, OPTIONS"

    def test_rewrite_wsdl_endpoint(self) -> None:
        content = '<soap:address location="http://old.example.com/svc"/>'
        result = _rewrite_wsdl_endpoint(content, "http://new.example.com/svc")
        assert 'location="http://new.example.com/svc"' in result
        assert "old.example.com" not in result

    def test_rewrite_wsdl_endpoint_no_match(self) -> None:
        content = "<wsdl:service>no address</wsdl:service>"
        result = _rewrite_wsdl_endpoint(content, "http://new.com")
        assert result == content

    def test_soap_success(self) -> None:
        result = _soap_success("TestResponse", {"key1": "val1", "key2": "val2"})
        assert "<?xml" in result
        assert "tns:TestResponse" in result
        assert f'xmlns:tns="{_SOAP_NS}"' in result
        assert "<key1>val1</key1>" in result
        assert "<key2>val2</key2>" in result

    def test_soap_fault(self) -> None:
        result = _soap_fault("Something went wrong")
        assert "<?xml" in result
        assert "soapenv:Fault" in result
        assert "Something went wrong" in result
        assert "soapenv:Client" in result
