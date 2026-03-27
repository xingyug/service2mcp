"""OpenAPI extractor — parses Swagger 2.0, OpenAPI 3.0, and 3.1 specs into ServiceIR."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, cast

import httpx
import yaml

from libs.extractors.base import SourceConfig
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
    Param,
    RequestBodyMode,
    ResponseExample,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

logger = logging.getLogger(__name__)
JSONDict = dict[str, Any]

# HTTP method → risk level mapping
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


class OpenAPIExtractor:
    """Extracts ServiceIR from OpenAPI / Swagger specs."""

    protocol_name: str = "openapi"

    def detect(self, source: SourceConfig) -> float:
        """Check if the source looks like an OpenAPI spec."""
        content = self._get_content(source)
        if content is None:
            return 0.0

        try:
            spec = self._parse_spec_string(content)
        except Exception:
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
        content = self._get_content(source)
        if content is None:
            raise ValueError("Could not read source content")

        spec = self._parse_spec_string(content)
        source_hash = hashlib.sha256(content.encode()).hexdigest()

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
        if source.file_content:
            return source.file_content
        if source.file_path:
            return Path(source.file_path).read_text()
        if source.url:
            try:
                resp = httpx.get(source.url, timeout=30, headers=self._auth_headers(source))
                resp.raise_for_status()
                return resp.text
            except Exception:
                logger.warning("Failed to fetch spec from %s", source.url, exc_info=True)
                return None
        return None

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        headers: dict[str, str] = {}
        if source.auth_header:
            headers["Authorization"] = source.auth_header
        elif source.auth_token:
            headers["Authorization"] = f"Bearer {source.auth_token}"
        return headers

    def _parse_spec_string(self, content: str) -> JSONDict:
        """Parse YAML or JSON spec string."""
        content = content.strip()
        if content.startswith("{"):
            spec = cast(JSONDict, json.loads(content))
        else:
            spec = cast(JSONDict, yaml.safe_load(content))
        self._resolve_refs(spec, spec)
        return spec

    def _resolve_refs(self, node: Any, root: JSONDict) -> None:
        """Recursively resolve $ref pointers in-place."""
        if isinstance(node, dict):
            if "$ref" in node and len(node) == 1:
                ref_path = node["$ref"]
                resolved = self._follow_ref(ref_path, root)
                node.clear()
                node.update(resolved)
            for v in node.values():
                self._resolve_refs(v, root)
        elif isinstance(node, list):
            for item in node:
                self._resolve_refs(item, root)

    def _follow_ref(self, ref: str, root: JSONDict) -> JSONDict:
        """Follow a JSON pointer like '#/components/schemas/Pet'."""
        if not ref.startswith("#/"):
            return {}
        parts = ref[2:].split("/")
        current: Any = root
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part, {})
            else:
                return {}
        return cast(JSONDict, current) if isinstance(current, dict) else {}

    def _extract_base_url(
        self,
        spec: JSONDict,
        source: SourceConfig,
        is_swagger: bool,
    ) -> str:
        if is_swagger:
            host = spec.get("host", "localhost")
            base_path = spec.get("basePath", "")
            schemes = spec.get("schemes", ["https"])
            scheme = schemes[0] if schemes else "https"
            return f"{scheme}://{host}{base_path}"
        servers = spec.get("servers", [])
        if isinstance(servers, list) and servers and isinstance(servers[0], dict):
            return str(servers[0].get("url", source.url or "http://localhost"))
        return source.url or "http://localhost"

    def _extract_auth(self, spec: JSONDict, is_swagger: bool) -> AuthConfig:
        if is_swagger:
            sec_defs = cast(JSONDict, spec.get("securityDefinitions", {}))
        else:
            components = cast(JSONDict, spec.get("components", {}))
            sec_defs = cast(JSONDict, components.get("securitySchemes", {}))

        if not sec_defs:
            return AuthConfig(type=AuthType.none)

        # Take the first security scheme
        scheme = next(iter(sec_defs.values()))
        if not isinstance(scheme, dict):
            return AuthConfig(type=AuthType.none)

        if is_swagger:
            return self._parse_swagger_auth(scheme)
        return self._parse_openapi_auth(scheme)

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
            return AuthConfig(type=AuthType.oauth2)
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
            return AuthConfig(type=AuthType.oauth2)
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

                enabled = risk_level != RiskLevel.unknown
                op = Operation(
                    id=op_id,
                    name=op_spec.get("summary", op_id),
                    description=op_spec.get("description", ""),
                    method=method.upper(),
                    path=path,
                    params=params,
                    request_body_mode=request_body_mode,
                    body_param_name=body_param_name,
                    error_schema=error_schema,
                    response_examples=response_examples,
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
        raw_params = path_item.get("parameters", []) + op_spec.get("parameters", [])

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

    def _flatten_schema_to_params(self, schema: JSONDict) -> list[Param]:
        """Extract top-level properties from a schema as params."""
        if schema.get("type") != "object" and "properties" not in schema:
            return []

        params: list[Param] = []
        required_fields = set(schema.get("required", []))
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return []

        for name, prop in properties.items():
            if not isinstance(name, str) or not isinstance(prop, dict):
                continue
            param_type = _TYPE_MAP.get(prop.get("type", "string"), "string")
            params.append(
                Param(
                    name=name,
                    type=param_type,
                    required=name in required_fields,
                    description=str(prop.get("description", "")),
                    source=SourceType.extractor,
                    confidence=0.9,
                )
            )

        return params


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    import re

    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
