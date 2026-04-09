"""Regression lock tests for green-parity protocols.

These tests assert exact operation counts from extractor runs against
known fixture files.  If an extractor change causes a count regression,
the test will fail, preventing accidental surface reduction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.odata import ODataExtractor
from libs.extractors.openapi import OpenAPIExtractor
from libs.extractors.soap import SOAPWSDLExtractor

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
REAL_TARGETS_DIR = Path(__file__).resolve().parents[3] / "deploy" / "k8s" / "real-targets"


# ---------------------------------------------------------------------------
# Parametrized regression locks
# ---------------------------------------------------------------------------

_REGRESSION_CASES: list[tuple[str, type, Path, int, str]] = [
    # (case_id, extractor_class, fixture_path, expected_ops, protocol)
    (
        "odata-northbreeze-17",
        ODataExtractor,
        FIXTURES_DIR / "odata_metadata" / "northbreeze.xml",
        17,
        "odata",
    ),
    (
        "soap-cxf-3",
        SOAPWSDLExtractor,
        REAL_TARGETS_DIR / "soap-cxf" / "src" / "main" / "resources" / "wsdl" / "OrderService.wsdl",
        3,
        "soap",
    ),
    (
        "openapi-petstore30-5",
        OpenAPIExtractor,
        FIXTURES_DIR / "openapi_specs" / "petstore_3_0.yaml",
        5,
        "openapi",
    ),
    (
        "openapi-petstore-swagger20-2",
        OpenAPIExtractor,
        FIXTURES_DIR / "openapi_specs" / "petstore_swagger_2_0.json",
        2,
        "openapi",
    ),
    (
        "odata-simple-entity-12",
        ODataExtractor,
        FIXTURES_DIR / "odata_metadata" / "simple_entity.xml",
        12,
        "odata",
    ),
    (
        "odata-complex-nav-16",
        ODataExtractor,
        FIXTURES_DIR / "odata_metadata" / "complex_nav.xml",
        16,
        "odata",
    ),
]


@pytest.mark.parametrize(
    "case_id, extractor_cls, fixture_path, expected_ops, protocol",
    _REGRESSION_CASES,
    ids=[c[0] for c in _REGRESSION_CASES],
)
def test_regression_lock_operation_count(
    case_id: str,
    extractor_cls: type,
    fixture_path: Path,
    expected_ops: int,
    protocol: str,
) -> None:
    """Assert extractor produces exactly the expected number of operations."""
    assert fixture_path.exists(), f"Fixture missing: {fixture_path}"

    extractor = extractor_cls()
    source = SourceConfig(file_path=str(fixture_path))
    service_ir = extractor.extract(source)

    assert service_ir.protocol == protocol
    assert len(service_ir.operations) == expected_ops, (
        f"Regression in {case_id}: expected {expected_ops} operations, "
        f"got {len(service_ir.operations)}. "
        f"Operation IDs: {[op.id for op in service_ir.operations]}"
    )


# ---------------------------------------------------------------------------
# Individual regression locks with deeper assertions
# ---------------------------------------------------------------------------


class TestNorthbreezeODataParity:
    """NorthBreeze OData: 3 EntitySets × 5 ops + 1 FunctionImport + 1 ActionImport = 17."""

    @pytest.fixture
    def service_ir(self):
        fixture = FIXTURES_DIR / "odata_metadata" / "northbreeze.xml"
        extractor = ODataExtractor()
        return extractor.extract(SourceConfig(file_path=str(fixture)))

    def test_exact_operation_count(self, service_ir) -> None:
        assert len(service_ir.operations) == 17

    def test_protocol(self, service_ir) -> None:
        assert service_ir.protocol == "odata"

    def test_entity_set_operations(self, service_ir) -> None:
        op_ids = {op.id for op in service_ir.operations}
        for entity_set in ("products", "categories", "suppliers"):
            for action_id in (
                f"list_{entity_set}",
                f"get_{entity_set}_by_key",
                f"create_{entity_set}",
                f"update_{entity_set}",
                f"delete_{entity_set}",
            ):
                assert action_id in op_ids, f"Missing operation: {action_id}"

    def test_function_import(self, service_ir) -> None:
        op_ids = {op.id for op in service_ir.operations}
        assert "func_get_top_products" in op_ids

    def test_action_import(self, service_ir) -> None:
        op_ids = {op.id for op in service_ir.operations}
        assert "action_reset_data" in op_ids

    def test_safe_operations_are_safe(self, service_ir) -> None:
        for op in service_ir.operations:
            if op.id.startswith("list_") or op.id.startswith("get_"):
                assert op.risk.risk_level == "safe", f"{op.id} should be safe"

    def test_delete_operations_are_dangerous(self, service_ir) -> None:
        for op in service_ir.operations:
            if op.id.startswith("delete_"):
                assert op.risk.risk_level == "dangerous", f"{op.id} should be dangerous"


class TestSoapCxfParity:
    """SOAP CXF OrderService: 3 operations (GetOrderStatus, SubmitOrder, CancelOrder)."""

    @pytest.fixture
    def service_ir(self):
        fixture = (
            REAL_TARGETS_DIR
            / "soap-cxf"
            / "src"
            / "main"
            / "resources"
            / "wsdl"
            / "OrderService.wsdl"
        )
        extractor = SOAPWSDLExtractor()
        return extractor.extract(SourceConfig(file_path=str(fixture)))

    def test_exact_operation_count(self, service_ir) -> None:
        assert len(service_ir.operations) == 3

    def test_protocol(self, service_ir) -> None:
        assert service_ir.protocol == "soap"

    def test_operation_names(self, service_ir) -> None:
        op_names = {op.name for op in service_ir.operations}
        assert "Get Order Status" in op_names
        assert "Submit Order" in op_names
        assert "Cancel Order" in op_names

    def test_all_operations_are_post(self, service_ir) -> None:
        for op in service_ir.operations:
            assert op.method == "POST", f"SOAP operation {op.name} should be POST"
