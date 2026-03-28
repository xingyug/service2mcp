"""Protocol capability matrix for runtime maturity and proof coverage."""

from __future__ import annotations

from dataclasses import dataclass

from libs.ir.models import EventSupportLevel, EventTransport, ServiceIR


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
            "Primary spec-driven HTTP path with live DeepSeek-enhanced proof."
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
            " GKE DeepSeek proof. Error model, response examples, drift detection."
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
            " GKE DeepSeek proof. Error model, response examples, drift detection."
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
            " GKE DeepSeek proof. Error model, response examples, drift detection."
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
            " GKE proof. Error model, response examples, drift detection."
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
            " GKE DeepSeek proof. Error model, response examples, drift detection."
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
            "and live GKE DeepSeek proof. Error model, response examples, drift detection."
        ),
    ),
    "odata": ProtocolCapability(
        key="odata",
        label="OData v4",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=False,
        llm_e2e=False,
        notes=(
            "OData v4 $metadata extraction produces CRUD operations per EntitySet "
            "with OData query params. Error model normalized. Dedicated runtime adapter "
            "re-adds $ prefix to system query options, unwraps collection responses, "
            "detects OData JSON errors. Local E2E proof via integration tests."
        ),
    ),
    "scim": ProtocolCapability(
        key="scim",
        label="SCIM 2.0",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=False,
        llm_e2e=False,
        notes=(
            "SCIM 2.0 schema extraction produces resource operations respecting "
            "attribute mutability. Error model normalized. Dedicated runtime adapter "
            "unwraps Resources array, detects SCIM error schema. "
            "Local E2E proof via integration tests."
        ),
    ),
    "jsonrpc": ProtocolCapability(
        key="jsonrpc",
        label="JSON-RPC 2.0",
        extract=True,
        compile=True,
        runtime=True,
        live_proof=False,
        llm_e2e=False,
        notes=(
            "JSON-RPC 2.0 extraction from OpenRPC specs or manual definitions. "
            "Error model normalized. Dedicated runtime adapter wraps calls in "
            "JSON-RPC 2.0 envelope, unwraps result, detects JSON-RPC error responses. "
            "Local E2E proof via integration tests."
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
)


def protocol_capability_matrix() -> tuple[ProtocolCapability, ...]:
    """Return the ordered protocol capability matrix."""

    return tuple(_CAPABILITY_ROWS[key] for key in _CAPABILITY_ORDER)


def protocol_capability_key(service_ir: ServiceIR) -> str:
    """Resolve the capability row key for a concrete IR instance."""

    if service_ir.protocol != "grpc":
        return service_ir.protocol

    if any(
        descriptor.transport is EventTransport.grpc_stream
        and descriptor.support is EventSupportLevel.supported
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

    return _CAPABILITY_ROWS[protocol_capability_key(service_ir)]
