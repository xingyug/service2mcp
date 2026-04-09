"""Tests for pagination inference."""

from __future__ import annotations

from libs.enhancer.pagination_inference import _detect_pagination, infer_pagination
from libs.ir.models import (
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)

# ── Helpers ────────────────────────────────────────────────────────────────

_SAFE_RISK = RiskMetadata(risk_level=RiskLevel.safe, confidence=0.9)


def _make_op(
    op_id: str = "op_1",
    *,
    params: list[Param] | None = None,
    response_schema: dict[str, object] | None = None,
) -> Operation:
    return Operation(
        id=op_id,
        name=f"Test {op_id}",
        method="GET",
        path=f"/{op_id}",
        risk=_SAFE_RISK,
        params=params or [],
        response_schema=response_schema,
        enabled=True,
    )


def _make_ir(operations: list[Operation]) -> ServiceIR:
    return ServiceIR(
        source_hash="abc123",
        protocol="test",
        service_name="test-service",
        base_url="https://example.com",
        operations=operations,
    )


def _param(name: str) -> Param:
    return Param(name=name, type="string")


# ── Cursor pagination detection ───────────────────────────────────────────


class TestCursorPagination:
    def test_detect_cursor_pagination(self) -> None:
        op = _make_op(params=[_param("cursor"), _param("limit")])
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "cursor"
        assert result.cursor_param == "cursor"
        assert result.limit_param == "limit"

    def test_detect_after_param_cursor(self) -> None:
        op = _make_op(params=[_param("after")])
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "cursor"
        assert result.cursor_param == "after"

    def test_detect_cursor_with_next_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "data": {"type": "array"},
                "next": {"type": "string"},
            },
        }
        op = _make_op(
            params=[_param("cursor"), _param("limit")],
            response_schema=schema,
        )
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "cursor"
        assert result.next_field == "next"

    def test_detect_page_token_cursor(self) -> None:
        op = _make_op(params=[_param("page_token"), _param("size")])
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "cursor"
        assert result.cursor_param == "page_token"
        assert result.limit_param == "size"


# ── Offset pagination detection ───────────────────────────────────────────


class TestOffsetPagination:
    def test_detect_offset_limit_pagination(self) -> None:
        op = _make_op(params=[_param("offset"), _param("limit")])
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "offset"
        assert result.page_param == "offset"
        assert result.limit_param == "limit"

    def test_detect_skip_count_pagination(self) -> None:
        op = _make_op(params=[_param("skip"), _param("count")])
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "offset"
        assert result.page_param == "skip"
        assert result.limit_param == "count"

    def test_detect_offset_with_total(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array"},
                "total": {"type": "integer"},
            },
        }
        op = _make_op(
            params=[_param("offset"), _param("limit")],
            response_schema=schema,
        )
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "offset"
        assert result.total_field == "total"


# ── Page pagination detection ─────────────────────────────────────────────


class TestPagePagination:
    def test_detect_page_number_pagination(self) -> None:
        op = _make_op(params=[_param("page")])
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "page"
        assert result.page_param == "page"

    def test_detect_page_with_per_page(self) -> None:
        op = _make_op(params=[_param("page"), _param("per_page")])
        result = _detect_pagination(op)
        assert result is not None
        assert result.style == "page"
        assert result.page_param == "page"
        assert result.limit_param == "per_page"


# ── No pagination ─────────────────────────────────────────────────────────


class TestNoPagination:
    def test_no_pagination_no_params(self) -> None:
        op = _make_op(params=[])
        result = _detect_pagination(op)
        assert result is None

    def test_no_pagination_non_matching_params(self) -> None:
        op = _make_op(params=[_param("name"), _param("id")])
        result = _detect_pagination(op)
        assert result is None

    def test_infer_preserves_non_paginated_ops(self) -> None:
        op = _make_op(params=[_param("name")])
        ir = _make_ir([op])
        result = infer_pagination(ir)
        assert result.operations[0].pagination is None


# ── Integration tests ─────────────────────────────────────────────────────


class TestInferPaginationIntegration:
    def test_infer_pagination_full_ir(self) -> None:
        ops = [
            _make_op("cursor_op", params=[_param("cursor"), _param("limit")]),
            _make_op("offset_op", params=[_param("offset"), _param("limit")]),
            _make_op("page_op", params=[_param("page")]),
        ]
        ir = _make_ir(ops)
        result = infer_pagination(ir)
        assert result.operations[0].pagination is not None
        assert result.operations[0].pagination.style == "cursor"
        assert result.operations[1].pagination is not None
        assert result.operations[1].pagination.style == "offset"
        assert result.operations[2].pagination is not None
        assert result.operations[2].pagination.style == "page"

    def test_infer_pagination_mixed_ops(self) -> None:
        ops = [
            _make_op("paginated", params=[_param("cursor")]),
            _make_op("plain", params=[_param("name")]),
        ]
        ir = _make_ir(ops)
        result = infer_pagination(ir)
        assert result.operations[0].pagination is not None
        assert result.operations[0].pagination.style == "cursor"
        assert result.operations[1].pagination is None

    def test_infer_idempotent(self) -> None:
        ops = [
            _make_op("op1", params=[_param("cursor"), _param("limit")]),
            _make_op("op2", params=[_param("name")]),
        ]
        ir = _make_ir(ops)
        first = infer_pagination(ir)
        second = infer_pagination(first)
        for i in range(len(first.operations)):
            assert first.operations[i].pagination == second.operations[i].pagination
