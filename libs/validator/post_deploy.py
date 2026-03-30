"""Post-deploy validation harness for the generic MCP runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, Self

import httpx
from pydantic import ValidationError

from libs.ir.models import (
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationType,
    Operation,
    ServiceIR,
    SqlOperationType,
)
from libs.runtime_contracts import stream_result_failure_reason, validate_tool_listing_payload
from libs.validator.audit import (
    AuditPolicy,
    ToolAuditResult,
    ToolAuditSummary,
    _has_synthetic_path_placeholder_samples,
)
from libs.validator.pre_deploy import ValidationReport, ValidationResult

ToolInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class PostDeployValidator:
    """Validate a deployed runtime before marking it as published."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        tool_invoker: ToolInvoker | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._owns_client = client is None
        self._tool_invoker = tool_invoker

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def validate(
        self,
        base_url: str,
        expected_ir: ServiceIR | dict[str, Any],
        *,
        sample_invocations: dict[str, dict[str, Any]] | None = None,
        preferred_smoke_tool_ids: tuple[str, ...] = (),
    ) -> ValidationReport:
        """Run post-deploy checks against a runtime base URL."""

        schema_result, service_ir = self._validate_schema(expected_ir)
        if service_ir is None:
            results = [
                schema_result,
                self._skipped_result(
                    "health", "Skipped because expected IR schema validation failed."
                ),
                self._skipped_result(
                    "tool_listing",
                    "Skipped because expected IR schema validation failed.",
                ),
                self._skipped_result(
                    "invocation_smoke",
                    "Skipped because expected IR schema validation failed.",
                ),
            ]
            return ValidationReport(results=results, overall_passed=False)

        health_result = await self._validate_health(base_url)
        tool_listing_result, available_tools = await self._validate_tool_listing(
            base_url,
            service_ir,
        )
        invocation_result = await self._validate_invocation_smoke(
            service_ir,
            available_tools=available_tools,
            sample_invocations=sample_invocations or {},
            preferred_smoke_tool_ids=preferred_smoke_tool_ids,
            health_passed=health_result.passed,
            tool_listing_passed=tool_listing_result.passed,
        )

        results = [schema_result, health_result, tool_listing_result, invocation_result]
        return ValidationReport(
            results=results,
            overall_passed=all(result.passed for result in results),
        )

    async def validate_with_audit(
        self,
        base_url: str,
        expected_ir: ServiceIR | dict[str, Any],
        *,
        sample_invocations: dict[str, dict[str, Any]] | None = None,
        audit_policy: AuditPolicy | None = None,
        preferred_smoke_tool_ids: tuple[str, ...] = (),
    ) -> tuple[ValidationReport, ToolAuditSummary]:
        """Run standard post-deploy validation plus a full generated-tool audit.

        Returns a ``(ValidationReport, ToolAuditSummary)`` tuple.  The
        standard validation report includes health, tool listing, and
        invocation smoke results.  The audit summary covers every enabled
        operation in the IR, applying the given ``audit_policy`` skip rules.
        """

        schema_result, service_ir = self._validate_schema(expected_ir)
        if service_ir is None:
            results = [
                schema_result,
                self._skipped_result(
                    "health", "Skipped because expected IR schema validation failed."
                ),
                self._skipped_result(
                    "tool_listing",
                    "Skipped because expected IR schema validation failed.",
                ),
                self._skipped_result(
                    "invocation_smoke",
                    "Skipped because expected IR schema validation failed.",
                ),
            ]
            audit_summary = ToolAuditSummary(
                discovered_operations=0,
                generated_tools=0,
                audited_tools=0,
                passed=0,
                failed=0,
                skipped=0,
                results=[],
            )
            report = ValidationReport(
                results=results,
                overall_passed=False,
                audit_summary=audit_summary,
            )
            return report, audit_summary
        policy = audit_policy or AuditPolicy()
        invocations = sample_invocations or {}

        health_result = await self._validate_health(base_url)
        tool_listing_result, available_tools = await self._validate_tool_listing(
            base_url,
            service_ir,
        )

        # Standard invocation smoke (same as validate)
        invocation_result = await self._validate_invocation_smoke(
            service_ir,
            available_tools=available_tools,
            sample_invocations=invocations,
            preferred_smoke_tool_ids=preferred_smoke_tool_ids,
            health_passed=health_result.passed,
            tool_listing_passed=tool_listing_result.passed,
        )

        results = [schema_result, health_result, tool_listing_result, invocation_result]

        # Full audit pass over all enabled operations
        audit_summary = await self._audit_all_enabled_operations(
            service_ir,
            available_tools=available_tools,
            sample_invocations=invocations,
            audit_policy=policy,
            health_passed=health_result.passed,
            tool_listing_passed=tool_listing_result.passed,
        )

        report = ValidationReport(
            results=results,
            overall_passed=all(result.passed for result in results) and audit_summary.failed == 0,
            audit_summary=audit_summary,
        )
        return report, audit_summary

    def _validate_schema(
        self,
        expected_ir: ServiceIR | dict[str, Any],
    ) -> tuple[ValidationResult, ServiceIR | None]:
        started_at = perf_counter()
        try:
            service_ir = (
                expected_ir
                if isinstance(expected_ir, ServiceIR)
                else ServiceIR.model_validate(expected_ir)
            )
        except ValidationError as exc:
            return (
                ValidationResult(
                    stage="schema",
                    passed=False,
                    details=f"Expected IR schema validation failed: {exc}",
                    duration_ms=self._duration_ms(started_at),
                ),
                None,
            )
        return (
            ValidationResult(
                stage="schema",
                passed=True,
                details="Expected IR schema is valid.",
                duration_ms=self._duration_ms(started_at),
            ),
            service_ir,
        )

    def _skipped_result(self, stage: str, details: str) -> ValidationResult:
        return ValidationResult(
            stage=stage,
            passed=False,
            details=details,
            duration_ms=0,
        )

    async def _audit_all_enabled_operations(
        self,
        service_ir: ServiceIR,
        *,
        available_tools: dict[str, dict[str, Any]],
        sample_invocations: dict[str, dict[str, Any]],
        audit_policy: AuditPolicy,
        health_passed: bool,
        tool_listing_passed: bool,
    ) -> ToolAuditSummary:
        """Iterate over every enabled operation and produce audit results."""

        enabled_operations = sorted(
            (op for op in service_ir.operations if op.enabled),
            key=lambda op: op.id,
        )
        runtime_tool_names = set(available_tools)
        audit_results: list[ToolAuditResult] = []

        for operation in enabled_operations:
            if operation.id not in runtime_tool_names:
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="failed",
                        reason="Runtime /tools listing does not expose this generated tool.",
                    )
                )
                continue

            skip_reason = audit_policy.skip_reason(operation, sample_invocations)
            if skip_reason is not None:
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="skipped",
                        reason=skip_reason,
                    )
                )
                continue

            if not health_passed or not tool_listing_passed or self._tool_invoker is None:
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="failed",
                        reason="Cannot invoke tool: runtime health check failed or no invoker.",
                    )
                )
                continue

            arguments = sample_invocations[operation.id]
            try:
                result = await self._tool_invoker(operation.id, arguments)
            except Exception as exc:
                failure_skip_reason = audit_policy.failure_skip_reason(operation, arguments)
                if failure_skip_reason is not None:
                    audit_results.append(
                        ToolAuditResult(
                            tool_name=operation.id,
                            outcome="skipped",
                            reason=failure_skip_reason,
                            arguments=arguments,
                        )
                    )
                    continue
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="failed",
                        reason=f"Invocation raised: {exc}",
                        arguments=arguments,
                    )
                )
                continue

            if not isinstance(result, dict):
                failure_reason = "Invocation returned non-dict result."
            else:
                failure_reason = _tool_result_failure_reason(service_ir, operation.id, result)
            if failure_reason is not None:
                failure_skip_reason = audit_policy.failure_skip_reason(operation, arguments)
                if failure_skip_reason is not None:
                    audit_results.append(
                        ToolAuditResult(
                            tool_name=operation.id,
                            outcome="skipped",
                            reason=failure_skip_reason,
                            arguments=arguments,
                            result=result if isinstance(result, dict) else {"raw": str(result)},
                        )
                    )
                    continue
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="failed",
                        reason=failure_reason,
                        arguments=arguments,
                        result=result if isinstance(result, dict) else {"raw": str(result)},
                    )
                )
                continue

            audit_results.append(
                ToolAuditResult(
                    tool_name=operation.id,
                    outcome="passed",
                    reason="Invocation succeeded.",
                    arguments=arguments,
                    result=result if isinstance(result, dict) else {"raw": str(result)},
                )
            )

        passed = sum(r.outcome == "passed" for r in audit_results)
        failed = sum(r.outcome == "failed" for r in audit_results)
        skipped = sum(r.outcome == "skipped" for r in audit_results)
        return ToolAuditSummary(
            discovered_operations=len(enabled_operations),
            generated_tools=len(runtime_tool_names),
            audited_tools=passed + failed,
            passed=passed,
            failed=failed,
            skipped=skipped,
            results=audit_results,
        )

    async def _validate_health(self, base_url: str) -> ValidationResult:
        started_at = perf_counter()
        health_url = f"{base_url.rstrip('/')}/healthz"
        ready_url = f"{base_url.rstrip('/')}/readyz"
        try:
            health_response = await self._client.get(health_url)
            ready_response = await self._client.get(ready_url)
        except httpx.RequestError as exc:
            return ValidationResult(
                stage="health",
                passed=False,
                details=f"Runtime health check failed: {exc}",
                duration_ms=self._duration_ms(started_at),
            )

        health_failure = self._health_endpoint_failure_detail("healthz", health_response)
        ready_failure = self._health_endpoint_failure_detail("readyz", ready_response)
        passed = health_failure is None and ready_failure is None
        details = "Runtime health endpoints are ready."
        if not passed:
            failure_details = [
                detail for detail in (health_failure, ready_failure) if detail is not None
            ]
            details = "Runtime health endpoints returned unexpected readiness state: " + "; ".join(
                failure_details
            )
        return ValidationResult(
            stage="health",
            passed=passed,
            details=details,
            duration_ms=self._duration_ms(started_at),
        )

    @staticmethod
    def _health_endpoint_failure_detail(endpoint: str, response: httpx.Response) -> str | None:
        if response.status_code != 200:
            return f"{endpoint}={response.status_code}"
        if not response.content:
            return None
        try:
            payload = response.json()
        except ValueError:
            return f"{endpoint} returned invalid JSON payload."
        if not isinstance(payload, dict):
            return f"{endpoint} returned JSON {type(payload).__name__}, expected object."
        status = payload.get("status")
        if "status" in payload and status != "ok":
            return f"{endpoint} reported status {status!r}"
        return None

    async def _validate_tool_listing(
        self,
        base_url: str,
        expected_ir: ServiceIR,
    ) -> tuple[ValidationResult, dict[str, dict[str, Any]]]:
        started_at = perf_counter()
        tools_url = f"{base_url.rstrip('/')}/tools"
        try:
            response = await self._client.get(tools_url)
        except httpx.RequestError as exc:
            return (
                ValidationResult(
                    stage="tool_listing",
                    passed=False,
                    details=f"Runtime tool listing failed: {exc}",
                    duration_ms=self._duration_ms(started_at),
                ),
                {},
            )

        if response.status_code != 200:
            return (
                ValidationResult(
                    stage="tool_listing",
                    passed=False,
                    details=f"Runtime tool listing returned HTTP {response.status_code}.",
                    duration_ms=self._duration_ms(started_at),
                ),
                {},
            )

        try:
            payload = response.json()
        except Exception:
            return (
                ValidationResult(
                    stage="tool_listing",
                    passed=False,
                    details="Runtime tool listing returned non-JSON response.",
                    duration_ms=self._duration_ms(started_at),
                ),
                {},
            )
        try:
            validated_tools = validate_tool_listing_payload(payload, context="Runtime tool listing")
        except RuntimeError as exc:
            return (
                ValidationResult(
                    stage="tool_listing",
                    passed=False,
                    details=str(exc),
                    duration_ms=self._duration_ms(started_at),
                ),
                {},
            )
        listed_tools = {tool["name"]: tool for tool in validated_tools}
        expected_tools = {operation.id for operation in expected_ir.operations if operation.enabled}

        if set(listed_tools) != expected_tools:
            return (
                ValidationResult(
                    stage="tool_listing",
                    passed=False,
                    details=(
                        "Runtime tool listing mismatch. "
                        f"expected={sorted(expected_tools)}, actual={sorted(listed_tools)}"
                    ),
                    duration_ms=self._duration_ms(started_at),
                ),
                listed_tools,
            )

        return (
            ValidationResult(
                stage="tool_listing",
                passed=True,
                details=f"Runtime exposes expected tools: {sorted(expected_tools)}.",
                duration_ms=self._duration_ms(started_at),
            ),
            listed_tools,
        )

    async def _validate_invocation_smoke(
        self,
        expected_ir: ServiceIR,
        *,
        available_tools: dict[str, dict[str, Any]],
        sample_invocations: dict[str, dict[str, Any]],
        preferred_smoke_tool_ids: tuple[str, ...],
        health_passed: bool,
        tool_listing_passed: bool,
    ) -> ValidationResult:
        started_at = perf_counter()

        if not health_passed:
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details="Skipped because runtime health validation failed.",
                duration_ms=self._duration_ms(started_at),
            )

        if not tool_listing_passed:
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details="Skipped because runtime tool listing validation failed.",
                duration_ms=self._duration_ms(started_at),
            )

        if self._tool_invoker is None:
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details="No tool invoker configured for invocation smoke validation.",
                duration_ms=self._duration_ms(started_at),
            )

        enabled_operations = [
            operation for operation in expected_ir.operations if operation.enabled
        ]
        if not enabled_operations:
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details="No enabled operations available for invocation smoke validation.",
                duration_ms=self._duration_ms(started_at),
            )

        operation = _select_smoke_operation(
            expected_ir,
            enabled_operations=enabled_operations,
            available_tools=available_tools,
            sample_invocations=sample_invocations,
            preferred_tool_ids=preferred_smoke_tool_ids,
        )
        if operation is None:
            available_operations = [
                candidate for candidate in enabled_operations if candidate.id in available_tools
            ]
            if not available_operations:
                return ValidationResult(
                    stage="invocation_smoke",
                    passed=False,
                    details="No enabled runtime tool is available for invocation smoke validation.",
                    duration_ms=self._duration_ms(started_at),
                )
            sampled_available_operations = [
                candidate
                for candidate in available_operations
                if candidate.id in sample_invocations
            ]
            if not sampled_available_operations:
                first_available_operation = available_operations[0]
                return ValidationResult(
                    stage="invocation_smoke",
                    passed=False,
                    details=(
                        f"No sample invocation provided for available tool "
                        f"{first_available_operation.id}."
                    ),
                    duration_ms=self._duration_ms(started_at),
                )
            rejection_details = _default_smoke_rejection_details(
                sampled_available_operations,
                sample_invocations=sample_invocations,
            )
            if rejection_details is None:
                rejection_details = (
                    "No safe runtime tool is available for invocation smoke validation."
                )
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details=rejection_details,
                duration_ms=self._duration_ms(started_at),
            )

        arguments = sample_invocations[operation.id]

        try:
            result = await self._tool_invoker(operation.id, arguments)
        except Exception as exc:
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details=f"Invocation smoke test failed for {operation.id}: {exc}",
                duration_ms=self._duration_ms(started_at),
            )

        if not isinstance(result, dict):
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details=f"Invocation smoke test for {operation.id} returned non-dict result.",
                duration_ms=self._duration_ms(started_at),
            )

        failure_reason = _tool_result_failure_reason(expected_ir, operation.id, result)
        if failure_reason is not None:
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details=f"Invocation smoke test for {operation.id} failed: {failure_reason}",
                duration_ms=self._duration_ms(started_at),
            )

        descriptor = _supported_descriptor_for_operation(expected_ir, operation.id)
        return ValidationResult(
            stage="invocation_smoke",
            passed=True,
            details=(
                f"Invocation smoke test succeeded for {operation.id}"
                + (f" using {descriptor.transport.value}." if descriptor is not None else ".")
            ),
            duration_ms=self._duration_ms(started_at),
        )

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)


def _supported_descriptor_for_operation(
    expected_ir: ServiceIR,
    operation_id: str,
) -> EventDescriptor | None:
    descriptors = [
        descriptor
        for descriptor in expected_ir.event_descriptors
        if descriptor.operation_id == operation_id
        and descriptor.support is EventSupportLevel.supported
    ]
    if not descriptors:
        return None
    if len(descriptors) > 1:
        raise ValueError(
            f"Post-deploy validation does not support multiple descriptors for {operation_id}."
        )
    return descriptors[0]


def _tool_result_failure_reason(
    expected_ir: ServiceIR,
    operation_id: str,
    result: dict[str, Any],
) -> str | None:
    status = result.get("status")
    if status != "ok":
        return f"Invocation returned unexpected status: {status!r}."
    if "result" not in result:
        return "Invocation returned ok status without a result payload."

    try:
        descriptor = _supported_descriptor_for_operation(expected_ir, operation_id)
    except ValueError as exc:
        return str(exc)
    if descriptor is None:
        return None

    transport = result.get("transport")
    if transport != descriptor.transport.value:
        return (
            f"Invocation returned transport {transport!r}, expected {descriptor.transport.value!r}."
        )

    return stream_result_failure_reason(
        result.get("result"),
        transport=descriptor.transport.value,
    )


def _select_smoke_operation(
    expected_ir: ServiceIR,
    *,
    enabled_operations: list[Operation],
    available_tools: dict[str, dict[str, Any]],
    sample_invocations: dict[str, dict[str, Any]],
    preferred_tool_ids: tuple[str, ...] = (),
) -> Operation | None:
    candidates = [
        operation
        for operation in enabled_operations
        if operation.id in available_tools and operation.id in sample_invocations
    ]
    if not candidates:
        return None
    preferred_candidate_by_id = {operation.id: operation for operation in candidates}
    for preferred_tool_id in preferred_tool_ids:
        preferred_candidate = preferred_candidate_by_id.get(preferred_tool_id)
        if preferred_candidate is not None and not _uses_default_placeholder_path_samples(
            preferred_candidate,
            sample_invocations=sample_invocations,
        ):
            return preferred_candidate
    safe_candidates = [
        operation
        for operation in candidates
        if _is_default_safe_smoke_candidate(operation, sample_invocations=sample_invocations)
    ]
    if not safe_candidates:
        return None
    return min(
        safe_candidates,
        key=lambda operation: _smoke_operation_priority(expected_ir, operation),
    )


def _uses_default_placeholder_path_samples(
    operation: Operation,
    *,
    sample_invocations: dict[str, dict[str, Any]],
) -> bool:
    return bool(
        _has_synthetic_path_placeholder_samples(
            operation,
            sample_invocations[operation.id],
            include_numeric_fallbacks=True,
        )
    )


def _is_default_safe_smoke_candidate(
    operation: Operation,
    *,
    sample_invocations: dict[str, dict[str, Any]],
) -> bool:
    if _uses_default_placeholder_path_samples(operation, sample_invocations=sample_invocations):
        return False
    return not operation.risk.writes_state and not operation.risk.destructive


def _default_smoke_rejection_details(
    operations: list[Operation],
    *,
    sample_invocations: dict[str, dict[str, Any]],
) -> str | None:
    placeholder_blocked = [
        operation.id
        for operation in operations
        if _uses_default_placeholder_path_samples(operation, sample_invocations=sample_invocations)
    ]
    risk_blocked = [
        operation.id
        for operation in operations
        if operation.id not in placeholder_blocked
        and (operation.risk.writes_state or operation.risk.destructive)
    ]
    reasons: list[str] = []
    if risk_blocked:
        reasons.append("remaining tools are state-mutating or destructive")
    if placeholder_blocked:
        reasons.append("remaining sample invocations still use synthetic placeholder path values")
    if not reasons:
        return None
    if len(reasons) == 1:
        reason_text = reasons[0]
    else:
        reason_text = f"{reasons[0]} and {reasons[1]}"
    return (
        f"No safe runtime tool is available for invocation smoke validation because {reason_text}."
    )


def _smoke_operation_priority(
    expected_ir: ServiceIR,
    operation: Operation,
) -> tuple[int, int, int, str]:
    try:
        descriptor = _supported_descriptor_for_operation(expected_ir, operation.id)
    except ValueError:
        descriptor = None
    category = 6

    if operation.sql is not None:
        category = 0 if operation.sql.action is SqlOperationType.query else 8
    elif operation.graphql is not None:
        category = 1 if operation.graphql.operation_type is GraphQLOperationType.query else 7
    elif operation.grpc_unary is not None:
        category = 2
    elif descriptor is not None and descriptor.transport is EventTransport.grpc_stream:
        category = 3
    elif operation.soap is not None:
        category = 4
    else:
        method = (operation.method or "").upper()
        if method in {"GET", "HEAD"}:
            category = 1
        elif method == "POST":
            category = 5
        elif method in {"PUT", "PATCH"}:
            category = 7
        elif method == "DELETE":
            category = 8

    risk_penalty = 0
    if operation.risk.external_side_effect:
        risk_penalty += 1
    if operation.risk.writes_state:
        risk_penalty += 2
    if operation.risk.destructive:
        risk_penalty += 4

    # Prefer operations with fewer required parameters — they are more
    # likely to succeed with placeholder sample values.
    required_param_count = sum(1 for p in operation.params if p.required)

    return (category, risk_penalty, required_param_count, operation.id)
