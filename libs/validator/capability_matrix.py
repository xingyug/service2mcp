"""Protocol capability matrix for runtime maturity and proof coverage."""

from __future__ import annotations

from dataclasses import dataclass

from libs.ir.models import (
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    ServiceIR,
)


@dataclass(frozen=True)
class ProtocolCapability:
    """A single protocol/runtime capability row."""

    key: str
    label: str
    extract: bool
    compile: bool
    runtime: bool
    live_proof: bool
    llm_e2e: bool
    notes: str = ""


_CAPABILITY_ROWS: dict[str, ProtocolCapability] = {
    "openapi": ProtocolCapability(
        key="openapi",
        label="OpenAPI",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "Primary spec-driven HTTP path with LLM-enhanced proof."
            " Error model, response examples, drift detection."
        ),
    ),
    "rest": ProtocolCapability(
        key="rest",
        label="REST Discovery",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "Discovered HTTP paths have both local LLM-enabled E2E and live"
            " LLM-enhanced E2E proof. Error model, response examples, drift detection."
        ),
    ),
    "graphql": ProtocolCapability(
        key="graphql",
        label="GraphQL",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "Typed GraphQL runtime has both local LLM-enabled E2E and live"
            " LLM-enhanced E2E proof. Error model, response examples, drift detection."
        ),
    ),
    "grpc": ProtocolCapability(
        key="grpc",
        label="gRPC (generic)",
        extract=True,
        compile=True,
        runtime=False,
        live_proof=False,
        llm_e2e=False,
        notes=(
            "Only typed unary/server-stream slices are runtime-capable."
            " Error model, response examples, drift detection."
        ),
    ),
    "grpc_unary": ProtocolCapability(
        key="grpc_unary",
        label="gRPC Unary",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "Native unary runtime has both local LLM-enabled E2E and live"
            " LLM-enhanced E2E proof. Error model, response examples, drift detection."
        ),
    ),
    "grpc_stream": ProtocolCapability(
        key="grpc_stream",
        label="gRPC Server Stream",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "Native server-stream runtime has local LLM-enabled E2E and live"
            " E2E proof. Error model, response examples, drift detection."
        ),
    ),
    "soap": ProtocolCapability(
        key="soap",
        label="SOAP / WSDL",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "SOAP envelope execution has both local LLM-enabled E2E and live"
            " LLM-enhanced E2E proof. Error model, response examples, drift detection."
        ),
    ),
    "sql": ProtocolCapability(
        key="sql",
        label="SQL",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "Reflected SQL query/insert runtime has both local LLM-enabled E2E "
            "and LLM-enhanced E2E proof. Error model, response examples, drift detection."
        ),
    ),
    "odata": ProtocolCapability(
        key="odata",
        label="OData v4",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "OData v4 $metadata extraction produces CRUD operations per EntitySet "
            "with OData query params. Error model normalized. Dedicated runtime adapter "
            "re-adds $ prefix to system query options, unwraps collection responses, "
            "detects OData JSON errors. Local E2E proof plus LLM-enhanced E2E proof."
        ),
    ),
    "scim": ProtocolCapability(
        key="scim",
        label="SCIM 2.0",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "SCIM 2.0 schema extraction produces resource operations respecting "
            "attribute mutability. Error model normalized. Dedicated runtime adapter "
            "unwraps Resources array, detects SCIM error schema. "
            "Local E2E proof plus LLM-enhanced E2E proof."
        ),
    ),
    "jsonrpc": ProtocolCapability(
        key="jsonrpc",
        label="JSON-RPC 2.0",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=True,
        llm_e2e=True,
        notes=(
            "JSON-RPC 2.0 extraction from OpenRPC specs or manual definitions. "
            "Error model normalized. Dedicated runtime adapter wraps calls in "
            "JSON-RPC 2.0 envelope, unwraps result, detects JSON-RPC error responses. "
            "Local E2E proof plus LLM-enhanced E2E proof."
        ),
    ),
    "cli": ProtocolCapability(
        key="cli",
        label="CLI",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=False,
        llm_e2e=False,
        notes=(
            "CLI tool compilation from .cli.yaml specs. Subprocess-based execution "
            "with exit-code error handling. Error model normalized. Mock proof case "
            "registered; E2E proof pending real CLI target."
        ),
    ),
    "asyncapi": ProtocolCapability(
        key="asyncapi",
        label="AsyncAPI",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=False,
        llm_e2e=False,
        notes=(
            "AsyncAPI 2.x/3.x extraction with broker and webhook runtime dispatch. "
            "Error model normalized. Stub broker client in place; real broker clients "
            "(aiokafka, aio-pika, aiomqtt) are follow-on work. Mock proof case registered."
        ),
    ),
}

_CAPABILITY_ORDER = (
    "openapi",
    "rest",
    "graphql",
    "grpc",
    "grpc_unary",
    "grpc_stream",
    "soap",
    "sql",
    "odata",
    "scim",
    "jsonrpc",
    "cli",
    "asyncapi",
)


def protocol_capability_matrix() -> tuple[ProtocolCapability, ...]:
    """Return the ordered protocol capability matrix."""

    return tuple(_CAPABILITY_ROWS[key] for key in _CAPABILITY_ORDER)


def protocol_capability_key(service_ir: ServiceIR) -> str:
    """Resolve the capability row key for a concrete IR instance."""

    protocol = str(service_ir.protocol)
    if protocol != "grpc":
        return protocol

    if any(
        _supports_native_grpc_stream_capability(descriptor)
        for descriptor in service_ir.event_descriptors
    ):
        return "grpc_stream"

    if any(
        operation.enabled and operation.grpc_unary is not None
        for operation in service_ir.operations
    ):
        return "grpc_unary"

    return "grpc"


def protocol_capability_for_service(service_ir: ServiceIR) -> ProtocolCapability:
    """Return the capability row that matches a concrete IR instance."""

    key = protocol_capability_key(service_ir)
    return _CAPABILITY_ROWS.get(key, _unknown_protocol_capability(key))


def _supports_native_grpc_stream_capability(descriptor: EventDescriptor) -> bool:
    return (
        descriptor.transport is EventTransport.grpc_stream
        and descriptor.support is EventSupportLevel.supported
        and descriptor.operation_id is not None
        and descriptor.grpc_stream is not None
        and descriptor.grpc_stream.mode is GrpcStreamMode.server
    )


def _unknown_protocol_capability(protocol: str) -> ProtocolCapability:
    return ProtocolCapability(
        key=protocol,
        label=f"Unknown ({protocol})",
        extract=False,
        compile=False,
        runtime=False,
        live_proof=False,
        llm_e2e=False,
        notes=(
            "Unknown protocol; no curated capability row is available, so extraction, "
            "runtime, and proof support are treated conservatively as unsupported."
        ),
    )
