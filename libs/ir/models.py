"""Intermediate Representation (IR) — the central contract between extractors and consumers.

All types defined here follow the spec in Section 7, Module: `ir` of the SDD.
The IR is versioned, persisted, diffable, and the single source of truth for
what a compiled service looks like.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ── Enums ──────────────────────────────────────────────────────────────────


class RiskLevel(StrEnum):
    safe = "safe"
    cautious = "cautious"
    dangerous = "dangerous"
    unknown = "unknown"


class SourceType(StrEnum):
    extractor = "extractor"
    llm = "llm"
    user_override = "user_override"


class AuthType(StrEnum):
    bearer = "bearer"
    basic = "basic"
    api_key = "api_key"
    custom_header = "custom_header"
    oauth2 = "oauth2"
    none = "none"


class TruncationPolicy(StrEnum):
    none = "none"
    truncate = "truncate"
    summarize = "summarize"


class RequestBodyMode(StrEnum):
    json = "json"
    multipart = "multipart"
    raw = "raw"


class AsyncStatusUrlSource(StrEnum):
    location_header = "location_header"
    response_body = "response_body"


class EventTransport(StrEnum):
    websocket = "websocket"
    sse = "sse"
    webhook = "webhook"
    callback = "callback"
    graphql_subscription = "graphql_subscription"
    grpc_stream = "grpc_stream"
    async_event = "async_event"


class EventDirection(StrEnum):
    inbound = "inbound"
    outbound = "outbound"
    bidirectional = "bidirectional"


class EventSupportLevel(StrEnum):
    unsupported = "unsupported"
    planned = "planned"
    supported = "supported"


class GrpcStreamMode(StrEnum):
    server = "server"
    client = "client"
    bidirectional = "bidirectional"


class ToolIntent(StrEnum):
    """Whether a tool is for read-only discovery or state-mutating action."""

    discovery = "discovery"
    action = "action"


# ── Component Models ───────────────────────────────────────────────────────


class Param(BaseModel):
    """A single parameter for an operation."""

    name: str
    type: str = Field(
        description="JSON Schema type (string, integer, number, boolean, array, object)"
    )
    required: bool = False
    description: str = ""
    default: Any | None = None
    source: SourceType = SourceType.extractor
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def extractor_source_requires_high_confidence(self) -> Param:
        if self.source == SourceType.extractor and self.confidence < 0.8:
            raise ValueError(
                f"Param '{self.name}' with source='extractor' must have confidence >= 0.8, "
                f"got {self.confidence}"
            )
        return self


class RiskMetadata(BaseModel):
    """Semantic risk classification for an operation."""

    writes_state: bool | None = None
    destructive: bool | None = None
    external_side_effect: bool | None = None
    idempotent: bool | None = None
    risk_level: RiskLevel = RiskLevel.unknown
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: SourceType = SourceType.extractor


class PaginationConfig(BaseModel):
    """Pagination strategy for an operation's response."""

    style: Literal["offset", "cursor", "page"] = "offset"
    page_param: str = "page"
    size_param: str = "page_size"
    default_size: int = 20
    max_size: int = 100


class ResponseStrategy(BaseModel):
    """How to handle the response from an upstream API call."""

    pagination: PaginationConfig | None = None
    max_response_bytes: int | None = Field(default=None, gt=0)
    max_array_items: int | None = Field(default=None, ge=1)
    field_filter: list[str] | None = None
    truncation_policy: TruncationPolicy = TruncationPolicy.none


class OAuth2ClientCredentialsConfig(BaseModel):
    """OAuth2 client credentials grant configuration."""

    token_url: str
    client_id_ref: str
    client_secret_ref: str
    scopes: list[str] = Field(default_factory=list)
    audience: str | None = None
    client_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic"


class MTLSConfig(BaseModel):
    """mTLS certificate references for runtime upstream calls."""

    cert_ref: str
    key_ref: str
    ca_ref: str | None = None


class RequestSigningConfig(BaseModel):
    """Request-signing configuration for upstream requests."""

    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    secret_ref: str
    signature_header_name: str = "X-Signature"
    timestamp_header_name: str = "X-Timestamp"
    key_id: str | None = None
    key_id_header_name: str = "X-Key-Id"


class AsyncJobConfig(BaseModel):
    """Polling configuration for async job style APIs."""

    initial_status_codes: list[int] = Field(default_factory=lambda: [202])
    status_url_source: AsyncStatusUrlSource = AsyncStatusUrlSource.location_header
    status_url_field: str | None = None
    status_field: str = "status"
    pending_status_values: list[str] = Field(
        default_factory=lambda: ["pending", "queued", "running", "in_progress"]
    )
    success_status_values: list[str] = Field(
        default_factory=lambda: ["completed", "succeeded", "done", "success"]
    )
    failure_status_values: list[str] = Field(
        default_factory=lambda: ["failed", "error", "cancelled", "canceled"]
    )
    poll_interval_seconds: float = Field(default=0.5, gt=0.0)
    timeout_seconds: float = Field(default=30.0, gt=0.0)

    @model_validator(mode="after")
    def response_body_source_requires_field(self) -> AsyncJobConfig:
        if (
            self.status_url_source == AsyncStatusUrlSource.response_body
            and not self.status_url_field
        ):
            raise ValueError("response_body async job source requires status_url_field.")
        return self


class GrpcStreamRuntimeConfig(BaseModel):
    """Native runtime configuration for a gRPC streaming contract."""

    rpc_path: str = Field(min_length=1)
    mode: GrpcStreamMode
    max_messages: int = Field(default=50, gt=0)
    idle_timeout_seconds: float = Field(default=15.0, gt=0.0)


class GrpcUnaryRuntimeConfig(BaseModel):
    """Native runtime configuration for a unary gRPC contract."""

    rpc_path: str = Field(min_length=1)
    timeout_seconds: float = Field(default=10.0, gt=0.0)


class SoapOperationConfig(BaseModel):
    """Typed SOAP execution contract for one WSDL-derived operation."""

    target_namespace: str = Field(min_length=1)
    request_element: str = Field(min_length=1)
    response_element: str | None = None
    soap_action: str | None = None
    soap_version: Literal["1.1"] = "1.1"
    binding_style: Literal["document"] = "document"
    body_use: Literal["literal"] = "literal"


class GraphQLOperationType(StrEnum):
    """GraphQL root operation kinds."""

    query = "query"
    mutation = "mutation"


class SqlRelationKind(StrEnum):
    """Reflected SQL relation kinds."""

    table = "table"
    view = "view"


class SqlOperationType(StrEnum):
    """Supported SQL runtime actions."""

    query = "query"
    insert = "insert"


class JsonRpcOperationConfig(BaseModel):
    """Typed JSON-RPC 2.0 execution contract for one IR operation."""

    jsonrpc_version: Literal["2.0"] = "2.0"
    method_name: str = Field(min_length=1, description="JSON-RPC method name (e.g. 'user.getById')")
    params_type: Literal["positional", "named"] = "named"
    params_names: list[str] = Field(default_factory=list)
    result_schema: dict[str, Any] | None = None


class GraphQLOperationConfig(BaseModel):
    """Typed GraphQL execution contract for one IR operation."""

    operation_type: GraphQLOperationType
    operation_name: str = Field(min_length=1)
    document: str = Field(min_length=1)
    variable_names: list[str] = Field(default_factory=list)


class SqlOperationConfig(BaseModel):
    """Typed SQL execution contract for one reflected relation operation."""

    schema_name: str = Field(min_length=1)
    relation_name: str = Field(min_length=1)
    relation_kind: SqlRelationKind
    action: SqlOperationType
    filterable_columns: list[str] = Field(default_factory=list)
    insertable_columns: list[str] = Field(default_factory=list)
    default_limit: int = Field(default=50, gt=0)
    max_limit: int = Field(default=200, gt=0)

    @model_validator(mode="after")
    def sql_contract_must_be_coherent(self) -> SqlOperationConfig:
        if self.default_limit > self.max_limit:
            raise ValueError("sql default_limit must be <= max_limit.")
        if self.action is SqlOperationType.query and not self.filterable_columns:
            raise ValueError("sql query operations require filterable_columns.")
        if self.action is SqlOperationType.insert:
            if self.relation_kind is not SqlRelationKind.table:
                raise ValueError("sql insert operations require relation_kind='table'.")
            if not self.insertable_columns:
                raise ValueError("sql insert operations require insertable_columns.")
        return self


class EventDescriptor(BaseModel):
    """Metadata describing an event-driven contract the runtime may not yet execute."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    transport: EventTransport
    direction: EventDirection = EventDirection.inbound
    support: EventSupportLevel = EventSupportLevel.unsupported
    channel: str | None = None
    operation_id: str | None = None
    grpc_stream: GrpcStreamRuntimeConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def grpc_stream_config_must_match_transport(self) -> EventDescriptor:
        if self.transport is EventTransport.grpc_stream:
            if self.grpc_stream is None:
                raise ValueError("grpc_stream descriptors require grpc_stream runtime config.")
            if self.channel is not None and self.channel != self.grpc_stream.rpc_path:
                raise ValueError("grpc_stream descriptor channel must match grpc_stream.rpc_path.")
            return self

        if self.grpc_stream is not None:
            raise ValueError("grpc_stream runtime config is only valid for grpc_stream transport.")
        return self


class ErrorResponse(BaseModel):
    """A single documented error response for an operation."""

    status_code: int | None = None  # None for non-HTTP protocols
    error_code: str | None = None  # protocol-specific error code
    description: str = ""
    error_body_schema: dict[str, Any] | None = None  # JSON Schema of error body


class ErrorSchema(BaseModel):
    """Unified error model for an operation, normalized across protocols."""

    responses: list[ErrorResponse] = Field(default_factory=list)
    default_error_schema: dict[str, Any] | None = None  # fallback error shape


class ResponseExample(BaseModel):
    """A synthetic or extracted example response for LLM context."""

    name: str = Field(min_length=1)
    description: str = ""
    status_code: int | None = None
    body: dict[str, Any] | str | None = None
    source: SourceType = SourceType.extractor


class Operation(BaseModel):
    """A single callable operation exposed as an MCP tool."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    method: str | None = None
    path: str | None = None
    params: list[Param] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    error_schema: ErrorSchema = Field(default_factory=ErrorSchema)
    response_examples: list[ResponseExample] = Field(default_factory=list)
    risk: RiskMetadata = Field(default_factory=RiskMetadata)
    response_strategy: ResponseStrategy = Field(default_factory=ResponseStrategy)
    request_body_mode: RequestBodyMode = RequestBodyMode.json
    body_param_name: str | None = None
    async_job: AsyncJobConfig | None = None
    graphql: GraphQLOperationConfig | None = None
    sql: SqlOperationConfig | None = None
    grpc_unary: GrpcUnaryRuntimeConfig | None = None
    soap: SoapOperationConfig | None = None
    jsonrpc: JsonRpcOperationConfig | None = None
    tags: list[str] = Field(default_factory=list)
    tool_intent: ToolIntent | None = None
    source: SourceType = SourceType.extractor
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    enabled: bool = True

    @model_validator(mode="after")
    def unknown_risk_must_be_disabled(self) -> Operation:
        if self.risk.risk_level == RiskLevel.unknown and self.enabled:
            self.enabled = False
        return self

    @model_validator(mode="after")
    def grpc_unary_contract_must_be_coherent(self) -> Operation:
        if self.grpc_unary is None:
            return self

        if self.graphql is not None:
            raise ValueError("grpc_unary execution contract cannot be combined with graphql.")
        if self.sql is not None:
            raise ValueError("grpc_unary execution contract cannot be combined with sql.")
        if self.soap is not None:
            raise ValueError("grpc_unary execution contract cannot be combined with soap.")
        if self.jsonrpc is not None:
            raise ValueError("grpc_unary execution contract cannot be combined with jsonrpc.")
        if self.method is not None and self.method.upper() != "POST":
            raise ValueError("grpc_unary operations must use method='POST'.")
        if self.path is not None and self.path != self.grpc_unary.rpc_path:
            raise ValueError("grpc_unary rpc_path must match operation.path.")
        return self

    @model_validator(mode="after")
    def soap_contract_must_be_coherent(self) -> Operation:
        if self.soap is None:
            return self

        if self.graphql is not None:
            raise ValueError("soap execution contract cannot be combined with graphql.")
        if self.sql is not None:
            raise ValueError("soap execution contract cannot be combined with sql.")
        if self.grpc_unary is not None:
            raise ValueError("soap execution contract cannot be combined with grpc_unary.")
        if self.jsonrpc is not None:
            raise ValueError("soap execution contract cannot be combined with jsonrpc.")
        if self.method is not None and self.method.upper() != "POST":
            raise ValueError("soap operations must use method='POST'.")
        return self

    @model_validator(mode="after")
    def sql_contract_must_match_operation_shape(self) -> Operation:
        if self.sql is None:
            return self

        if self.graphql is not None:
            raise ValueError("sql execution contract cannot be combined with graphql.")
        if self.grpc_unary is not None:
            raise ValueError("sql execution contract cannot be combined with grpc_unary.")
        if self.soap is not None:
            raise ValueError("sql execution contract cannot be combined with soap.")
        if self.jsonrpc is not None:
            raise ValueError("sql execution contract cannot be combined with jsonrpc.")

        expected_method = "GET" if self.sql.action is SqlOperationType.query else "POST"
        if self.method is not None and self.method.upper() != expected_method:
            raise ValueError(
                f"sql {self.sql.action.value} operations must use method='{expected_method}'."
            )
        return self

    @model_validator(mode="after")
    def jsonrpc_contract_must_be_coherent(self) -> Operation:
        if self.jsonrpc is None:
            return self

        for other, label in (
            (self.graphql, "graphql"),
            (self.sql, "sql"),
            (self.grpc_unary, "grpc_unary"),
            (self.soap, "soap"),
        ):
            if other is not None:
                raise ValueError(f"jsonrpc execution contract cannot be combined with {label}.")
        if self.method is not None and self.method.upper() != "POST":
            raise ValueError("jsonrpc operations must use method='POST'.")
        return self


class AuthConfig(BaseModel):
    """Authentication configuration for accessing the upstream API."""

    type: AuthType = AuthType.none
    header_name: str | None = None
    header_prefix: str | None = None
    api_key_param: str | None = None
    api_key_location: Literal["header", "query"] | None = None
    oauth2_token_url: str | None = None
    oauth2_scopes: list[str] | None = None
    compile_time_secret_ref: str | None = None
    runtime_secret_ref: str | None = None
    oauth2: OAuth2ClientCredentialsConfig | None = None
    mtls: MTLSConfig | None = None
    request_signing: RequestSigningConfig | None = None

    @model_validator(mode="after")
    def nested_auth_configuration_must_be_coherent(self) -> AuthConfig:
        if self.type == AuthType.custom_header and not self.header_name:
            raise ValueError("custom_header auth requires header_name.")

        if self.oauth2 is not None and self.type != AuthType.oauth2:
            raise ValueError("oauth2 client credentials config requires auth.type=oauth2.")

        return self


class OperationChain(BaseModel):
    """A sequence of operations that should be invoked together."""

    id: str = Field(min_length=1)
    name: str
    description: str = ""
    steps: list[str] = Field(description="Ordered list of operation IDs")


class ToolGroup(BaseModel):
    """A semantic grouping of operations by business intent."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    intent: str = ""
    operation_ids: list[str] = Field(default_factory=list)
    source: SourceType = SourceType.extractor
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


# ── MCP Resource & Prompt Models ──────────────────────────────────────────


class ResourceDefinition(BaseModel):
    """A read-only data resource the agent can access as context."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    uri: str = Field(min_length=1, description="MCP resource URI, e.g. 'service://petstore/schema'")
    mime_type: str = "application/json"
    content_type: Literal["static", "dynamic"] = "static"
    content: str | None = None
    operation_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class PromptArgument(BaseModel):
    """An argument for a prompt template."""

    name: str = Field(min_length=1)
    description: str = ""
    required: bool = False
    default: str | None = None


class PromptDefinition(BaseModel):
    """A reusable prompt template for interacting with the service's tools."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    template: str = Field(
        min_length=1,
        description="Prompt template text with {placeholder} variables",
    )
    arguments: list[PromptArgument] = Field(default_factory=list)
    tool_ids: list[str] = Field(
        default_factory=list,
        description="Operations this prompt is designed for",
    )
    tags: list[str] = Field(default_factory=list)


# ── Top-Level IR ───────────────────────────────────────────────────────────

IR_VERSION = "1.0.0"


class ServiceIR(BaseModel):
    """The complete Intermediate Representation of a compiled service.

    This is the single source of truth for what a service looks like after
    compilation. Everything upstream (extractors) produces this; everything
    downstream (runtime, generator, registry) consumes it.
    """

    ir_version: str = Field(default=IR_VERSION)
    compiler_version: str = Field(default="0.1.0")
    source_url: str | None = None
    source_hash: str = Field(description="SHA256 of source input")
    protocol: str = Field(description="openapi, rest, graphql, sql, etc.")
    service_name: str = Field(min_length=1)
    service_description: str = ""
    base_url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    operations: list[Operation] = Field(default_factory=list)
    operation_chains: list[OperationChain] = Field(default_factory=list)
    tool_grouping: list[ToolGroup] = Field(default_factory=list)
    event_descriptors: list[EventDescriptor] = Field(default_factory=list)
    resource_definitions: list[ResourceDefinition] = Field(default_factory=list)
    prompt_definitions: list[PromptDefinition] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant: str | None = None
    environment: str | None = None

    @model_validator(mode="after")
    def operation_ids_must_be_unique(self) -> ServiceIR:
        ids = [op.id for op in self.operations]
        duplicates = {x for x in ids if ids.count(x) > 1}
        if duplicates:
            raise ValueError(f"Duplicate operation IDs: {duplicates}")
        return self

    @model_validator(mode="after")
    def chain_steps_must_reference_valid_operations(self) -> ServiceIR:
        op_ids = {op.id for op in self.operations}
        for chain in self.operation_chains:
            invalid = set(chain.steps) - op_ids
            if invalid:
                raise ValueError(
                    f"OperationChain '{chain.id}' references unknown operations: {invalid}"
                )
        return self

    @model_validator(mode="after")
    def event_descriptors_must_reference_valid_operations(self) -> ServiceIR:
        op_ids = {op.id for op in self.operations}
        invalid_refs = {
            descriptor.operation_id
            for descriptor in self.event_descriptors
            if descriptor.operation_id is not None and descriptor.operation_id not in op_ids
        }
        if invalid_refs:
            raise ValueError(
                f"Event descriptors reference unknown operations: {sorted(invalid_refs)}"
            )
        return self

    @model_validator(mode="after")
    def tool_grouping_must_reference_valid_operations(self) -> ServiceIR:
        op_ids = {op.id for op in self.operations}
        for group in self.tool_grouping:
            invalid = set(group.operation_ids) - op_ids
            if invalid:
                raise ValueError(f"ToolGroup '{group.id}' references unknown operations: {invalid}")
        return self

    @model_validator(mode="after")
    def resource_definition_ids_must_be_unique(self) -> ServiceIR:
        ids = [r.id for r in self.resource_definitions]
        duplicates = {x for x in ids if ids.count(x) > 1}
        if duplicates:
            raise ValueError(f"Duplicate resource definition IDs: {duplicates}")
        return self

    @model_validator(mode="after")
    def prompt_definition_ids_must_be_unique(self) -> ServiceIR:
        ids = [p.id for p in self.prompt_definitions]
        duplicates = {x for x in ids if ids.count(x) > 1}
        if duplicates:
            raise ValueError(f"Duplicate prompt definition IDs: {duplicates}")
        return self

    @model_validator(mode="after")
    def prompt_tool_ids_must_reference_valid_operations(self) -> ServiceIR:
        op_ids = {op.id for op in self.operations}
        for prompt in self.prompt_definitions:
            invalid = set(prompt.tool_ids) - op_ids
            if invalid:
                raise ValueError(
                    f"PromptDefinition '{prompt.id}' references unknown operations: {invalid}"
                )
        return self

    @model_validator(mode="after")
    def resource_operation_ids_must_reference_valid_operations(self) -> ServiceIR:
        op_ids = {op.id for op in self.operations}
        invalid_refs = {
            resource.operation_id
            for resource in self.resource_definitions
            if resource.operation_id is not None and resource.operation_id not in op_ids
        }
        if invalid_refs:
            raise ValueError(
                f"Resource definitions reference unknown operations: {sorted(invalid_refs)}"
            )
        return self
