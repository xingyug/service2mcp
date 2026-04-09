"""OpenAPI extractor — parses Swagger 2.0, OpenAPI 3.0, and 3.1 specs into ServiceIR."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import httpx
import yaml

from libs.extractors.base import SourceConfig
from libs.extractors.utils import (
    compute_content_hash,
    get_auth_headers,
    get_content,
)
from libs.extractors.utils import (
    slugify as _slugify,
)
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorResponse,
    ErrorSchema,
    EventDescriptor,
    EventDirection,
    EventSupportLevel,
    EventTransport,
    Operation,
    PaginationConfig,
    Param,
    RequestBodyMode,
    ResponseExample,
    ResponseStrategy,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

logger = logging.getLogger(__name__)
JSONDict = dict[str, Any]

# HTTP method → risk level mapping
_logger = logging.getLogger(__name__)

_METHOD_RISK: dict[str, RiskLevel] = {
    "GET": RiskLevel.safe,
    "HEAD": RiskLevel.safe,
    "OPTIONS": RiskLevel.safe,
    "POST": RiskLevel.cautious,
    "PUT": RiskLevel.cautious,
    "PATCH": RiskLevel.cautious,
    "DELETE": RiskLevel.dangerous,
}

# JSON Schema type mapping from OpenAPI types
_TYPE_MAP: dict[str, str] = {
    "integer": "integer",
    "number": "number",
    "string": "string",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
    "file": "string",
}
_PATH_TEMPLATE_PARAM_PATTERN = re.compile(r"{([^{}]+)}")

_JSON_SCHEMA_MAX_DEPTH = 5
_MAX_COMPOSITION_DEPTH = 10


def _extract_nested_json_schema(prop: JSONDict, *, _depth: int = 0) -> dict[str, Any] | None:
    """Build a JSON Schema dict for a property with nested structure.

    Returns ``None`` when the property is a scalar or has no sub-properties,
    meaning the standard ``type`` string is sufficient.
    """
    if _depth > _JSON_SCHEMA_MAX_DEPTH:
        return None

    prop_type = prop.get("type", "")

    # Object with sub-properties
    if prop_type == "object" and "properties" in prop:
        sub_props = prop["properties"]
        if not isinstance(sub_props, dict):
            return None
        js: dict[str, Any] = {"type": "object", "properties": {}}
        for k, v in sub_props.items():
            if not isinstance(v, dict):
                continue
            child = _extract_nested_json_schema(v, _depth=_depth + 1)
            if child is not None:
                js["properties"][k] = child
            else:
                child_type = _TYPE_MAP.get(v.get("type", "string"), "string")
                entry: dict[str, Any] = {"type": child_type}
                if v.get("description"):
                    entry["description"] = str(v["description"])
                if "enum" in v:
                    entry["enum"] = v["enum"]
                js["properties"][k] = entry
        sub_required = prop.get("required")
        if isinstance(sub_required, list) and sub_required:
            js["required"] = sub_required
        return js

    # Array with structured items
    if prop_type == "array" and "items" in prop:
        items = prop["items"]
        if isinstance(items, dict):
            items_schema = _extract_nested_json_schema(items, _depth=_depth + 1)
            if items_schema is not None:
                return {"type": "array", "items": items_schema}
            # Scalar items — still emit json_schema so loader knows item type
            item_type = _TYPE_MAP.get(items.get("type", "string"), "string")
            item_entry: dict[str, Any] = {"type": item_type}
            if items.get("enum"):
                item_entry["enum"] = items["enum"]
            return {"type": "array", "items": item_entry}

    return None


class OpenAPIExtractor:
    """Extracts ServiceIR from OpenAPI / Swagger specs."""

    protocol_name: str = "openapi"

    def __init__(self) -> None:
        self._external_ref_cache: dict[str, JSONDict] = {}
        self._external_doc_cache: dict[str, JSONDict] = {}
        self._resolving_external_refs: set[str] = set()
        self._base_url: str | None = None
        self._base_dir: Path | None = None

    def detect(self, source: SourceConfig) -> float:
        """Check if the source looks like an OpenAPI spec."""
        content = self._get_content(source)
        if content is None:
            return 0.0

        try:
            spec = self._parse_spec_string(content)
        except (ValueError, KeyError, json.JSONDecodeError):
            return 0.0
        except (yaml.YAMLError, TypeError):
            _logger.debug("Unexpected error during OpenAPI detection", exc_info=True)
            return 0.0

        if "openapi" in spec:
            return 0.95
        if "swagger" in spec:
            return 0.95
        if "paths" in spec and "info" in spec:
            return 0.6
        return 0.0

    def extract(self, source: SourceConfig) -> ServiceIR:
        """Parse an OpenAPI/Swagger spec and produce a ServiceIR."""
        # Store base location for resolving relative external $refs
        if source.file_path:
            self._base_dir = Path(source.file_path).resolve().parent
        elif source.url:
            self._base_url = source.url

        content = self._get_content(source)
        if content is None:
            raise ValueError("Could not read source content")

        spec = self._parse_spec_string(content)
        source_hash = compute_content_hash(content)

        is_swagger = "swagger" in spec
        version = spec.get("swagger", spec.get("openapi", "unknown"))

        base_url = self._extract_base_url(spec, source, is_swagger)
        auth = self._extract_auth(spec, is_swagger)
        operations, ignored_callbacks, callback_descriptors = self._extract_operations(
            spec,
            is_swagger,
        )
        metadata: JSONDict = {
            "openapi_version": version,
            "spec_title": spec.get("info", {}).get("title", ""),
        }
        ignored_webhooks = self._extract_ignored_webhooks(spec)
        event_descriptors = callback_descriptors + self._extract_webhook_descriptors(spec)
        event_descriptors.sort(key=lambda descriptor: descriptor.id)
        if ignored_callbacks:
            metadata["ignored_callbacks"] = ignored_callbacks
        if ignored_webhooks:
            metadata["ignored_webhooks"] = ignored_webhooks

        service_name = spec.get("info", {}).get("title", "unnamed-api")
        service_name = _slugify(service_name)

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="openapi",
            service_name=service_name,
            service_description=spec.get("info", {}).get("description", ""),
            base_url=base_url,
            auth=auth,
            operations=operations,
            event_descriptors=event_descriptors,
            metadata=metadata,
        )

    # ── Private helpers ────────────────────────────────────────────────────

    def _get_content(self, source: SourceConfig) -> str | None:
        """Get spec content from URL, file path, or inline content."""
        return get_content(source)

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        return get_auth_headers(source)

    def _parse_spec_string(self, content: str) -> JSONDict:
        """Parse YAML or JSON spec string."""
        content = content.strip()
        if content.startswith("{"):
            spec = cast(JSONDict, json.loads(content))
        else:
            spec = cast(JSONDict, yaml.safe_load(content))
        self._resolve_refs(spec, spec)
        return spec

    def _resolve_refs(self, node: Any, root: JSONDict, _seen: set[int] | None = None) -> None:
        """Recursively resolve $ref pointers in-place (with cycle detection)."""
        if _seen is None:
            _seen = set()
        node_id = id(node)
        if node_id in _seen:
            return
        _seen.add(node_id)
        if isinstance(node, dict):
            if "$ref" in node:
                ref_path = node.pop("$ref")
                resolved = self._follow_ref(ref_path, root)
                # Merge: existing siblings override resolved values
                merged = {**resolved, **node}
                node.clear()
                node.update(merged)
            for v in node.values():
                self._resolve_refs(v, root, _seen)
        elif isinstance(node, list):
            for item in node:
                self._resolve_refs(item, root, _seen)

    def _follow_ref(self, ref: str, root: JSONDict) -> JSONDict:
        """Follow a JSON pointer like '#/components/schemas/Pet' or an external ref."""
        if ref.startswith("#/"):
            return self._follow_json_pointer(ref[2:], root)
        return self._resolve_external_ref(ref)

    @staticmethod
    def _follow_json_pointer(pointer: str, doc: JSONDict) -> JSONDict:
        """Walk *pointer* (slash-separated path without leading ``#/``) inside *doc*."""
        current: Any = doc
        for part in pointer.split("/"):
            if isinstance(current, dict):
                current = current.get(part, {})
            else:
                return {}
        return cast(JSONDict, current) if isinstance(current, dict) else {}

    # ── External $ref resolution ──────────────────────────────────────────

    def _resolve_external_ref(self, ref: str) -> JSONDict:
        """Resolve an external ``$ref`` (best-effort)."""
        if ref in self._external_ref_cache:
            return self._external_ref_cache[ref]

        # Cycle detection
        if ref in self._resolving_external_refs:
            logger.warning("Cycle detected in external $ref: %s", ref)
            return {}
        self._resolving_external_refs.add(ref)

        try:
            # Split "url_or_path#/json/pointer" into location and pointer parts
            if "#/" in ref:
                location, pointer = ref.split("#/", 1)
            else:
                location = ref
                pointer = ""

            doc = self._fetch_external_document(location)
            if doc is None:
                result: JSONDict = {}
            elif pointer:
                result = self._follow_json_pointer(pointer, doc)
            else:
                result = doc

            self._external_ref_cache[ref] = result
            return result
        except Exception:
            logger.warning("Failed to resolve external $ref: %s", ref, exc_info=True)
            return {}
        finally:
            self._resolving_external_refs.discard(ref)

    def _fetch_external_document(self, location: str) -> JSONDict | None:
        """Fetch and parse an external document by path or URL.

        Results are cached per resolved location so the same file / URL is
        not fetched twice.
        """
        resolved = self._resolve_location(location)
        if resolved is None:
            logger.warning("Cannot resolve external ref location: %s", location)
            return None

        if resolved in self._external_doc_cache:
            return self._external_doc_cache[resolved]

        try:
            raw = self._read_external_location(resolved)
        except Exception:
            logger.warning("Failed to fetch external document: %s", resolved, exc_info=True)
            return None

        doc = self._parse_external_content(raw)
        if doc is not None:
            self._external_doc_cache[resolved] = doc
        return doc

    def _resolve_location(self, location: str) -> str | None:
        """Turn a possibly-relative *location* into an absolute path or URL."""
        if location.startswith(("http://", "https://")):
            return location

        # Relative path — resolve against source base directory or URL
        if self._base_dir is not None:
            return str(self._base_dir / location)

        if self._base_url is not None:
            return urljoin(self._base_url, location)

        return None

    @staticmethod
    def _read_external_location(resolved: str) -> str:
        """Read raw content from an absolute path or URL."""
        if resolved.startswith(("http://", "https://")):
            resp = httpx.get(resolved, timeout=15.0)
            resp.raise_for_status()
            return resp.text

        return Path(resolved).read_text(encoding="utf-8")

    @staticmethod
    def _parse_external_content(raw: str) -> JSONDict | None:
        """Try JSON first, then YAML."""
        raw = raw.strip()
        try:
            if raw.startswith("{"):
                return cast(JSONDict, json.loads(raw))
            return cast(JSONDict, yaml.safe_load(raw))
        except (json.JSONDecodeError, yaml.YAMLError):
            return None

    def _extract_base_url(
        self,
        spec: JSONDict,
        source: SourceConfig,
        is_swagger: bool,
    ) -> str:
        if is_swagger:
            source_parts = urlsplit(source.url or "")
            raw_host = str(spec.get("host") or "")
            host = (
                source_parts.netloc if not raw_host or _is_loopback_host(raw_host) else raw_host
            ) or "localhost"
            base_path = spec.get("basePath", "")
            schemes = spec.get("schemes", ["https"])
            use_source_scheme = not raw_host or _is_loopback_host(raw_host)
            scheme = (
                source_parts.scheme
                if use_source_scheme and source_parts.scheme
                else (schemes[0] if schemes else (source_parts.scheme or "https"))
            )
            return f"{scheme}://{host}{base_path}"
        servers = spec.get("servers", [])
        if isinstance(servers, list) and servers and isinstance(servers[0], dict):
            return self._resolve_server_url(
                str(servers[0].get("url", source.url or "http://localhost")),
                source,
            )
        return source.url or "http://localhost"

    def _resolve_server_url(self, server_url: str, source: SourceConfig) -> str:
        parsed = urlsplit(server_url)
        if parsed.scheme and parsed.netloc:
            return server_url
        if not source.url:
            return server_url
        if server_url.startswith("/"):
            source_parts = urlsplit(source.url)
            return f"{source_parts.scheme}://{source_parts.netloc}{server_url}"
        return urljoin(source.url, server_url)

    def _extract_auth(self, spec: JSONDict, is_swagger: bool) -> AuthConfig:
        if is_swagger:
            sec_defs = cast(JSONDict, spec.get("securityDefinitions", {}))
        else:
            components = cast(JSONDict, spec.get("components", {}))
            sec_defs = cast(JSONDict, components.get("securitySchemes", {}))

        if not sec_defs:
            return AuthConfig(type=AuthType.none)

        parsed_schemes: list[tuple[str, AuthConfig]] = []
        for name, raw_scheme in sec_defs.items():
            if not isinstance(name, str) or not isinstance(raw_scheme, dict):
                continue
            parsed = (
                self._parse_swagger_auth(raw_scheme)
                if is_swagger
                else self._parse_openapi_auth(raw_scheme)
            )
            if parsed.type is not AuthType.none:
                parsed_schemes.append((name, parsed))

        if not parsed_schemes:
            return AuthConfig(type=AuthType.none)

        if len(parsed_schemes) > 1:
            logger.info(
                "Multiple security schemes declared: %s; using first available.",
                [name for name, _ in parsed_schemes],
            )

        return parsed_schemes[0][1]

    def _parse_swagger_auth(self, scheme: JSONDict) -> AuthConfig:
        auth_type = scheme.get("type", "")
        if auth_type == "apiKey":
            location = scheme.get("in", "header")
            return AuthConfig(
                type=AuthType.api_key,
                api_key_param=scheme.get("name", "api_key"),
                api_key_location=location if location in ("header", "query") else "header",
            )
        if auth_type == "oauth2":
            token_url = scheme.get("tokenUrl")
            scopes = scheme.get("scopes", {})
            scope_names = list(scopes.keys()) if isinstance(scopes, dict) else None
            if isinstance(token_url, str) and token_url:
                return AuthConfig(
                    type=AuthType.oauth2,
                    oauth2_token_url=token_url,
                    oauth2_scopes=scope_names,
                )
            return AuthConfig(type=AuthType.oauth2, oauth2_scopes=scope_names)
        if auth_type == "basic":
            return AuthConfig(type=AuthType.basic)
        return AuthConfig(type=AuthType.none)

    def _parse_openapi_auth(self, scheme: JSONDict) -> AuthConfig:
        auth_type = scheme.get("type", "")
        if auth_type == "http":
            http_scheme = scheme.get("scheme", "bearer")
            if http_scheme == "bearer":
                return AuthConfig(
                    type=AuthType.bearer,
                    header_name="Authorization",
                    header_prefix="Bearer",
                )
            if http_scheme == "basic":
                return AuthConfig(type=AuthType.basic)
        if auth_type == "apiKey":
            location = scheme.get("in", "header")
            return AuthConfig(
                type=AuthType.api_key,
                api_key_param=scheme.get("name", "api_key"),
                api_key_location=location if location in ("header", "query") else "header",
            )
        if auth_type == "oauth2":
            flows = scheme.get("flows", {})
            if not isinstance(flows, dict):
                return AuthConfig(type=AuthType.oauth2)

            for flow_name in (
                "clientCredentials",
                "password",
                "authorizationCode",
                "implicit",
            ):
                flow = flows.get(flow_name)
                if not isinstance(flow, dict):
                    continue
                scopes = flow.get("scopes", {})
                scope_names = list(scopes.keys()) if isinstance(scopes, dict) else None
                token_url = flow.get("tokenUrl")
                if isinstance(token_url, str) and token_url:
                    return AuthConfig(
                        type=AuthType.oauth2,
                        oauth2_token_url=token_url,
                        oauth2_scopes=scope_names,
                    )
                return AuthConfig(type=AuthType.oauth2, oauth2_scopes=scope_names)
        return AuthConfig(type=AuthType.none)

    def _extract_operations(
        self,
        spec: JSONDict,
        is_swagger: bool,
    ) -> tuple[list[Operation], list[str], list[EventDescriptor]]:
        operations: list[Operation] = []
        ignored_callbacks: list[str] = []
        event_descriptors: list[EventDescriptor] = []
        paths = spec.get("paths", {})
        if not isinstance(paths, dict):
            return operations, ignored_callbacks, event_descriptors

        for path, path_item in paths.items():
            if not isinstance(path, str) or not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete", "head", "options"):
                if method not in path_item:
                    continue
                op_spec = path_item[method]
                if not isinstance(op_spec, dict):
                    continue

                op_id = op_spec.get("operationId", f"{method}_{_slugify(path)}")
                risk_level = _METHOD_RISK.get(method.upper(), RiskLevel.unknown)

                params, request_body_mode, body_param_name = self._extract_params(
                    op_spec,
                    path_item,
                    is_swagger,
                )
                params = self._ensure_path_template_params(path, params)
                callbacks = op_spec.get("callbacks", {})
                if isinstance(callbacks, dict):
                    for callback_name in callbacks:
                        if not isinstance(callback_name, str):
                            continue
                        callback_id = f"{op_id}:{callback_name}"
                        ignored_callbacks.append(callback_id)
                        event_descriptors.append(
                            EventDescriptor(
                                id=callback_id,
                                name=callback_name,
                                transport=EventTransport.callback,
                                direction=EventDirection.inbound,
                                support=EventSupportLevel.unsupported,
                                operation_id=op_id,
                                channel=callback_name,
                            )
                        )

                error_schema = self._extract_error_schema(op_spec, is_swagger)
                response_examples = self._extract_response_examples(op_spec, is_swagger)

                pagination = self._infer_pagination(op_spec, params, is_swagger)
                response_strategy = (
                    ResponseStrategy(pagination=pagination)
                    if pagination and method.upper() == "GET"
                    else ResponseStrategy()
                )

                enabled = risk_level != RiskLevel.unknown
                op = Operation(
                    id=op_id,
                    name=op_spec.get("summary", op_id),
                    description=op_spec.get("description") or op_spec.get("summary", ""),
                    method=method.upper(),
                    path=path,
                    params=params,
                    request_body_mode=request_body_mode,
                    body_param_name=body_param_name,
                    error_schema=error_schema,
                    response_examples=response_examples,
                    response_strategy=response_strategy,
                    risk=RiskMetadata(
                        writes_state=method.upper() in ("POST", "PUT", "PATCH", "DELETE"),
                        destructive=method.upper() == "DELETE",
                        idempotent=method.upper() in ("GET", "PUT", "DELETE", "HEAD", "OPTIONS"),
                        risk_level=risk_level,
                        confidence=0.9,
                        source=SourceType.extractor,
                    ),
                    tags=op_spec.get("tags", []),
                    source=SourceType.extractor,
                    confidence=0.9,
                    enabled=enabled,
                )
                operations.append(op)

        ignored_callbacks.sort()
        return operations, ignored_callbacks, event_descriptors

    def _extract_params(
        self,
        op_spec: JSONDict,
        path_item: JSONDict,
        is_swagger: bool,
    ) -> tuple[list[Param], RequestBodyMode, str | None]:
        params: list[Param] = []
        seen_names: set[str] = set()
        request_body_mode = RequestBodyMode.json
        body_param_name: str | None = None

        # Path-level params + operation-level params
        raw_params = (path_item.get("parameters") or []) + (op_spec.get("parameters") or [])
        for p in raw_params:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "")
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            if is_swagger and p.get("in") == "body":
                # Swagger 2.0 body param — extract from schema
                body_schema = p.get("schema", {})
                body_params = self._flatten_schema_to_params(body_schema)
                params.extend(body_params)
                continue

            param_type = self._resolve_param_type(p, is_swagger)
            params.append(
                Param(
                    name=name,
                    type=param_type,
                    required=bool(p.get("required", False)),
                    description=str(p.get("description", "")),
                    default=p.get("default"),
                    source=SourceType.extractor,
                    confidence=0.9,
                )
            )

        # OpenAPI 3.x requestBody
        if not is_swagger and "requestBody" in op_spec:
            body_params, request_body_mode, body_param_name = self._extract_request_body_params(
                op_spec["requestBody"]
            )
            for bp in body_params:
                if bp.name not in seen_names:
                    params.append(bp)
                    seen_names.add(bp.name)

        return params, request_body_mode, body_param_name

    def _ensure_path_template_params(self, path: str, params: list[Param]) -> list[Param]:
        existing_names = {param.name for param in params}
        augmented = list(params)
        for name in _PATH_TEMPLATE_PARAM_PATTERN.findall(path):
            if name in existing_names:
                continue
            augmented.append(
                Param(
                    name=name,
                    type="string",
                    required=True,
                    description="Path parameter inferred from the URL template.",
                    source=SourceType.extractor,
                    confidence=0.8,
                )
            )
            existing_names.add(name)
        return augmented

    def _resolve_param_type(self, param: JSONDict, is_swagger: bool) -> str:
        if is_swagger:
            return _TYPE_MAP.get(str(param.get("type", "string")), "string")
        schema = param.get("schema", {})
        if not isinstance(schema, dict):
            return "string"
        return _TYPE_MAP.get(schema.get("type", "string"), "string")

    def _extract_request_body_params(
        self,
        body: JSONDict,
    ) -> tuple[list[Param], RequestBodyMode, str | None]:
        content = body.get("content", {})
        if not isinstance(content, dict):
            return [], RequestBodyMode.json, None

        required = bool(body.get("required", False))

        if "multipart/form-data" in content:
            return (
                [
                    Param(
                        name="payload",
                        type="object",
                        required=required,
                        description="Multipart form payload.",
                        source=SourceType.extractor,
                        confidence=0.9,
                    )
                ],
                RequestBodyMode.multipart,
                "payload",
            )

        if "application/octet-stream" in content:
            return (
                [
                    Param(
                        name="payload",
                        type="object",
                        required=required,
                        description="Raw request body payload.",
                        source=SourceType.extractor,
                        confidence=0.9,
                    )
                ],
                RequestBodyMode.raw,
                "payload",
            )

        json_content = content.get("application/json", {})
        if not isinstance(json_content, dict):
            return [], RequestBodyMode.json, None
        schema = json_content.get("schema", {})
        if not isinstance(schema, dict):
            return [], RequestBodyMode.json, None
        return self._flatten_schema_to_params(schema), RequestBodyMode.json, None

    def _extract_ignored_webhooks(self, spec: JSONDict) -> list[str]:
        webhooks = spec.get("webhooks", {})
        if not isinstance(webhooks, dict):
            return []
        ignored_webhooks = [
            webhook_name for webhook_name in webhooks if isinstance(webhook_name, str)
        ]
        ignored_webhooks.sort()
        return ignored_webhooks

    def _extract_webhook_descriptors(self, spec: JSONDict) -> list[EventDescriptor]:
        webhooks = spec.get("webhooks", {})
        if not isinstance(webhooks, dict):
            return []
        descriptors = [
            EventDescriptor(
                id=webhook_name,
                name=webhook_name,
                transport=EventTransport.webhook,
                direction=EventDirection.inbound,
                support=EventSupportLevel.unsupported,
                channel=webhook_name,
            )
            for webhook_name in webhooks
            if isinstance(webhook_name, str)
        ]
        descriptors.sort(key=lambda descriptor: descriptor.id)
        return descriptors

    # ── Pagination inference ────────────────────────────────────────────

    _CURSOR_PARAM_NAMES: set[str] = {
        "cursor",
        "next_cursor",
        "page_token",
        "next_page_token",
        "after",
        "before",
        "starting_after",
        "ending_before",
    }
    _CURSOR_RESPONSE_FIELDS: set[str] = {"next_cursor", "next_page_token", "cursor", "next"}
    _PAGE_SIZE_PARAM_NAMES: set[str] = {"per_page", "page_size", "pageSize", "size", "count"}
    _PAGINATION_RESPONSE_FIELDS: set[str] = {
        "total",
        "total_count",
        "count",
        "page",
        "pages",
        "has_more",
        "has_next",
    }

    def _infer_pagination(
        self,
        op_spec: JSONDict,
        params: list[Param],
        is_swagger: bool,
    ) -> PaginationConfig | None:
        """Detect pagination patterns from params and response schema."""
        param_names = {p.name for p in params}
        response_schema = self._get_success_response_schema(op_spec, is_swagger)
        response_fields = (
            set(response_schema.get("properties", {}).keys())
            if isinstance(response_schema, dict)
            else set()
        )

        # 1. Cursor-based pagination
        cursor_params = param_names & self._CURSOR_PARAM_NAMES
        cursor_response = response_fields & self._CURSOR_RESPONSE_FIELDS
        if cursor_params or cursor_response:
            cursor_param = next(iter(sorted(cursor_params))) if cursor_params else "cursor"
            size_param = self._detect_size_param(param_names, default="limit")
            return PaginationConfig(
                style="cursor", cursor_param=cursor_param, limit_param=size_param
            )

        # 2. Page-based pagination
        if "page" in param_names:
            size_match = param_names & self._PAGE_SIZE_PARAM_NAMES
            if size_match:
                size_param = next(iter(sorted(size_match)))
                return PaginationConfig(style="page", page_param="page", limit_param=size_param)

        # 3. Offset-based pagination
        if "offset" in param_names and "limit" in param_names:
            return PaginationConfig(style="offset", page_param="offset", limit_param="limit")

        # 4. Response envelope detection
        if isinstance(response_schema, dict):
            props = response_schema.get("properties", {})
            data_prop = props.get("data", {})
            data_is_array = isinstance(data_prop, dict) and data_prop.get("type") == "array"
            has_meta = bool({"meta", "pagination"} & set(props.keys()))
            if data_is_array and has_meta:
                meta_key = "meta" if "meta" in props else "pagination"
                meta_schema = props.get(meta_key, {})
                meta_fields = (
                    set(meta_schema.get("properties", {}).keys())
                    if isinstance(meta_schema, dict)
                    else set()
                )
                if meta_fields & self._PAGINATION_RESPONSE_FIELDS:
                    size_param = self._detect_size_param(param_names, default="page_size")
                    return PaginationConfig(
                        style="offset", page_param="offset", limit_param=size_param
                    )

        return None

    @staticmethod
    def _detect_size_param(param_names: set[str], *, default: str) -> str:
        for candidate in ("limit", "per_page", "page_size", "pageSize", "size", "count"):
            if candidate in param_names:
                return candidate
        return default

    def _get_success_response_schema(self, op_spec: JSONDict, is_swagger: bool) -> JSONDict | None:
        """Return the JSON Schema of the first 2xx response, if any."""
        responses = op_spec.get("responses", {})
        if not isinstance(responses, dict):
            return None
        for code in ("200", "201", "202", "203", "204"):
            resp = responses.get(code)
            if not isinstance(resp, dict):
                continue
            if is_swagger:
                schema = resp.get("schema")
                if isinstance(schema, dict):
                    return schema
            else:
                content = resp.get("content", {})
                if not isinstance(content, dict):
                    continue
                json_content = content.get("application/json", {})
                if isinstance(json_content, dict):
                    schema = json_content.get("schema")
                    if isinstance(schema, dict):
                        return schema
        return None

    # ── Error-schema & response-examples extraction ──────────────────────

    def _extract_error_schema(self, op_spec: JSONDict, is_swagger: bool) -> ErrorSchema:
        """Extract error responses (4xx/5xx/default) into an ErrorSchema."""
        responses = op_spec.get("responses", {})
        if not isinstance(responses, dict):
            return ErrorSchema()

        error_responses: list[ErrorResponse] = []
        default_error_schema: dict[str, Any] | None = None

        for status_code_str, resp_obj in responses.items():
            if not isinstance(resp_obj, dict):
                continue

            is_default = status_code_str == "default"
            is_error = False
            status_int: int | None = None

            if not is_default:
                try:
                    status_int = int(status_code_str)
                    is_error = status_int >= 400
                except (ValueError, TypeError):
                    continue

            if not is_error and not is_default:
                continue

            description = str(resp_obj.get("description", ""))
            body_schema = self._response_body_schema(resp_obj, is_swagger)

            if is_default:
                default_error_schema = body_schema
            else:
                error_responses.append(
                    ErrorResponse(
                        status_code=status_int,
                        description=description,
                        error_body_schema=body_schema,
                    )
                )

        error_responses.sort(key=lambda r: r.status_code or 0)
        return ErrorSchema(responses=error_responses, default_error_schema=default_error_schema)

    def _extract_response_examples(
        self,
        op_spec: JSONDict,
        is_swagger: bool,
    ) -> list[ResponseExample]:
        """Extract inline examples from 2xx responses."""
        responses = op_spec.get("responses", {})
        if not isinstance(responses, dict):
            return []

        examples: list[ResponseExample] = []

        for status_code_str, resp_obj in responses.items():
            if not isinstance(resp_obj, dict):
                continue

            # Only 2xx
            try:
                status_int = int(status_code_str)
            except (ValueError, TypeError):
                continue
            if not (200 <= status_int < 300):
                continue

            if is_swagger:
                # Swagger 2.x: responses/<code>/examples/application/json
                swagger_examples = resp_obj.get("examples", {})
                if isinstance(swagger_examples, dict):
                    json_example = swagger_examples.get("application/json")
                    if json_example is not None:
                        examples.append(
                            ResponseExample(
                                name=f"example_{status_int}",
                                description=str(resp_obj.get("description", "")),
                                status_code=status_int,
                                body=self._normalize_example_body(json_example),
                                source=SourceType.extractor,
                            )
                        )
                # Swagger 2.x: schema-level example
                schema = resp_obj.get("schema", {})
                if isinstance(schema, dict) and "example" in schema:
                    examples.append(
                        ResponseExample(
                            name=f"schema_example_{status_int}",
                            description=f"Schema example for {status_int}",
                            status_code=status_int,
                            body=self._normalize_example_body(schema["example"]),
                            source=SourceType.extractor,
                        )
                    )
            else:
                # OpenAPI 3.x
                content = resp_obj.get("content", {})
                if not isinstance(content, dict):
                    continue
                json_content = content.get("application/json", {})
                if not isinstance(json_content, dict):
                    continue

                # Single example
                if "example" in json_content:
                    examples.append(
                        ResponseExample(
                            name=f"example_{status_int}",
                            description=str(resp_obj.get("description", "")),
                            status_code=status_int,
                            body=self._normalize_example_body(json_content["example"]),
                            source=SourceType.extractor,
                        )
                    )

                # Examples map
                examples_map = json_content.get("examples", {})
                if isinstance(examples_map, dict):
                    for ex_name, ex_obj in examples_map.items():
                        if not isinstance(ex_obj, dict):
                            continue
                        examples.append(
                            ResponseExample(
                                name=str(ex_name),
                                description=str(ex_obj.get("summary", "")),
                                status_code=status_int,
                                body=self._normalize_example_body(ex_obj.get("value")),
                                source=SourceType.extractor,
                            )
                        )

                # Schema-level example
                schema = json_content.get("schema", {})
                if isinstance(schema, dict) and "example" in schema:
                    examples.append(
                        ResponseExample(
                            name=f"schema_example_{status_int}",
                            description=f"Schema example for {status_int}",
                            status_code=status_int,
                            body=self._normalize_example_body(schema["example"]),
                            source=SourceType.extractor,
                        )
                    )

        return examples

    @staticmethod
    def _normalize_example_body(body: Any) -> dict[str, Any] | str | None:
        """Coerce an example body to a type accepted by ResponseExample.body."""
        if body is None or isinstance(body, (dict, str)):
            return body
        return json.dumps(body, default=str)

    @staticmethod
    def _response_body_schema(resp_obj: JSONDict, is_swagger: bool) -> dict[str, Any] | None:
        """Extract the JSON Schema from a response object."""
        if is_swagger:
            schema = resp_obj.get("schema")
            return schema if isinstance(schema, dict) else None
        content = resp_obj.get("content", {})
        if not isinstance(content, dict):
            return None
        json_content = content.get("application/json", {})
        if not isinstance(json_content, dict):
            return None
        schema = json_content.get("schema")
        return schema if isinstance(schema, dict) else None

    def _flatten_schema_to_params(self, schema: JSONDict, *, _depth: int = 0) -> list[Param]:
        """Extract top-level properties from a schema as params."""
        if _depth >= _MAX_COMPOSITION_DEPTH:
            return []
        merged_props: dict[str, dict[str, Any]] = {}
        merged_required: set[str] = set()
        min_confidence = 0.9

        # --- Handle allOf: merge all sub-schemas, union required fields ---
        for sub in schema.get("allOf", []):
            if not isinstance(sub, dict):
                continue
            sub_params = self._flatten_schema_to_params(sub, _depth=_depth + 1)
            for p in sub_params:
                if p.name not in merged_props:
                    merged_props[p.name] = {
                        "type": p.type,
                        "description": p.description,
                        "json_schema": p.json_schema,
                    }
                if p.required:
                    merged_required.add(p.name)
                min_confidence = min(min_confidence, p.confidence)

        # --- Handle oneOf / anyOf: merge properties, nothing required, lower confidence ---
        for keyword in ("oneOf", "anyOf"):
            branches = schema.get(keyword, [])
            if not branches:
                continue
            # Only process if no top-level properties (top-level takes precedence)
            if "properties" in schema:
                continue
            pre_count = len(merged_props)
            for sub in branches:
                if not isinstance(sub, dict):
                    continue
                sub_params = self._flatten_schema_to_params(sub, _depth=_depth + 1)
                for p in sub_params:
                    if p.name not in merged_props:
                        merged_props[p.name] = {
                            "type": p.type,
                            "description": p.description,
                            "json_schema": p.json_schema,
                        }
            if len(merged_props) > pre_count:
                min_confidence = min(min_confidence, 0.8)

        # --- Handle top-level properties ---
        top_required = set(schema.get("required", []))
        top_props = schema.get("properties", {})
        if isinstance(top_props, dict):
            for name, prop in top_props.items():
                if not isinstance(name, str) or not isinstance(prop, dict):
                    continue
                prop_type = _TYPE_MAP.get(prop.get("type", "string"), "string")
                prop_json_schema = _extract_nested_json_schema(prop)
                merged_props[name] = {
                    "type": prop_type,
                    "description": str(prop.get("description", "")),
                    "json_schema": prop_json_schema,
                }
                if name in top_required:
                    merged_required.add(name)

        has_composition = any(k in schema for k in ("allOf", "oneOf", "anyOf"))
        if not merged_props and not has_composition:
            if schema.get("type") != "object" and "properties" not in schema:
                return []

        params: list[Param] = []
        for name, info in merged_props.items():
            params.append(
                Param(
                    name=name,
                    type=info["type"],
                    required=name in merged_required,
                    description=info["description"],
                    source=SourceType.extractor,
                    confidence=min_confidence,
                    json_schema=info.get("json_schema"),
                )
            )

        return params


def _is_loopback_host(host: str) -> bool:
    hostname = urlsplit(f"//{host}").hostname or host
    return hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
