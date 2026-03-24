"""Intermediate Representation (IR) — the central contract between extractors and consumers.

All types defined here follow the spec in Section 7, Module: `ir` of the SDD.
The IR is versioned, persisted, diffable, and the single source of truth for
what a compiled service looks like.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    safe = "safe"
    cautious = "cautious"
    dangerous = "dangerous"
    unknown = "unknown"


class SourceType(str, Enum):
    extractor = "extractor"
    llm = "llm"
    user_override = "user_override"


class AuthType(str, Enum):
    bearer = "bearer"
    basic = "basic"
    api_key = "api_key"
    custom_header = "custom_header"
    oauth2 = "oauth2"
    none = "none"


class TruncationPolicy(str, Enum):
    none = "none"
    truncate = "truncate"
    summarize = "summarize"


# ── Component Models ───────────────────────────────────────────────────────

class Param(BaseModel):
    """A single parameter for an operation."""

    name: str
    type: str = Field(description="JSON Schema type (string, integer, number, boolean, array, object)")
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
    max_response_bytes: int | None = None
    field_filter: list[str] | None = None
    truncation_policy: TruncationPolicy = TruncationPolicy.none


class Operation(BaseModel):
    """A single callable operation exposed as an MCP tool."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    method: str | None = None
    path: str | None = None
    params: list[Param] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    risk: RiskMetadata = Field(default_factory=RiskMetadata)
    response_strategy: ResponseStrategy = Field(default_factory=ResponseStrategy)
    tags: list[str] = Field(default_factory=list)
    source: SourceType = SourceType.extractor
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    enabled: bool = True

    @model_validator(mode="after")
    def unknown_risk_must_be_disabled(self) -> Operation:
        if self.risk.risk_level == RiskLevel.unknown and self.enabled:
            raise ValueError(
                f"Operation '{self.id}' has risk_level='unknown' but enabled=True. "
                f"Operations with unknown risk must be disabled."
            )
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


class OperationChain(BaseModel):
    """A sequence of operations that should be invoked together."""

    id: str = Field(min_length=1)
    name: str
    description: str = ""
    steps: list[str] = Field(description="Ordered list of operation IDs")


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
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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
