"""Contract: every extractor MUST produce a valid ServiceIR.

Validates that each extractor produces output conforming to the ServiceIR
Pydantic schema, has required fields populated, ir_version is set, and
operations have valid types.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

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
from libs.ir.models import IR_VERSION, RiskLevel, ServiceIR

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# ── helpers ────────────────────────────────────────────────────────────────


def _file_source(rel: str) -> SourceConfig:
    return SourceConfig(file_path=str(FIXTURES / rel))


def _extract_from_file(extractor: ExtractorProtocol, rel: str) -> ServiceIR:
    return extractor.extract(_file_source(rel))


def _make_sqlite_db() -> str:
    """Create a minimal SQLite database and return its async URL."""
    db_path = FIXTURES / "sql_schemas" / "_contract_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
    conn.commit()
    conn.close()
    return f"sqlite+aiosqlite:///{db_path}"


def _cleanup_sqlite_db() -> None:
    db_path = FIXTURES / "sql_schemas" / "_contract_test.db"
    if db_path.exists():
        os.unlink(db_path)


# ── parametrized extractor matrix ─────────────────────────────────────────

_FILE_EXTRACTORS: list[tuple[str, ExtractorProtocol, str]] = [
    ("openapi", OpenAPIExtractor(), "openapi_specs/petstore_3_0.yaml"),
    ("graphql", GraphQLExtractor(), "graphql_schemas/catalog_introspection.json"),
    ("grpc", GrpcProtoExtractor(), "grpc_protos/inventory.proto"),
    ("soap", SOAPWSDLExtractor(), "wsdl/order_service.wsdl"),
    ("jsonrpc", JsonRpcExtractor(), "jsonrpc_specs/manual_user_service.json"),
    ("odata", ODataExtractor(), "odata_metadata/simple_entity.xml"),
    ("scim", SCIMExtractor(), "scim_schemas/user_group.json"),
]


@pytest.fixture(params=[e[0] for e in _FILE_EXTRACTORS], ids=[e[0] for e in _FILE_EXTRACTORS])
def file_extractor_ir(request: pytest.FixtureRequest) -> ServiceIR:
    """Extract IR from a file-based fixture for the parametrized extractor."""
    for name, ext, fixture in _FILE_EXTRACTORS:
        if name == request.param:
            return _extract_from_file(ext, fixture)
    raise ValueError(f"Unknown extractor: {request.param}")  # pragma: no cover


@pytest.fixture()
def sql_extractor_ir() -> ServiceIR:
    """Extract IR from a transient SQLite database."""
    url = _make_sqlite_db()
    try:
        ext = SQLExtractor()
        src = SourceConfig(url=url, hints={"protocol": "sql"})
        return ext.extract(src)
    finally:
        _cleanup_sqlite_db()


# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.contract
class TestExtractorProducesValidIR:
    """Every file-based extractor MUST emit a valid ServiceIR."""

    def test_output_is_service_ir_instance(self, file_extractor_ir: ServiceIR) -> None:
        assert isinstance(file_extractor_ir, ServiceIR)

    def test_ir_version_is_set(self, file_extractor_ir: ServiceIR) -> None:
        assert file_extractor_ir.ir_version == IR_VERSION

    def test_protocol_is_nonempty(self, file_extractor_ir: ServiceIR) -> None:
        assert file_extractor_ir.protocol

    def test_service_name_is_nonempty(self, file_extractor_ir: ServiceIR) -> None:
        assert file_extractor_ir.service_name

    def test_source_hash_is_hex(self, file_extractor_ir: ServiceIR) -> None:
        assert len(file_extractor_ir.source_hash) >= 16
        int(file_extractor_ir.source_hash, 16)  # must be valid hex

    def test_operations_list_is_populated(self, file_extractor_ir: ServiceIR) -> None:
        assert len(file_extractor_ir.operations) > 0

    def test_operation_ids_are_unique(self, file_extractor_ir: ServiceIR) -> None:
        ids = [op.id for op in file_extractor_ir.operations]
        assert len(ids) == len(set(ids))

    def test_operations_have_valid_risk_levels(self, file_extractor_ir: ServiceIR) -> None:
        valid = set(RiskLevel)
        for op in file_extractor_ir.operations:
            assert op.risk.risk_level in valid, f"op {op.id} has invalid risk_level"

    def test_round_trip_through_pydantic(self, file_extractor_ir: ServiceIR) -> None:
        """model_dump → model_validate round-trip preserves the IR."""
        raw: dict[str, Any] = file_extractor_ir.model_dump(mode="json")
        restored = ServiceIR.model_validate(raw)
        assert restored.service_name == file_extractor_ir.service_name
        assert len(restored.operations) == len(file_extractor_ir.operations)


@pytest.mark.contract
class TestSQLExtractorProducesValidIR:
    """The SQL extractor MUST produce a valid IR from a live SQLite database."""

    def test_output_is_service_ir_instance(self, sql_extractor_ir: ServiceIR) -> None:
        assert isinstance(sql_extractor_ir, ServiceIR)

    def test_protocol_is_sql(self, sql_extractor_ir: ServiceIR) -> None:
        assert sql_extractor_ir.protocol == "sql"

    def test_ir_version_matches(self, sql_extractor_ir: ServiceIR) -> None:
        assert sql_extractor_ir.ir_version == IR_VERSION

    def test_operations_include_crud(self, sql_extractor_ir: ServiceIR) -> None:
        op_ids = {op.id for op in sql_extractor_ir.operations}
        assert any("query" in oid for oid in op_ids), "Missing query operation"
        assert any("insert" in oid for oid in op_ids), "Missing insert operation"

    def test_sql_operations_have_sql_config(self, sql_extractor_ir: ServiceIR) -> None:
        for op in sql_extractor_ir.operations:
            assert op.sql is not None, f"Operation {op.id} missing SqlOperationConfig"
