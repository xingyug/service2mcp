"""Integration tests for SLA-aware latency tracking."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from apps.mcp_runtime.observability import RuntimeObservability
from apps.mcp_runtime.proxy import RuntimeProxy
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SlaConfig,
    SourceType,
)


def _make_ir_with_sla(budget_ms: int) -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol="openapi",
        service_name="sla-test",
        base_url="https://api.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="get-item",
                name="get-item",
                description="Get an item",
                method="GET",
                path="/items/{id}",
                params=[Param(name="id", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
                sla=SlaConfig(latency_budget_ms=budget_ms),
            ),
        ],
    )


def _make_ir_without_sla() -> ServiceIR:
    return ServiceIR(
        source_hash="a" * 64,
        protocol="openapi",
        service_name="no-sla-test",
        base_url="https://api.test",
        auth=AuthConfig(type=AuthType.none),
        operations=[
            Operation(
                id="get-item",
                name="get-item",
                description="Get an item",
                method="GET",
                path="/items/{id}",
                params=[Param(name="id", type="string", required=True)],
                risk=RiskMetadata(
                    risk_level=RiskLevel.safe,
                    confidence=1.0,
                    source=SourceType.extractor,
                    writes_state=False,
                    destructive=False,
                    external_side_effect=False,
                    idempotent=True,
                ),
            ),
        ],
    )


class TestSlaRuntimeIntegration:
    @pytest.mark.asyncio
    async def test_sla_breach_detected_on_slow_response(self) -> None:
        """When upstream is slow, SLA breach should be logged."""
        ir = _make_ir_with_sla(budget_ms=100)
        obs = RuntimeObservability()

        async def slow_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "1"}, request=request)

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))
        proxy = RuntimeProxy(ir, observability=obs, client=upstream)

        # Simulate a slow response by patching perf_counter so elapsed > budget
        counter_values = iter([0.0, 0.5])  # 0.5s = 500ms > 100ms budget
        try:
            with patch("apps.mcp_runtime.proxy.perf_counter", side_effect=counter_values):
                await proxy.invoke(ir.operations[0], {"id": "1"})
        finally:
            await proxy.aclose()

        val = obs.sla_breaches_total.labels(operation_id="get-item")._value.get()
        assert val == 1.0

    @pytest.mark.asyncio
    async def test_sla_no_breach_on_fast_response(self) -> None:
        """When upstream responds quickly, no SLA breach."""
        ir = _make_ir_with_sla(budget_ms=1000)
        obs = RuntimeObservability()

        async def fast_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "1"}, request=request)

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(fast_handler))
        proxy = RuntimeProxy(ir, observability=obs, client=upstream)

        # 0.01s = 10ms < 1000ms budget
        counter_values = iter([0.0, 0.01])
        try:
            with patch("apps.mcp_runtime.proxy.perf_counter", side_effect=counter_values):
                await proxy.invoke(ir.operations[0], {"id": "1"})
        finally:
            await proxy.aclose()

        val = obs.sla_breaches_total.labels(operation_id="get-item")._value.get()
        assert val == 0.0

    @pytest.mark.asyncio
    async def test_no_sla_config_skips_check(self) -> None:
        """Operations without SLA config should not trigger SLA checking."""
        ir = _make_ir_without_sla()
        obs = RuntimeObservability()

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "1"}, request=request)

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        proxy = RuntimeProxy(ir, observability=obs, client=upstream)

        try:
            await proxy.invoke(ir.operations[0], {"id": "1"})
        finally:
            await proxy.aclose()

        # No SLA breach counter samples should exist (only HELP/TYPE lines, no data)
        metrics = obs.render_metrics().decode()
        for line in metrics.splitlines():
            if line.startswith("mcp_runtime_sla_breaches_total{"):
                pytest.fail(f"Unexpected SLA breach sample: {line}")
