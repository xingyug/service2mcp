"""Post-deploy validation harness for the generic MCP runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, Self

import httpx

from libs.ir.models import (
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationType,
    Operation,
    ServiceIR,
    SqlOperationType,
)
from libs.validator.audit import (
    AuditPolicy,
    ToolAuditResult,
    ToolAuditSummary,
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
    ) -> ValidationReport:
        """Run post-deploy checks against a runtime base URL."""

        service_ir = (
            expected_ir
            if isinstance(expected_ir, ServiceIR)
            else ServiceIR.model_validate(expected_ir)
        )

        health_result = await self._validate_health(base_url)
        tool_listing_result, available_tools = await self._validate_tool_listing(
            base_url,
            service_ir,
        )
        invocation_result = await self._validate_invocation_smoke(
            service_ir,
            available_tools=available_tools,
            sample_invocations=sample_invocations or {},
            health_passed=health_result.passed,
            tool_listing_passed=tool_listing_result.passed,
        )

        results = [health_result, tool_listing_result, invocation_result]
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
    ) -> tuple[ValidationReport, ToolAuditSummary]:
        """Run standard post-deploy validation plus a full generated-tool audit.

        Returns a ``(ValidationReport, ToolAuditSummary)`` tuple.  The
        standard validation report includes health, tool listing, and
        invocation smoke results.  The audit summary covers every enabled
        operation in the IR, applying the given ``audit_policy`` skip rules.
        """

        service_ir = (
            expected_ir
            if isinstance(expected_ir, ServiceIR)
            else ServiceIR.model_validate(expected_ir)
        )
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
            health_passed=health_result.passed,
            tool_listing_passed=tool_listing_result.passed,
        )

        results = [health_result, tool_listing_result, invocation_result]

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
            overall_passed=all(result.passed for result in results),
            audit_summary=audit_summary,
        )
        return report, audit_summary

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
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="failed",
                        reason=f"Invocation raised: {exc}",
                        arguments=arguments,
                    )
                )
                continue

            status = result.get("status") if isinstance(result, dict) else None
            if status != "ok":
                audit_results.append(
                    ToolAuditResult(
                        tool_name=operation.id,
                        outcome="failed",
                        reason=f"Invocation returned unexpected status: {status!r}.",
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

        passed = health_response.status_code == 200 and ready_response.status_code == 200
        details = (
            "Runtime health endpoints are ready."
            if passed
            else (
                "Runtime health endpoints returned unexpected status codes: "
                f"healthz={health_response.status_code}, readyz={ready_response.status_code}"
            )
        )
        return ValidationResult(
            stage="health",
            passed=passed,
            details=details,
            duration_ms=self._duration_ms(started_at),
        )

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

        payload = response.json()
        listed_tools = {
            tool["name"]: tool
            for tool in payload.get("tools", [])
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
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
        )
        if operation is None:
            first_available_operation = next(
                (candidate for candidate in enabled_operations if candidate.id in available_tools),
                None,
            )
            if first_available_operation is None:
                return ValidationResult(
                    stage="invocation_smoke",
                    passed=False,
                    details="No enabled runtime tool is available for invocation smoke validation.",
                    duration_ms=self._duration_ms(started_at),
                )
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details=(
                    f"No sample invocation provided for available tool "
                    f"{first_available_operation.id}."
                ),
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

        status = result.get("status")
        if status != "ok":
            return ValidationResult(
                stage="invocation_smoke",
                passed=False,
                details=(
                    f"Invocation smoke test for {operation.id} returned "
                    f"unexpected status: {status!r}"
                ),
                duration_ms=self._duration_ms(started_at),
            )

        descriptor = _supported_descriptor_for_operation(expected_ir, operation.id)
        if descriptor is not None:
            transport = result.get("transport")
            if transport != descriptor.transport.value:
                return ValidationResult(
                    stage="invocation_smoke",
                    passed=False,
                    details=(
                        f"Invocation smoke test for {operation.id} returned transport "
                        f"{transport!r}, expected {descriptor.transport.value!r}."
                    ),
                    duration_ms=self._duration_ms(started_at),
                )

            stream_result = result.get("result")
            if not isinstance(stream_result, dict):
                return ValidationResult(
                    stage="invocation_smoke",
                    passed=False,
                    details=(
                        f"Invocation smoke test for {operation.id} returned a non-object "
                        "stream payload."
                    ),
                    duration_ms=self._duration_ms(started_at),
                )

            events = stream_result.get("events")
            lifecycle = stream_result.get("lifecycle")
            if not isinstance(events, list) or not isinstance(lifecycle, dict):
                return ValidationResult(
                    stage="invocation_smoke",
                    passed=False,
                    details=(
                        f"Invocation smoke test for {operation.id} did not return the "
                        "expected streaming lifecycle structure."
                    ),
                    duration_ms=self._duration_ms(started_at),
                )

        return ValidationResult(
            stage="invocation_smoke",
            passed=True,
            details=(
                f"Invocation smoke test succeeded for {operation.id}"
                + (
                    f" using {descriptor.transport.value}."
                    if descriptor is not None
                    else "."
                )
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
        return descriptors[0]
    return descriptors[0]


def _select_smoke_operation(
    expected_ir: ServiceIR,
    *,
    enabled_operations: list[Operation],
    available_tools: dict[str, dict[str, Any]],
    sample_invocations: dict[str, dict[str, Any]],
) -> Operation | None:
    candidates = [
        operation
        for operation in enabled_operations
        if operation.id in available_tools and operation.id in sample_invocations
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda operation: _smoke_operation_priority(expected_ir, operation),
    )


def _smoke_operation_priority(
    expected_ir: ServiceIR,
    operation: Operation,
) -> tuple[int, int, str]:
    descriptor = _supported_descriptor_for_operation(expected_ir, operation.id)
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
    return (category, risk_penalty, operation.id)
