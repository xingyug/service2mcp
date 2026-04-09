"""GraphQL protocol proxy helpers."""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.proxy_utils import PreparedRequestPayload, _parse_response_payload
from libs.ir.models import Operation


def prepare_graphql_payload(
    operation: Operation,
    remaining: dict[str, Any],
) -> PreparedRequestPayload:
    if operation.graphql is None:
        raise ToolError(f"Operation {operation.id} is missing GraphQL runtime metadata.")

    variable_names = (
        operation.graphql.variable_names
        if operation.graphql.variable_names
        else list(remaining.keys())
    )
    variables = {
        variable_name: remaining[variable_name]
        for variable_name in variable_names
        if variable_name in remaining
    }
    json_body: dict[str, Any] = {
        "query": operation.graphql.document,
        "variables": variables,
    }
    if operation.graphql.operation_name is not None:
        json_body["operationName"] = operation.graphql.operation_name
    return PreparedRequestPayload(
        query_params={},
        json_body=json_body,
        signable_body=json_body,
    )


def graphql_error_message(
    response: httpx.Response,
    operation: Operation,
) -> str | None:
    if operation.graphql is None:
        return None

    payload = _parse_response_payload(response)
    if not isinstance(payload, dict):
        return f"GraphQL operation {operation.id} returned a non-object response body."

    raw_errors = payload.get("errors")
    if not isinstance(raw_errors, list) or not raw_errors:
        return None

    messages = [
        error.get("message", "unknown GraphQL error") if isinstance(error, dict) else str(error)
        for error in raw_errors
    ]
    return f"GraphQL operation {operation.id} failed: {'; '.join(messages)}"


def _unwrap_graphql_payload(payload: Any, operation: Operation) -> Any:
    if operation.graphql is None:
        return payload
    if not isinstance(payload, dict):
        raise ToolError(f"GraphQL operation {operation.id} returned a non-object response body.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ToolError(f"GraphQL operation {operation.id} returned no data object.")
    return data.get(operation.graphql.operation_name, data)
