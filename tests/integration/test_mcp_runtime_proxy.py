"""Integration tests for runtime upstream proxy behavior."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest
import websockets
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime import create_app
from apps.mcp_runtime.observability import RuntimeObservability
from apps.mcp_runtime.proxy import RuntimeProxy
from libs.extractors.base import SourceConfig
from libs.extractors.rest import (
    DiscoveredEndpoint,
    EndpointClassification,
    EndpointClassifier,
    RESTExtractor,
)
from libs.extractors.soap import SOAPWSDLExtractor
from libs.ir.models import (
    AsyncJobConfig,
    AsyncStatusUrlSource,
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationConfig,
    GraphQLOperationType,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    MTLSConfig,
    OAuth2ClientCredentialsConfig,
    Operation,
    Param,
    RequestBodyMode,
    RequestSigningConfig,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.ir.schema import serialize_ir

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ir"
PROXY_IR_PATH = FIXTURES_DIR / "service_ir_proxy.json"
WSDL_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "wsdl" / "order_service.wsdl"
)


def _build_proxy_ir(
    *,
    auth: AuthConfig,
    operations: list[Operation] | None = None,
    event_descriptors: list[EventDescriptor] | None = None,
) -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol="openapi",
        service_name="Advanced Auth API",
        service_description="Proxy auth test service",
        base_url="https://api.example.test",
        auth=auth,
        operations=operations
        or [
            Operation(
                id="getAccount",
                name="Get Account",
                description="Fetch one account.",
                method="GET",
                path="/accounts/{account_id}",
                params=[Param(name="account_id", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        event_descriptors=event_descriptors or [],
    )


def _build_graphql_ir() -> ServiceIR:
    return ServiceIR(
        source_hash="b" * 64,
        protocol="graphql",
        service_name="catalog-graphql",
        service_description="GraphQL runtime fixture",
        base_url="https://catalog.example.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="searchProducts",
                name="Search Products",
                description="Search products by term.",
                method="POST",
                path="/graphql",
                params=[
                    Param(name="term", type="string", required=True),
                    Param(name="limit", type="integer", required=False, default=10),
                ],
                graphql=GraphQLOperationConfig(
                    operation_type=GraphQLOperationType.query,
                    operation_name="searchProducts",
                    document=(
                        "query searchProducts($term: String!, $limit: Int) {\n"
                        "  searchProducts(term: $term, limit: $limit) { id name }\n"
                        "}"
                    ),
                    variable_names=["term", "limit"],
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
    )


class RelativeRESTClassifier(EndpointClassifier):
    """Classifier double that returns a path relative to the discovery entrypoint."""

    def classify(
        self,
        *,
        base_url: str,
        endpoints: list[DiscoveredEndpoint],
    ) -> list[EndpointClassification]:
        assert base_url == "https://catalog.example.test/catalog"
        assert endpoints
        return [
            EndpointClassification(
                path="/products/{product_id}?view=detail",
                method="GET",
                name="Get Product",
                description="Fetch a product from the catalog subtree.",
                confidence=0.93,
                tags=("products", "read"),
            )
        ]


def _write_service_ir(tmp_path: Path, name: str, service_ir: ServiceIR) -> Path:
    output_path = tmp_path / name
    output_path.write_text(serialize_ir(service_ir), encoding="utf-8")
    return output_path


def _build_soap_ir() -> ServiceIR:
    return SOAPWSDLExtractor().extract(SourceConfig(file_path=str(WSDL_FIXTURE_PATH)))


def _xml_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


@pytest.mark.asyncio
async def test_runtime_tool_call_proxies_get_request_and_filters_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["verbose"] = request.url.params.get("verbose")
        return httpx.Response(
            200,
            json={"id": "acct-1", "name": "Primary", "secret": "hidden"},
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "getAccount",
            {"account_id": "acct-1", "verbose": True},
        )
    finally:
        await upstream_client.aclose()

    assert captured == {
        "method": "GET",
        "url": "https://api.example.test/accounts/acct-1?verbose=true",
        "auth": "Bearer runtime-token",
        "verbose": "true",
    }
    assert structured["status"] == "ok"
    assert structured["result"] == {"id": "acct-1", "name": "Primary"}
    assert structured["truncated"] is False


@pytest.mark.asyncio
async def test_runtime_tool_call_sends_json_body_and_truncates_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={"note": "x" * 200, "status": "created"},
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "createNote",
            {
                "account_id": "acct-1",
                "payload": {"title": "Hello", "body": "Long text"},
            },
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.example.test/accounts/acct-1/notes"
    assert captured["auth"] == "Bearer runtime-token"
    assert captured["body"] == '{"title":"Hello","body":"Long text"}'
    assert structured["status"] == "ok"
    assert structured["truncated"] is True
    assert structured["result"]["truncated"] is True
    assert structured["result"]["original_type"] == "dict"


@pytest.mark.asyncio
async def test_runtime_tool_call_validation_error_happens_before_upstream_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        with pytest.raises(ToolError, match="validation error"):
            await app.state.runtime_state.mcp_server.call_tool("getAccount", {})
    finally:
        await upstream_client.aclose()

    assert call_count == 0


@pytest.mark.asyncio
async def test_runtime_tool_call_builds_soap_envelope_and_parses_response(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8")
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["soap_action"] = request.headers.get("SOAPAction")
        captured["content_type"] = request.headers.get("Content-Type")
        captured["body"] = body
        return httpx.Response(
            200,
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
                'xmlns:tns="http://example.com/orders/wsdl">'
                "<soap:Body>"
                "<tns:GetOrderStatusResponse>"
                "<tns:status>ready</tns:status>"
                "<tns:estimatedShipDate>2026-03-25T10:15:00Z</tns:estimatedShipDate>"
                "</tns:GetOrderStatusResponse>"
                "</soap:Body>"
                "</soap:Envelope>"
            ),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            request=request,
        )

    service_ir_path = _write_service_ir(tmp_path, "soap_runtime_ir.json", _build_soap_ir())
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "GetOrderStatus",
            {"orderId": "ord-123", "includeHistory": True},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "POST"
    assert captured["url"] == "https://orders.example.com/soap/order-service"
    assert captured["soap_action"] == '"http://example.com/orders/GetOrderStatus"'
    assert str(captured["content_type"]).startswith("text/xml")
    envelope = ET.fromstring(str(captured["body"]))
    body = next(element for element in envelope.iter() if _xml_local_name(element.tag) == "Body")
    request_payload = next(iter(body))
    assert _xml_local_name(request_payload.tag) == "GetOrderStatusRequest"
    fields = {_xml_local_name(child.tag): child.text for child in request_payload}
    assert fields == {"orderId": "ord-123", "includeHistory": "true"}
    assert structured["status"] == "ok"
    assert structured["result"] == {
        "status": "ready",
        "estimatedShipDate": "2026-03-25T10:15:00Z",
    }
    assert structured["truncated"] is False


@pytest.mark.asyncio
async def test_runtime_tool_call_surfaces_soap_faults_as_tool_errors(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                "<soap:Body>"
                "<soap:Fault>"
                "<faultcode>soap:Client</faultcode>"
                "<faultstring>Unknown orderId</faultstring>"
                "</soap:Fault>"
                "</soap:Body>"
                "</soap:Envelope>"
            ),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            request=request,
        )

    service_ir_path = _write_service_ir(
        tmp_path,
        "soap_fault_runtime_ir.json",
        _build_soap_ir(),
    )
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        with pytest.raises(ToolError, match="Unknown orderId"):
            await app.state.runtime_state.mcp_server.call_tool(
                "GetOrderStatus",
                {"orderId": "missing-order"},
            )
    finally:
        await upstream_client.aclose()


@pytest.mark.asyncio
async def test_runtime_tool_call_supports_rest_discovery_base_paths(
    tmp_path: Path,
) -> None:
    def discovery_handler(request: httpx.Request) -> httpx.Response:
        routes: dict[tuple[str, str], httpx.Response] = {
            (
                "GET",
                "https://catalog.example.test/catalog",
            ): httpx.Response(
                200,
                text=(
                    "<html><body>"
                    '<a href="/catalog/products/{product_id}?view=detail">Product</a>'
                    "</body></html>"
                ),
                headers={"content-type": "text/html"},
                request=request,
            ),
            (
                "GET",
                "https://catalog.example.test/catalog/products/%7Bproduct_id%7D?view=detail",
            ): httpx.Response(200, json={"ok": True}, request=request),
            (
                "OPTIONS",
                "https://catalog.example.test/catalog/products/%7Bproduct_id%7D?view=detail",
            ): httpx.Response(200, headers={"allow": "GET"}, request=request),
        }
        return routes.get((request.method, str(request.url)), httpx.Response(404, request=request))

    discovery_client = httpx.Client(
        transport=httpx.MockTransport(discovery_handler),
        follow_redirects=True,
    )
    extractor = RESTExtractor(
        client=discovery_client,
        classifier=RelativeRESTClassifier(),
    )
    service_ir = extractor.extract(SourceConfig(url="https://catalog.example.test/catalog"))
    extractor.close()

    captured: dict[str, object] = {}

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"id": "sku-1", "view": request.url.params.get("view")},
            request=request,
        )

    service_ir_path = _write_service_ir(tmp_path, "rest_runtime_ir.json", service_ir)
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "get_products_product_id",
            {"product_id": "sku-1", "view": "detail"},
        )
    finally:
        await upstream_client.aclose()

    assert service_ir.base_url == "https://catalog.example.test/catalog"
    assert captured == {
        "method": "GET",
        "url": "https://catalog.example.test/catalog/products/sku-1?view=detail",
        "query": {"view": "detail"},
    }
    assert structured["status"] == "ok"
    assert structured["result"] == {"id": "sku-1", "view": "detail"}


@pytest.mark.asyncio
async def test_runtime_tool_call_serializes_graphql_query_and_unwraps_data(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.read().decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "data": {
                    "searchProducts": [
                        {"id": "sku-1", "name": "Widget"},
                    ]
                }
            },
            request=request,
        )

    service_ir_path = _write_service_ir(tmp_path, "graphql_runtime_ir.json", _build_graphql_ir())
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "searchProducts",
            {"term": "widget", "limit": 5},
        )
    finally:
        await upstream_client.aclose()

    assert captured["method"] == "POST"
    assert captured["url"] == "https://catalog.example.test/graphql"
    assert captured["body"] == {
        "query": (
            "query searchProducts($term: String!, $limit: Int) {\n"
            "  searchProducts(term: $term, limit: $limit) { id name }\n"
            "}"
        ),
        "operationName": "searchProducts",
        "variables": {"term": "widget", "limit": 5},
    }
    assert structured["status"] == "ok"
    assert structured["result"] == [{"id": "sku-1", "name": "Widget"}]
    assert structured["truncated"] is False


@pytest.mark.asyncio
async def test_runtime_tool_call_raises_for_graphql_errors_in_200_response(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": [{"message": "Unknown catalog"}]},
            request=request,
        )

    service_ir_path = _write_service_ir(tmp_path, "graphql_runtime_ir.json", _build_graphql_ir())
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        with pytest.raises(ToolError, match="Unknown catalog"):
            await app.state.runtime_state.mcp_server.call_tool(
                "searchProducts",
                {"term": "widget"},
            )
    finally:
        await upstream_client.aclose()


@pytest.mark.asyncio
async def test_runtime_timeout_and_circuit_breaker_fast_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout_calls = 0

    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_calls
        timeout_calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    timeout_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))

    try:
        timeout_app = create_app(
            service_ir_path=PROXY_IR_PATH,
            upstream_client=timeout_client,
            proxy_timeout=0.01,
        )
        with pytest.raises(ToolError, match="Upstream timeout"):
            await timeout_app.state.runtime_state.mcp_server.call_tool(
                "getAccount",
                {"account_id": "acct-1"},
            )
    finally:
        await timeout_client.aclose()

    assert timeout_calls == 1

    failure_calls = 0

    async def failing_handler(request: httpx.Request) -> httpx.Response:
        nonlocal failure_calls
        failure_calls += 1
        return httpx.Response(503, json={"error": "unavailable"}, request=request)

    failure_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))

    try:
        breaker_app = create_app(
            service_ir_path=PROXY_IR_PATH,
            upstream_client=failure_client,
            failure_threshold=5,
        )
        for _ in range(5):
            with pytest.raises(ToolError, match="status 503"):
                await breaker_app.state.runtime_state.mcp_server.call_tool(
                    "getAccount",
                    {"account_id": "acct-1"},
                )

        with pytest.raises(ToolError, match="Circuit breaker is open"):
            await breaker_app.state.runtime_state.mcp_server.call_tool(
                "getAccount",
                {"account_id": "acct-1"},
            )
    finally:
        await failure_client.aclose()

    assert failure_calls == 5
    assert breaker_app.state.runtime_state.proxy is not None
    assert breaker_app.state.runtime_state.proxy.breakers["getAccount"].is_open is True


@pytest.mark.asyncio
async def test_runtime_proxy_fetches_oauth2_client_credentials_token_and_signs_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            captured["token_method"] = request.method
            captured["token_auth"] = request.headers.get("Authorization")
            captured["token_body"] = request.read().decode("utf-8")
            return httpx.Response(
                200,
                json={"access_token": "oauth-access-token", "token_type": "Bearer"},
                request=request,
            )

        captured["upstream_auth"] = request.headers.get("Authorization")
        captured["signature"] = request.headers.get("X-Signature")
        captured["timestamp"] = request.headers.get("X-Timestamp")
        captured["key_id"] = request.headers.get("X-Key-Id")
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setenv("OAUTH_CLIENT_ID", "inventory-client")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "inventory-secret")
    monkeypatch.setenv("SIGNING_SECRET", "signing-secret-value")
    monkeypatch.setattr("apps.mcp_runtime.proxy.time.time", lambda: 1700000000.0)

    service_ir = _build_proxy_ir(
        auth=AuthConfig(
            type=AuthType.oauth2,
            oauth2=OAuth2ClientCredentialsConfig(
                token_url="https://auth.example.test/oauth/token",
                client_id_ref="OAUTH_CLIENT_ID",
                client_secret_ref="OAUTH_CLIENT_SECRET",
                scopes=["accounts.read", "accounts.write"],
                audience="accounts-api",
            ),
            request_signing=RequestSigningConfig(
                secret_ref="SIGNING_SECRET",
                key_id="runtime-key",
            ),
        )
    )
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy = RuntimeProxy(
        service_ir,
        observability=RuntimeObservability(),
        client=upstream_client,
    )
    operation = service_ir.operations[0]

    try:
        result = await proxy.invoke(operation, {"account_id": "acct-1"})
    finally:
        await proxy.aclose()

    expected_basic = base64.b64encode(b"inventory-client:inventory-secret").decode("ascii")
    assert captured["token_method"] == "POST"
    assert captured["token_auth"] == f"Basic {expected_basic}"
    assert captured["token_body"] == (
        "grant_type=client_credentials&scope=accounts.read+accounts.write&audience=accounts-api"
    )
    assert captured["upstream_auth"] == "Bearer oauth-access-token"
    assert captured["timestamp"] == "1700000000"
    expected_signature = hmac.new(
        b"signing-secret-value",
        b"GET\n/accounts/acct-1\n\n\n1700000000",
        hashlib.sha256,
    ).hexdigest()
    assert captured["signature"] == expected_signature
    assert captured["key_id"] == "runtime-key"
    assert result["status"] == "ok"
    assert result["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_runtime_proxy_builds_mtls_client_with_secret_backed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

        async def aclose(self) -> None:
            return None

    monkeypatch.setenv("MTLS_CERT_REF", "/tmp/client-cert.pem")
    monkeypatch.setenv("MTLS_KEY_REF", "/tmp/client-key.pem")
    monkeypatch.setenv("MTLS_CA_REF", "/tmp/ca.pem")
    monkeypatch.setattr("apps.mcp_runtime.proxy.httpx.AsyncClient", FakeAsyncClient)

    service_ir = _build_proxy_ir(
        auth=AuthConfig(
            type=AuthType.none,
            mtls=MTLSConfig(
                cert_ref="MTLS_CERT_REF",
                key_ref="MTLS_KEY_REF",
                ca_ref="MTLS_CA_REF",
            ),
        )
    )
    proxy = RuntimeProxy(service_ir, observability=RuntimeObservability())

    client = proxy._get_client()
    await proxy.aclose()

    assert isinstance(client, FakeAsyncClient)
    assert captured_kwargs["cert"] == ("/tmp/client-cert.pem", "/tmp/client-key.pem")
    assert captured_kwargs["verify"] == "/tmp/ca.pem"


@pytest.mark.asyncio
async def test_runtime_proxy_sends_multipart_form_and_file_payloads() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("Content-Type")
        captured["body"] = request.read().decode("utf-8", errors="ignore")
        return httpx.Response(200, json={"uploaded": True}, request=request)

    service_ir = _build_proxy_ir(
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="uploadInvoice",
                name="Upload Invoice",
                description="Upload an invoice PDF.",
                method="POST",
                path="/uploads",
                params=[Param(name="payload", type="object", required=True)],
                request_body_mode=RequestBodyMode.multipart,
                body_param_name="payload",
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=False,
                    external_side_effect=True,
                    idempotent=False,
                ),
                enabled=True,
            )
        ],
    )
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy = RuntimeProxy(
        service_ir,
        observability=RuntimeObservability(),
        client=upstream_client,
    )

    try:
        result = await proxy.invoke(
            service_ir.operations[0],
            {
                "payload": {
                    "form": {"account_id": "acct-1", "notify": True},
                    "files": {
                        "document": {
                            "filename": "invoice.pdf",
                            "content_base64": base64.b64encode(b"%PDF-1.4").decode("ascii"),
                            "content_type": "application/pdf",
                        }
                    },
                }
            },
        )
    finally:
        await proxy.aclose()

    assert "multipart/form-data" in str(captured["content_type"])
    assert 'name="account_id"' in str(captured["body"])
    assert "acct-1" in str(captured["body"])
    assert 'name="notify"' in str(captured["body"])
    assert "true" in str(captured["body"])
    assert 'filename="invoice.pdf"' in str(captured["body"])
    assert "%PDF-1.4" in str(captured["body"])
    assert result["status"] == "ok"
    assert result["result"] == {"uploaded": True}


@pytest.mark.asyncio
async def test_runtime_proxy_wraps_binary_responses_as_base64_payloads() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x00\x01tool-compiler",
            headers={"Content-Type": "application/octet-stream"},
            request=request,
        )

    service_ir = _build_proxy_ir(auth=AuthConfig(type=AuthType.none))
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy = RuntimeProxy(
        service_ir,
        observability=RuntimeObservability(),
        client=upstream_client,
    )

    try:
        result = await proxy.invoke(service_ir.operations[0], {"account_id": "acct-1"})
    finally:
        await proxy.aclose()

    assert result["status"] == "ok"
    assert result["truncated"] is False
    assert result["result"] == {
        "binary": True,
        "content_type": "application/octet-stream",
        "content_base64": base64.b64encode(b"\x00\x01tool-compiler").decode("ascii"),
        "size_bytes": 15,
    }


@pytest.mark.asyncio
async def test_runtime_proxy_sends_raw_binary_request_bodies() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("Content-Type")
        captured["body"] = request.read()
        return httpx.Response(200, json={"stored": True}, request=request)

    service_ir = _build_proxy_ir(
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="uploadBlob",
                name="Upload Blob",
                description="Upload a binary blob.",
                method="POST",
                path="/blobs",
                params=[Param(name="payload", type="object", required=True)],
                request_body_mode=RequestBodyMode.raw,
                body_param_name="payload",
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=False,
                    external_side_effect=True,
                    idempotent=False,
                ),
                enabled=True,
            )
        ],
    )
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy = RuntimeProxy(
        service_ir,
        observability=RuntimeObservability(),
        client=upstream_client,
    )

    try:
        result = await proxy.invoke(
            service_ir.operations[0],
            {
                "payload": {
                    "content_base64": base64.b64encode(b"\x00\xffblob-data").decode("ascii"),
                    "content_type": "application/octet-stream",
                }
            },
        )
    finally:
        await proxy.aclose()

    assert captured["content_type"] == "application/octet-stream"
    assert captured["body"] == b"\x00\xffblob-data"
    assert result["status"] == "ok"
    assert result["result"] == {"stored": True}


@pytest.mark.asyncio
async def test_runtime_proxy_polls_async_job_until_completion() -> None:
    captured_paths: list[str] = []
    poll_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        captured_paths.append(request.url.path)
        if request.url.path == "/exports":
            return httpx.Response(
                202,
                json={"job": {"status_url": "/jobs/job-1"}},
                request=request,
            )
        if request.url.path == "/jobs/job-1":
            poll_count += 1
            if poll_count == 1:
                return httpx.Response(
                    200,
                    json={"job": {"state": "running"}},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"job": {"state": "completed"}, "artifact_id": "artifact-1"},
                request=request,
            )
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    service_ir = _build_proxy_ir(
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="exportAccounts",
                name="Export Accounts",
                description="Start an export job and wait for completion.",
                method="POST",
                path="/exports",
                params=[Param(name="payload", type="object", required=True)],
                body_param_name="payload",
                async_job=AsyncJobConfig(
                    status_url_source=AsyncStatusUrlSource.response_body,
                    status_url_field="job.status_url",
                    status_field="job.state",
                    poll_interval_seconds=0.0 + 0.01,
                    timeout_seconds=1.0,
                ),
                risk=RiskMetadata(
                    risk_level=RiskLevel.cautious,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=True,
                    destructive=False,
                    external_side_effect=True,
                    idempotent=False,
                ),
                enabled=True,
            )
        ],
    )
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    proxy = RuntimeProxy(
        service_ir,
        observability=RuntimeObservability(),
        client=upstream_client,
    )

    try:
        result = await proxy.invoke(service_ir.operations[0], {"payload": {"scope": "all"}})
    finally:
        await proxy.aclose()

    assert captured_paths == ["/exports", "/jobs/job-1", "/jobs/job-1"]
    assert result["status"] == "ok"
    assert result["upstream_status"] == 200
    assert result["result"] == {"job": {"state": "completed"}, "artifact_id": "artifact-1"}


@pytest.mark.asyncio
async def test_runtime_tool_call_consumes_sse_stream_and_reports_lifecycle(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "event: account.updated\n"
                'data: {"account_id":"acct-1","status":"ready"}\n\n'
                "event: account.updated\n"
                'data: {"account_id":"acct-1","status":"done"}\n\n'
            ),
            headers={"Content-Type": "text/event-stream"},
            request=request,
        )

    service_ir = _build_proxy_ir(
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="streamAccountEvents",
                name="Stream Account Events",
                description="Consume account event updates.",
                method="GET",
                path="/accounts/{account_id}/events",
                params=[Param(name="account_id", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        event_descriptors=[
            EventDescriptor(
                id="streamAccountEvents:sse",
                name="streamAccountEvents",
                transport=EventTransport.sse,
                support=EventSupportLevel.supported,
                operation_id="streamAccountEvents",
                channel="/accounts/{account_id}/events",
                metadata={"max_events": 1},
            )
        ],
    ).model_copy(update={"base_url": "https://api.example.test"})
    service_ir_path = _write_service_ir(tmp_path, "service_ir_sse.json", service_ir)
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=service_ir_path, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "streamAccountEvents",
            {"account_id": "acct-1"},
        )
    finally:
        await upstream_client.aclose()

    assert structured["status"] == "ok"
    assert structured["transport"] == "sse"
    assert structured["result"]["events"] == [
        {
            "event": "account.updated",
            "data": '{"account_id":"acct-1","status":"ready"}',
            "parsed_data": {"account_id": "acct-1", "status": "ready"},
        }
    ]
    assert structured["result"]["lifecycle"]["termination_reason"] == "max_events"
    assert structured["result"]["lifecycle"]["events_collected"] == 1


@pytest.mark.asyncio
async def test_runtime_tool_call_consumes_websocket_stream_and_reports_lifecycle(
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    received_messages: list[str] = []

    async def websocket_handler(connection: websockets.ServerConnection) -> None:
        received = await connection.recv()
        assert isinstance(received, str)
        received_messages.append(received)
        await connection.send('{"event":"inventory.updated","sku":"sku-1"}')

    server = await websockets.serve(websocket_handler, "127.0.0.1", unused_tcp_port)
    service_ir = _build_proxy_ir(
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="watchInventory",
                name="Watch Inventory",
                description="Open a websocket watch session.",
                method="GET",
                path="/socket",
                params=[Param(name="payload", type="object", required=False)],
                body_param_name="payload",
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        event_descriptors=[
            EventDescriptor(
                id="watchInventory:websocket",
                name="watchInventory",
                transport=EventTransport.websocket,
                support=EventSupportLevel.supported,
                operation_id="watchInventory",
                channel="/socket",
                metadata={"max_messages": 1},
            )
        ],
    ).model_copy(update={"base_url": f"http://127.0.0.1:{unused_tcp_port}"})
    service_ir_path = _write_service_ir(tmp_path, "service_ir_websocket.json", service_ir)

    try:
        app = create_app(service_ir_path=service_ir_path)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "watchInventory",
            {"payload": {"topic": "inventory"}},
        )
    finally:
        server.close()
        await server.wait_closed()

    assert received_messages == ['{"topic":"inventory"}']
    assert structured["status"] == "ok"
    assert structured["transport"] == "websocket"
    assert structured["result"]["events"] == [
        {
            "message_type": "text",
            "text": '{"event":"inventory.updated","sku":"sku-1"}',
            "parsed_data": {"event": "inventory.updated", "sku": "sku-1"},
        }
    ]
    assert structured["result"]["lifecycle"]["termination_reason"] == "max_messages"
    assert structured["result"]["lifecycle"]["messages_sent"] == 1


@pytest.mark.asyncio
async def test_runtime_tool_call_rejects_unsupported_stream_transport(
    tmp_path: Path,
) -> None:
    service_ir = _build_proxy_ir(
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="watchInventory",
                name="Watch Inventory",
                description="Attempt a gRPC watch stream.",
                method="POST",
                path="/catalog.v1.InventoryService/WatchInventory",
                params=[Param(name="payload", type="object", required=False)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        event_descriptors=[
            EventDescriptor(
                id="WatchInventory",
                name="WatchInventory",
                transport=EventTransport.grpc_stream,
                support=EventSupportLevel.supported,
                operation_id="watchInventory",
                channel="/catalog.v1.InventoryService/WatchInventory",
                grpc_stream=GrpcStreamRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/WatchInventory",
                    mode=GrpcStreamMode.server,
                ),
            )
        ],
    )
    service_ir_path = _write_service_ir(tmp_path, "service_ir_grpc_stream.json", service_ir)
    app = create_app(service_ir_path=service_ir_path)

    with pytest.raises(ToolError, match="configured grpc stream executor"):
        await app.state.runtime_state.mcp_server.call_tool(
            "watchInventory",
            {"payload": {"sku": "sku-1"}},
        )


@pytest.mark.asyncio
async def test_runtime_tool_call_uses_native_grpc_stream_executor_when_configured(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class StubGrpcStreamExecutor:
        async def invoke(
            self,
            *,
            operation: Operation,
            arguments: dict[str, object],
            descriptor: EventDescriptor,
            config: GrpcStreamRuntimeConfig,
        ) -> dict[str, object]:
            captured["operation_id"] = operation.id
            captured["arguments"] = dict(arguments)
            captured["descriptor_id"] = descriptor.id
            captured["rpc_path"] = config.rpc_path
            captured["mode"] = config.mode.value
            return {
                "events": [
                    {
                        "message_type": "json",
                        "parsed_data": {"sku": "sku-1", "status": "updated"},
                    }
                ],
                "lifecycle": {
                    "termination_reason": "max_messages",
                    "messages_collected": 1,
                },
            }

    service_ir = _build_proxy_ir(
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="watchInventory",
                name="Watch Inventory",
                description="Consume a native gRPC inventory stream.",
                method="POST",
                path="/catalog.v1.InventoryService/WatchInventory",
                params=[Param(name="payload", type="object", required=False)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                enabled=True,
            )
        ],
        event_descriptors=[
            EventDescriptor(
                id="WatchInventory",
                name="WatchInventory",
                transport=EventTransport.grpc_stream,
                support=EventSupportLevel.supported,
                operation_id="watchInventory",
                channel="/catalog.v1.InventoryService/WatchInventory",
                grpc_stream=GrpcStreamRuntimeConfig(
                    rpc_path="/catalog.v1.InventoryService/WatchInventory",
                    mode=GrpcStreamMode.server,
                    max_messages=1,
                ),
            )
        ],
    )
    service_ir_path = _write_service_ir(
        tmp_path,
        "service_ir_grpc_stream_supported.json",
        service_ir,
    )
    app = create_app(
        service_ir_path=service_ir_path,
        grpc_stream_executor=StubGrpcStreamExecutor(),
    )

    _, structured = await app.state.runtime_state.mcp_server.call_tool(
        "watchInventory",
        {"payload": {"sku": "sku-1"}},
    )

    assert captured == {
        "operation_id": "watchInventory",
        "arguments": {"payload": {"sku": "sku-1"}},
        "descriptor_id": "WatchInventory",
        "rpc_path": "/catalog.v1.InventoryService/WatchInventory",
        "mode": "server",
    }
    assert structured["status"] == "ok"
    assert structured["transport"] == "grpc_stream"
    assert structured["result"]["events"] == [
        {
            "message_type": "json",
            "parsed_data": {"sku": "sku-1", "status": "updated"},
        }
    ]


# ── Response pruning (field filter + array limit) ────────────────────────


@pytest.mark.asyncio
async def test_runtime_array_limit_truncates_list_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_array_items=2 on listTransactions truncates a 5-item list to 2."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "tx-1", "amount": 100},
                {"id": "tx-2", "amount": 200},
                {"id": "tx-3", "amount": 300},
                {"id": "tx-4", "amount": 400},
                {"id": "tx-5", "amount": 500},
            ],
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "listTransactions",
            {"account_id": "acct-1"},
        )
    finally:
        await upstream_client.aclose()

    assert structured["status"] == "ok"
    assert structured["result"] == [
        {"id": "tx-1", "amount": 100},
        {"id": "tx-2", "amount": 200},
    ]
    assert structured["truncated"] is False


@pytest.mark.asyncio
async def test_runtime_nested_field_filter_with_dot_and_bracket_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """getAccountDetailed filters nested dot-paths and array bracket paths."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "acct-1",
                "balance": 9999,
                "owner": {"email": "a@b.com", "phone": "555-0100"},
                "items": [
                    {"name": "Widget", "price": 10, "sku": "w-1"},
                    {"name": "Gadget", "price": 20, "sku": "g-1"},
                    {"name": "Doohickey", "price": 30, "sku": "d-1"},
                    {"name": "Thingamajig", "price": 40, "sku": "t-1"},
                ],
            },
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "getAccountDetailed",
            {"account_id": "acct-1"},
        )
    finally:
        await upstream_client.aclose()

    assert structured["status"] == "ok"
    # field_filter: ["id", "owner.email", "items[].name"]
    # max_array_items: 3 — applied after field filtering
    result = structured["result"]
    assert result["id"] == "acct-1"
    assert "balance" not in result
    assert result["owner"] == {"email": "a@b.com"}
    # items filtered to name-only, then truncated to 3
    assert result["items"] == [
        {"name": "Widget"},
        {"name": "Gadget"},
        {"name": "Doohickey"},
    ]
    assert structured["truncated"] is False


@pytest.mark.asyncio
async def test_runtime_array_limit_on_dict_payload_truncates_nested_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_array_items on a dict response truncates list-typed values."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total": 5,
                "items": [
                    {"id": "tx-1"},
                    {"id": "tx-2"},
                    {"id": "tx-3"},
                    {"id": "tx-4"},
                    {"id": "tx-5"},
                ],
            },
            request=request,
        )

    monkeypatch.setenv("BILLING_SECRET", "runtime-token")
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        app = create_app(service_ir_path=PROXY_IR_PATH, upstream_client=upstream_client)
        _, structured = await app.state.runtime_state.mcp_server.call_tool(
            "listTransactions",
            {"account_id": "acct-1"},
        )
    finally:
        await upstream_client.aclose()

    assert structured["status"] == "ok"
    # max_array_items=2 applies to list values inside dicts
    assert structured["result"] == {
        "total": 5,
        "items": [{"id": "tx-1"}, {"id": "tx-2"}],
    }
