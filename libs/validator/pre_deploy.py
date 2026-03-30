"""Pre-deploy validation harness for compiled service IR artifacts."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Self
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field

from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventSupportLevel,
    EventTransport,
    GrpcStreamMode,
    ServiceIR,
)
from libs.secret_refs import MissingSecretReferenceError, resolve_secret_ref
from libs.validator.audit import ToolAuditSummary

_APPROVED_STREAM_TRANSPORTS = {EventTransport.sse, EventTransport.websocket}


def _has_primary_auth_reference(auth: AuthConfig) -> bool:
    return any(
        (
            auth.compile_time_secret_ref,
            auth.runtime_secret_ref,
            auth.basic_password_ref,
        )
    )


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

        if auth.type is AuthType.none and auth.mtls is None and auth.request_signing is None:
            return ValidationResult(
                stage="auth_smoke",
                passed=True,
                details="No auth smoke test required for auth.type=none.",
                duration_ms=self._duration_ms(started_at),
            )

        if auth.mtls is not None:
            try:
                resolve_secret_ref(
                    auth.mtls.cert_ref,
                    purpose="mTLS client certificate",
                    context="pre-deploy auth smoke",
                )
                resolve_secret_ref(
                    auth.mtls.key_ref,
                    purpose="mTLS client key",
                    context="pre-deploy auth smoke",
                )
                if auth.mtls.ca_ref:
                    resolve_secret_ref(
                        auth.mtls.ca_ref,
                        purpose="mTLS CA bundle",
                        context="pre-deploy auth smoke",
                    )
            except MissingSecretReferenceError as exc:
                return ValidationResult(
                    stage="auth_smoke",
                    passed=False,
                    details=str(exc),
                    duration_ms=self._duration_ms(started_at),
                )
            details.append("mTLS secret references resolved.")

        if auth.request_signing is not None:
            try:
                resolve_secret_ref(
                    auth.request_signing.secret_ref,
                    purpose="request signing secret",
                    context="pre-deploy auth smoke",
                )
            except MissingSecretReferenceError as exc:
                return ValidationResult(
                    stage="auth_smoke",
                    passed=False,
                    details=str(exc),
                    duration_ms=self._duration_ms(started_at),
                )
            details.append("Request signing secret resolved.")

        if auth.type is AuthType.oauth2:
            oauth_result = await self._validate_oauth2_endpoint(service_ir, started_at)
            if not oauth_result.passed:
                return oauth_result
            details.append(oauth_result.details)
        elif auth.type is not AuthType.none:
            if not _has_primary_auth_reference(auth):
                return ValidationResult(
                    stage="auth_smoke",
                    passed=False,
                    details=(
                        "Auth configuration requires a compile_time_secret_ref or "
                        "runtime_secret_ref (or basic_password_ref for basic auth)."
                    ),
                    duration_ms=self._duration_ms(started_at),
                )
            details.append("Primary auth configuration includes a secret reference.")

        if auth.mtls is not None:
            details.append("mTLS configuration present.")

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
            oauth2 = auth.oauth2
            token_url = oauth2.token_url
            try:
                client_id = (
                    oauth2.client_id
                    if oauth2.client_id is not None
                    else resolve_secret_ref(
                        oauth2.client_id_ref or "",
                        purpose="oauth2 client id",
                        context="pre-deploy auth smoke",
                    )
                )
                client_secret = resolve_secret_ref(
                    oauth2.client_secret_ref,
                    purpose="oauth2 client secret",
                    context="pre-deploy auth smoke",
                )
            except MissingSecretReferenceError as exc:
                return ValidationResult(
                    stage="auth_smoke",
                    passed=False,
                    details=str(exc),
                    duration_ms=self._duration_ms(started_at),
                )

            form_payload: dict[str, str] = {"grant_type": "client_credentials"}
            if oauth2.scopes:
                form_payload["scope"] = " ".join(oauth2.scopes)
            if oauth2.audience:
                form_payload["audience"] = oauth2.audience

            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if oauth2.client_auth_method == "client_secret_basic":
                encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
                headers["Authorization"] = f"Basic {encoded}"
            else:
                form_payload["client_id"] = client_id
                form_payload["client_secret"] = client_secret

            request_content = urlencode(form_payload)
            details_prefix = "OAuth2 client credentials token exchange succeeded"
        else:
            token_url = auth.oauth2_token_url
            request_content = urlencode({"grant_type": "client_credentials"})
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            details_prefix = "Auth token endpoint accepted POST probe"

        if not token_url:
            return ValidationResult(
                stage="auth_smoke",
                passed=False,
                details=(
                    "OAuth2 auth requires oauth2 client credentials config or oauth2_token_url."
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
            response = await self._client.post(
                token_url,
                content=request_content,
                headers=headers,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            return ValidationResult(
                stage="auth_smoke",
                passed=False,
                details=f"Auth smoke test could not reach token endpoint: {exc}",
                duration_ms=self._duration_ms(started_at),
            )

        if not response.is_success:
            return ValidationResult(
                stage="auth_smoke",
                passed=False,
                details=(
                    "Auth smoke test received an unhealthy response from token endpoint: "
                    f"HTTP {response.status_code}"
                ),
                duration_ms=self._duration_ms(started_at),
            )

        if auth.oauth2 is not None:
            try:
                payload = response.json()
            except Exception:
                return ValidationResult(
                    stage="auth_smoke",
                    passed=False,
                    details=(
                        "Auth smoke test received a non-JSON response from token endpoint: "
                        f"HTTP {response.status_code}"
                    ),
                    duration_ms=self._duration_ms(started_at),
                )
            if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
                return ValidationResult(
                    stage="auth_smoke",
                    passed=False,
                    details=(
                        "Auth smoke test did not receive an access_token from token endpoint: "
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
                    if descriptor.grpc_stream.mode is not GrpcStreamMode.server:
                        invalid_descriptors.append(
                            f"{descriptor.id}=grpc_stream_mode_{descriptor.grpc_stream.mode.value}"
                        )
                        continue
                    supported_ids.append(f"{descriptor.id}({descriptor.transport.value})")
                    continue
                invalid_descriptors.append(f"{descriptor.id}={descriptor.transport.value}")
                continue
            if descriptor.operation_id is None:
                invalid_descriptors.append(f"{descriptor.id}=missing_operation_id")
                continue
            supported_ids.append(f"{descriptor.id}({descriptor.transport.value})")

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
                f"Approved streaming transports configured: {', '.join(supported_ids)}"
            )
        if supported_native_operations:
            details_parts.append(
                "Approved native unary transports configured: "
                f"{', '.join(supported_native_operations)}"
            )
        if unsupported_ids:
            details_parts.append(
                f"Explicit unsupported descriptors recorded: {', '.join(unsupported_ids)}"
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
