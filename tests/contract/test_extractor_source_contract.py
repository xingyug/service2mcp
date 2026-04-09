"""Contract: every extractor output MUST set source='extractor'.

Verifies the source-tracking contract across all file-based extractors:
- Operation.source must be SourceType.extractor
- Param.source must be SourceType.extractor
- RiskMetadata.source must be SourceType.extractor
- No extractor may emit source='llm' or source='user_override'
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from libs.extractors import (
    GraphQLExtractor,
    GrpcProtoExtractor,
    JsonRpcExtractor,
    ODataExtractor,
    OpenAPIExtractor,
    SCIMExtractor,
    SOAPWSDLExtractor,
    SQLExtractor,
)
from libs.extractors.base import ExtractorProtocol, SourceConfig
from libs.ir.models import ServiceIR, SourceType

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# ── helpers ────────────────────────────────────────────────────────────────


def _file_source(rel: str) -> SourceConfig:
    return SourceConfig(file_path=str(FIXTURES / rel))


_FILE_CASES: list[tuple[str, ExtractorProtocol, str]] = [
    ("openapi", OpenAPIExtractor(), "openapi_specs/petstore_3_0.yaml"),
    ("graphql", GraphQLExtractor(), "graphql_schemas/catalog_introspection.json"),
    ("grpc", GrpcProtoExtractor(), "grpc_protos/inventory.proto"),
    ("soap", SOAPWSDLExtractor(), "wsdl/order_service.wsdl"),
    ("jsonrpc", JsonRpcExtractor(), "jsonrpc_specs/manual_user_service.json"),
    ("jsonrpc_openrpc", JsonRpcExtractor(), "jsonrpc_specs/openrpc_calculator.json"),
    ("odata", ODataExtractor(), "odata_metadata/simple_entity.xml"),
    ("scim", SCIMExtractor(), "scim_schemas/user_group.json"),
]


@pytest.fixture(
    params=[c[0] for c in _FILE_CASES],
    ids=[c[0] for c in _FILE_CASES],
)
def extractor_ir(request: pytest.FixtureRequest) -> ServiceIR:
    for name, ext, fixture in _FILE_CASES:
        if name == request.param:
            return ext.extract(_file_source(fixture))
    raise ValueError(f"Unknown case: {request.param}")  # pragma: no cover


@pytest.fixture()
def sql_ir() -> ServiceIR:
    db_path = FIXTURES / "sql_schemas" / "_source_contract_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.commit()
    conn.close()
    try:
        ext = SQLExtractor()
        src = SourceConfig(url=f"sqlite+aiosqlite:///{db_path}", hints={"protocol": "sql"})
        return ext.extract(src)
    finally:
        if db_path.exists():
            os.unlink(db_path)


# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.contract
class TestExtractorSourceTracking:
    """Extractor output MUST use source='extractor' — never 'llm' or 'user_override'."""

    def test_operation_source_is_extractor(self, extractor_ir: ServiceIR) -> None:
        for op in extractor_ir.operations:
            assert op.source == SourceType.extractor, (
                f"Operation {op.id} has source={op.source}, expected 'extractor'"
            )

    def test_param_source_is_extractor(self, extractor_ir: ServiceIR) -> None:
        for op in extractor_ir.operations:
            for param in op.params:
                assert param.source == SourceType.extractor, (
                    f"Param {param.name} on op {op.id} has source={param.source}"
                )

    def test_risk_source_is_extractor(self, extractor_ir: ServiceIR) -> None:
        for op in extractor_ir.operations:
            assert op.risk.source == SourceType.extractor, (
                f"Risk on op {op.id} has source={op.risk.source}"
            )

    def test_no_llm_source_anywhere(self, extractor_ir: ServiceIR) -> None:
        for op in extractor_ir.operations:
            assert op.source != SourceType.llm
            assert op.risk.source != SourceType.llm
            for p in op.params:
                assert p.source != SourceType.llm

    def test_no_user_override_source_anywhere(self, extractor_ir: ServiceIR) -> None:
        for op in extractor_ir.operations:
            assert op.source != SourceType.user_override
            assert op.risk.source != SourceType.user_override
            for p in op.params:
                assert p.source != SourceType.user_override

    def test_param_confidence_above_threshold(self, extractor_ir: ServiceIR) -> None:
        """Extractor params with source='extractor' must have confidence >= 0.8."""
        for op in extractor_ir.operations:
            for param in op.params:
                assert param.confidence >= 0.8, (
                    f"Param {param.name} on op {op.id} confidence={param.confidence} < 0.8"
                )

    def test_operation_confidence_in_valid_range(self, extractor_ir: ServiceIR) -> None:
        for op in extractor_ir.operations:
            assert 0.0 <= op.confidence <= 1.0, (
                f"Op {op.id} confidence={op.confidence} out of range"
            )

    def test_risk_confidence_in_valid_range(self, extractor_ir: ServiceIR) -> None:
        for op in extractor_ir.operations:
            assert 0.0 <= op.risk.confidence <= 1.0, (
                f"Op {op.id} risk confidence={op.risk.confidence} out of range"
            )


@pytest.mark.contract
class TestSQLExtractorSourceTracking:
    """SQL extractor MUST also follow the source='extractor' contract."""

    def test_sql_operation_source(self, sql_ir: ServiceIR) -> None:
        for op in sql_ir.operations:
            assert op.source == SourceType.extractor

    def test_sql_param_source(self, sql_ir: ServiceIR) -> None:
        for op in sql_ir.operations:
            for p in op.params:
                assert p.source == SourceType.extractor

    def test_sql_risk_source(self, sql_ir: ServiceIR) -> None:
        for op in sql_ir.operations:
            assert op.risk.source == SourceType.extractor
