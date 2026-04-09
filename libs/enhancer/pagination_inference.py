"""Heuristic pagination pattern detection for operations.

Pure inference — no LLM calls.  Examines operation parameters and response
schemas to infer pagination style.
"""

from __future__ import annotations

import logging
from typing import Any

from libs.ir.models import Operation, PaginationConfig, ServiceIR

logger = logging.getLogger(__name__)

# Known pagination parameter patterns
_OFFSET_PARAMS = {"offset", "skip", "start"}
_LIMIT_PARAMS = {"limit", "count", "size", "per_page", "pageSize", "page_size", "top"}
_CURSOR_PARAMS = {"cursor", "after", "before", "next_token", "continuation_token", "page_token"}
_PAGE_PARAMS = {"page", "pageNumber", "page_number"}


def infer_pagination(ir: ServiceIR) -> ServiceIR:
    """Detect and annotate pagination patterns on operations.

    Examines operation parameters and response schemas to infer pagination style.
    Pure inference — no LLM calls.
    """
    ops: list[Operation] = []
    for op in ir.operations:
        pagination = _detect_pagination(op)
        if pagination is not None:
            op = op.model_copy(update={"pagination": pagination})
        ops.append(op)
    return ir.model_copy(update={"operations": ops})


def _detect_pagination(op: Operation) -> PaginationConfig | None:
    """Detect pagination pattern from operation parameters and response schema."""
    param_names = {p.name.lower() for p in op.params}

    # Check for cursor-based pagination
    cursor_match = param_names & _CURSOR_PARAMS
    if cursor_match:
        cursor_param = next(iter(cursor_match))
        limit_match = param_names & _LIMIT_PARAMS
        return PaginationConfig(
            style="cursor",
            cursor_param=cursor_param,
            limit_param=next(iter(limit_match)) if limit_match else None,
            next_field=_detect_next_field(op.response_schema),
        )

    # Check for offset-based
    offset_match = param_names & _OFFSET_PARAMS
    limit_match = param_names & _LIMIT_PARAMS
    if offset_match and limit_match:
        return PaginationConfig(
            style="offset",
            page_param=next(iter(offset_match)),
            limit_param=next(iter(limit_match)),
            total_field=_detect_total_field(op.response_schema),
        )

    # Check for page-number based
    page_match = param_names & _PAGE_PARAMS
    if page_match:
        return PaginationConfig(
            style="page",
            page_param=next(iter(page_match)),
            limit_param=next(iter(limit_match)) if limit_match else None,
            total_field=_detect_total_field(op.response_schema),
        )

    # Check for limit-only (common in REST APIs)
    if limit_match and not offset_match:
        return PaginationConfig(
            style="offset",
            limit_param=next(iter(limit_match)),
        )

    return None


def _detect_next_field(schema: dict[str, Any] | None) -> str | None:
    """Detect the 'next page' field in a response schema."""
    if not schema:
        return None
    props = schema.get("properties", {})
    for key in (
        "next",
        "nextPage",
        "next_page",
        "nextCursor",
        "next_cursor",
        "nextToken",
        "next_token",
        "cursor",
    ):
        if key in props:
            return key
    return None


def _detect_total_field(schema: dict[str, Any] | None) -> str | None:
    """Detect the 'total count' field in a response schema."""
    if not schema:
        return None
    props = schema.get("properties", {})
    for key in ("total", "totalCount", "total_count", "count", "totalItems", "total_items"):
        if key in props:
            return key
    return None
