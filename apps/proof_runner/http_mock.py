"""HTTP mock service used by the live LLM-enabled proof track."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from defusedxml.ElementTree import fromstring as _defused_fromstring
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
GRAPHQL_INTROSPECTION_PATH = FIXTURES_ROOT / "graphql_schemas" / "catalog_introspection.json"
SOAP_WSDL_PATH = FIXTURES_ROOT / "wsdl" / "order_service.wsdl"
_SOAP_ADDRESS_PATTERN = re.compile(r'location="[^"]+"')
_SOAP_NS = "http://example.com/orders/wsdl"
_GRAPHQL_DOCUMENT_START = re.compile(r"^\s*(query|mutation|\{)")
_GRAPHQL_SUPPORTED_OPERATIONS = ("searchProducts", "adjustInventory")

_GRAPHQL_INTROSPECTION = json.loads(GRAPHQL_INTROSPECTION_PATH.read_text(encoding="utf-8"))
_SOAP_WSDL_TEMPLATE = SOAP_WSDL_PATH.read_text(encoding="utf-8")

app = FastAPI(title="service2mcp LLM Proof HTTP Mock", version="0.1.0")


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
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _graphql_error("Malformed JSON request body.")
    if not isinstance(payload, dict):
        return _graphql_error("GraphQL request must be an object.")

    query = str(payload.get("query", ""))
    operation_name = str(payload.get("operationName", "") or "")
    variables = payload.get("variables")
    if not isinstance(variables, dict):
        variables = {}

    operation_name, query_error = _resolve_graphql_operation(query, operation_name)
    if query_error is not None:
        return _graphql_error(query_error)

    if operation_name == "IntrospectionQuery":
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
        try:
            delta = int(variables.get("delta", 0) or 0)
        except (TypeError, ValueError):
            return _graphql_error("adjustInventory.delta must be numeric.")
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
    parsed_body = _parse_xml_body(body)
    if parsed_body is None:
        return Response(
            content=_soap_fault("Malformed SOAP XML request."),
            status_code=500,
            media_type="text/xml",
        )
    body_operation = _soap_body_operation(parsed_body)

    if soap_action.endswith("/GetOrderStatus") or body_operation == "GetOrderStatusRequest":
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

    if soap_action.endswith("/SubmitOrder") or body_operation == "SubmitOrderRequest":
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


def _graphql_error(message: str) -> JSONResponse:
    return JSONResponse({"errors": [{"message": message}]})


def _resolve_graphql_operation(
    query: str,
    operation_name: str,
) -> tuple[str, str | None]:
    stripped_query = query.strip()
    if not stripped_query:
        return "", "GraphQL query must be a non-empty string."
    if "__schema" in query:
        return "IntrospectionQuery", None
    if _GRAPHQL_DOCUMENT_START.match(query) is None or "{" not in query or "}" not in query:
        return "", "Invalid GraphQL query."

    matched_supported_operations = [
        candidate for candidate in _GRAPHQL_SUPPORTED_OPERATIONS if candidate in query
    ]
    if operation_name:
        if operation_name in _GRAPHQL_SUPPORTED_OPERATIONS:
            if operation_name not in matched_supported_operations:
                return "", "GraphQL query does not match operationName."
            return operation_name, None
        return operation_name, None

    if len(matched_supported_operations) == 1:
        return matched_supported_operations[0], None
    if not matched_supported_operations:
        return "", None
    return "", "GraphQL operationName is required for ambiguous queries."


def _rewrite_wsdl_endpoint(content: str, endpoint: str) -> str:
    return _SOAP_ADDRESS_PATTERN.sub(f'location="{endpoint}"', content, count=1)


def _parse_xml_body(body: bytes) -> ElementTree.Element | None:
    try:
        result: ElementTree.Element = _defused_fromstring(body)
        return result
    except (ElementTree.ParseError, Exception):
        return None


def _soap_body_operation(root: ElementTree.Element) -> str | None:
    for element in root.iter():
        tag = element.tag
        if not isinstance(tag, str):
            continue
        local_name = tag.rsplit("}", 1)[-1]
        if local_name in {"GetOrderStatusRequest", "SubmitOrderRequest"}:
            return local_name
    return None


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
