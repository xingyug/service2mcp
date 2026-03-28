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


def test_extract_raises_for_none_content(extractor: ODataExtractor) -> None:
    """Test extraction raises ValueError when content is None."""
    with pytest.raises(ValueError, match="Could not read source content"):
        extractor.extract(SourceConfig(url="https://nonexistent.invalid"))


def test_extract_raises_for_malformed_xml(extractor: ODataExtractor) -> None:
    """Test extraction raises ValueError for malformed XML."""
    with pytest.raises(ValueError, match="Malformed XML in OData metadata document"):
        extractor.extract(SourceConfig(file_content="<invalid xml"))


def test_extract_raises_for_non_edmx_root(extractor: ODataExtractor) -> None:
    """Test extraction raises ValueError when root is not edmx:Edmx."""
    content = """<?xml version="1.0"?>
    <NotEdmx xmlns="http://docs.oasis-open.org/odata/ns/edmx"/>"""

    with pytest.raises(ValueError, match="OData extractor requires an EDMX metadata document"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_raises_for_missing_schema(extractor: ODataExtractor) -> None:
    """Test extraction raises ValueError when no Schema element found."""
    content = """<?xml version="1.0"?>
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
        <edmx:DataServices>
        </edmx:DataServices>
    </edmx:Edmx>"""

    with pytest.raises(ValueError, match="No Schema element found in EDMX document"):
        extractor.extract(SourceConfig(file_content=content))


def test_extract_strips_metadata_suffix_from_url(extractor: ODataExtractor) -> None:
    """Test extraction strips $metadata suffix from base URL."""
    content = """<?xml version="1.0"?>
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"
               xmlns:edm="http://docs.oasis-open.org/odata/ns/edm" Version="4.0">
        <edmx:DataServices>
            <edm:Schema Namespace="Test.Model">
                <edm:EntityContainer Name="TestContainer">
                </edm:EntityContainer>
            </edm:Schema>
        </edmx:DataServices>
    </edmx:Edmx>"""

    source = SourceConfig(file_content=content, url="https://api.example.com/odata/$metadata")
    ir = extractor.extract(source)
    assert ir.base_url == "https://api.example.com/odata"


def test_extract_strips_metadata_suffix_without_slash(extractor: ODataExtractor) -> None:
    """Test extraction strips $metadata suffix without leading slash."""
    content = """<?xml version="1.0"?>
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"
               xmlns:edm="http://docs.oasis-open.org/odata/ns/edm" Version="4.0">
        <edmx:DataServices>
            <edm:Schema Namespace="Test.Model">
                <edm:EntityContainer Name="TestContainer">
                </edm:EntityContainer>
            </edm:Schema>
        </edmx:DataServices>
    </edmx:Edmx>"""

    source = SourceConfig(file_content=content, url="https://api.example.com/odata$metadata")
    ir = extractor.extract(source)
    assert ir.base_url == "https://api.example.com/odata"


def test_extract_skips_unknown_entity_types(extractor: ODataExtractor) -> None:
    """Test extraction skips entity sets with unknown entity types."""
    content = """<?xml version="1.0"?>
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"
               xmlns:edm="http://docs.oasis-open.org/odata/ns/edm" Version="4.0">
        <edmx:DataServices>
            <edm:Schema Namespace="Test.Model">
                <edm:EntityType Name="KnownType">
                    <edm:Key>
                        <edm:PropertyRef Name="Id"/>
                    </edm:Key>
                    <edm:Property Name="Id" Type="Edm.Int32" Nullable="false"/>
                </edm:EntityType>
                <edm:EntityContainer Name="TestContainer">
                    <edm:EntitySet Name="KnownSet" EntityType="Test.Model.KnownType"/>
                    <edm:EntitySet Name="UnknownSet" EntityType="Test.Model.UnknownType"/>
                </edm:EntityContainer>
            </edm:Schema>
        </edmx:DataServices>
    </edmx:Edmx>"""

    ir = extractor.extract(SourceConfig(file_content=content))

    # Should only have operations for KnownSet, not UnknownSet
    op_ids = {op.id for op in ir.operations}
    assert any(op_id.startswith("list_knownset") for op_id in op_ids)
    assert not any(op_id.startswith("list_unknownset") for op_id in op_ids)


def test_extract_creates_function_imports_with_empty_params_for_unknown_functions(
    extractor: ODataExtractor,
) -> None:
    """Test that unknown function imports are preserved with empty params."""
    content = """<?xml version="1.0"?>
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"
               xmlns:edm="http://docs.oasis-open.org/odata/ns/edm" Version="4.0">
        <edmx:DataServices>
            <edm:Schema Namespace="Test.Model">
                <edm:Function Name="KnownFunction">
                    <edm:Parameter Name="param1" Type="Edm.String"/>
                    <edm:ReturnType Type="Collection(Edm.String)"/>
                </edm:Function>
                <edm:EntityContainer Name="TestContainer">
                    <edm:FunctionImport Name="KnownFunction" Function="Test.Model.KnownFunction"/>
                    <edm:FunctionImport
                        Name="UnknownFunction"
                        Function="Test.Model.UnknownFunction"
                    />
                </edm:EntityContainer>
            </edm:Schema>
        </edmx:DataServices>
    </edmx:Edmx>"""

    ir = extractor.extract(SourceConfig(file_content=content))

    # Should have operations for both function imports
    op_ids = {op.id: op for op in ir.operations}
    assert "func_known_function" in op_ids
    assert "func_unknown_function" in op_ids

    # Known function should have parameters, unknown should be empty
    known_op = op_ids["func_known_function"]
    unknown_op = op_ids["func_unknown_function"]

    assert len(known_op.params) == 1
    assert known_op.params[0].name == "param1"
    assert len(unknown_op.params) == 0


def test_get_content_handles_url_fetch_failure(extractor: ODataExtractor) -> None:
    """Test _get_content handles URL fetch failures gracefully."""
    import httpx
    import respx

    with respx.mock:
        respx.get("https://example.com/test.xml").mock(
            side_effect=httpx.RequestError("Connection failed")
        )

        content = extractor._get_content(SourceConfig(url="https://example.com/test.xml"))
        assert content is None


def test_get_content_with_auth_header(extractor: ODataExtractor) -> None:
    """Test _get_content uses auth_header correctly."""
    import httpx
    import respx

    xml_content = """<?xml version="1.0"?>
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"/>"""

    with respx.mock:
        respx.get("https://example.com/odata/$metadata").mock(
            return_value=httpx.Response(
                200,
                text=xml_content,
                request=httpx.Request("GET", "https://example.com/odata/$metadata"),
            )
        )

        content = extractor._get_content(
            SourceConfig(url="https://example.com/odata/$metadata", auth_header="Bearer token123")
        )
        assert content == xml_content


def test_get_content_with_auth_token(extractor: ODataExtractor) -> None:
    """Test _get_content uses auth_token correctly."""
    import httpx
    import respx

    xml_content = """<?xml version="1.0"?>
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"/>"""

    with respx.mock:
        respx.get("https://example.com/odata/$metadata").mock(
            return_value=httpx.Response(
                200,
                text=xml_content,
                request=httpx.Request("GET", "https://example.com/odata/$metadata"),
            )
        )

        content = extractor._get_content(
            SourceConfig(url="https://example.com/odata/$metadata", auth_token="token123")
        )
        assert content == xml_content


def test_parse_functions_skips_missing_names() -> None:
    """Test _parse_functions skips functions without names."""
    import xml.etree.ElementTree as ET

    from libs.extractors.odata import _parse_functions

    content = """<edm:Schema xmlns:edm="http://docs.oasis-open.org/odata/ns/edm">
        <edm:Function Name="ValidFunction">
            <edm:Parameter Name="param1" Type="Edm.String"/>
        </edm:Function>
        <edm:Function>
            <edm:Parameter Name="param2" Type="Edm.String"/>
        </edm:Function>
    </edm:Schema>"""

    schema = ET.fromstring(content)
    functions = _parse_functions(schema)

    assert "ValidFunction" in functions
    assert len(functions) == 1


def test_parse_actions_skips_missing_names() -> None:
    """Test _parse_actions skips actions without names."""
    import xml.etree.ElementTree as ET

    from libs.extractors.odata import _parse_actions

    content = """<edm:Schema xmlns:edm="http://docs.oasis-open.org/odata/ns/edm">
        <edm:Action Name="ValidAction">
            <edm:Parameter Name="param1" Type="Edm.String"/>
        </edm:Action>
        <edm:Action>
            <edm:Parameter Name="param2" Type="Edm.String"/>
        </edm:Action>
    </edm:Schema>"""

    schema = ET.fromstring(content)
    actions = _parse_actions(schema)

    assert "ValidAction" in actions
    assert len(actions) == 1


def test_strip_namespace_with_matching_namespace() -> None:
    """Test _strip_namespace removes matching namespace prefix."""
    from libs.extractors.odata import _strip_namespace

    result = _strip_namespace("Example.Model.Product", "Example.Model")
    assert result == "Product"


def test_strip_namespace_with_non_matching_namespace() -> None:
    """Test _strip_namespace keeps name when namespace doesn't match."""
    from libs.extractors.odata import _strip_namespace

    result = _strip_namespace("Other.Model.Product", "Example.Model")
    assert result == "Other.Model.Product"


def test_local_name_with_namespace_brace() -> None:
    """Test _local_name extracts local name from namespace with braces."""
    from libs.extractors.odata import _local_name

    result = _local_name("{http://example.com/ns}LocalName")
    assert result == "LocalName"


def test_local_name_with_colon_prefix() -> None:
    """Test _local_name extracts local name from prefixed name with colon."""
    from libs.extractors.odata import _local_name

    result = _local_name("prefix:LocalName")
    assert result == "LocalName"


def test_local_name_without_prefix() -> None:
    """Test _local_name returns name unchanged when no prefix."""
    from libs.extractors.odata import _local_name

    result = _local_name("LocalName")
    assert result == "LocalName"
