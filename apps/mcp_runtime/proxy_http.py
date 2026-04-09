"""HTTP/REST proxy helpers — request preparation, body builders, async polling."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any
from urllib.parse import urljoin

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.proxy_enterprise import prepare_jsonrpc_payload, prepare_odata_payload
from apps.mcp_runtime.proxy_graphql import prepare_graphql_payload
from apps.mcp_runtime.proxy_soap import prepare_soap_payload
from apps.mcp_runtime.proxy_utils import (
    _BODY_PARAM_NAMES,
    _WRITE_METHODS,
    PreparedRequestPayload,
    _is_same_origin,
    _split_url_query,
    build_auth,
    resolve_url,
    send_request,
)
from libs.ir.models import AsyncJobConfig, Operation, RequestBodyMode, ServiceIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request payload preparation (dispatch to protocol)
# ---------------------------------------------------------------------------


def prepare_request_payload(
    operation: Operation,
    arguments: dict[str, Any],
    *,
    path_argument_names: set[str],
    service_ir: ServiceIR,
    split_query_and_body: Any,
) -> PreparedRequestPayload:
    remaining = {
        key: value
        for key, value in arguments.items()
        if key not in path_argument_names and value is not None
    }
    if operation.soap is not None:
        return prepare_soap_payload(operation, remaining)
    if operation.graphql is not None:
        return prepare_graphql_payload(operation, remaining)
    if operation.jsonrpc is not None:
        return prepare_jsonrpc_payload(operation.jsonrpc, remaining)
    if service_ir.protocol == "odata":
        return prepare_odata_payload(
            operation, remaining, split_query_and_body=split_query_and_body
        )

    if not remaining:
        return PreparedRequestPayload(query_params={})

    if operation.request_body_mode == RequestBodyMode.multipart:
        body_key = select_body_argument_name(operation, remaining)
        body_value = remaining[body_key]
        query_params = {key: value for key, value in remaining.items() if key != body_key}
        form_data, files, signable_body = _build_multipart_request_body(
            operation.id,
            body_value,
        )
        return PreparedRequestPayload(
            query_params=query_params,
            form_data=form_data or None,
            files=files or None,
            signable_body=signable_body,
        )

    if operation.request_body_mode == RequestBodyMode.raw:
        body_key = select_body_argument_name(operation, remaining)
        body_value = remaining[body_key]
        query_params = {key: value for key, value in remaining.items() if key != body_key}
        raw_body, content_type, signable_body = _build_raw_request_body(
            operation.id,
            body_value,
        )
        return PreparedRequestPayload(
            query_params=query_params,
            raw_body=raw_body,
            content_type=content_type,
            signable_body=signable_body,
        )

    query_params, json_body = split_query_and_body(operation, remaining)
    return PreparedRequestPayload(
        query_params=query_params,
        json_body=json_body,
        signable_body=json_body,
    )


# ---------------------------------------------------------------------------
# Query / body splitting
# ---------------------------------------------------------------------------


def split_query_and_body(
    operation: Operation,
    remaining: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | list[Any] | None]:
    if not remaining:
        return {}, None

    if (operation.method or "").upper() in _WRITE_METHODS:
        if operation.body_param_name and operation.body_param_name in remaining:
            body = remaining[operation.body_param_name]
            query_params = {
                key: value for key, value in remaining.items() if key != operation.body_param_name
            }
            return query_params, body
        if len(remaining) == 1:
            key, value = next(iter(remaining.items()))
            param = next((param for param in operation.params if param.name == key), None)
            is_object_like = param is not None and param.type in {"object", "array"}
            if key in _BODY_PARAM_NAMES or is_object_like:
                return {}, value
        return {}, remaining

    return remaining, None


def select_body_argument_name(
    operation: Operation,
    remaining: dict[str, Any],
) -> str:
    if operation.body_param_name:
        if operation.body_param_name not in remaining:
            raise ToolError(
                f"Operation {operation.id} expects body parameter {operation.body_param_name!r}."
            )
        return operation.body_param_name

    for candidate in _BODY_PARAM_NAMES:
        if candidate in remaining:
            return candidate

    object_like_keys = [
        param.name
        for param in operation.params
        if param.name in remaining and param.type in {"object", "array"}
    ]
    if len(object_like_keys) == 1:
        return object_like_keys[0]

    if len(remaining) == 1:
        return next(iter(remaining))

    raise ToolError(
        f"Operation {operation.id} could not determine which argument should be "
        f"used as the {operation.request_body_mode.value} request body."
    )


# ---------------------------------------------------------------------------
# Perform HTTP request
# ---------------------------------------------------------------------------


async def perform_request(
    operation: Operation,
    arguments: dict[str, Any],
    *,
    service_ir: ServiceIR,
    oauth_token_cache: dict[str, tuple[str, float | None]],
    oauth_lock: Any,
    get_client: Any,
    timeout: float,
) -> httpx.Response:
    if not operation.method or not operation.path:
        raise ToolError(f"Operation {operation.id} is missing method or path metadata.")

    url, path_arguments = resolve_url(operation.path, arguments, service_ir)
    payload = prepare_request_payload(
        operation,
        arguments,
        path_argument_names=path_arguments,
        service_ir=service_ir,
        split_query_and_body=split_query_and_body,
    )
    headers, auth_query_params = await build_auth(
        operation.id,
        method=operation.method.upper(),
        url=url,
        query_params=payload.query_params,
        body_for_signing=payload.signable_body,
        service_ir=service_ir,
        oauth_token_cache=oauth_token_cache,
        oauth_lock=oauth_lock,
        get_client=get_client,
        timeout=timeout,
    )
    if operation.soap is not None:
        headers.setdefault("Accept", "text/xml, application/xml")
        if operation.soap.soap_action:
            headers.setdefault("SOAPAction", f'"{operation.soap.soap_action}"')
    query_params = dict(payload.query_params)
    query_params.update(auth_query_params)

    response = await send_request(
        operation.method.upper(),
        url,
        headers=headers,
        params=query_params or None,
        payload=payload,
        client=get_client(),
        timeout=timeout,
    )
    if operation.async_job is not None:
        return await poll_async_job(
            operation.id,
            response,
            operation.async_job,
            service_ir=service_ir,
            oauth_token_cache=oauth_token_cache,
            oauth_lock=oauth_lock,
            get_client=get_client,
            timeout=timeout,
        )
    return response


# ---------------------------------------------------------------------------
# Multipart / raw body builders
# ---------------------------------------------------------------------------


def _build_multipart_request_body(
    operation_id: str,
    value: Any,
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str | None]], dict[str, Any]]:
    if not isinstance(value, dict):
        raise ToolError(
            f"Multipart operation {operation_id} requires an object payload with form/files."
        )

    raw_form = value.get("form", {})
    raw_files = value.get("files", {})
    if not isinstance(raw_form, dict):
        raise ToolError(f"Multipart form payload must be an object for operation {operation_id}.")
    if not isinstance(raw_files, dict):
        raise ToolError(f"Multipart files payload must be an object for operation {operation_id}.")

    form_data = {
        str(key): _normalize_form_value(field_value) for key, field_value in raw_form.items()
    }
    files: dict[str, tuple[str, bytes, str | None]] = {}
    signable_files: dict[str, dict[str, str]] = {}
    for field_name, file_value in raw_files.items():
        filename, content, content_type = _normalize_multipart_file_part(
            operation_id,
            field_name,
            file_value,
        )
        files[str(field_name)] = (filename, content, content_type)
        signable_files[str(field_name)] = {
            "filename": filename,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "content_type": content_type or "application/octet-stream",
        }

    return form_data, files, {"form": form_data, "files": signable_files}


def _normalize_multipart_file_part(
    operation_id: str,
    field_name: str,
    value: Any,
) -> tuple[str, bytes, str | None]:
    if isinstance(value, str):
        return field_name, value.encode("utf-8"), "text/plain"

    if not isinstance(value, dict):
        raise ToolError(
            f"Multipart file field {field_name!r} must be a string or object "
            f"for operation {operation_id}."
        )

    filename = value.get("filename")
    if filename is None:
        filename = field_name
    if not isinstance(filename, str) or not filename:
        raise ToolError(
            f"Multipart file field {field_name!r} has an invalid filename "
            f"for operation {operation_id}."
        )

    content_type = value.get("content_type")
    if content_type is not None and not isinstance(content_type, str):
        raise ToolError(
            f"Multipart file field {field_name!r} has an invalid content_type "
            f"for operation {operation_id}."
        )

    if "content_base64" in value:
        encoded = value["content_base64"]
        if not isinstance(encoded, str):
            raise ToolError(
                f"Multipart file field {field_name!r} has a non-string content_base64 "
                f"for operation {operation_id}."
            )
        try:
            decoded_content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ToolError(
                f"Multipart file field {field_name!r} contains invalid base64 "
                f"for operation {operation_id}."
            ) from exc
        return filename, decoded_content, content_type

    content = value.get("content")
    if not isinstance(content, str):
        raise ToolError(
            f"Multipart file field {field_name!r} requires string content or content_base64 "
            f"for operation {operation_id}."
        )
    return filename, content.encode("utf-8"), content_type or "text/plain"


def _build_raw_request_body(
    operation_id: str,
    value: Any,
) -> tuple[bytes | str, str | None, Any]:
    if isinstance(value, str):
        return value, None, value

    if not isinstance(value, dict):
        raise ToolError(f"Raw-body operation {operation_id} requires a string or object payload.")

    content_type = value.get("content_type")
    if content_type is not None and not isinstance(content_type, str):
        raise ToolError(f"Raw-body operation {operation_id} received a non-string content_type.")

    if "content_base64" in value:
        encoded = value["content_base64"]
        if not isinstance(encoded, str):
            raise ToolError(
                f"Raw-body operation {operation_id} received a non-string content_base64."
            )
        try:
            decoded_content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ToolError(
                f"Raw-body operation {operation_id} received invalid base64 content."
            ) from exc
        return (
            decoded_content,
            content_type,
            {"content_base64": encoded, "content_type": content_type},
        )

    content = value.get("content")
    if not isinstance(content, str):
        raise ToolError(
            f"Raw-body operation {operation_id} requires string content or content_base64."
        )
    return content, content_type, {"content": content, "content_type": content_type}


def _normalize_form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return str(value)


# ---------------------------------------------------------------------------
# Async job polling
# ---------------------------------------------------------------------------


async def poll_async_job(
    operation_id: str,
    response: httpx.Response,
    async_job: AsyncJobConfig,
    *,
    service_ir: ServiceIR,
    oauth_token_cache: dict[str, tuple[str, float | None]],
    oauth_lock: Any,
    get_client: Any,
    timeout: float,
    send_fn: Any | None = None,
) -> httpx.Response:
    if response.status_code not in async_job.initial_status_codes:
        return response

    try:
        status_url = _extract_async_status_url(async_job, response)
    except _InvalidJsonPayloadError as exc:
        await response.aclose()
        raise ToolError(
            f"Async kickoff for operation {operation_id} received invalid JSON: {exc}"
        ) from exc
    if not status_url:
        await response.aclose()
        raise ToolError(
            f"Async job operation {operation_id} did not provide a pollable status URL."
        )

    # Initial response has been consumed; close it before polling.
    await response.aclose()

    deadline = time.monotonic() + async_job.timeout_seconds
    pending_states = {value.lower() for value in async_job.pending_status_values}
    success_states = {value.lower() for value in async_job.success_status_values}
    failure_states = {value.lower() for value in async_job.failure_status_values}

    async def _default_send(**kw: Any) -> httpx.Response:
        remaining = max(deadline - time.monotonic(), 1.0)
        effective_timeout = min(timeout, remaining)
        return await send_request(**kw, client=get_client(), timeout=effective_timeout)

    _do_send = send_fn if send_fn is not None else _default_send

    while True:
        if time.monotonic() > deadline:
            raise ToolError(f"Async job polling timed out for operation {operation_id}.")

        request_url, query_params = _split_url_query(status_url)
        headers, auth_query_params = await build_auth(
            operation_id,
            method="GET",
            url=request_url,
            query_params=query_params,
            body_for_signing=None,
            service_ir=service_ir,
            oauth_token_cache=oauth_token_cache,
            oauth_lock=oauth_lock,
            get_client=get_client,
            timeout=timeout,
        )
        query_params.update(auth_query_params)
        poll_response = await _do_send(
            method="GET",
            url=request_url,
            headers=headers,
            params=query_params or None,
            follow_redirects=True,
        )

        try:
            status_value = _extract_async_status_value(async_job, poll_response)
        except _InvalidJsonPayloadError as exc:
            await poll_response.aclose()
            raise ToolError(
                f"Async poll for operation {operation_id} received invalid JSON "
                f"(HTTP {poll_response.status_code})."
            ) from exc
        normalized_status = status_value.lower() if status_value is not None else None
        if normalized_status in success_states:
            return poll_response
        if normalized_status in failure_states:
            await poll_response.aclose()
            raise ToolError(
                f"Async job polling failed for operation {operation_id} "
                f"with terminal status {status_value!r}."
            )

        if (
            poll_response.status_code in async_job.initial_status_codes
            or normalized_status in pending_states
        ):
            await poll_response.aclose()
            await asyncio.sleep(async_job.poll_interval_seconds)
            continue

        return poll_response


# ---------------------------------------------------------------------------
# Async job helpers
# ---------------------------------------------------------------------------


def _extract_async_status_url(async_job: AsyncJobConfig, response: httpx.Response) -> str | None:
    if async_job.status_url_source == "location_header":
        location_value = response.headers.get("Location") or response.headers.get(
            "Content-Location"
        )
        if not isinstance(location_value, str) or not location_value:
            return None
        request_url = str(response.request.url) if response.request is not None else ""
        resolved = urljoin(request_url, location_value)
        if request_url and not _is_same_origin(request_url, resolved):
            logger.warning(
                "Async poll URL %r resolved to different origin than request %r; "
                "blocking potential SSRF",
                resolved,
                request_url,
            )
            return None
        return resolved

    payload = _maybe_parse_json_payload(response)
    if payload is None or async_job.status_url_field is None:
        return None
    url_value = _extract_nested_value(payload, async_job.status_url_field)
    if not isinstance(url_value, str) or not url_value:
        return None
    request_url = str(response.request.url) if response.request is not None else ""
    resolved = urljoin(request_url, url_value)
    if request_url and not _is_same_origin(request_url, resolved):
        logger.warning(
            "Async poll URL %r resolved to different origin than request %r; "
            "blocking potential SSRF",
            resolved,
            request_url,
        )
        return None
    return resolved


def _extract_async_status_value(async_job: AsyncJobConfig, response: httpx.Response) -> str | None:
    payload = _maybe_parse_json_payload(response)
    if payload is None:
        return None
    status_value = _extract_nested_value(payload, async_job.status_field)
    if isinstance(status_value, str):
        return status_value
    return None


class _InvalidJsonPayloadError(Exception):
    """Raised when a response claims JSON content-type but the body is not valid JSON."""


def _maybe_parse_json_payload(response: httpx.Response) -> Any | None:
    """Parse a JSON response body.

    Returns ``None`` when the content-type is not JSON.
    Raises :class:`_InvalidJsonPayloadError` when the content-type indicates JSON
    but the body cannot be decoded.
    """
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise _InvalidJsonPayloadError(
            f"Response declared content-type {content_type!r} "
            f"but body is not valid JSON (HTTP {response.status_code})"
        ) from exc


def _extract_nested_value(payload: Any, dotted_path: str) -> Any | None:
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
