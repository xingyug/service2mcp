"""gRPC proto extractor foundation for unary RPC services."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

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
    GrpcStreamMode,
    GrpcStreamRuntimeConfig,
    GrpcUnaryRuntimeConfig,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

logger = logging.getLogger(__name__)

_SYNTAX_PATTERN = re.compile(r'^\s*syntax\s*=\s*"(?P<syntax>[^"]+)"\s*;', re.MULTILINE)
_PACKAGE_PATTERN = re.compile(r"^\s*package\s+(?P<package>[\w.]+)\s*;", re.MULTILINE)
_RPC_PATTERN = re.compile(
    r"rpc\s+"
    r"(?P<name>\w+)\s*"
    r"\(\s*(?P<request_stream>stream\s+)?(?P<request>[\w.]+)\s*\)\s*"
    r"returns\s*\(\s*(?P<response_stream>stream\s+)?(?P<response>[\w.]+)\s*\)\s*"
    r"(?:;|\{[^}]*\})",
)


def _find_blocks(keyword: str, text: str) -> list[tuple[str, str]]:
    """Find top-level `keyword Name { ... }` blocks using brace counting."""
    pattern = re.compile(rf"{keyword}\s+(\w+)\s*\{{")
    results: list[tuple[str, str]] = []
    for m in pattern.finditer(text):
        name = m.group(1)
        start = m.end() - 1  # position of opening brace
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    body = text[start + 1 : i]
                    results.append((name, body))
                    break
    return results


_PROTO_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "bool": "boolean",
    "bytes": "string",
    "double": "number",
    "float": "number",
    "int32": "integer",
    "int64": "integer",
    "uint32": "integer",
    "uint64": "integer",
    "sint32": "integer",
    "sint64": "integer",
    "fixed32": "integer",
    "fixed64": "integer",
    "sfixed32": "integer",
    "sfixed64": "integer",
}
_SAFE_RPC_PREFIXES = ("Get", "List", "Search", "Read", "Fetch", "Describe", "Lookup", "Count")
_DANGEROUS_RPC_PREFIXES = ("Delete", "Remove", "Destroy", "Drop", "Purge", "Truncate")


@dataclass(frozen=True)
class ProtoField:
    """Normalized protobuf field definition."""

    name: str
    type_name: str
    repeated: bool
    required: bool


@dataclass(frozen=True)
class ProtoRpc:
    """Normalized protobuf RPC definition."""

    name: str
    request_type: str
    response_type: str
    client_streaming: bool
    server_streaming: bool


@dataclass(frozen=True)
class ProtoService:
    """Normalized protobuf service definition."""

    name: str
    rpcs: list[ProtoRpc]


class GrpcProtoExtractor:
    """Extract unary gRPC operations from a Protocol Buffers service definition."""

    protocol_name: str = "grpc"

    def detect(self, source: SourceConfig) -> float:
        content = self._get_content(source)
        if content is None:
            return 0.0

        file_path = (source.file_path or "").lower()
        syntax_match = _SYNTAX_PATTERN.search(content)
        rpc_count = len(_RPC_PATTERN.findall(content))
        service_count = len(_find_blocks("service", content))
        if file_path.endswith(".proto") and syntax_match and rpc_count > 0:
            return 0.98
        if syntax_match and service_count > 0 and rpc_count > 0:
            return 0.95
        if "service " in content and " rpc " in f" {content} ":
            return 0.6
        return 0.0

    def extract(self, source: SourceConfig) -> ServiceIR:
        content = self._get_content(source)
        if content is None:
            raise ValueError("Could not read source content")

        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        proto_syntax = _extract_pattern_value(_SYNTAX_PATTERN, content, default="proto3")
        proto_package = _extract_pattern_value(_PACKAGE_PATTERN, content, default="")
        messages = _parse_messages(content)
        enums = _parse_enums(content)
        services = _parse_services(content)
        if not services:
            raise ValueError("No gRPC service definitions found in proto source.")

        enable_native_stream = _hint_enabled(source, "enable_native_grpc_stream")
        operations: list[Operation] = []
        ignored_streaming_rpcs: list[str] = []
        event_descriptors: list[EventDescriptor] = []
        for service in services:
            for rpc in service.rpcs:
                if rpc.client_streaming or rpc.server_streaming:
                    rpc_path = _rpc_path(proto_package, service.name, rpc.name)
                    grpc_stream_mode = _grpc_stream_mode_for_rpc(rpc)
                    supported_native_stream = (
                        enable_native_stream and grpc_stream_mode is GrpcStreamMode.server
                    )
                    if supported_native_stream:
                        operations.append(
                            _stream_rpc_to_operation(
                                rpc=rpc,
                                service_name=service.name,
                                package_name=proto_package,
                                messages=messages,
                                enums=enums,
                            )
                        )
                    else:
                        ignored_streaming_rpcs.append(rpc.name)
                    event_descriptors.append(
                        EventDescriptor(
                            id=rpc.name,
                            name=rpc.name,
                            operation_id=rpc.name if supported_native_stream else None,
                            transport=EventTransport.grpc_stream,
                            direction=_event_direction_for_rpc(rpc),
                            support=(
                                EventSupportLevel.supported
                                if supported_native_stream
                                else EventSupportLevel.unsupported
                            ),
                            channel=rpc_path,
                            grpc_stream=GrpcStreamRuntimeConfig(
                                rpc_path=rpc_path,
                                mode=grpc_stream_mode,
                            ),
                            metadata={
                                "client_streaming": rpc.client_streaming,
                                "server_streaming": rpc.server_streaming,
                            },
                        )
                    )
                    continue
                operations.append(
                    _rpc_to_operation(
                        rpc=rpc,
                        service_name=service.name,
                        package_name=proto_package,
                        messages=messages,
                        enums=enums,
                    )
                )

        if not operations:
            raise ValueError("No unary RPC methods found in proto source.")

        event_descriptors.sort(key=lambda descriptor: descriptor.id)

        primary_service_name = services[0].name
        display_name = " ".join(part for part in (proto_package, primary_service_name) if part)
        slug_name = _slugify(display_name or primary_service_name)
        base_url = source.url or f"grpc://{slug_name}"

        return ServiceIR(
            source_url=source.url,
            source_hash=source_hash,
            protocol="grpc",
            service_name=slug_name,
            service_description=f"gRPC service extracted from {primary_service_name}.",
            base_url=base_url,
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            event_descriptors=event_descriptors,
            metadata={
                "proto_syntax": proto_syntax,
                "proto_package": proto_package,
                "proto_service": primary_service_name,
                "proto_services": [service.name for service in services],
                "ignored_streaming_rpcs": ignored_streaming_rpcs,
            },
        )

    def _get_content(self, source: SourceConfig) -> str | None:
        if source.file_content:
            return source.file_content
        if source.file_path:
            return Path(source.file_path).read_text(encoding="utf-8")
        if source.url:
            try:
                response = httpx.get(source.url, timeout=30, headers=self._auth_headers(source))
                response.raise_for_status()
                return response.text
            except Exception:
                logger.warning("Failed to fetch proto source from %s", source.url, exc_info=True)
                return None
        return None

    def _auth_headers(self, source: SourceConfig) -> dict[str, str]:
        headers: dict[str, str] = {}
        if source.auth_header:
            headers["Authorization"] = source.auth_header
        elif source.auth_token:
            headers["Authorization"] = f"Bearer {source.auth_token}"
        return headers


def _parse_services(content: str) -> list[ProtoService]:
    services: list[ProtoService] = []
    for service_name, body in _find_blocks("service", content):
        rpcs = [
            ProtoRpc(
                name=rpc_match.group("name"),
                request_type=rpc_match.group("request"),
                response_type=rpc_match.group("response"),
                client_streaming=rpc_match.group("request_stream") is not None,
                server_streaming=rpc_match.group("response_stream") is not None,
            )
            for rpc_match in _RPC_PATTERN.finditer(body)
        ]
        services.append(ProtoService(name=service_name, rpcs=rpcs))
    return services


def _event_direction_for_rpc(rpc: ProtoRpc) -> EventDirection:
    if rpc.client_streaming and rpc.server_streaming:
        return EventDirection.bidirectional
    if rpc.client_streaming:
        return EventDirection.outbound
    return EventDirection.inbound


def _grpc_stream_mode_for_rpc(rpc: ProtoRpc) -> GrpcStreamMode:
    if rpc.client_streaming and rpc.server_streaming:
        return GrpcStreamMode.bidirectional
    if rpc.client_streaming:
        return GrpcStreamMode.client
    return GrpcStreamMode.server


def _parse_messages(content: str) -> dict[str, list[ProtoField]]:
    messages: dict[str, list[ProtoField]] = {}
    for name, body in _find_blocks("message", content):
        messages[name] = _parse_fields(body)
    return messages


def _parse_enums(content: str) -> set[str]:
    return {name for name, _ in _find_blocks("enum", content)}


def _parse_fields(body: str) -> list[ProtoField]:
    fields: list[ProtoField] = []
    for raw_line in body.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line or "=" not in line or line.startswith(("option ", "reserved ", "oneof ")):
            continue
        if line.endswith(";"):
            line = line[:-1]
        tokens = line.replace("=", " = ").split()
        if "=" not in tokens:
            continue
        equals_index = tokens.index("=")
        if equals_index < 2:
            continue
        field_name = tokens[equals_index - 1]
        type_tokens = tokens[: equals_index - 1]
        repeated = False
        required = False
        while type_tokens and type_tokens[0] in {"repeated", "optional", "required"}:
            label = type_tokens.pop(0)
            if label == "repeated":
                repeated = True
            elif label == "required":
                required = True
        if not type_tokens:
            continue
        type_name = " ".join(type_tokens)
        fields.append(
            ProtoField(
                name=field_name,
                type_name=type_name,
                repeated=repeated,
                required=required,
            )
        )
    return fields


def _rpc_to_operation(
    *,
    rpc: ProtoRpc,
    service_name: str,
    package_name: str,
    messages: dict[str, list[ProtoField]],
    enums: set[str],
) -> Operation:
    request_type = _strip_qualification(rpc.request_type)
    response_type = _strip_qualification(rpc.response_type)
    request_fields = messages.get(request_type, [])
    response_fields = messages.get(response_type, [])
    risk = _risk_for_rpc_name(rpc.name)
    return Operation(
        id=rpc.name,
        name=_humanize_identifier(rpc.name),
        description=f"Unary gRPC call {rpc.name} exposed from {service_name}.",
        method="POST",
        path=_rpc_path(package_name, service_name, rpc.name),
        params=[
            Param(
                name=field.name,
                type=_ir_type_for_proto_field(field, messages=messages, enums=enums),
                required=field.required,
                description="",
                source=SourceType.extractor,
                confidence=1.0,
            )
            for field in request_fields
        ],
        response_schema=_response_schema_for_fields(
            response_fields,
            messages=messages,
            enums=enums,
        ),
        risk=risk,
        grpc_unary=GrpcUnaryRuntimeConfig(rpc_path=_rpc_path(package_name, service_name, rpc.name)),
        tags=["grpc", service_name],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(
            responses=[
                ErrorResponse(
                    error_code="INVALID_ARGUMENT",
                    description="Client specified an invalid argument.",
                ),
                ErrorResponse(
                    error_code="NOT_FOUND",
                    description="Requested entity was not found.",
                ),
                ErrorResponse(
                    error_code="PERMISSION_DENIED",
                    description="Caller does not have permission.",
                ),
                ErrorResponse(
                    error_code="INTERNAL",
                    description="Internal server error.",
                ),
                ErrorResponse(
                    error_code="UNAVAILABLE",
                    description="Service is currently unavailable.",
                ),
            ]
        ),
    )


def _stream_rpc_to_operation(
    *,
    rpc: ProtoRpc,
    service_name: str,
    package_name: str,
    messages: dict[str, list[ProtoField]],
    enums: set[str],
) -> Operation:
    request_type = _strip_qualification(rpc.request_type)
    request_fields = messages.get(request_type, [])
    risk = _risk_for_rpc_name(rpc.name)
    return Operation(
        id=rpc.name,
        name=_humanize_identifier(rpc.name),
        description=f"Native gRPC stream {rpc.name} exposed from {service_name}.",
        method="POST",
        path=_rpc_path(package_name, service_name, rpc.name),
        params=[
            Param(
                name=field.name,
                type=_ir_type_for_proto_field(field, messages=messages, enums=enums),
                required=field.required,
                description="",
                source=SourceType.extractor,
                confidence=1.0,
            )
            for field in request_fields
        ],
        risk=risk,
        tags=["grpc", service_name, "stream"],
        source=SourceType.extractor,
        confidence=1.0,
        enabled=True,
        error_schema=ErrorSchema(
            responses=[
                ErrorResponse(
                    error_code="INVALID_ARGUMENT",
                    description="Client specified an invalid argument.",
                ),
                ErrorResponse(
                    error_code="NOT_FOUND",
                    description="Requested entity was not found.",
                ),
                ErrorResponse(
                    error_code="PERMISSION_DENIED",
                    description="Caller does not have permission.",
                ),
                ErrorResponse(
                    error_code="INTERNAL",
                    description="Internal server error.",
                ),
                ErrorResponse(
                    error_code="UNAVAILABLE",
                    description="Service is currently unavailable.",
                ),
            ]
        ),
    )


def _rpc_path(package_name: str, service_name: str, rpc_name: str) -> str:
    package_prefix = f"{package_name}." if package_name else ""
    return f"/{package_prefix}{service_name}/{rpc_name}"


def _response_schema_for_fields(
    fields: list[ProtoField],
    *,
    messages: dict[str, list[ProtoField]],
    enums: set[str],
) -> dict[str, Any] | None:
    if not fields:
        return None
    properties = {
        field.name: {"type": _ir_type_for_proto_field(field, messages=messages, enums=enums)}
        for field in fields
    }
    required = [field.name for field in fields if field.required]
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _ir_type_for_proto_field(
    field: ProtoField,
    *,
    messages: dict[str, list[ProtoField]],
    enums: set[str],
) -> str:
    if field.repeated:
        return "array"
    type_name = _strip_qualification(field.type_name)
    if type_name in _PROTO_TYPE_MAP:
        return _PROTO_TYPE_MAP[type_name]
    if type_name in enums:
        return "string"
    if type_name.startswith("map<"):
        return "object"
    if type_name in messages:
        return "object"
    return "object"


def _risk_for_rpc_name(rpc_name: str) -> RiskMetadata:
    if rpc_name.startswith(_SAFE_RPC_PREFIXES):
        return RiskMetadata(
            writes_state=False,
            destructive=False,
            external_side_effect=False,
            idempotent=True,
            risk_level=RiskLevel.safe,
            confidence=0.9,
            source=SourceType.extractor,
        )
    if rpc_name.startswith(_DANGEROUS_RPC_PREFIXES):
        return RiskMetadata(
            writes_state=True,
            destructive=True,
            external_side_effect=True,
            idempotent=False,
            risk_level=RiskLevel.dangerous,
            confidence=0.9,
            source=SourceType.extractor,
        )
    return RiskMetadata(
        writes_state=True,
        destructive=False,
        external_side_effect=True,
        idempotent=False,
        risk_level=RiskLevel.cautious,
        confidence=0.85,
        source=SourceType.extractor,
    )


def _extract_pattern_value(pattern: re.Pattern[str], content: str, *, default: str) -> str:
    match = pattern.search(content)
    if match is None:
        return default
    return str(next(iter(match.groupdict().values())))


def _strip_qualification(type_name: str) -> str:
    return type_name.rsplit(".", 1)[-1]


def _humanize_identifier(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).strip()


def _hint_enabled(source: SourceConfig, name: str) -> bool:
    value = source.hints.get(name)
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _slugify(text: str) -> str:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "-", text).lower().strip()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


__all__ = ["GrpcProtoExtractor"]
