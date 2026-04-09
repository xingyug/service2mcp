"""Shared utilities for the MCP runtime proxy modules."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from libs.ir.models import (
    AuthConfig,
    AuthType,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    OAuth2ClientCredentialsConfig,
    Operation,
    RequestSigningConfig,
    ResponseStrategy,
    ServiceIR,
    SqlOperationConfig,
    TruncationPolicy,
)
from libs.secret_refs import candidate_env_names, resolve_secret_ref

logger = logging.getLogger(__name__)

_PATH_PARAM_PATTERN = re.compile(r"{([^{}]+)}")
_WRITE_METHODS = {"POST", "PUT", "PATCH"}
_BODY_PARAM_NAMES = {"body", "payload", "data"}
_STREAM_MESSAGE_PARAM_NAMES = ("messages", "payload", "body", "data")
_TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/x-www-form-urlencoded",
)
_SOAP_ENVELOPE_NS = "http://schemas.xmlsoap.org/soap/envelope/"
_DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS = 15.0


class GrpcStreamExecutor(Protocol):
    """Dedicated native executor for grpc_stream event descriptors."""

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        descriptor: Any,
        config: GrpcStreamRuntimeConfig,
    ) -> dict[str, Any]:
        """Execute a native grpc_stream tool invocation."""
        ...


class GrpcUnaryExecutor(Protocol):
    """Dedicated native executor for grpc unary operations."""

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        config: GrpcUnaryRuntimeConfig,
    ) -> dict[str, Any]:
        """Execute a native grpc unary tool invocation."""
        ...


class SqlExecutor(Protocol):
    """Dedicated native executor for reflected SQL operations."""

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        config: SqlOperationConfig,
    ) -> dict[str, Any]:
        """Execute a native SQL tool invocation."""
        ...


@dataclass(slots=True)
class PreparedRequestPayload:
    """Normalized request payload emitted from IR tool arguments."""

    query_params: dict[str, Any]
    json_body: dict[str, Any] | list[Any] | None = None
    form_data: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str | None]] | None = None
    raw_body: bytes | str | None = None
    content_type: str | None = None
    signable_body: Any | None = None


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


def resolve_url(
    raw_path: str,
    arguments: dict[str, Any],
    service_ir: ServiceIR,
) -> tuple[str, set[str]]:
    path_argument_names = set(_PATH_PARAM_PATTERN.findall(raw_path))
    resolved_path = raw_path
    for path_argument in path_argument_names:
        value = arguments.get(path_argument)
        if value is None:
            raise ToolError(f"Missing path parameter {path_argument}.")
        resolved_path = resolved_path.replace(
            f"{{{path_argument}}}",
            quote(str(value), safe=""),
        )

    original_base_url = service_ir.base_url
    base_parts = urlsplit(original_base_url)
    resolved_parts = urlsplit(resolved_path if resolved_path else "/")
    if resolved_parts.path in {"", "/"}:
        return (
            urlunsplit(
                (
                    base_parts.scheme,
                    base_parts.netloc,
                    base_parts.path or "/",
                    resolved_parts.query or base_parts.query,
                    resolved_parts.fragment,
                )
            ),
            path_argument_names,
        )

    base_path = (base_parts.path or "").rstrip("/")
    path_suffix = (
        resolved_parts.path if resolved_parts.path.startswith("/") else f"/{resolved_parts.path}"
    )
    if base_parts.path == path_suffix and not resolved_parts.query and not resolved_parts.fragment:
        return original_base_url, path_argument_names
    return (
        urlunsplit(
            (
                base_parts.scheme,
                base_parts.netloc,
                f"{base_path}{path_suffix}",
                resolved_parts.query,
                resolved_parts.fragment,
            )
        ),
        path_argument_names,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def build_auth(
    operation_id: str,
    *,
    method: str,
    url: str,
    query_params: dict[str, Any],
    body_for_signing: Any | None,
    service_ir: ServiceIR,
    oauth_token_cache: dict[str, tuple[str, float | None]],
    oauth_lock: Any,
    get_client: Any,
    timeout: float,
) -> tuple[dict[str, str], dict[str, str]]:
    headers, query = await build_primary_auth(
        operation_id,
        auth=service_ir.auth,
        oauth_token_cache=oauth_token_cache,
        oauth_lock=oauth_lock,
        get_client=get_client,
        timeout=timeout,
    )
    signed_query = dict(query_params)
    signed_query.update(query)
    headers.update(
        build_request_signing(
            operation_id,
            method=method,
            url=url,
            query_params=signed_query,
            body_for_signing=body_for_signing,
            signing=service_ir.auth.request_signing,
        )
    )
    return headers, query


async def build_primary_auth(
    operation_id: str,
    *,
    auth: AuthConfig,
    oauth_token_cache: dict[str, tuple[str, float | None]],
    oauth_lock: Any,
    get_client: Any,
    timeout: float,
) -> tuple[dict[str, str], dict[str, str]]:
    if auth.type == AuthType.none:
        return {}, {}

    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}

    if auth.type == AuthType.oauth2 and auth.oauth2 is not None:
        access_token = await fetch_oauth2_access_token(
            operation_id,
            auth.oauth2,
            oauth_token_cache=oauth_token_cache,
            oauth_lock=oauth_lock,
            get_client=get_client,
            timeout=timeout,
        )
        header_name = auth.header_name or "Authorization"
        header_prefix = auth.header_prefix or "Bearer"
        headers[header_name] = f"{header_prefix} {access_token}".strip()
    elif auth.type in {AuthType.bearer, AuthType.oauth2}:
        secret = resolve_secret_value(auth, operation_id)
        header_name = auth.header_name or "Authorization"
        header_prefix = auth.header_prefix or "Bearer"
        headers[header_name] = f"{header_prefix} {secret}".strip()
    elif auth.type == AuthType.basic:
        if auth.basic_username and auth.basic_password_ref:
            password = resolve_secret_ref_for_operation(
                auth.basic_password_ref,
                operation_id,
                purpose="basic auth password",
            )
            secret = f"{auth.basic_username}:{password}"
        else:
            secret = resolve_secret_value(auth, operation_id)
        encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    elif auth.type == AuthType.api_key:
        secret = resolve_secret_value(auth, operation_id)
        api_key_name = auth.api_key_param or "api_key"
        if auth.api_key_location == "query":
            query_params[api_key_name] = secret
        else:
            headers[api_key_name] = secret
    elif auth.type == AuthType.custom_header:
        secret = resolve_secret_value(auth, operation_id)
        if not auth.header_name:
            raise ToolError(
                f"Operation {operation_id} uses custom_header auth without header_name."
            )
        if auth.header_prefix:
            headers[auth.header_name] = f"{auth.header_prefix} {secret}".strip()
        else:
            headers[auth.header_name] = secret
    else:
        raise ToolError(f"Unsupported auth type {auth.type} for operation {operation_id}.")

    return headers, query_params


async def fetch_oauth2_access_token(
    operation_id: str,
    oauth2: OAuth2ClientCredentialsConfig,
    *,
    oauth_token_cache: dict[str, tuple[str, float | None]],
    oauth_lock: Any,
    get_client: Any,
    timeout: float,
) -> str:
    cache_key = "\x00".join(
        [
            oauth2.token_url,
            oauth2.client_id or oauth2.client_id_ref or "",
            ",".join(sorted(oauth2.scopes)),
            oauth2.audience or "",
        ]
    )
    cached = oauth_token_cache.get(cache_key)
    now = time.time()
    if cached is not None:
        token, expires_at = cached
        if expires_at is None or expires_at > now + 30:
            return token

    async with oauth_lock:
        # Re-check after acquiring lock (another coroutine may have refreshed)
        cached = oauth_token_cache.get(cache_key)
        now = time.time()
        if cached is not None:
            token, expires_at = cached
            if expires_at is None or expires_at > now + 30:
                return token

        client_id = (
            oauth2.client_id
            if oauth2.client_id is not None
            else resolve_secret_ref_for_operation(
                oauth2.client_id_ref or "",
                operation_id,
                purpose="oauth2 client id",
            )
        )
        client_secret = resolve_secret_ref_for_operation(
            oauth2.client_secret_ref,
            operation_id,
            purpose="oauth2 client secret",
        )
        form_payload: dict[str, str] = {"grant_type": "client_credentials"}
        if oauth2.scopes:
            form_payload["scope"] = " ".join(oauth2.scopes)
        if oauth2.audience:
            form_payload["audience"] = oauth2.audience

        form_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if oauth2.client_auth_method == "client_secret_basic":
            encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
            form_headers["Authorization"] = f"Basic {encoded}"
        else:
            form_payload["client_id"] = client_id
            form_payload["client_secret"] = client_secret

        client = get_client()
        try:
            response = await client.post(
                oauth2.token_url,
                content=urlencode(form_payload),
                headers=form_headers,
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ToolError(
                f"OAuth2 token request timed out for {operation_id} (endpoint: {oauth2.token_url})."
            ) from exc
        except httpx.ConnectError as exc:
            raise ToolError(
                f"OAuth2 token endpoint unreachable for {operation_id} "
                f"(endpoint: {oauth2.token_url}): {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ToolError(
                f"OAuth2 token request failed for {operation_id} "
                f"with status {exc.response.status_code} "
                f"(endpoint: {oauth2.token_url})."
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolError(
                f"OAuth2 token endpoint returned non-JSON response for {operation_id}."
            ) from exc
        if not isinstance(payload, dict):
            raise ToolError(f"OAuth2 token endpoint returned non-object JSON for {operation_id}.")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ToolError(
                f"OAuth2 token endpoint did not return an access_token for {operation_id}."
            )

        expires_at = None
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, int | float) and expires_in >= 0:
            expires_at = now + float(expires_in)
        oauth_token_cache[cache_key] = (access_token, expires_at)
        return access_token


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------


def resolve_secret_value(auth: AuthConfig, operation_id: str) -> str:
    if not auth.runtime_secret_ref:
        raise ToolError(
            f"Operation {operation_id} requires auth but runtime_secret_ref is not configured."
        )

    return resolve_secret_ref_for_operation(
        auth.runtime_secret_ref,
        operation_id,
        purpose="runtime auth secret",
    )


def resolve_secret_ref_for_operation(secret_ref: str, operation_id: str, *, purpose: str) -> str:
    try:
        return resolve_secret_ref(
            secret_ref,
            purpose=purpose,
            context=f"operation {operation_id}",
        )
    except LookupError as exc:
        logger.warning("Secret resolution failed for %s/%s: %s", operation_id, purpose, exc)
        raise ToolError(
            f"Failed to resolve {purpose} secret for operation {operation_id}."
        ) from exc


# ---------------------------------------------------------------------------
# Request signing
# ---------------------------------------------------------------------------


def build_request_signing(
    operation_id: str,
    *,
    method: str,
    url: str,
    query_params: dict[str, Any],
    body_for_signing: Any | None,
    signing: RequestSigningConfig | None,
) -> dict[str, str]:
    if signing is None:
        return {}

    secret = resolve_secret_ref_for_operation(
        signing.secret_ref,
        operation_id,
        purpose="request signing secret",
    )
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        _build_signing_payload(
            method=method,
            url=url,
            query_params=query_params,
            body_for_signing=body_for_signing,
            timestamp=timestamp,
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        signing.signature_header_name: signature,
        signing.timestamp_header_name: timestamp,
    }
    if signing.key_id and signing.key_id_header_name:
        headers[signing.key_id_header_name] = signing.key_id
    return headers


def _build_signing_payload(
    *,
    method: str,
    url: str,
    query_params: dict[str, Any],
    body_for_signing: Any | None,
    timestamp: str,
) -> str:
    path = urlsplit(url).path or "/"
    normalized_query = urlencode(
        sorted((str(key), _normalize_query_value(value)) for key, value in query_params.items())
    )
    if body_for_signing is None:
        normalized_body = ""
    elif isinstance(body_for_signing, str):
        normalized_body = body_for_signing
    elif isinstance(body_for_signing, bytes):
        normalized_body = base64.b64encode(body_for_signing).decode("ascii")
    else:
        normalized_body = json.dumps(body_for_signing, ensure_ascii=True, separators=(",", ":"))
    return "\n".join([method.upper(), path, normalized_query, normalized_body, timestamp])


# ---------------------------------------------------------------------------
# HTTP sending
# ---------------------------------------------------------------------------


def build_request_kwargs(
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None,
    payload: PreparedRequestPayload | None,
    timeout: float,
) -> dict[str, Any]:
    request_headers = dict(headers)
    request_kwargs: dict[str, Any] = {
        "headers": request_headers,
        "params": params,
        "timeout": timeout,
    }
    if payload is None:
        return request_kwargs

    if payload.content_type and "Content-Type" not in request_headers:
        request_headers["Content-Type"] = payload.content_type
    if payload.json_body is not None:
        request_kwargs["json"] = payload.json_body
    elif payload.files is not None:
        request_kwargs["data"] = payload.form_data
        request_kwargs["files"] = payload.files
    elif payload.raw_body is not None:
        request_kwargs["content"] = payload.raw_body
    elif payload.form_data is not None:
        request_kwargs["data"] = payload.form_data
    return request_kwargs


async def send_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None,
    payload: PreparedRequestPayload | None = None,
    follow_redirects: bool = False,
    client: httpx.AsyncClient,
    timeout: float,
) -> httpx.Response:
    return await client.request(
        method,
        url,
        follow_redirects=follow_redirects,
        **build_request_kwargs(
            headers=headers,
            params=params,
            payload=payload,
            timeout=timeout,
        ),
    )


def get_client(
    *,
    existing_client: httpx.AsyncClient | None,
    service_ir: ServiceIR,
) -> httpx.AsyncClient:
    if existing_client is not None:
        return existing_client
    client_kwargs: dict[str, Any] = {}
    # Explicit connection pool limits to avoid exhaustion on large APIs
    client_kwargs["limits"] = httpx.Limits(
        max_connections=200,
        max_keepalive_connections=50,
    )
    mtls = service_ir.auth.mtls
    if mtls is not None:
        client_kwargs["cert"] = (
            resolve_secret_ref_for_operation(
                mtls.cert_ref,
                "__runtime__",
                purpose="mTLS client certificate",
            ),
            resolve_secret_ref_for_operation(
                mtls.key_ref,
                "__runtime__",
                purpose="mTLS client key",
            ),
        )
        if mtls.ca_ref:
            client_kwargs["verify"] = resolve_secret_ref_for_operation(
                mtls.ca_ref,
                "__runtime__",
                purpose="mTLS CA bundle",
            )
    return httpx.AsyncClient(**client_kwargs)


# ---------------------------------------------------------------------------
# Response parsing / query helpers
# ---------------------------------------------------------------------------


def _normalize_query_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


_MAX_BINARY_ENCODE_BYTES = 10 * 1024 * 1024  # 10 MB


def _parse_response_payload(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    normalized_content_type = content_type.lower()
    if "json" in normalized_content_type:
        try:
            parsed = response.json()
            return _sanitize_json_floats(parsed)
        except (ValueError, UnicodeDecodeError):
            return response.text
    if any(
        normalized_content_type.startswith(textual) or textual in normalized_content_type
        for textual in _TEXTUAL_CONTENT_TYPES
    ):
        return response.text
    raw = response.content
    if len(raw) > _MAX_BINARY_ENCODE_BYTES:
        return {
            "binary": True,
            "content_type": content_type or "application/octet-stream",
            "truncated": True,
            "size_bytes": len(raw),
            "message": (
                f"Binary response too large to encode ({len(raw)} bytes, "
                f"limit {_MAX_BINARY_ENCODE_BYTES} bytes)."
            ),
        }
    return {
        "binary": True,
        "content_type": content_type or "application/octet-stream",
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "size_bytes": len(raw),
    }


def _sanitize_json_floats(value: Any) -> Any:
    """Replace non-finite floats (NaN, Inf, -Inf) with string representations."""
    if isinstance(value, float) and (value != value or value == math.inf or value == -math.inf):
        return str(value)
    if isinstance(value, dict):
        return {k: _sanitize_json_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_floats(item) for item in value]
    return value


def _parse_stream_payload(payload: str) -> Any:
    stripped = payload.strip()
    if not stripped:
        return payload
    if stripped[0] not in {"{", "["}:
        return payload
    try:
        parsed = json.loads(stripped)
        return _sanitize_json_floats(parsed)
    except json.JSONDecodeError:
        return payload


def _candidate_env_names(secret_ref: str) -> list[str]:
    return candidate_env_names(secret_ref)


def _to_websocket_url(url: str, query_params: dict[str, Any]) -> str:
    parts = urlsplit(url)
    scheme = "wss" if parts.scheme in ("https", "wss") else "ws"
    normalized_query = urlencode(
        sorted((str(key), _normalize_query_value(value)) for key, value in query_params.items())
    )
    return urlunsplit((scheme, parts.netloc, parts.path, normalized_query, ""))


def _split_url_query(url: str) -> tuple[str, dict[str, str]]:
    from urllib.parse import parse_qsl

    parsed = urlsplit(url)
    base_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
    return base_url, dict(parse_qsl(parsed.query, keep_blank_values=True))


def _is_same_origin(base_url: str, resolved_url: str) -> bool:
    """Check that resolved_url shares the same scheme+host+port as base_url."""
    base = urlsplit(base_url)
    resolved = urlsplit(resolved_url)
    return base.scheme == resolved.scheme and base.netloc == resolved.netloc


def _extract_nested_value(payload: Any, dotted_path: str) -> Any | None:
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


# ---------------------------------------------------------------------------
# Field filtering / truncation
# ---------------------------------------------------------------------------


def _split_escaped_dot_path(path: str) -> list[str]:
    r"""Split a dot-delimited path, treating ``\.`` as a literal dot.

    >>> _split_escaped_dot_path(r"Address\.City")
    ['Address.City']
    >>> _split_escaped_dot_path("user.name")
    ['user', 'name']
    """
    segments: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(path):
        if path[i] == "\\" and i + 1 < len(path) and path[i + 1] == ".":
            current.append(".")
            i += 2
        elif path[i] == ".":
            segments.append("".join(current))
            current = []
            i += 1
        else:
            current.append(path[i])
            i += 1
    segments.append("".join(current))
    return segments


def _has_unescaped_dot(path: str) -> bool:
    r"""Return True if *path* contains at least one unescaped dot."""
    i = 0
    while i < len(path):
        if path[i] == "\\" and i + 1 < len(path) and path[i + 1] == ".":
            i += 2
        elif path[i] == ".":
            return True
        else:
            i += 1
    return False


def _apply_field_filter(payload: Any, field_filter: list[str] | None) -> Any:
    r"""Filter response fields by allowlist.

    Supports three path styles:
    - ``"name"`` — top-level key
    - ``"user.name"`` — nested key via dot notation
    - ``"items[].id"`` — key inside each element of a top-level array

    Use ``\.`` to represent a literal dot inside a field name
    (e.g. ``r"Address\.City"`` selects the key ``Address.City``).
    """
    if not field_filter:
        return payload

    # Separate plain top-level keys from dot/bracket paths.
    top_keys: set[str] = set()
    nested_paths: list[tuple[str, list[str]]] = []  # (root, remaining segments)
    array_paths: dict[str, list[str]] = {}  # root[] -> inner field names

    for path in field_filter:
        if "[]." in path:
            root, rest = path.split("[].", 1)
            array_paths.setdefault(root, []).append(rest)
        elif _has_unescaped_dot(path):
            segments = _split_escaped_dot_path(path)
            root = segments[0]
            nested_paths.append((root, segments[1:]))
        else:
            # Either a plain key or a key that only contains escaped dots.
            top_keys.add(_split_escaped_dot_path(path)[0])

    if isinstance(payload, dict):
        return _filter_dict(payload, top_keys, nested_paths, array_paths)

    if isinstance(payload, list):
        # When the payload itself is a list, apply filters to each dict item.
        all_inner_fields = top_keys | {p for paths in array_paths.values() for p in paths}
        if not nested_paths and not array_paths:
            # Simple flat filter on list items.
            return [
                {k: v for k, v in item.items() if k in top_keys} if isinstance(item, dict) else item
                for item in payload
            ]
        return [
            _filter_dict(item, all_inner_fields, nested_paths, array_paths)
            if isinstance(item, dict)
            else item
            for item in payload
        ]

    return payload


def _filter_dict(
    d: dict[str, Any],
    top_keys: set[str],
    nested_paths: list[tuple[str, list[str]]],
    array_paths: dict[str, list[str]],
) -> dict[str, Any]:
    """Build a filtered copy of *d* keeping only requested paths."""
    result: dict[str, Any] = {}

    # Top-level keys.
    for key in top_keys:
        if key in d:
            result[key] = d[key]

    # Nested dot-paths — drill into sub-dicts.
    for root, segments in nested_paths:
        if root not in d:
            continue
        _set_nested(result, root, segments, d[root])

    # Array bracket paths — filter items inside a top-level list field.
    for root, inner_fields in array_paths.items():
        if root not in d:
            continue
        value = d[root]
        if isinstance(value, list):
            inner_set = set(inner_fields)
            result[root] = [
                {k: v for k, v in item.items() if k in inner_set}
                if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            result[root] = value

    return result


def _set_nested(
    target: dict[str, Any],
    root: str,
    segments: list[str],
    source: Any,
) -> None:
    """Copy a nested value from *source* into *target[root]* following *segments*."""
    if not segments:
        return
    node = source
    for seg in segments:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return  # path not present — skip silently

    # Build / merge nested structure in *target*.
    cur = target
    if root not in cur:
        cur[root] = {}
    cur = cur[root]
    for seg in segments[:-1]:
        if not isinstance(cur, dict):
            return
        if seg not in cur:
            cur[seg] = {}
        cur = cur[seg]
    if isinstance(cur, dict):
        cur[segments[-1]] = node


def _apply_array_limit(payload: Any, max_items: int | None) -> Any:
    """Truncate top-level list payloads (or list values inside a dict) to *max_items*."""
    if max_items is None:
        return payload
    if isinstance(payload, list):
        return payload[:max_items]
    if isinstance(payload, dict):
        return {k: v[:max_items] if isinstance(v, list) else v for k, v in payload.items()}
    return payload


def _truncate_utf8_prefix(payload_bytes: bytes, max_bytes: int) -> tuple[str, bool]:
    """Return the largest valid UTF-8 prefix not exceeding ``max_bytes`` bytes."""

    candidate = payload_bytes[:max_bytes]
    try:
        return candidate.decode("utf-8"), False
    except UnicodeDecodeError:
        end = len(candidate)
        while end > 0:
            try:
                return candidate[:end].decode("utf-8"), True
            except UnicodeDecodeError:
                end -= 1
        return "", True


def _apply_truncation(payload: Any, strategy: ResponseStrategy) -> tuple[Any, bool]:
    if strategy.max_response_bytes is None or strategy.max_response_bytes <= 0:
        return payload, False

    serialized = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=True)
    payload_bytes = serialized.encode("utf-8")
    if len(payload_bytes) <= strategy.max_response_bytes:
        return payload, False

    if strategy.truncation_policy == TruncationPolicy.none:
        return payload, False

    truncated, utf8_boundary_trimmed = _truncate_utf8_prefix(
        payload_bytes,
        strategy.max_response_bytes,
    )
    result = {
        "content": truncated,
        "original_type": type(payload).__name__,
        "truncated": True,
    }
    if utf8_boundary_trimmed:
        result["utf8_boundary_trimmed"] = True
    return result, True


# ---------------------------------------------------------------------------
# Response sanitization
# ---------------------------------------------------------------------------


def sanitize_response(
    response: httpx.Response,
    operation: Operation,
    *,
    protocol: str = "",
) -> tuple[Any, bool]:
    from apps.mcp_runtime.proxy_enterprise import (
        _unwrap_jsonrpc_payload,
        _unwrap_odata_payload,
        _unwrap_scim_payload,
    )
    from apps.mcp_runtime.proxy_graphql import _unwrap_graphql_payload
    from apps.mcp_runtime.proxy_soap import _unwrap_soap_payload

    payload = _parse_response_payload(response)
    if operation.soap is not None:
        payload = _unwrap_soap_payload(response, operation)
    if operation.graphql is not None:
        payload = _unwrap_graphql_payload(payload, operation)
    if protocol == "odata":
        payload = _unwrap_odata_payload(payload)
    if protocol == "scim":
        payload = _unwrap_scim_payload(payload)
    if operation.jsonrpc is not None:
        payload = _unwrap_jsonrpc_payload(payload, operation)
    payload = _apply_field_filter(payload, operation.response_strategy.field_filter)
    payload = _apply_array_limit(payload, operation.response_strategy.max_array_items)
    return _apply_truncation(payload, operation.response_strategy)


# ---------------------------------------------------------------------------
# Observability micro-helpers (used by proxy.py dispatcher)
# ---------------------------------------------------------------------------


def obs_success(
    breaker: Any,
    obs: Any,
    oid: str,
) -> None:
    breaker.record_success()
    obs.record_tool_call(oid, "success")
    obs.set_circuit_breaker_state(oid, False)


def obs_fail(
    breaker: Any,
    obs: Any,
    oid: str,
    kind: str,
    span: Any,
    exc: Exception,
    label: str,
) -> None:
    breaker.record_failure()
    obs.record_tool_call(oid, "error")
    obs.record_upstream_error(oid, kind)
    obs.set_circuit_breaker_state(oid, breaker.is_open)
    span.record_exception(exc)
    obs.logger.warning(
        f"runtime tool invocation {label}",
        extra={"extra_fields": {"operation_id": oid}},
    )


def proto_fail(
    breaker: Any,
    span: Any,
    obs: Any,
    oid: str,
    kind: str,
    label: str,
    msg: str,
) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    breaker.record_failure()
    obs.record_tool_call(oid, "error")
    obs.record_upstream_error(oid, kind)
    obs.set_circuit_breaker_state(oid, breaker.is_open)
    te = ToolError(msg)
    span.record_exception(te)
    obs.logger.warning(
        f"runtime {label} tool invocation failed",
        extra={"extra_fields": {"operation_id": oid}},
    )
    raise te


def check_protocol_errors(
    response: Any,
    operation: Any,
    breaker: Any,
    span: Any,
    obs: Any,
    protocol: str,
) -> None:
    """Check response for protocol-specific errors; raise ToolError on match."""
    from mcp.server.fastmcp.exceptions import ToolError

    from apps.mcp_runtime.proxy_enterprise import (
        jsonrpc_error_message,
        odata_error_message,
        scim_error_message,
    )
    from apps.mcp_runtime.proxy_graphql import graphql_error_message
    from apps.mcp_runtime.proxy_soap import soap_fault_message

    oid = operation.id
    sf = soap_fault_message(response, operation)
    if sf is not None:
        proto_fail(breaker, span, obs, oid, "soap_fault", "soap", sf)

    # Check protocol-specific errors BEFORE the generic HTTP status check so
    # that structured error bodies (OData, SCIM, JSON-RPC, GraphQL) are
    # surfaced even when the upstream returns an HTTP 4xx/5xx status code.
    protocol_errors: list[tuple[str | None, str, str]] = [
        (graphql_error_message(response, operation), "graphql_error", "graphql"),
        (odata_error_message(response, protocol), "odata_error", "odata"),
        (jsonrpc_error_message(response, operation), "jsonrpc_error", "jsonrpc"),
        (scim_error_message(response, protocol), "scim_error", "scim"),
    ]
    for proto_msg, kind, lbl in protocol_errors:
        if proto_msg is not None:
            proto_fail(breaker, span, obs, oid, kind, lbl, proto_msg)

    if response.is_error:
        msg = f"Upstream request failed for {oid} with status {response.status_code}."
        breaker.record_failure()
        obs.record_tool_call(oid, "error")
        obs.record_upstream_error(oid, "upstream_status")
        obs.set_circuit_breaker_state(oid, breaker.is_open)
        te = ToolError(msg)
        span.record_exception(te)
        obs.logger.warning(
            "runtime tool invocation failed",
            extra={
                "extra_fields": {
                    "operation_id": oid,
                    "status_code": response.status_code,
                }
            },
        )
        raise te
