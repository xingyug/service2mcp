"""Cross-protocol error model normalization.

Post-extraction pass that ensures all operations have at least a minimal
error schema.  Extractors populate what they know; this normalizer fills
the gaps so downstream consumers can always rely on error_schema being
meaningful.
"""

from __future__ import annotations

import logging

from libs.ir.models import ErrorResponse, ErrorSchema, Operation, ServiceIR

logger = logging.getLogger(__name__)

_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CAUTIOUS_METHODS = {"POST", "PUT", "PATCH"}
_DANGEROUS_METHODS = {"DELETE"}

_SAFE_ERRORS: list[ErrorResponse] = [
    ErrorResponse(status_code=400, description="Bad Request"),
    ErrorResponse(status_code=404, description="Not Found"),
    ErrorResponse(status_code=500, description="Internal Server Error"),
]

_CAUTIOUS_ERRORS: list[ErrorResponse] = [
    ErrorResponse(status_code=400, description="Bad Request"),
    ErrorResponse(status_code=404, description="Not Found"),
    ErrorResponse(status_code=409, description="Conflict"),
    ErrorResponse(status_code=422, description="Unprocessable Entity"),
    ErrorResponse(status_code=500, description="Internal Server Error"),
]

_DANGEROUS_ERRORS: list[ErrorResponse] = [
    ErrorResponse(status_code=400, description="Bad Request"),
    ErrorResponse(status_code=404, description="Not Found"),
    ErrorResponse(status_code=409, description="Conflict"),
    ErrorResponse(status_code=500, description="Internal Server Error"),
]

_GENERIC_DEFAULT_ERROR_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "error": {"type": "string"},
        "code": {"type": "string"},
    },
}


def _has_error_info(op: Operation) -> bool:
    """Return True if the operation already carries extractor-provided error data."""
    return bool(op.error_schema.responses) or op.error_schema.default_error_schema is not None


def _infer_http_errors(method: str) -> list[ErrorResponse]:
    upper = method.upper()
    if upper in _SAFE_METHODS:
        return list(_SAFE_ERRORS)
    if upper in _CAUTIOUS_METHODS:
        return list(_CAUTIOUS_ERRORS)
    if upper in _DANGEROUS_METHODS:
        return list(_DANGEROUS_ERRORS)
    # Unknown HTTP-ish method – fall back to safe defaults
    return list(_SAFE_ERRORS)


def _normalize_operation(op: Operation) -> Operation:
    """Return a copy of *op* with a guaranteed non-empty error_schema."""
    if _has_error_info(op):
        return op

    method = (op.method or "").upper()

    if method in _HTTP_METHODS:
        new_schema = ErrorSchema(responses=_infer_http_errors(method))
    else:
        new_schema = ErrorSchema(default_error_schema=dict(_GENERIC_DEFAULT_ERROR_SCHEMA))

    return op.model_copy(update={"error_schema": new_schema})


def normalize_error_schemas(ir: ServiceIR) -> ServiceIR:
    """Return a copy of the IR with normalized error schemas on all operations.

    Rules:
    1. If an operation already has error_schema.responses or default_error_schema,
       keep it unchanged (never overwrite extractor data).
    2. For HTTP operations (method is GET/POST/PUT/PATCH/DELETE) without error info,
       infer standard HTTP error responses based on the method and risk level.
    3. For non-HTTP operations without error info, add a generic default_error_schema.
    4. Every operation MUST leave with a non-empty error_schema.
    """
    new_ops = [_normalize_operation(op) for op in ir.operations]

    filled = sum(1 for old, new in zip(ir.operations, new_ops) if old is not new)
    if filled:
        logger.info(
            "Normalized error schemas for %d / %d operations in '%s'",
            filled,
            len(new_ops),
            ir.service_name,
        )

    return ir.model_copy(update={"operations": new_ops})
