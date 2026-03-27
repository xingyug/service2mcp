"""Tests for libs.enhancer.error_normalizer."""

from __future__ import annotations

import copy

from libs.enhancer.error_normalizer import normalize_error_schemas
from libs.ir.models import (
    ErrorResponse,
    ErrorSchema,
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_op(op_id: str, **kwargs: object) -> Operation:
    defaults: dict[str, object] = {
        "id": op_id,
        "name": op_id,
        "risk": RiskMetadata(risk_level=RiskLevel.safe),
    }
    defaults.update(kwargs)
    return Operation(**defaults)


def _make_ir(operations: list[Operation]) -> ServiceIR:
    return ServiceIR(
        source_hash="abc123",
        protocol="openapi",
        service_name="test-svc",
        base_url="https://api.example.com",
        operations=operations,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExistingErrorSchemaPreserved:
    """Rule 1: never overwrite extractor-provided data."""

    def test_operations_with_existing_error_schema_unchanged(self) -> None:
        existing = ErrorSchema(
            responses=[ErrorResponse(status_code=418, description="I'm a teapot")],
        )
        op = _make_op("tea", method="GET", error_schema=existing)
        result = normalize_error_schemas(_make_ir([op]))

        assert result.operations[0].error_schema == existing

    def test_graphql_operation_keeps_graphql_errors(self) -> None:
        gql_errors = ErrorSchema(
            default_error_schema={
                "type": "object",
                "properties": {"errors": {"type": "array"}},
            },
        )
        op = _make_op("gql-query", error_schema=gql_errors)
        result = normalize_error_schemas(_make_ir([op]))

        assert result.operations[0].error_schema == gql_errors


class TestHttpInference:
    """Rules 2 & 3: infer errors for HTTP ops without error info."""

    def test_safe_http_operation_gets_standard_errors(self) -> None:
        op = _make_op("list-items", method="GET")
        result = normalize_error_schemas(_make_ir([op]))

        codes = {r.status_code for r in result.operations[0].error_schema.responses}
        assert codes == {400, 404, 500}

    def test_cautious_http_operation_gets_standard_errors(self) -> None:
        op = _make_op("create-item", method="POST")
        result = normalize_error_schemas(_make_ir([op]))

        codes = {r.status_code for r in result.operations[0].error_schema.responses}
        assert codes == {400, 404, 409, 422, 500}

    def test_dangerous_http_operation_gets_standard_errors(self) -> None:
        op = _make_op("delete-item", method="DELETE")
        result = normalize_error_schemas(_make_ir([op]))

        codes = {r.status_code for r in result.operations[0].error_schema.responses}
        assert codes == {400, 404, 409, 500}


class TestNonHttpFallback:
    """Rule 3: non-HTTP ops get a generic default_error_schema."""

    def test_non_http_operation_gets_generic_default(self) -> None:
        op = _make_op("rpc-call")  # no method
        result = normalize_error_schemas(_make_ir([op]))

        schema = result.operations[0].error_schema
        assert schema.default_error_schema is not None
        assert "error" in schema.default_error_schema["properties"]
        assert "code" in schema.default_error_schema["properties"]


class TestInvariantsAndSafety:
    """Cross-cutting guarantees."""

    def test_all_operations_have_error_schema_after_normalization(self) -> None:
        ops = [
            _make_op("get-op", method="GET"),
            _make_op("post-op", method="POST"),
            _make_op("del-op", method="DELETE"),
            _make_op("rpc-op"),
        ]
        result = normalize_error_schemas(_make_ir(ops))

        for op in result.operations:
            has_responses = bool(op.error_schema.responses)
            has_default = op.error_schema.default_error_schema is not None
            assert has_responses or has_default, (
                f"Operation '{op.id}' has empty error_schema after normalization"
            )

    def test_normalizer_does_not_mutate_original(self) -> None:
        op = _make_op("get-users", method="GET")
        ir = _make_ir([op])
        original_schema = copy.deepcopy(ir.operations[0].error_schema)

        normalize_error_schemas(ir)

        assert ir.operations[0].error_schema == original_schema

    def test_mixed_protocol_ir(self) -> None:
        existing = ErrorSchema(
            responses=[ErrorResponse(status_code=503, description="Service Unavailable")],
        )
        ops = [
            _make_op("with-errors", method="GET", error_schema=existing),
            _make_op("no-errors-http", method="PUT"),
            _make_op("no-errors-rpc"),
        ]
        result = normalize_error_schemas(_make_ir(ops))

        # Existing kept
        assert result.operations[0].error_schema == existing

        # HTTP gap filled
        codes = {r.status_code for r in result.operations[1].error_schema.responses}
        assert codes == {400, 404, 409, 422, 500}

        # Non-HTTP gap filled
        assert result.operations[2].error_schema.default_error_schema is not None
