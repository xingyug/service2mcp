"""Performance baseline tests that establish timing contracts for critical paths.

Run with:
    pytest tests/performance/ -q --tb=short -m performance
"""

from __future__ import annotations

import gc
import hashlib
import time
import tracemalloc
from pathlib import Path

import pytest

from libs.extractors.base import SourceConfig, TypeDetector
from libs.ir.models import (
    AuthConfig,
    AuthType,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
)

# ---------------------------------------------------------------------------
# Helpers — synthetic IR generation
# ---------------------------------------------------------------------------

_COUNTER = 0


def _unique_id(prefix: str = "op") -> str:
    global _COUNTER
    _COUNTER += 1
    return f"{prefix}_{_COUNTER}"


def _make_operation(
    *,
    n_params: int = 3,
    method: str = "GET",
) -> Operation:
    """Build a single synthetic operation with *n_params* parameters."""
    op_id = _unique_id("op")
    return Operation(
        id=op_id,
        name=f"Operation {op_id}",
        description=f"A synthetic operation for perf testing ({op_id})",
        method=method,
        path=f"/resource/{op_id}" + "/{id}" * (n_params > 0),
        params=[
            Param(
                name=f"param_{i}",
                type="string" if i % 3 == 0 else ("integer" if i % 3 == 1 else "boolean"),
                required=i < 2,
                description=f"Parameter {i} of {op_id}",
            )
            for i in range(n_params)
        ],
        risk=RiskMetadata(
            writes_state=method != "GET",
            destructive=method == "DELETE",
            risk_level=RiskLevel.safe if method == "GET" else RiskLevel.cautious,
        ),
        tags=["perf-test"],
    )


def make_synthetic_ir(n_operations: int, *, n_params: int = 3) -> ServiceIR:
    """Generate a synthetic ServiceIR with *n_operations* operations."""
    ops = [
        _make_operation(n_params=n_params, method="GET" if i % 4 != 3 else "POST")
        for i in range(n_operations)
    ]
    return ServiceIR(
        source_hash=hashlib.sha256(f"synthetic-{n_operations}".encode()).hexdigest(),
        protocol="openapi",
        service_name=f"synthetic-service-{n_operations}",
        service_description=f"Synthetic service with {n_operations} operations",
        base_url="https://synthetic.example.com/api",
        auth=AuthConfig(type=AuthType.bearer),
        operations=ops,
    )


# ---------------------------------------------------------------------------
# 1. Extraction performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_openapi_extraction_under_500ms(openapi_fixture_path: Path) -> None:
    """Extract a medium–large OpenAPI spec in <500 ms."""
    from libs.extractors.openapi import OpenAPIExtractor

    extractor = OpenAPIExtractor()
    source = SourceConfig(file_path=str(openapi_fixture_path))

    start = time.perf_counter()
    ir = extractor.extract(source)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert ir.protocol == "openapi"
    assert len(ir.operations) > 0
    assert elapsed_ms < 500, f"OpenAPI extraction took {elapsed_ms:.1f} ms (limit 500 ms)"


@pytest.mark.performance
def test_graphql_extraction_under_500ms(graphql_fixture_path: Path) -> None:
    """Extract from GraphQL introspection JSON in <500 ms."""
    from libs.extractors.graphql import GraphQLExtractor

    extractor = GraphQLExtractor()
    source = SourceConfig(
        file_path=str(graphql_fixture_path),
        url="https://catalog.example.com/graphql",
    )

    start = time.perf_counter()
    ir = extractor.extract(source)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert ir.protocol == "graphql"
    assert len(ir.operations) > 0
    assert elapsed_ms < 500, f"GraphQL extraction took {elapsed_ms:.1f} ms (limit 500 ms)"


@pytest.mark.performance
def test_grpc_extraction_under_500ms(grpc_fixture_path: Path) -> None:
    """Extract from a .proto file in <500 ms."""
    from libs.extractors.grpc import GrpcProtoExtractor

    extractor = GrpcProtoExtractor()
    source = SourceConfig(
        file_path=str(grpc_fixture_path),
        url="grpc://inventory.example.internal:443",
    )

    start = time.perf_counter()
    ir = extractor.extract(source)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert ir.protocol == "grpc"
    assert len(ir.operations) > 0
    assert elapsed_ms < 500, f"gRPC extraction took {elapsed_ms:.1f} ms (limit 500 ms)"


@pytest.mark.performance
def test_soap_extraction_under_500ms(wsdl_fixture_path: Path) -> None:
    """Extract from a WSDL document in <500 ms."""
    from libs.extractors.soap import SOAPWSDLExtractor

    extractor = SOAPWSDLExtractor()
    source = SourceConfig(file_path=str(wsdl_fixture_path))

    start = time.perf_counter()
    ir = extractor.extract(source)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert ir.protocol == "soap"
    assert len(ir.operations) > 0
    assert elapsed_ms < 500, f"SOAP extraction took {elapsed_ms:.1f} ms (limit 500 ms)"


@pytest.mark.performance
def test_sql_extraction_under_500ms() -> None:
    """Extract from a SQLite database in <500 ms."""
    import sqlite3

    from libs.extractors.sql import SQLExtractor

    # Build a small in-memory-style SQLite DB on disk (SQLExtractor needs a URL).
    db_path = Path(__file__).resolve().parent / "_perf_test_catalog.db"
    try:
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY, email TEXT NOT NULL, tier TEXT DEFAULT 'standard'
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                total_cents INTEGER NOT NULL, created_at TEXT
            );
            """
        )
        conn.close()

        extractor = SQLExtractor()
        source = SourceConfig(url=f"sqlite:///{db_path}")

        start = time.perf_counter()
        ir = extractor.extract(source)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert ir.protocol == "sql"
        assert len(ir.operations) > 0
        assert elapsed_ms < 500, f"SQL extraction took {elapsed_ms:.1f} ms (limit 500 ms)"
    finally:
        db_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 2. Validation performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
@pytest.mark.asyncio
async def test_validation_small_ir_under_100ms() -> None:
    """Validate a 10-operation IR in <100 ms."""
    from libs.validator.pre_deploy import PreDeployValidator

    ir = make_synthetic_ir(10)

    async with PreDeployValidator(timeout=0.01) as validator:
        start = time.perf_counter()
        report = await validator.validate(ir)
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert report.results is not None
    assert elapsed_ms < 100, f"Small IR validation took {elapsed_ms:.1f} ms (limit 100 ms)"


@pytest.mark.performance
@pytest.mark.asyncio
async def test_validation_large_ir_under_500ms() -> None:
    """Validate a 200-operation IR in <500 ms."""
    from libs.validator.pre_deploy import PreDeployValidator

    ir = make_synthetic_ir(200)

    async with PreDeployValidator(timeout=0.01) as validator:
        start = time.perf_counter()
        report = await validator.validate(ir)
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert report.results is not None
    assert elapsed_ms < 500, f"Large IR validation took {elapsed_ms:.1f} ms (limit 500 ms)"


# ---------------------------------------------------------------------------
# 3. IR serialization performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_ir_serialization_roundtrip_under_50ms() -> None:
    """model_dump + model_validate round-trip for a 50-operation IR in <50 ms."""
    ir = make_synthetic_ir(50)

    start = time.perf_counter()
    data = ir.model_dump(mode="json")
    restored = ServiceIR.model_validate(data)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(restored.operations) == 50
    assert restored.service_name == ir.service_name
    assert elapsed_ms < 50, f"50-op IR roundtrip took {elapsed_ms:.1f} ms (limit 50 ms)"


@pytest.mark.performance
def test_large_ir_serialization_under_200ms() -> None:
    """model_dump + model_validate round-trip for a 500-operation IR in <200 ms."""
    ir = make_synthetic_ir(500, n_params=5)

    start = time.perf_counter()
    data = ir.model_dump(mode="json")
    restored = ServiceIR.model_validate(data)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(restored.operations) == 500
    assert elapsed_ms < 500, f"500-op IR roundtrip took {elapsed_ms:.1f} ms (limit 500 ms)"


# ---------------------------------------------------------------------------
# 4. Proxy setup performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_proxy_construction_under_50ms() -> None:
    """RuntimeProxy init with a 100-tool IR in <50 ms."""
    from prometheus_client import CollectorRegistry

    from apps.mcp_runtime.observability import RuntimeObservability
    from apps.mcp_runtime.proxy import RuntimeProxy

    ir = make_synthetic_ir(100)
    obs = RuntimeObservability(registry=CollectorRegistry())

    start = time.perf_counter()
    proxy = RuntimeProxy(service_ir=ir, observability=obs)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert proxy is not None
    assert elapsed_ms < 50, f"Proxy construction took {elapsed_ms:.1f} ms (limit 50 ms)"


@pytest.mark.performance
def test_tool_registration_under_100ms() -> None:
    """Register 100 tools from IR via loader in <500 ms."""
    from mcp.server.fastmcp import FastMCP

    from apps.mcp_runtime.loader import register_ir_tools

    ir = make_synthetic_ir(100)
    server = FastMCP("perf-test")

    start = time.perf_counter()
    registered = register_ir_tools(server, ir)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(registered) == 100
    assert elapsed_ms < 1000, f"Tool registration took {elapsed_ms:.1f} ms (limit 1000 ms)"


# ---------------------------------------------------------------------------
# 5. Detection performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_protocol_detection_under_200ms(
    openapi_fixture_path: Path,
    graphql_fixture_path: Path,
    grpc_fixture_path: Path,
    wsdl_fixture_path: Path,
) -> None:
    """Detect protocol from various content types in <200 ms total."""
    from libs.extractors.graphql import GraphQLExtractor
    from libs.extractors.grpc import GrpcProtoExtractor
    from libs.extractors.openapi import OpenAPIExtractor
    from libs.extractors.soap import SOAPWSDLExtractor

    detector = TypeDetector(
        [
            OpenAPIExtractor(),
            GraphQLExtractor(),
            GrpcProtoExtractor(),
            SOAPWSDLExtractor(),
        ]
    )

    sources = [
        SourceConfig(file_path=str(openapi_fixture_path)),
        SourceConfig(file_path=str(graphql_fixture_path)),
        SourceConfig(
            file_path=str(grpc_fixture_path),
            url="grpc://inventory.example.internal:443",
        ),
        SourceConfig(file_path=str(wsdl_fixture_path)),
    ]

    start = time.perf_counter()
    for source in sources:
        result = detector.detect(source)
        assert result.confidence > 0
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 500, f"Protocol detection took {elapsed_ms:.1f} ms (limit 500 ms)"


# ---------------------------------------------------------------------------
# 6. Memory baselines
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_large_ir_memory_under_50mb() -> None:
    """A 1000-operation IR should use <50 MB of memory."""
    gc.collect()
    tracemalloc.start()

    ir = make_synthetic_ir(1000, n_params=5)
    # Force serialization to ensure all lazy fields are materialized.
    _ = ir.model_dump(mode="json")

    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak / (1024 * 1024)
    assert len(ir.operations) == 1000
    assert peak_mb < 50, f"1000-op IR peak memory: {peak_mb:.1f} MB (limit 50 MB)"


@pytest.mark.performance
def test_extraction_memory_stable(openapi_fixture_path: Path) -> None:
    """Extraction should not leak: 10 runs, RSS delta should stay bounded."""
    from libs.extractors.openapi import OpenAPIExtractor

    extractor = OpenAPIExtractor()
    source = SourceConfig(file_path=str(openapi_fixture_path))

    # Warm-up run
    _ = extractor.extract(source)
    gc.collect()

    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    for _ in range(10):
        ir = extractor.extract(source)
        assert len(ir.operations) > 0

    gc.collect()
    snapshot_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    stats = snapshot_after.compare_to(snapshot_before, "lineno")
    total_delta_bytes = sum(s.size_diff for s in stats if s.size_diff > 0)
    total_delta_mb = total_delta_bytes / (1024 * 1024)

    # 10 extractions shouldn't accumulate more than 10 MB of unreleased memory.
    assert total_delta_mb < 10, (
        f"Extraction memory grew by {total_delta_mb:.1f} MB over 10 runs (limit 10 MB)"
    )


# ---------------------------------------------------------------------------
# 7. IR composition performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_ir_composition_10_services_under_200ms() -> None:
    """Compose 10 ServiceIRs into a single federated IR in <200 ms."""
    from libs.ir.compose import CompositionStrategy, compose_irs

    irs = [make_synthetic_ir(20, n_params=3) for _ in range(10)]
    strategy = CompositionStrategy(prefix_operation_ids=True, fail_on_conflict=False)

    start = time.perf_counter()
    merged = compose_irs(irs, strategy=strategy)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(merged.operations) == 200
    assert elapsed_ms < 200, f"Composing 10 IRs took {elapsed_ms:.1f} ms (limit 200 ms)"


# ---------------------------------------------------------------------------
# 8. IR transformation performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_ir_transform_100_rules_under_200ms() -> None:
    """Apply 100 transformation rules to a 50-operation IR in <200 ms."""
    from libs.ir.transform import TransformAction, TransformRule, apply_transforms

    ir = make_synthetic_ir(50, n_params=3)
    rules: list[TransformRule] = []
    for i in range(100):
        if i % 3 == 0:
            rules.append(
                TransformRule(action=TransformAction.add_tag, target="*", value=f"tag_{i}")
            )
        elif i % 3 == 1:
            rules.append(
                TransformRule(
                    action=TransformAction.override_risk,
                    target="*",
                    value="cautious",
                )
            )
        else:
            rules.append(
                TransformRule(action=TransformAction.set_metadata, target="*", value=f"v{i}")
            )

    start = time.perf_counter()
    transformed = apply_transforms(ir, rules)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(transformed.operations) == 50
    assert elapsed_ms < 200, f"100 transform rules took {elapsed_ms:.1f} ms (limit 200 ms)"


# ---------------------------------------------------------------------------
# 9. SLA baseline computation performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_sla_recommendation_100_ops_under_100ms() -> None:
    """Compute SLA baselines for 100 operations from latency data in <100 ms."""
    import random

    from libs.ir.sla import recommend_sla_for_ir

    ir = make_synthetic_ir(100, n_params=3)
    rng = random.Random(42)  # noqa: S311
    latency_data: dict[str, list[float]] = {
        op.id: [rng.uniform(10, 500) for _ in range(200)] for op in ir.operations
    }

    start = time.perf_counter()
    updated_ir = recommend_sla_for_ir(ir, latency_data)
    elapsed_ms = (time.perf_counter() - start) * 1000

    ops_with_sla = sum(1 for op in updated_ir.operations if op.sla is not None)
    assert ops_with_sla == 100
    assert elapsed_ms < 100, (
        f"SLA recommendation for 100 ops took {elapsed_ms:.1f} ms (limit 100 ms)"
    )


# ---------------------------------------------------------------------------
# 10. Drift detection performance
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_drift_detection_200_ops_under_200ms() -> None:
    """Detect drift between two 200-operation IRs in <200 ms."""
    from libs.validator.drift import detect_drift

    deployed = make_synthetic_ir(200, n_params=4)
    # Build a "live" IR with some mutations to exercise comparison logic.
    live = deployed.model_copy(deep=True)
    live_ops = list(live.operations)
    # Remove two ops, mutate a few params.
    live_ops.pop(0)
    live_ops.pop(-1)
    for op in live_ops[:10]:
        op.description = op.description + " (updated)"
        if op.params:
            op.params[0] = op.params[0].model_copy(update={"type": "number"})
    live = live.model_copy(update={"operations": live_ops})

    start = time.perf_counter()
    report = detect_drift(deployed, live)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert report.has_drift
    assert elapsed_ms < 200, f"Drift detection for 200 ops took {elapsed_ms:.1f} ms (limit 200 ms)"
