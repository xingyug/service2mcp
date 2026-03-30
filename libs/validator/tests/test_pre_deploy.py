"""Tests for the pre-deploy validation harness."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from libs.extractors.base import SourceConfig
from libs.extractors.grpc import GrpcProtoExtractor
from libs.extractors.soap import SOAPWSDLExtractor
from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    MTLSConfig,
    OAuth2ClientCredentialsConfig,
    Operation,
    Param,
    RequestSigningConfig,
    ResponseStrategy,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)
from libs.validator.audit import ToolAuditSummary
from libs.validator.pre_deploy import PreDeployValidator, ValidationReport

WSDL_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "tests"
    / "fixtures"
    / "wsdl"
    / "order_service.wsdl"
)
PROTO_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "tests"
    / "fixtures"
    / "grpc_protos"
    / "inventory.proto"
)


def _build_ir(*, auth: AuthConfig | None = None) -> ServiceIR:
    return ServiceIR(
        source_hash="c" * 64,
        protocol="openapi",
        service_name="Inventory API",
        service_description="Compiled inventory service",
        base_url="https://inventory.example.com",
        auth=auth or AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="listItems",
                name="List Items",
                description="List inventory items.",
                method="GET",
                path="/items",
                params=[Param(name="limit", type="integer", required=False, confidence=1.0)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                response_strategy=ResponseStrategy(max_response_bytes=4096),
                enabled=True,
            )
        ],
    )


@pytest.mark.asyncio
async def test_valid_ir_passes_pre_deploy_validation() -> None:
    validator = PreDeployValidator()

    try:
        report = await validator.validate(_build_ir())
    finally:
        await validator.aclose()

    assert report.overall_passed is True
    assert report.get_result("schema").passed is True
    assert report.get_result("event_support").passed is True
    assert report.get_result("auth_smoke").passed is True


@pytest.mark.asyncio
async def test_extracted_soap_ir_passes_pre_deploy_validation() -> None:
    service_ir = SOAPWSDLExtractor().extract(SourceConfig(file_path=str(WSDL_FIXTURE_PATH)))
    validator = PreDeployValidator()

    try:
        report = await validator.validate(service_ir.model_dump(mode="json"))
    finally:
        await validator.aclose()

    assert report.overall_passed is True
    assert report.get_result("schema").passed is True
    assert report.get_result("event_support").passed is True


@pytest.mark.asyncio
async def test_invalid_ir_fails_schema_validation() -> None:
    validator = PreDeployValidator()
    invalid_payload = _build_ir().model_dump(mode="json")
    invalid_payload["operations"] = invalid_payload["operations"] * 2

    try:
        report = await validator.validate(invalid_payload)
    finally:
        await validator.aclose()

    assert report.overall_passed is False
    assert report.get_result("schema").passed is False
    assert "validation failed" in report.get_result("schema").details.lower()
    assert report.get_result("event_support").passed is False
    assert "skipped" in report.get_result("event_support").details.lower()
    assert report.get_result("auth_smoke").passed is False
    assert "skipped" in report.get_result("auth_smoke").details.lower()


@pytest.mark.asyncio
@respx.mock
async def test_unreachable_auth_endpoint_fails_auth_smoke() -> None:
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2_token_url="https://auth.example.com/oauth/token",
        runtime_secret_ref="inventory-oauth-secret",
    )
    ir = _build_ir(auth=auth)
    respx.post("https://auth.example.com/oauth/token").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.overall_passed is False
    assert report.get_result("schema").passed is True
    assert report.get_result("auth_smoke").passed is False
    assert "could not reach token endpoint" in report.get_result("auth_smoke").details.lower()


@pytest.mark.asyncio
@respx.mock
async def test_advanced_auth_config_passes_when_oauth2_token_exchange_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVENTORY_CLIENT_ID", "inventory-client")
    monkeypatch.setenv("INVENTORY_CLIENT_SECRET", "inventory-secret")
    monkeypatch.setenv("INVENTORY_MTLS_CERT", "/tmp/cert.pem")
    monkeypatch.setenv("INVENTORY_MTLS_KEY", "/tmp/key.pem")
    monkeypatch.setenv("INVENTORY_MTLS_CA", "/tmp/ca.pem")
    monkeypatch.setenv("INVENTORY_SIGNING_SECRET", "signing-secret")
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2=OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/oauth/token",
            client_id_ref="inventory-client-id",
            client_secret_ref="inventory-client-secret",
            scopes=["inventory.read"],
            audience="inventory-api",
        ),
        mtls=MTLSConfig(
            cert_ref="inventory-mtls-cert",
            key_ref="inventory-mtls-key",
            ca_ref="inventory-mtls-ca",
        ),
        request_signing=RequestSigningConfig(secret_ref="inventory-signing-secret"),
    )
    ir = _build_ir(auth=auth)
    route = respx.post("https://auth.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token"})
    )

    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.overall_passed is True
    assert report.get_result("schema").passed is True
    assert report.get_result("auth_smoke").passed is True
    assert (
        "oauth2 client credentials token exchange succeeded"
        in report.get_result("auth_smoke").details.lower()
    )
    assert "mtls secret references resolved" in report.get_result("auth_smoke").details.lower()
    assert "request signing secret resolved" in report.get_result("auth_smoke").details.lower()
    request = route.calls[0].request
    assert request.method == "POST"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert request.headers["Authorization"].startswith("Basic ")
    assert request.content.decode() == (
        "grant_type=client_credentials&scope=inventory.read&audience=inventory-api"
    )


@pytest.mark.asyncio
async def test_incomplete_advanced_auth_config_fails_schema_validation() -> None:
    invalid_payload = _build_ir().model_dump(mode="json")
    invalid_payload["auth"] = {
        "type": "oauth2",
        "oauth2": {
            "token_url": "https://auth.example.com/oauth/token",
            "client_id_ref": "inventory-client-id",
        },
        "request_signing": {
            "algorithm": "hmac-sha256",
        },
        "mtls": {
            "cert_ref": "inventory-mtls-cert",
        },
    }

    validator = PreDeployValidator()
    try:
        report = await validator.validate(invalid_payload)
    finally:
        await validator.aclose()

    assert report.overall_passed is False
    assert report.get_result("schema").passed is False
    assert "validation failed" in report.get_result("schema").details.lower()
    assert report.get_result("auth_smoke").passed is False


@pytest.mark.asyncio
async def test_unsupported_event_descriptors_pass_pre_deploy_validation() -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="invoiceSigned",
                    name="invoiceSigned",
                    transport=EventTransport.webhook,
                    support=EventSupportLevel.unsupported,
                )
            ]
        }
    )
    validator = PreDeployValidator()

    try:
        report = await validator.validate(ir.model_dump(mode="json"))
    finally:
        await validator.aclose()

    assert report.overall_passed is True
    assert report.get_result("event_support").passed is True
    assert "unsupported" in report.get_result("event_support").details.lower()


@pytest.mark.asyncio
async def test_supported_event_descriptors_fail_until_runtime_support_lands() -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="inventoryChanged",
                    name="inventoryChanged",
                    transport=EventTransport.grpc_stream,
                    support=EventSupportLevel.supported,
                    operation_id="listItems",
                    channel="/graphql",
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/graphql",
                        mode=GrpcStreamMode.server,
                    ),
                )
            ]
        }
    )
    validator = PreDeployValidator()

    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.overall_passed is False
    assert report.get_result("schema").passed is True
    assert report.get_result("event_support").passed is False
    assert "native runtime enablement" in report.get_result("event_support").details.lower()


@pytest.mark.asyncio
async def test_supported_grpc_stream_descriptor_passes_with_native_opt_in() -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="watchInventory",
                    name="watchInventory",
                    transport=EventTransport.grpc_stream,
                    support=EventSupportLevel.supported,
                    operation_id="listItems",
                    channel="/catalog.v1.InventoryService/WatchInventory",
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/catalog.v1.InventoryService/WatchInventory",
                        mode=GrpcStreamMode.server,
                    ),
                )
            ]
        }
    )
    validator = PreDeployValidator(allow_native_grpc_stream=True)

    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.overall_passed is True
    assert report.get_result("event_support").passed is True
    assert "grpc_stream" in report.get_result("event_support").details.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_fragment"),
    [
        (GrpcStreamMode.client, "grpc_stream_mode_client"),
        (GrpcStreamMode.bidirectional, "grpc_stream_mode_bidirectional"),
    ],
)
async def test_supported_grpc_stream_descriptor_rejects_unimplemented_native_modes(
    mode: GrpcStreamMode,
    expected_fragment: str,
) -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="watchInventory",
                    name="watchInventory",
                    transport=EventTransport.grpc_stream,
                    support=EventSupportLevel.supported,
                    operation_id="listItems",
                    channel="/catalog.v1.InventoryService/WatchInventory",
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/catalog.v1.InventoryService/WatchInventory",
                        mode=mode,
                    ),
                )
            ]
        }
    )
    validator = PreDeployValidator(allow_native_grpc_stream=True)

    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.overall_passed is False
    assert report.get_result("event_support").passed is False
    assert expected_fragment in report.get_result("event_support").details


@pytest.mark.asyncio
async def test_extracted_grpc_ir_with_native_stream_enabled_passes_pre_deploy_validation() -> None:
    service_ir = GrpcProtoExtractor().extract(
        SourceConfig(
            file_path=str(PROTO_FIXTURE_PATH),
            url="grpc://inventory.example.internal:443",
            hints={"enable_native_grpc_stream": "true"},
        )
    )
    validator = PreDeployValidator(
        allow_native_grpc_stream=True,
        allow_native_grpc_unary=True,
    )

    try:
        report = await validator.validate(service_ir.model_dump(mode="json"))
    finally:
        await validator.aclose()

    assert report.overall_passed is True
    assert report.get_result("event_support").passed is True
    assert "watchinventory(grpc_stream)" in report.get_result("event_support").details.lower()


@pytest.mark.asyncio
async def test_native_grpc_unary_operation_fails_until_runtime_support_lands() -> None:
    ir = _build_ir().model_copy(
        update={
            "protocol": "grpc",
            "base_url": "grpc://inventory.example.test:443",
            "operations": [
                _build_ir()
                .operations[0]
                .model_copy(
                    update={
                        "id": "ListItems",
                        "name": "List Items",
                        "method": "POST",
                        "path": "/catalog.v1.InventoryService/ListItems",
                        "grpc_unary": GrpcUnaryRuntimeConfig(
                            rpc_path="/catalog.v1.InventoryService/ListItems"
                        ),
                    }
                )
            ],
        }
    )
    validator = PreDeployValidator()

    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.overall_passed is False
    assert report.get_result("event_support").passed is False
    assert "grpc_unary" in report.get_result("event_support").details.lower()


@pytest.mark.asyncio
async def test_native_grpc_unary_operation_passes_with_native_opt_in() -> None:
    ir = _build_ir().model_copy(
        update={
            "protocol": "grpc",
            "base_url": "grpc://inventory.example.test:443",
            "operations": [
                _build_ir()
                .operations[0]
                .model_copy(
                    update={
                        "id": "ListItems",
                        "name": "List Items",
                        "method": "POST",
                        "path": "/catalog.v1.InventoryService/ListItems",
                        "grpc_unary": GrpcUnaryRuntimeConfig(
                            rpc_path="/catalog.v1.InventoryService/ListItems"
                        ),
                    }
                )
            ],
        }
    )
    validator = PreDeployValidator(allow_native_grpc_unary=True)

    try:
        report = await validator.validate(ir.model_dump(mode="json"))
    finally:
        await validator.aclose()

    assert report.overall_passed is True
    assert report.get_result("event_support").passed is True
    assert "grpc_unary" in report.get_result("event_support").details.lower()


@pytest.mark.asyncio
async def test_supported_sse_event_descriptor_passes_with_operation_reference() -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="streamInventory",
                    name="streamInventory",
                    transport=EventTransport.sse,
                    support=EventSupportLevel.supported,
                    operation_id="listItems",
                    channel="/events",
                )
            ]
        }
    )
    validator = PreDeployValidator()

    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.overall_passed is True
    assert report.get_result("event_support").passed is True
    assert (
        "approved streaming transports configured"
        in report.get_result("event_support").details.lower()
    )


class TestValidationReportAuditSummary:
    def test_report_without_audit_summary(self) -> None:
        report = ValidationReport(results=[], overall_passed=True)
        assert report.audit_summary is None

    def test_report_with_audit_summary(self) -> None:
        summary = ToolAuditSummary(
            discovered_operations=10,
            generated_tools=10,
            audited_tools=7,
            passed=7,
            failed=0,
            skipped=3,
            results=[],
        )
        report = ValidationReport(
            results=[],
            overall_passed=True,
            audit_summary=summary,
        )
        assert report.audit_summary is not None
        assert report.audit_summary.passed == 7

    def test_report_serialization_with_audit(self) -> None:
        summary = ToolAuditSummary(
            discovered_operations=5,
            generated_tools=5,
            audited_tools=3,
            passed=3,
            failed=0,
            skipped=2,
            results=[],
        )
        report = ValidationReport(
            results=[],
            overall_passed=True,
            audit_summary=summary,
        )
        data = report.model_dump()
        assert data["audit_summary"]["passed"] == 3


# ---------------------------------------------------------------------------
# ValidationReport.get_result raises KeyError (line 51)
# ---------------------------------------------------------------------------


def test_get_result_raises_key_error_for_missing_stage() -> None:
    report = ValidationReport(results=[], overall_passed=True)
    with pytest.raises(KeyError, match="not_a_stage"):
        report.get_result("not_a_stage")


# ---------------------------------------------------------------------------
# Auth type not none but missing both secret refs (lines 162-173)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_none_auth_without_secret_refs_fails() -> None:
    auth = AuthConfig(type=AuthType.bearer)
    ir = _build_ir(auth=auth)
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    auth_detail = report.get_result("auth_smoke").details
    assert "compile_time_secret_ref or runtime_secret_ref" in auth_detail


@pytest.mark.asyncio
async def test_basic_auth_with_password_ref_passes_secret_check() -> None:
    auth = AuthConfig(
        type=AuthType.basic,
        basic_username="svc-user",
        basic_password_ref="basic-password",
    )
    ir = _build_ir(auth=auth)
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is True


# ---------------------------------------------------------------------------
# OAuth2 missing token_url (line 203)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth2_without_token_url_fails() -> None:
    auth = AuthConfig(type=AuthType.oauth2)
    ir = _build_ir(auth=auth)
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    assert "oauth2 auth requires" in report.get_result("auth_smoke").details.lower()


# ---------------------------------------------------------------------------
# OAuth2 with token_url but no oauth2 config and no secret refs (line 214)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth2_with_token_url_but_no_secret_refs_fails() -> None:
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2_token_url="https://auth.example.com/token",
    )
    ir = _build_ir(auth=auth)
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    oauth_detail = report.get_result("auth_smoke").details
    assert "compile_time_secret_ref or runtime_secret_ref" in oauth_detail


# ---------------------------------------------------------------------------
# Token endpoint returns unhealthy statuses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_token_endpoint_returning_404_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CID", "inventory-client")
    monkeypatch.setenv("CSEC", "inventory-secret")
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2=OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="cid",
            client_secret_ref="csec",
        ),
    )
    ir = _build_ir(auth=auth)
    respx.post("https://auth.example.com/token").mock(return_value=httpx.Response(404))
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    assert "unhealthy response" in report.get_result("auth_smoke").details.lower()
    assert "404" in report.get_result("auth_smoke").details


@pytest.mark.asyncio
@respx.mock
async def test_token_endpoint_returning_500_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CID", "inventory-client")
    monkeypatch.setenv("CSEC", "inventory-secret")
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2=OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="cid",
            client_secret_ref="csec",
        ),
    )
    ir = _build_ir(auth=auth)
    respx.post("https://auth.example.com/token").mock(return_value=httpx.Response(500))
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    assert "unhealthy response" in report.get_result("auth_smoke").details.lower()
    assert "500" in report.get_result("auth_smoke").details


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [302, 400, 401, 405])
@respx.mock
async def test_token_endpoint_non_success_statuses_fail(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CID", "inventory-client")
    monkeypatch.setenv("CSEC", "inventory-secret")
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2=OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/token",
            client_id_ref="cid",
            client_secret_ref="csec",
        ),
    )
    ir = _build_ir(auth=auth)
    response_headers = (
        {"location": "https://auth.example.com/redirected"} if status_code == 302 else {}
    )
    respx.post("https://auth.example.com/token").mock(
        return_value=httpx.Response(status_code, headers=response_headers)
    )

    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    assert "unhealthy response" in report.get_result("auth_smoke").details.lower()
    assert str(status_code) in report.get_result("auth_smoke").details


@pytest.mark.asyncio
@respx.mock
async def test_advanced_auth_config_fails_when_oauth2_secret_ref_is_missing() -> None:
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2=OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/oauth/token",
            client_id_ref="inventory-client-id",
            client_secret_ref="inventory-client-secret",
        ),
    )
    ir = _build_ir(auth=auth)
    route = respx.post("https://auth.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token"})
    )

    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    assert "oauth2 client id" in report.get_result("auth_smoke").details.lower()
    assert route.called is False


@pytest.mark.asyncio
@respx.mock
async def test_advanced_auth_config_fails_when_mtls_secret_ref_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVENTORY_CLIENT_ID", "inventory-client")
    monkeypatch.setenv("INVENTORY_CLIENT_SECRET", "inventory-secret")
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2=OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/oauth/token",
            client_id_ref="inventory-client-id",
            client_secret_ref="inventory-client-secret",
        ),
        mtls=MTLSConfig(
            cert_ref="inventory-mtls-cert",
            key_ref="inventory-mtls-key",
        ),
    )
    ir = _build_ir(auth=auth)
    route = respx.post("https://auth.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token"})
    )

    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    assert "mtls client certificate" in report.get_result("auth_smoke").details.lower()
    assert route.called is False


@pytest.mark.asyncio
@respx.mock
async def test_advanced_auth_config_fails_when_request_signing_secret_ref_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVENTORY_CLIENT_ID", "inventory-client")
    monkeypatch.setenv("INVENTORY_CLIENT_SECRET", "inventory-secret")
    auth = AuthConfig(
        type=AuthType.oauth2,
        oauth2=OAuth2ClientCredentialsConfig(
            token_url="https://auth.example.com/oauth/token",
            client_id_ref="inventory-client-id",
            client_secret_ref="inventory-client-secret",
        ),
        request_signing=RequestSigningConfig(secret_ref="inventory-signing-secret"),
    )
    ir = _build_ir(auth=auth)
    route = respx.post("https://auth.example.com/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "token"})
    )

    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("auth_smoke").passed is False
    assert "request signing secret" in report.get_result("auth_smoke").details.lower()
    assert route.called is False


# ---------------------------------------------------------------------------
# Event descriptor with EventSupportLevel.planned (lines 261-262)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planned_event_descriptor_fails() -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="futureEvent",
                    name="futureEvent",
                    transport=EventTransport.sse,
                    support=EventSupportLevel.planned,
                )
            ]
        }
    )
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("event_support").passed is False
    assert "planned" in report.get_result("event_support").details.lower()


# ---------------------------------------------------------------------------
# gRPC stream descriptor missing operation_id (lines 266-267)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grpc_stream_missing_operation_id_fails() -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="streamEvent",
                    name="streamEvent",
                    transport=EventTransport.grpc_stream,
                    support=EventSupportLevel.supported,
                    grpc_stream=GrpcStreamRuntimeConfig(
                        rpc_path="/test/Stream",
                        mode=GrpcStreamMode.server,
                    ),
                )
            ]
        }
    )
    validator = PreDeployValidator(allow_native_grpc_stream=True)
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("event_support").passed is False
    assert "missing_operation_id" in report.get_result("event_support").details


# ---------------------------------------------------------------------------
# gRPC stream descriptor missing grpc_stream config (lines 269-270)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grpc_stream_missing_grpc_stream_config_fails() -> None:
    descriptor = EventDescriptor.model_construct(
        id="streamEvent",
        name="streamEvent",
        transport=EventTransport.grpc_stream,
        support=EventSupportLevel.supported,
        operation_id="listItems",
        channel=None,
        grpc_stream=None,
        description=None,
    )
    ir = _build_ir().model_copy(update={"event_descriptors": [descriptor]})
    validator = PreDeployValidator(allow_native_grpc_stream=True)
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("event_support").passed is False
    assert "missing_grpc_stream" in report.get_result("event_support").details


# ---------------------------------------------------------------------------
# Non-gRPC descriptor with unsupported transport (lines 281-282)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_grpc_unsupported_transport_fails() -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="webhookEvent",
                    name="webhookEvent",
                    transport=EventTransport.webhook,
                    support=EventSupportLevel.supported,
                    operation_id="listItems",
                )
            ]
        }
    )
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("event_support").passed is False
    assert "webhook" in report.get_result("event_support").details.lower()


# ---------------------------------------------------------------------------
# SSE/WebSocket descriptor missing operation_id (lines 284-285)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_descriptor_missing_operation_id_fails() -> None:
    ir = _build_ir().model_copy(
        update={
            "event_descriptors": [
                EventDescriptor(
                    id="streamEvent",
                    name="streamEvent",
                    transport=EventTransport.sse,
                    support=EventSupportLevel.supported,
                )
            ]
        }
    )
    validator = PreDeployValidator()
    try:
        report = await validator.validate(ir)
    finally:
        await validator.aclose()

    assert report.get_result("event_support").passed is False
    assert "missing_operation_id" in report.get_result("event_support").details
