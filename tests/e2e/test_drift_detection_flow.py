"""E2E: Drift detection pipeline — compare deployed vs live ServiceIR.

Tests the full detect_drift() pipeline covering:
- Operation additions and removals (breaking)
- Parameter changes (type, required, added, removed)
- Risk level changes (breaking)
- Path and method changes (breaking)
- SLA drift: relaxed budget = breaking, tightened = non-breaking
- Schema-level drift: auth, base_url, resources, prompts, events
- No-drift baseline
"""

from __future__ import annotations

import pytest

from libs.ir.models import (
    AuthConfig,
    AuthType,
    EventDescriptor,
    EventSupportLevel,
    EventTransport,
    Operation,
    Param,
    PromptDefinition,
    ResourceDefinition,
    RetryConfig,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SlaConfig,
)
from libs.validator.drift import DriftSeverity, detect_drift

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _op(
    op_id: str,
    *,
    risk_level: RiskLevel = RiskLevel.safe,
    method: str = "GET",
    path: str | None = None,
    params: list[Param] | None = None,
    sla: SlaConfig | None = None,
    enabled: bool = True,
) -> Operation:
    op = Operation(
        id=op_id,
        name=op_id,
        description=f"Op {op_id}",
        method=method,
        path=path or f"/{op_id}",
        risk=RiskMetadata(risk_level=risk_level, confidence=0.9),
        params=params or [],
        sla=sla,
    )
    if not enabled and op.enabled:
        op = op.model_copy(update={"enabled": False})
    return op


def _ir(
    operations: list[Operation],
    *,
    name: str = "test-svc",
    base_url: str = "https://api.example.com",
    auth: AuthConfig | None = None,
    events: list[EventDescriptor] | None = None,
    resources: list[ResourceDefinition] | None = None,
    prompts: list[PromptDefinition] | None = None,
) -> ServiceIR:
    return ServiceIR(
        source_url="https://example.com/spec",
        source_hash="hash123",
        protocol="openapi",
        service_name=name,
        base_url=base_url,
        auth=auth or AuthConfig(),
        operations=operations,
        event_descriptors=events or [],
        resource_definitions=resources or [],
        prompt_definitions=prompts or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoDrift:
    """Identical IRs produce a clean report."""

    async def test_identical_irs(self) -> None:
        ops = [_op("op1"), _op("op2")]
        deployed = _ir(ops)
        live = _ir(ops)

        report = detect_drift(deployed, live)
        assert report.has_drift is False
        assert report.severity == DriftSeverity.non_breaking
        assert report.added_operations == []
        assert report.removed_operations == []
        assert report.modified_operations == []


class TestOperationSetDrift:
    """Additions and removals of operations."""

    async def test_added_operation(self) -> None:
        deployed = _ir([_op("op1")])
        live = _ir([_op("op1"), _op("op2")])

        report = detect_drift(deployed, live)
        assert report.has_drift is True
        assert "op2" in report.added_operations
        # Additions are non-breaking
        assert report.severity == DriftSeverity.non_breaking

    async def test_removed_operation_is_breaking(self) -> None:
        deployed = _ir([_op("op1"), _op("op2")])
        live = _ir([_op("op1")])

        report = detect_drift(deployed, live)
        assert report.has_drift is True
        assert "op2" in report.removed_operations
        assert report.severity == DriftSeverity.breaking


class TestParameterDrift:
    """Per-operation parameter changes."""

    async def test_param_type_change_is_breaking(self) -> None:
        deployed = _ir([_op("op1", params=[Param(name="limit", type="integer", required=True)])])
        live = _ir([_op("op1", params=[Param(name="limit", type="string", required=True)])])

        report = detect_drift(deployed, live)
        assert report.has_drift is True
        mod = report.modified_operations[0]
        assert any("type changed" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.breaking

    async def test_param_required_change_is_breaking(self) -> None:
        deployed = _ir([_op("op1", params=[Param(name="q", type="string", required=False)])])
        live = _ir([_op("op1", params=[Param(name="q", type="string", required=True)])])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("required changed" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.breaking

    async def test_param_added_is_non_breaking(self) -> None:
        deployed = _ir([_op("op1", params=[])])
        live = _ir([_op("op1", params=[Param(name="new_param", type="string")])])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("param added" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.non_breaking

    async def test_param_removed_is_breaking(self) -> None:
        deployed = _ir([_op("op1", params=[Param(name="old_param", type="string")])])
        live = _ir([_op("op1", params=[])])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("param removed" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.breaking


class TestRiskAndPathDrift:
    """Risk level and path/method changes."""

    async def test_risk_level_change_is_breaking(self) -> None:
        deployed = _ir([_op("op1", risk_level=RiskLevel.safe)])
        live = _ir([_op("op1", risk_level=RiskLevel.dangerous)])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("risk level changed" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.breaking

    async def test_method_change_is_breaking(self) -> None:
        deployed = _ir([_op("op1", method="GET")])
        live = _ir([_op("op1", method="POST")])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("method changed" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.breaking

    async def test_path_change_is_breaking(self) -> None:
        deployed = _ir([_op("op1", path="/v1/items")])
        live = _ir([_op("op1", path="/v2/items")])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("path changed" in c for c in mod.changes)
        # Path change is breaking — consumers relying on old path will fail
        assert mod.severity == DriftSeverity.breaking


class TestSLADrift:
    """SLA configuration changes — relaxed budget is breaking."""

    async def test_sla_latency_relaxed_is_breaking(self) -> None:
        deployed = _ir([_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=500))])
        live = _ir([_op("op1", sla=SlaConfig(latency_budget_ms=200, timeout_ms=500))])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("sla latency budget relaxed" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.breaking

    async def test_sla_latency_tightened_is_non_breaking(self) -> None:
        deployed = _ir([_op("op1", sla=SlaConfig(latency_budget_ms=200, timeout_ms=500))])
        live = _ir([_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=500))])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("sla latency budget tightened" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.non_breaking

    async def test_sla_timeout_relaxed_is_breaking(self) -> None:
        deployed = _ir([_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=500))])
        live = _ir([_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=1000))])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("sla timeout relaxed" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.breaking

    async def test_sla_removed_is_breaking(self) -> None:
        deployed = _ir([_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=500))])
        live = _ir([_op("op1")])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("sla config removed" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.breaking

    async def test_sla_added_is_non_breaking(self) -> None:
        deployed = _ir([_op("op1")])
        live = _ir([_op("op1", sla=SlaConfig(latency_budget_ms=100, timeout_ms=500))])

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("sla config added" in c for c in mod.changes)
        assert mod.severity == DriftSeverity.non_breaking

    async def test_sla_retry_change(self) -> None:
        deployed = _ir(
            [
                _op(
                    "op1",
                    sla=SlaConfig(
                        latency_budget_ms=100,
                        retry=RetryConfig(max_retries=3, backoff_multiplier=2.0),
                    ),
                )
            ]
        )
        live = _ir(
            [
                _op(
                    "op1",
                    sla=SlaConfig(
                        latency_budget_ms=100,
                        retry=RetryConfig(max_retries=5, backoff_multiplier=2.0),
                    ),
                )
            ]
        )

        report = detect_drift(deployed, live)
        mod = report.modified_operations[0]
        assert any("sla retry config changed" in c for c in mod.changes)


class TestSchemaLevelDrift:
    """Auth, base_url, resource/prompt/event changes."""

    async def test_auth_type_change_is_breaking(self) -> None:
        deployed = _ir([_op("op1")], auth=AuthConfig(type=AuthType.none))
        live = _ir(
            [_op("op1")],
            auth=AuthConfig(
                type=AuthType.bearer,
                compile_time_secret_ref="env:TOKEN",
            ),
        )

        report = detect_drift(deployed, live)
        assert any("auth type changed" in c for c in report.schema_changes)
        assert report.severity == DriftSeverity.breaking

    async def test_base_url_change(self) -> None:
        deployed = _ir([_op("op1")], base_url="https://v1.api.example.com")
        live = _ir([_op("op1")], base_url="https://v2.api.example.com")

        report = detect_drift(deployed, live)
        assert any("base_url changed" in c for c in report.schema_changes)

    async def test_resource_added(self) -> None:
        deployed = _ir([_op("op1")])
        live = _ir(
            [_op("op1")],
            resources=[
                ResourceDefinition(
                    id="r1",
                    name="new_resource",
                    uri="service://test/new",
                    content="{}",
                )
            ],
        )

        report = detect_drift(deployed, live)
        assert any("resource added" in c for c in report.schema_changes)

    async def test_event_removed(self) -> None:
        evt = EventDescriptor(
            id="e1",
            name="event",
            transport=EventTransport.websocket,
            support=EventSupportLevel.unsupported,
        )
        deployed = _ir([_op("op1")], events=[evt])
        live = _ir([_op("op1")])

        report = detect_drift(deployed, live)
        assert any("event descriptor removed" in c for c in report.schema_changes)
        assert report.severity == DriftSeverity.breaking


class TestMultipleChanges:
    """Multiple simultaneous changes in one report."""

    async def test_combined_changes(self) -> None:
        deployed = _ir(
            [
                _op("op1", params=[Param(name="q", type="string")]),
                _op("op2"),
            ]
        )
        live = _ir(
            [
                _op("op1", params=[Param(name="q", type="integer")]),
                _op("op3"),
            ]
        )

        report = detect_drift(deployed, live)
        assert report.has_drift is True
        assert "op2" in report.removed_operations
        assert "op3" in report.added_operations
        assert len(report.modified_operations) == 1
        assert report.severity == DriftSeverity.breaking
