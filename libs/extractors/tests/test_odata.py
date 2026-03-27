"""Tests for the OData v4 $metadata extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.odata import ODataExtractor
from libs.ir.models import RiskLevel

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "odata_metadata"


@pytest.fixture
def extractor() -> ODataExtractor:
    return ODataExtractor()


# ── Detection tests ────────────────────────────────────────────────────────


def test_detect_with_protocol_hint(extractor: ODataExtractor) -> None:
    source = SourceConfig(file_content="<anything/>", hints={"protocol": "odata"})
    assert extractor.detect(source) == 1.0


def test_detect_with_metadata_url(extractor: ODataExtractor) -> None:
    source = SourceConfig(
        file_content="<edmx:Edmx/>",
        url="https://api.example.com/odata/$metadata",
    )
    assert extractor.detect(source) == 0.95


def test_detect_with_edmx_content(extractor: ODataExtractor) -> None:
    content = (FIXTURES_DIR / "simple_entity.xml").read_text()
    source = SourceConfig(file_content=content)
    assert extractor.detect(source) == 0.9


def test_detect_non_odata(extractor: ODataExtractor) -> None:
    source = SourceConfig(file_content="<html><body>Not OData</body></html>")
    assert extractor.detect(source) == 0.0


# ── Extraction tests — simple_entity.xml ───────────────────────────────────


def test_extract_simple_entity(extractor: ODataExtractor) -> None:
    source = SourceConfig(file_path=str(FIXTURES_DIR / "simple_entity.xml"))
    ir = extractor.extract(source)

    assert ir.protocol == "odata"

    # 2 EntitySets × 5 ops + 1 FunctionImport + 1 ActionImport = 12
    assert len(ir.operations) == 12

    # Verify list operation has OData query params
    list_products = next(op for op in ir.operations if op.id == "list_products")
    param_names = {p.name for p in list_products.params}
    assert {"$filter", "$select", "$top", "$skip", "$orderby"} == param_names

    # Verify $top is integer type
    top_param = next(p for p in list_products.params if p.name == "$top")
    assert top_param.type == "integer"

    # Create operation should have non-key properties
    create_products = next(op for op in ir.operations if op.id == "create_products")
    create_param_names = {p.name for p in create_products.params}
    assert "Id" not in create_param_names
    assert "Name" in create_param_names
    assert "Price" in create_param_names
    assert "Category" in create_param_names

    # Delete has dangerous risk
    delete_products = next(op for op in ir.operations if op.id == "delete_products")
    assert delete_products.risk.risk_level is RiskLevel.dangerous

    # FunctionImport → GET
    func_op = next(op for op in ir.operations if op.id == "func_get_top_products")
    assert func_op.method == "GET"

    # ActionImport → POST
    action_op = next(op for op in ir.operations if op.id == "action_reset_product_data")
    assert action_op.method == "POST"

    # All operations have error schema
    for op in ir.operations:
        assert op.error_schema is not None
        assert op.error_schema.default_error_schema is not None
        assert "error" in op.error_schema.default_error_schema["properties"]

    # Metadata
    assert ir.metadata["odata_version"] == "4.0"
    assert ir.metadata["schema_namespace"] == "Example.Model"
    assert "Product" in ir.metadata["entity_types"]
    assert "Category" in ir.metadata["entity_types"]
    assert "Products" in ir.metadata["entity_sets"]
    assert "Categories" in ir.metadata["entity_sets"]


# ── Extraction tests — complex_nav.xml ─────────────────────────────────────


def test_extract_complex_nav(extractor: ODataExtractor) -> None:
    source = SourceConfig(file_path=str(FIXTURES_DIR / "complex_nav.xml"))
    ir = extractor.extract(source)

    assert ir.protocol == "odata"

    # 3 EntitySets × 5 ops + 1 FunctionImport = 16
    assert len(ir.operations) == 16

    # All entity sets produce operations
    op_ids = {op.id for op in ir.operations}
    for es_name in ("orders", "customers", "orderitems"):
        assert f"list_{es_name}" in op_ids
        assert f"get_{es_name}_by_key" in op_ids
        assert f"create_{es_name}" in op_ids
        assert f"update_{es_name}" in op_ids
        assert f"delete_{es_name}" in op_ids

    # FunctionImport with parameter
    func_op = next(op for op in ir.operations if op.id == "func_get_orders_by_status")
    assert func_op.method == "GET"
    assert len(func_op.params) == 1
    assert func_op.params[0].name == "status"
    assert func_op.params[0].type == "string"
