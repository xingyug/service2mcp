"""JSON-RPC 2.0 extractor — parses OpenRPC specs and manual service definitions."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx

from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorResponse,
    ErrorSchema,
    JsonRpcOperationConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)

logger = logging.getLogger(__name__)

_SAFE_PREFIXES = ("get", "list", "query", "fetch", "describe", "find", "search", "count")
_DANGEROUS_PREFIXES = ("delete", "remove", "purge", "drop")

_JSON_SCHEMA_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}

_JSONRPC_ERROR_SCHEMA = ErrorSchema(
    default_error_schema={
        "type": "object",
        "properties": {
            "jsonrpc": {"type": "string", "const": "2.0"},
            "error": {
                "type": "object",
                "properties": {
                    "code": {"type": "integer"},
                    "message": {"type": "string"},
                    "data": {},
                },
                "required": ["code", "message"],
            },
            "id": {},
        },
    },
    responses=[
        ErrorResponse(error_code="-32700", description="Parse error"),
        ErrorResponse(error_code="-32600", description="Invalid Request"),
        ErrorResponse(error_code="-32601", description="Method not found"),
        ErrorResponse(error_code="-32602", description="Invalid params"),
        ErrorResponse(error_code="-32603", description="Internal error"),
    ],
)


def _classify_risk(method_name: str) -> RiskMetadata:
    """Classify risk based on the last segment of a dotted method name."""
    # For dotted names like "user.getById", use the last segment "getById"
    segment = method_name.rsplit(".", maxsplit=1)[-1].lower()

    if any(segment.startswith(p) for p in _SAFE_PREFIXES):
        return RiskMetadata(
            risk_level=RiskLevel.safe,
            writes_state=False,
            destructive=False,
            confidence=0.8,
        )
    if any(segment.startswith(p) for p in _DANGEROUS_PREFIXES):
        return RiskMetadata(
            risk_level=RiskLevel.dangerous,
            writes_state=True,
            destructive=True,
            confidence=0.8,
        )
    return RiskMetadata(
        risk_level=RiskLevel.cautious,
        writes_state=True,
        destructive=False,
        confidence=0.6,
    )


def _map_type(schema: dict[str, Any]) -> str:
    """Map a JSON Schema type to a normalized IR type string."""
    raw = schema.get("type", "object")
    return _JSON_SCHEMA_TYPE_MAP.get(raw, "object")


class JsonRpcExtractor:
    """Extract JSON-RPC 2.0 operations from OpenRPC specs and manual service definitions."""

    protocol_name: str = "jsonrpc"

    # ── detection ──────────────────────────────────────────────────────────

    def detect(self, source: SourceConfig) -> float:
        if source.hints.get("protocol") == "jsonrpc":
            return 1.0

        content = self._get_content(source)
        if content is None:
            return 0.0

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return 0.0

        if not isinstance(data, dict):
            return 0.0

        if "openrpc" in data:
            return 0.95
        if data.get("jsonrpc_service") is True:
            return 0.9
        if isinstance(data.get("methods"), list) and any(
            isinstance(m, dict) and "params" in m for m in data["methods"]
        ):
            return 0.7
        return 0.0

    # ── extraction ─────────────────────────────────────────────────────────

    def extract(self, source: SourceConfig) -> ServiceIR:
        content = self._get_content(source)
        if content is None:
            raise ValueError("Could not read source content")

        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        data = json.loads(content)

        is_openrpc = "openrpc" in data

        # Service info
        info = data.get("info", {})
        title = info.get("title", "JSON-RPC Service")
        description = info.get("description", "")
        version = info.get("version", "0.0.0")

        # Base URL
        base_url = self._resolve_base_url(data, source, is_openrpc)
        endpoint_path = urlparse(base_url).path or "/"

        # Build operations
        methods: list[dict[str, Any]] = data.get("methods", [])
        operations: list[Operation] = []
        for method in methods:
            op = self._method_to_operation(method, endpoint_path)
            operations.append(op)

        # Metadata
        metadata: dict[str, Any] = {
            "service_version": version,
            "method_count": len(operations),
        }
        if is_openrpc:
            metadata["openrpc_version"] = data["openrpc"]

        service_name = title.lower().replace(" ", "-")

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="jsonrpc",
            service_name=service_name,
            service_description=description,
            base_url=base_url,
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            metadata=metadata,
        )

    # ── private helpers ────────────────────────────────────────────────────

    def _method_to_operation(
        self,
        method: dict[str, Any],
        endpoint_path: str,
    ) -> Operation:
        method_name: str = method["name"]
        op_id = method_name.replace(".", "_")
        params_type = _resolve_params_type(method)

        # Build params
        params: list[Param] = []
        param_names: list[str] = []
        for p in method.get("params", []):
            p_name = p["name"]
            param_names.append(p_name)
            p_schema = p.get("schema", {"type": "object"})
            params.append(
                Param(
                    name=p_name,
                    type=_map_type(p_schema),
                    required=p.get("required", False),
                    description=p.get("description", ""),
                    default=p.get("default", p_schema.get("default")),
                )
            )

        # Result schema
        result = method.get("result", {})
        result_schema: dict[str, Any] | None = result.get("schema") if result else None

        # Description: prefer description, fall back to summary
        desc = method.get("description", "") or method.get("summary", "")

        jsonrpc_config = JsonRpcOperationConfig(
            method_name=method_name,
            params_type=params_type,
            params_names=param_names,
            result_schema=result_schema,
        )

        risk = _classify_risk(method_name)

        return Operation(
            id=op_id,
            name=method_name,
            description=desc,
            method="POST",
            path=endpoint_path,
            params=params,
            jsonrpc=jsonrpc_config,
            risk=risk,
            error_schema=_JSONRPC_ERROR_SCHEMA,
        )

    @staticmethod
    def _resolve_base_url(
        data: dict[str, Any],
        source: SourceConfig,
        is_openrpc: bool,
    ) -> str:
        if is_openrpc:
            servers = data.get("servers", [])
            if servers and isinstance(servers[0], dict):
                url = servers[0].get("url")
                if url:
                    return str(url)
        else:
            endpoint = data.get("endpoint")
            if endpoint:
                return str(endpoint)

        if source.url:
            if is_openrpc:
                return JsonRpcExtractor._default_openrpc_endpoint(source.url)
            return source.url

        return "http://localhost:8080/rpc"

    @staticmethod
    def _default_openrpc_endpoint(source_url: str) -> str:
        parsed = urlparse(source_url)
        path = parsed.path or ""
        for suffix in ("/openrpc.json", "/openrpc.yaml", "/openrpc.yml"):
            if path.endswith(suffix):
                endpoint_path = f"{path[: -len(suffix)]}/rpc" or "/rpc"
                return parsed._replace(
                    path=endpoint_path,
                    params="",
                    query="",
                    fragment="",
                ).geturl()
        return source_url

    def _get_content(self, source: SourceConfig) -> str | None:
        if source.file_content:
            return source.file_content
        if source.file_path:
            return Path(source.file_path).read_text(encoding="utf-8")
        if source.url:
            try:
                response = httpx.get(
                    source.url,
                    timeout=30,
                    headers=self._auth_headers(source),
                )
                response.raise_for_status()
                return response.text
            except Exception:
                logger.warning(
                    "Failed to fetch JSON-RPC spec from %s",
                    source.url,
                    exc_info=True,
                )
                return None
        return None

    @staticmethod
    def _auth_headers(source: SourceConfig) -> dict[str, str]:
        headers: dict[str, str] = {}
        if source.auth_header:
            headers["Authorization"] = source.auth_header
        elif source.auth_token:
            headers["Authorization"] = f"Bearer {source.auth_token}"
        return headers


def _resolve_params_type(method: dict[str, Any]) -> Literal["named", "positional"]:
    raw_params_type = method.get("params_type")
    if raw_params_type in {"named", "positional"}:
        return cast(Literal["named", "positional"], raw_params_type)

    param_structure = method.get("paramStructure")
    if param_structure == "by-position":
        return "positional"
    if param_structure in {"by-name", "either"}:
        return "named"
    return "named"
