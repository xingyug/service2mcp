"""Pre-deploy validation harness for compiled service IR artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field

from libs.ir.models import AuthType, EventSupportLevel, EventTransport, ServiceIR
from libs.validator.audit import ToolAuditSummary

_APPROVED_STREAM_TRANSPORTS = {EventTransport.sse, EventTransport.websocket}


class ValidationResult(BaseModel):
    """A single validation check outcome."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    passed: bool
    details: str
    duration_ms: int


class ValidationReport(BaseModel):
    """Aggregate validation results for a single pre-deploy run."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    results: list[ValidationResult]
    overall_passed: bool
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit_summary: ToolAuditSummary | None = None

    def get_result(self, stage: str) -> ValidationResult:
        """Return the result for the named validation stage."""

        for result in self.results:
            if result.stage == stage:
                return result
        raise KeyError(f"Validation stage {stage!r} not present in report.")


class PreDeployValidator:
    """Validate IR structure and auth reachability before deployment."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
        allow_native_grpc_stream: bool = False,
        allow_native_grpc_unary: bool = False,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._owns_client = client is None
        self._allow_native_grpc_stream = allow_native_grpc_stream
        self._allow_native_grpc_unary = allow_native_grpc_unary

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def validate(self, ir_payload: ServiceIR | dict[str, Any]) -> ValidationReport:
        """Run all pre-deploy validation checks and return an aggregate report."""

        schema_result, service_ir = self._validate_schema(ir_payload)
        results = [schema_result]

        if service_ir is None:
            results.append(
                ValidationResult(
                    stage="event_support",
                    passed=False,
                    details="Skipped because IR schema validation failed.",
                    duration_ms=0,
                )
            )
            results.append(
                ValidationResult(
                    stage="auth_smoke",
                    passed=False,
                    details="Skipped because IR schema validation failed.",
                    duration_ms=0,
                )
            )
        else:
            results.append(self._validate_event_support(service_ir))
            results.append(await self._validate_auth_smoke(service_ir))

        return ValidationReport(
            results=results,
            overall_passed=all(result.passed for result in results),
        )

    def _validate_schema(
        self,
        ir_payload: ServiceIR | dict[str, Any],
    ) -> tuple[ValidationResult, ServiceIR | None]:
        started_at = perf_counter()
        try:
            service_ir = (
                ir_payload
                if isinstance(ir_payload, ServiceIR)
                else ServiceIR.model_validate(ir_payload)
            )
        except Exception as exc:
            return (
                ValidationResult(
                    stage="schema",
                    passed=False,
                    details=f"IR schema validation failed: {exc}",
                    duration_ms=self._duration_ms(started_at),
                ),
                None,
            )

        return (
            ValidationResult(
                stage="schema",
                passed=True,
                details="IR schema validation passed.",
                duration_ms=self._duration_ms(started_at),
            ),
            service_ir,
        )

    async def _validate_auth_smoke(self, service_ir: ServiceIR) -> ValidationResult:
        started_at = perf_counter()
        auth = service_ir.auth
        details: list[str] = []

        if (
            auth.type is AuthType.none
            and auth.mtls is None
            and auth.request_signing is None
        ):
            return ValidationResult(
                stage="auth_smoke",
                passed=True,
                details="No auth smoke test required for auth.type=none.",
                duration_ms=self._duration_ms(started_at),
            )

        if auth.type is AuthType.oauth2:
            oauth_result = await self._validate_oauth2_endpoint(service_ir, started_at)
            if not oauth_result.passed:
                return oauth_result
            details.append(oauth_result.details)
        elif auth.type is not AuthType.none:
            if not auth.compile_time_secret_ref and not auth.runtime_secret_ref:
                return ValidationResult(
                    stage="auth_smoke",
                    passed=False,
                    details=(
                        "Auth configuration requires a compile_time_secret_ref or "
                        "runtime_secret_ref."
                    ),
                    duration_ms=self._duration_ms(started_at),
                )
            details.append("Primary auth configuration includes a secret reference.")

        if auth.mtls is not None:
            details.append("mTLS certificate references configured.")

        if auth.request_signing is not None:
            details.append("Request signing configuration present.")

        return ValidationResult(
            stage="auth_smoke",
            passed=True,
            details=" ".join(details),
            duration_ms=self._duration_ms(started_at),
        )

    async def _validate_oauth2_endpoint(
        self,
        service_ir: ServiceIR,
        started_at: float,
    ) -> ValidationResult:
        auth = service_ir.auth
        token_url: str | None
        if auth.oauth2 is not None:
            token_url = auth.oauth2.token_url
            details_prefix = "OAuth2 client credentials endpoint reachable"
        else:
            token_url = auth.oauth2_token_url
            details_prefix = "Auth token endpoint reachable"

        if not token_url:
            return ValidationResult(
                stage="auth_smoke",
                passed=False,
                details=(
                    "OAuth2 auth requires oauth2 client credentials config "
                    "or oauth2_token_url."
                ),
                duration_ms=self._duration_ms(started_at),
            )
        assert token_url is not None

        if auth.oauth2 is None and not auth.compile_time_secret_ref and not auth.runtime_secret_ref:
            return ValidationResult(
                stage="auth_smoke",
                passed=False,
                details="OAuth2 auth requires a compile_time_secret_ref or runtime_secret_ref.",
                duration_ms=self._duration_ms(started_at),
            )

        try:
            response = await self._client.get(token_url)
        except httpx.RequestError as exc:
            return ValidationResult(
                stage="auth_smoke",
                passed=False,
                details=f"Auth smoke test could not reach token endpoint: {exc}",
                duration_ms=self._duration_ms(started_at),
            )

        if response.status_code == 404 or response.status_code >= 500:
            return ValidationResult(
                stage="auth_smoke",
                passed=False,
                details=(
                    "Auth smoke test received an unhealthy response from token endpoint: "
                    f"HTTP {response.status_code}"
                ),
                duration_ms=self._duration_ms(started_at),
            )

        return ValidationResult(
            stage="auth_smoke",
            passed=True,
            details=f"{details_prefix}: HTTP {response.status_code}.",
            duration_ms=self._duration_ms(started_at),
        )

    def _validate_event_support(self, service_ir: ServiceIR) -> ValidationResult:
        started_at = perf_counter()
        unsupported_ids = []
        supported_ids = []
        invalid_descriptors: list[str] = []
        supported_native_operations: list[str] = []

        for descriptor in service_ir.event_descriptors:
            if descriptor.support is EventSupportLevel.unsupported:
                unsupported_ids.append(descriptor.id)
                continue
            if descriptor.support is EventSupportLevel.planned:
                invalid_descriptors.append(f"{descriptor.id}=planned")
                continue
            if descriptor.transport not in _APPROVED_STREAM_TRANSPORTS:
                if descriptor.transport is EventTransport.grpc_stream:
                    if descriptor.operation_id is None:
                        invalid_descriptors.append(f"{descriptor.id}=missing_operation_id")
                        continue
                    if descriptor.grpc_stream is None:
                        invalid_descriptors.append(f"{descriptor.id}=missing_grpc_stream")
                        continue
                    if not self._allow_native_grpc_stream:
                        invalid_descriptors.append(f"{descriptor.id}=grpc_stream_disabled")
                        continue
                    supported_ids.append(
                        f"{descriptor.id}({descriptor.transport.value})"
                    )
                    continue
                invalid_descriptors.append(
                    f"{descriptor.id}={descriptor.transport.value}"
                )
                continue
            if descriptor.operation_id is None:
                invalid_descriptors.append(f"{descriptor.id}=missing_operation_id")
                continue
            supported_ids.append(
                f"{descriptor.id}({descriptor.transport.value})"
            )

        for operation in service_ir.operations:
            if operation.grpc_unary is None:
                continue
            if not self._allow_native_grpc_unary:
                invalid_descriptors.append(f"{operation.id}=grpc_unary_disabled")
                continue
            supported_native_operations.append(f"{operation.id}(grpc_unary)")

        if invalid_descriptors:
            return ValidationResult(
                stage="event_support",
                passed=False,
                details=(
                    "Streaming/event descriptors and native grpc_unary operations must "
                    "either stay unsupported, use approved HTTP-native transports with "
                    "an operation reference, or use grpc_stream/grpc_unary with "
                    "explicit native runtime enablement: "
                    f"{', '.join(invalid_descriptors)}"
                ),
                duration_ms=self._duration_ms(started_at),
            )

        details_parts: list[str] = []
        if supported_ids:
            details_parts.append(
                "Approved streaming transports configured: "
                f"{', '.join(supported_ids)}"
            )
        if supported_native_operations:
            details_parts.append(
                "Approved native unary transports configured: "
                f"{', '.join(supported_native_operations)}"
            )
        if unsupported_ids:
            details_parts.append(
                "Explicit unsupported descriptors recorded: "
                f"{', '.join(unsupported_ids)}"
            )
        if not details_parts:
            details_parts.append(
                "No streaming/event descriptors or native grpc_unary operations present."
            )

        return ValidationResult(
            stage="event_support",
            passed=True,
            details=" ".join(details_parts),
            duration_ms=self._duration_ms(started_at),
        )

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)
