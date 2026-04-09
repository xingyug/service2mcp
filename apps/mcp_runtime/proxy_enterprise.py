"""OData, SCIM, and JSON-RPC protocol proxy helpers."""

from __future__ import annotations

from typing import Any

import httpx

from apps.mcp_runtime.proxy_utils import PreparedRequestPayload, _parse_response_payload
from libs.ir.models import JsonRpcOperationConfig, Operation

# ---------------------------------------------------------------------------
# OData
# ---------------------------------------------------------------------------


def prepare_odata_payload(
    operation: Operation,
    remaining: dict[str, Any],
    *,
    split_query_and_body: Any,
) -> PreparedRequestPayload:
    """Prepare request payload for OData v4 operations.

    FastMCP strips the ``$`` prefix from OData system query parameters
    (e.g. ``$filter`` → ``filter``).  This method re-adds the prefix so
    the upstream OData service receives the correct query option names.
    """
    dollar_params: set[str] = set()
    for param in operation.params:
        if param.name.startswith("$"):
            dollar_params.add(param.name[1:])

    odata_query: dict[str, Any] = {}
    non_odata: dict[str, Any] = {}
    for key, value in remaining.items():
        if key in dollar_params:
            odata_query[f"${key}"] = value
        else:
            non_odata[key] = value

    query_params, json_body = split_query_and_body(operation, non_odata)
    query_params.update(odata_query)
    return PreparedRequestPayload(
        query_params=query_params,
        json_body=json_body,
        signable_body=json_body,
    )


def odata_error_message(
    response: httpx.Response,
    protocol: str,
) -> str | None:
    """Extract an OData JSON error message if present."""
    if protocol != "odata":
        return None
    payload = _parse_response_payload(response)
    if not isinstance(payload, dict):
        return None
    if "error" not in payload:
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return "OData response returned a malformed error envelope."
    code = error.get("code", "")
    message = error.get("message", "OData error")
    if isinstance(message, dict):
        message = message.get("value", str(message))
    return f"OData error ({code}): {message}" if code else f"OData error: {message}"


def _unwrap_odata_payload(payload: Any) -> Any:
    """Unwrap OData collection responses.

    OData services wrap collection results in ``{"value": [...]}`` with optional
    ``@odata.count`` and ``@odata.nextLink`` metadata.  This function extracts
    the ``value`` array while preserving single-entity responses as-is.
    """
    if not isinstance(payload, dict):
        return payload
    if "value" in payload and isinstance(payload["value"], list):
        result: dict[str, Any] = {"items": payload["value"]}
        count = payload.get("@odata.count")
        if count is not None:
            result["total_count"] = count
        next_link = payload.get("@odata.nextLink")
        if next_link is not None:
            result["next_link"] = next_link
        return result
    return payload


# ---------------------------------------------------------------------------
# SCIM
# ---------------------------------------------------------------------------


def scim_error_message(
    response: httpx.Response,
    protocol: str,
) -> str | None:
    """Extract a SCIM 2.0 error message if present."""
    if protocol != "scim":
        return None
    payload = _parse_response_payload(response)
    if not isinstance(payload, dict):
        return None
    schemas = payload.get("schemas")
    if not isinstance(schemas, list):
        if schemas is not None and (
            isinstance(schemas, str)
            or "detail" in payload
            or "status" in payload
            or "scimType" in payload
        ):
            return "SCIM response returned a malformed error envelope."
        return None
    if not any("Error" in s for s in schemas if isinstance(s, str)):
        return None
    detail = payload.get("detail", "SCIM error")
    status = payload.get("status", "")
    scim_type = payload.get("scimType", "")
    parts = ["SCIM error"]
    if status:
        parts[0] = f"SCIM error (status {status})"
    if scim_type:
        parts.append(scim_type)
    parts.append(str(detail))
    return ": ".join(parts)


def _unwrap_scim_payload(payload: Any) -> Any:
    """Unwrap SCIM 2.0 list responses.

    SCIM services wrap collection results in
    ``{"Resources": [...], "totalResults": N, "startIndex": N, "itemsPerPage": N}``.
    This function extracts the ``Resources`` array while preserving single-resource
    responses as-is.
    """
    if not isinstance(payload, dict):
        return payload
    resources = payload.get("Resources")
    if isinstance(resources, list):
        result: dict[str, Any] = {"items": resources}
        total = payload.get("totalResults")
        if total is not None:
            result["total_count"] = total
        start = payload.get("startIndex")
        if start is not None:
            result["start_index"] = start
        per_page = payload.get("itemsPerPage")
        if per_page is not None:
            result["items_per_page"] = per_page
        return result
    return payload


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------


def prepare_jsonrpc_payload(
    config: JsonRpcOperationConfig,
    remaining: dict[str, Any],
) -> PreparedRequestPayload:
    """Wrap tool arguments in a JSON-RPC 2.0 request envelope."""
    if config.params_type == "positional":
        params_list: list[Any] = []
        for name in config.params_names:
            if name not in remaining:
                continue
            value = remaining[name]
            if name == "payload":
                if isinstance(value, list):
                    params_list.extend(value)
                elif value is not None:
                    params_list.append(value)
                continue
            if value is not None:
                params_list.append(value)
        params: Any = params_list
    else:
        params_dict: dict[str, Any] = {
            name: remaining[name]
            for name in config.params_names
            if name in remaining and name != "payload"
        }
        payload_value = remaining.get("payload")
        if isinstance(payload_value, dict):
            params_dict.update(payload_value)
        elif payload_value is not None:
            params_dict["payload"] = payload_value
        params = params_dict or remaining
    json_body: dict[str, Any] = {
        "jsonrpc": config.jsonrpc_version,
        "method": config.method_name,
        "params": params,
        "id": 1,
    }
    return PreparedRequestPayload(
        query_params={},
        json_body=json_body,
        signable_body=json_body,
    )


def jsonrpc_error_message(
    response: httpx.Response,
    operation: Operation,
) -> str | None:
    """Extract a JSON-RPC 2.0 error message if present."""
    if operation.jsonrpc is None:
        return None
    payload = _parse_response_payload(response)
    if not isinstance(payload, dict):
        return None
    if "error" not in payload:
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return f"JSON-RPC operation {operation.id} returned a malformed error envelope."
    code = error.get("code", "")
    message = error.get("message", "JSON-RPC error")
    return f"JSON-RPC error ({code}): {message}" if code else f"JSON-RPC error: {message}"


def _unwrap_jsonrpc_payload(payload: Any, operation: Operation) -> Any:
    """Unwrap JSON-RPC 2.0 response envelope.

    Extracts ``result`` from ``{"jsonrpc": "2.0", "result": ..., "id": N}``.
    """
    if operation.jsonrpc is None:
        return payload
    if not isinstance(payload, dict):
        return payload
    if "result" in payload:
        return payload["result"]
    return payload
