"""HTTP mock service used by the live LLM-enabled proof track."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
GRAPHQL_INTROSPECTION_PATH = FIXTURES_ROOT / "graphql_schemas" / "catalog_introspection.json"
SOAP_WSDL_PATH = FIXTURES_ROOT / "wsdl" / "order_service.wsdl"
_SOAP_ADDRESS_PATTERN = re.compile(r'location="[^"]+"')
_SOAP_NS = "http://example.com/orders/wsdl"

_GRAPHQL_INTROSPECTION = json.loads(GRAPHQL_INTROSPECTION_PATH.read_text(encoding="utf-8"))
_SOAP_WSDL_TEMPLATE = SOAP_WSDL_PATH.read_text(encoding="utf-8")

app = FastAPI(title="Tool Compiler LLM Proof HTTP Mock", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route("/rest/catalog", methods=["GET", "OPTIONS"])
@app.api_route("/rest/catalog/", methods=["GET", "OPTIONS"])
async def rest_catalog_root(request: Request) -> Response:
    if request.method == "OPTIONS":
        return _allow_response("GET, OPTIONS")

    html = """
    <html>
      <body>
        <a href="/rest/catalog/items/{item_id}?view=detail">Item Detail</a>
      </body>
    </html>
    """.strip()
    return HTMLResponse(html)


@app.api_route("/rest/catalog/items/{item_id}", methods=["GET", "OPTIONS"])
async def rest_item_detail(item_id: str, request: Request) -> Response:
    if request.method == "OPTIONS":
        return _allow_response("GET, OPTIONS")

    view = request.query_params.get("view", "detail")
    return JSONResponse(
        {
            "item_id": item_id,
            "view": view,
            "name": "Puzzle Box",
            "status": "active",
            "category": "games",
        }
    )


@app.post("/graphql")
async def graphql_endpoint(request: Request) -> Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"errors": [{"message": "GraphQL request must be an object."}]})

    query = str(payload.get("query", ""))
    operation_name = str(payload.get("operationName", "") or "")
    variables = payload.get("variables")
    if not isinstance(variables, dict):
        variables = {}

    if "IntrospectionQuery" in query:
        return JSONResponse(_GRAPHQL_INTROSPECTION)

    if operation_name == "searchProducts":
        term = str(variables.get("term", "sample"))
        normalized_term = term.strip().lower() or "sample"
        return JSONResponse(
            {
                "data": {
                    "searchProducts": [
                        {
                            "id": f"sku-{normalized_term}",
                            "name": f"{normalized_term.title()} Starter Kit",
                        }
                    ]
                }
            }
        )

    if operation_name == "adjustInventory":
        sku = str(variables.get("sku", "sku-1"))
        delta = int(variables.get("delta", 0) or 0)
        return JSONResponse(
            {
                "data": {
                    "adjustInventory": {
                        "operation_id": f"adj-{sku}-{delta}",
                    }
                }
            }
        )

    return JSONResponse(
        {"errors": [{"message": f"Unsupported GraphQL operation {operation_name or 'unknown'}."}]}
    )


@app.get("/soap/order-service.wsdl")
async def soap_wsdl(request: Request) -> Response:
    endpoint = f"{str(request.base_url).rstrip('/')}/soap/order-service"
    return PlainTextResponse(
        _rewrite_wsdl_endpoint(_SOAP_WSDL_TEMPLATE, endpoint),
        media_type="text/xml",
    )


@app.post("/soap/order-service")
async def soap_order_service(request: Request) -> Response:
    body = await request.body()
    soap_action = request.headers.get("SOAPAction", "").strip('"')

    if soap_action.endswith("/GetOrderStatus") or b"GetOrderStatusRequest" in body:
        return Response(
            content=_soap_success(
                "GetOrderStatusResponse",
                {
                    "status": "SHIPPED",
                    "estimatedShipDate": "2026-03-26T10:00:00Z",
                },
            ),
            media_type="text/xml",
        )

    if soap_action.endswith("/SubmitOrder") or b"SubmitOrderRequest" in body:
        return Response(
            content=_soap_success(
                "SubmitOrderResponse",
                {"confirmationId": "CONF-12345"},
            ),
            media_type="text/xml",
        )

    return Response(
        content=_soap_fault("Unsupported SOAP action."),
        status_code=500,
        media_type="text/xml",
    )


def _allow_response(allow_header: str) -> Response:
    return Response(status_code=200, headers={"Allow": allow_header})


def _rewrite_wsdl_endpoint(content: str, endpoint: str) -> str:
    return _SOAP_ADDRESS_PATTERN.sub(f'location="{endpoint}"', content, count=1)


def _soap_success(response_element: str, payload: dict[str, Any]) -> str:
    body = "".join(f"<{key}>{value}</{key}>" for key, value in payload.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Body>"
        f'<tns:{response_element} xmlns:tns="{_SOAP_NS}">'
        f"{body}"
        f"</tns:{response_element}>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


def _soap_fault(message: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Body>"
        "<soapenv:Fault>"
        "<faultcode>soapenv:Client</faultcode>"
        f"<faultstring>{message}</faultstring>"
        "</soapenv:Fault>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )
