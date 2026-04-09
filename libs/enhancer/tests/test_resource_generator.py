"""Tests for auto-generated MCP resources (IRX-003)."""

from __future__ import annotations

import json
from typing import Any

from libs.enhancer.resource_generator import generate_resources
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorResponse,
    ErrorSchema,
    OAuth2ClientCredentialsConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)


def _make_param(**overrides: Any) -> Param:
    defaults: dict[str, Any] = {
        "name": "id",
        "type": "integer",
        "required": True,
    }
    return Param(**(defaults | overrides))


def _make_risk(level: RiskLevel = RiskLevel.safe) -> RiskMetadata:
    return RiskMetadata(
        writes_state=level != RiskLevel.safe,
        destructive=level == RiskLevel.dangerous,
        risk_level=level,
        confidence=0.9,
    )


def _make_op(id: str = "list_pets", **overrides: Any) -> Operation:
    defaults: dict[str, Any] = {
        "id": id,
        "name": f"Op {id}",
        "description": f"Desc {id}",
        "method": "GET",
        "path": f"/{id}",
        "params": [_make_param()],
        "risk": _make_risk(),
        "enabled": True,
    }
    return Operation(**(defaults | overrides))


def _make_ir(**overrides: Any) -> ServiceIR:
    defaults: dict[str, Any] = {
        "source_hash": "abc123",
        "protocol": "openapi",
        "service_name": "petstore",
        "service_description": "The Petstore API",
        "base_url": "https://petstore.example.com/v1",
        "operations": [_make_op()],
    }
    return ServiceIR(**(defaults | overrides))


class TestGenerateResources:
    def test_generates_five_resources(self) -> None:
        ir = _make_ir()
        resources = generate_resources(ir)
        assert len(resources) == 5

    def test_resource_ids(self) -> None:
        ir = _make_ir()
        resources = generate_resources(ir)
        ids = {r.id for r in resources}
        assert ids == {
            "petstore-schema",
            "petstore-operations",
            "petstore-auth-requirements",
            "petstore-risk-profile",
            "petstore-error-catalog",
        }

    def test_resource_uris(self) -> None:
        ir = _make_ir()
        resources = generate_resources(ir)
        uris = {r.uri for r in resources}
        assert uris == {
            "service:///petstore/schema",
            "service:///petstore/operations",
            "service:///petstore/auth-requirements",
            "service:///petstore/risk-profile",
            "service:///petstore/error-catalog",
        }

    def test_all_resources_are_static(self) -> None:
        ir = _make_ir()
        for r in generate_resources(ir):
            assert r.content_type == "static"
            assert r.mime_type == "application/json"

    def test_all_resources_have_content(self) -> None:
        ir = _make_ir()
        for r in generate_resources(ir):
            assert r.content is not None
            json.loads(r.content)  # must be valid JSON


class TestSchemaResource:
    def test_schema_resource_content(self) -> None:
        ir = _make_ir()
        resources = generate_resources(ir)
        schema_r = next(r for r in resources if "schema" in r.id)
        content = json.loads(schema_r.content)
        assert content["service_name"] == "petstore"
        assert content["protocol"] == "openapi"
        assert content["base_url"] == "https://petstore.example.com/v1"
        assert content["description"] == "The Petstore API"
        assert content["operation_count"] == 1


class TestOperationsResource:
    def test_operations_resource_lists_all_ops(self) -> None:
        ir = _make_ir(
            operations=[_make_op("list_pets"), _make_op("get_pet")],
        )
        resources = generate_resources(ir)
        ops_r = next(r for r in resources if "operations" in r.id)
        ops = json.loads(ops_r.content)
        assert len(ops) == 2
        assert ops[0]["id"] == "list_pets"
        assert ops[1]["id"] == "get_pet"

    def test_operations_resource_includes_risk_level(self) -> None:
        ir = _make_ir()
        resources = generate_resources(ir)
        ops_r = next(r for r in resources if "operations" in r.id)
        ops = json.loads(ops_r.content)
        assert ops[0]["risk_level"] == "safe"


class TestAuthRequirementsResource:
    def test_auth_none(self) -> None:
        ir = _make_ir()
        resources = generate_resources(ir)
        auth_r = next(r for r in resources if "auth" in r.id)
        content = json.loads(auth_r.content)
        assert content["type"] == "none"

    def test_auth_bearer(self) -> None:
        ir = _make_ir(
            auth=AuthConfig(
                type=AuthType.bearer,
                header_name="Authorization",
                header_prefix="Bearer",
            ),
        )
        resources = generate_resources(ir)
        auth_r = next(r for r in resources if "auth" in r.id)
        content = json.loads(auth_r.content)
        assert content["type"] == "bearer"
        assert content["header_name"] == "Authorization"

    def test_auth_oauth2(self) -> None:
        ir = _make_ir(
            auth=AuthConfig(
                type=AuthType.oauth2,
                oauth2=OAuth2ClientCredentialsConfig(
                    token_url="https://auth.example.com/token",
                    client_id_ref="my-client-id",
                    client_secret_ref="my-client-secret",
                    scopes=["read", "write"],
                ),
            ),
        )
        resources = generate_resources(ir)
        auth_r = next(r for r in resources if "auth" in r.id)
        content = json.loads(auth_r.content)
        assert content["type"] == "oauth2"
        assert content["oauth2_token_url"] == ("https://auth.example.com/token")
        assert content["oauth2_scopes"] == ["read", "write"]


class TestGenerateResourcesIntegration:
    def test_resources_can_be_added_to_service_ir(self) -> None:
        """Generated resources can be set on ServiceIR without error."""
        ir = _make_ir()
        resources = generate_resources(ir)
        ir_with_resources = ir.model_copy(
            update={"resource_definitions": resources},
        )
        assert len(ir_with_resources.resource_definitions) == 5
        assert ir_with_resources.resource_definitions[0].id == ("petstore-schema")


class TestRiskProfileResource:
    def test_risk_distribution(self) -> None:
        ir = _make_ir(
            operations=[
                _make_op("list_pets", risk=_make_risk(RiskLevel.safe)),
                _make_op("add_pet", risk=_make_risk(RiskLevel.cautious)),
                _make_op("delete_pet", risk=_make_risk(RiskLevel.dangerous)),
            ],
        )
        resources = generate_resources(ir)
        risk_r = next(r for r in resources if "risk-profile" in r.id)
        content = json.loads(risk_r.content)
        assert content["total_operations"] == 3
        assert content["risk_distribution"]["safe"] == 1
        assert content["risk_distribution"]["cautious"] == 1
        assert content["risk_distribution"]["dangerous"] == 1

    def test_flags_counts(self) -> None:
        ir = _make_ir(
            operations=[
                _make_op("list_pets", risk=_make_risk(RiskLevel.safe)),
                _make_op("delete_pet", risk=_make_risk(RiskLevel.dangerous)),
            ],
        )
        resources = generate_resources(ir)
        risk_r = next(r for r in resources if "risk-profile" in r.id)
        content = json.loads(risk_r.content)
        assert content["flags"]["destructive"] == 1
        assert content["flags"]["writes_state"] == 1

    def test_empty_operations(self) -> None:
        ir = _make_ir(operations=[])
        resources = generate_resources(ir)
        risk_r = next(r for r in resources if "risk-profile" in r.id)
        content = json.loads(risk_r.content)
        assert content["total_operations"] == 0
        assert content["risk_distribution"] == {
            "safe": 0,
            "cautious": 0,
            "dangerous": 0,
        }


class TestErrorCatalogResource:
    def test_error_catalog_with_errors(self) -> None:
        error_schema = ErrorSchema(
            responses=[
                ErrorResponse(status_code=404, error_code="NOT_FOUND"),
                ErrorResponse(status_code=500, error_code="SERVER_ERROR"),
            ],
        )
        ir = _make_ir(
            operations=[
                _make_op("get_pet", error_schema=error_schema),
            ],
        )
        resources = generate_resources(ir)
        err_r = next(r for r in resources if "error-catalog" in r.id)
        content = json.loads(err_r.content)
        assert content["total_error_codes"] == 2
        assert "NOT_FOUND" in content["error_codes"]
        assert content["error_codes"]["NOT_FOUND"]["operations"] == ["get_pet"]

    def test_error_catalog_empty(self) -> None:
        ir = _make_ir()
        resources = generate_resources(ir)
        err_r = next(r for r in resources if "error-catalog" in r.id)
        content = json.loads(err_r.content)
        assert content["total_error_codes"] == 0
        assert content["error_codes"] == {}

    def test_error_catalog_deduplicates_ops(self) -> None:
        """Same error code from same op should appear only once."""
        error_schema = ErrorSchema(
            responses=[
                ErrorResponse(status_code=404, error_code="NOT_FOUND"),
                ErrorResponse(status_code=404, error_code="NOT_FOUND"),
            ],
        )
        ir = _make_ir(
            operations=[_make_op("get_pet", error_schema=error_schema)],
        )
        resources = generate_resources(ir)
        err_r = next(r for r in resources if "error-catalog" in r.id)
        content = json.loads(err_r.content)
        assert content["error_codes"]["NOT_FOUND"]["count"] == 1

    def test_error_catalog_aggregates_across_ops(self) -> None:
        """Same error code from multiple ops should list all."""
        error_schema = ErrorSchema(
            responses=[ErrorResponse(error_code="UNAUTHORIZED")],
        )
        ir = _make_ir(
            operations=[
                _make_op("list_pets", error_schema=error_schema),
                _make_op("get_pet", error_schema=error_schema),
            ],
        )
        resources = generate_resources(ir)
        err_r = next(r for r in resources if "error-catalog" in r.id)
        content = json.loads(err_r.content)
        assert content["error_codes"]["UNAUTHORIZED"]["count"] == 2
        assert set(content["error_codes"]["UNAUTHORIZED"]["operations"]) == {
            "list_pets",
            "get_pet",
        }
