"""Contract: the runtime MUST accept any valid ServiceIR and construct a proxy.

Tests that RuntimeProxy can be instantiated from various well-formed IR
shapes and that it correctly indexes operations and initialises circuit
breakers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.mcp_runtime.observability import RuntimeObservability
from apps.mcp_runtime.proxy import RuntimeProxy
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# ── helpers ────────────────────────────────────────────────────────────────


def _minimal_ir(
    *,
    protocol: str = "openapi",
    ops: list[Operation] | None = None,
    service_name: str = "runtime-contract-svc",
    auth: AuthConfig | None = None,
) -> ServiceIR:
    return ServiceIR(
        source_hash="b" * 64,
        protocol=protocol,
        service_name=service_name,
        base_url="https://upstream.example.com",
        auth=auth or AuthConfig(),
        operations=ops or [],
    )


def _safe_op(op_id: str) -> Operation:
    return Operation(
        id=op_id,
        name=op_id.replace("_", " ").title(),
        method="GET",
        path=f"/{op_id}",
        risk=RiskMetadata(
            writes_state=False,
            destructive=False,
            external_side_effect=False,
            idempotent=True,
            risk_level=RiskLevel.safe,
            confidence=1.0,
            source=SourceType.extractor,
        ),
        source=SourceType.extractor,
        confidence=1.0,
    )


def _load_proxy_ir() -> ServiceIR:
    """Load the dedicated proxy fixture IR."""
    path = FIXTURES / "ir" / "service_ir_proxy.json"
    return ServiceIR.model_validate(json.loads(path.read_text()))


def _make_proxy(ir: ServiceIR) -> RuntimeProxy:
    obs = RuntimeObservability()
    return RuntimeProxy(ir, observability=obs)


# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.contract
class TestRuntimeAcceptsValidIR:
    """RuntimeProxy MUST be constructable from any valid ServiceIR."""

    def test_proxy_from_fixture_ir(self) -> None:
        ir = _load_proxy_ir()
        proxy = _make_proxy(ir)
        assert proxy._service_ir is ir

    def test_proxy_from_empty_operations(self) -> None:
        ir = _minimal_ir(ops=[])
        proxy = _make_proxy(ir)
        assert len(proxy._service_ir.operations) == 0

    def test_proxy_from_many_operations(self) -> None:
        ops = [_safe_op(f"op_{i}") for i in range(100)]
        ir = _minimal_ir(ops=ops)
        proxy = _make_proxy(ir)
        assert len(proxy._service_ir.operations) == 100

    def test_proxy_breakers_initially_empty(self) -> None:
        ir = _minimal_ir(ops=[_safe_op("a"), _safe_op("b")])
        proxy = _make_proxy(ir)
        assert proxy.breakers == {}

    def test_proxy_with_bearer_auth_ir(self) -> None:
        ir = _minimal_ir(
            auth=AuthConfig(type=AuthType.bearer, runtime_secret_ref="my-secret"),
        )
        proxy = _make_proxy(ir)
        assert proxy._service_ir.auth.type == AuthType.bearer

    @pytest.mark.parametrize(
        "protocol",
        ["openapi", "graphql", "grpc", "soap", "sql", "jsonrpc", "odata", "scim", "rest"],
    )
    def test_proxy_accepts_all_protocols(self, protocol: str) -> None:
        ir = _minimal_ir(protocol=protocol, ops=[_safe_op("check")])
        proxy = _make_proxy(ir)
        assert proxy._service_ir.protocol == protocol

    def test_proxy_preserves_operation_order(self) -> None:
        ops = [_safe_op(f"step_{i}") for i in range(10)]
        ir = _minimal_ir(ops=ops)
        proxy = _make_proxy(ir)
        actual_ids = [op.id for op in proxy._service_ir.operations]
        expected_ids = [f"step_{i}" for i in range(10)]
        assert actual_ids == expected_ids

    def test_proxy_round_trip_dict_ir(self) -> None:
        """Construct proxy from an IR that went through dict serialization."""
        ir = _minimal_ir(ops=[_safe_op("rt")])
        raw: dict[str, Any] = ir.model_dump(mode="json")
        restored = ServiceIR.model_validate(raw)
        proxy = _make_proxy(restored)
        assert proxy._service_ir.service_name == ir.service_name

    def test_proxy_observability_is_wired(self) -> None:
        ir = _minimal_ir(ops=[_safe_op("obs_check")])
        obs = RuntimeObservability()
        proxy = RuntimeProxy(ir, observability=obs)
        assert proxy._observability is obs

    def test_proxy_from_valid_ir_fixture(self) -> None:
        ir_data = json.loads((FIXTURES / "ir" / "service_ir_valid.json").read_text())
        ir = ServiceIR.model_validate(ir_data)
        proxy = _make_proxy(ir)
        assert len(proxy._service_ir.operations) == len(ir.operations)
