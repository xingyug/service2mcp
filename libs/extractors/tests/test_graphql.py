"""Tests for the GraphQL extractor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.graphql import GraphQLExtractor
from libs.ir.models import (
    AuthType,
    EventSupportLevel,
    EventTransport,
    GraphQLOperationType,
    RiskLevel,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures"
GRAPHQL_FIXTURE_PATH = FIXTURES_DIR / "graphql_schemas" / "catalog_introspection.json"


def test_detects_graphql_introspection_fixture() -> None:
    extractor = GraphQLExtractor()
    confidence = extractor.detect(SourceConfig(file_path=str(GRAPHQL_FIXTURE_PATH)))

    assert confidence >= 0.9


def test_extracts_queries_mutations_nested_types_and_enums() -> None:
    extractor = GraphQLExtractor()
    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(GRAPHQL_FIXTURE_PATH),
            url="https://catalog.example.com/graphql",
        )
    )

    assert service_ir.protocol == "graphql"
    assert service_ir.service_name == "catalog-introspection"
    assert service_ir.base_url == "https://catalog.example.com"
    assert len(service_ir.operations) == 2

    search_products = next(
        operation for operation in service_ir.operations if operation.id == "searchProducts"
    )
    assert search_products.risk.risk_level is RiskLevel.safe
    assert search_products.method == "POST"
    assert search_products.path == "/graphql"
    param_types = {param.name: param.type for param in search_products.params}
    assert param_types == {
        "term": "string",
        "category": "string",
        "filter": "object",
        "limit": "integer",
    }
    required = {param.name: param.required for param in search_products.params}
    assert required["term"] is True
    assert required["category"] is False
    assert required["filter"] is False
    assert required["limit"] is False
    assert search_products.graphql is not None
    assert search_products.graphql.operation_type is GraphQLOperationType.query
    assert search_products.graphql.operation_name == "searchProducts"
    assert search_products.graphql.variable_names == ["term", "category", "filter", "limit"]
    assert "query searchProducts" in search_products.graphql.document
    assert "searchProducts(term: $term" in search_products.graphql.document
    assert "{ id" in search_products.graphql.document
    limit_param = next(param for param in search_products.params if param.name == "limit")
    assert limit_param.default == 10

    adjust_inventory = next(
        operation for operation in service_ir.operations if operation.id == "adjustInventory"
    )
    assert adjust_inventory.risk.risk_level is RiskLevel.cautious
    mutation_param_types = {param.name: param.type for param in adjust_inventory.params}
    assert mutation_param_types == {
        "sku": "string",
        "delta": "integer",
        "reason": "string",
    }
    assert adjust_inventory.graphql is not None
    assert adjust_inventory.graphql.operation_type is GraphQLOperationType.mutation
    assert "mutation adjustInventory" in adjust_inventory.graphql.document
    assert all(param.required is True for param in adjust_inventory.params[:2])


def test_extracts_subscription_descriptors_as_explicit_unsupported_metadata() -> None:
    extractor = GraphQLExtractor()
    fixture_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "tests"
        / "fixtures"
        / "conformance"
        / "graphql"
        / "catalog_with_subscription.json"
    )
    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(fixture_path),
            url="https://catalog.example.com/graphql",
        )
    )

    assert service_ir.metadata["ignored_subscriptions"] == ["inventoryChanged"]
    assert len(service_ir.event_descriptors) == 1
    assert service_ir.event_descriptors[0].id == "inventoryChanged"
    assert service_ir.event_descriptors[0].transport is EventTransport.graphql_subscription
    assert service_ir.event_descriptors[0].support is EventSupportLevel.unsupported
    assert service_ir.event_descriptors[0].channel == "/graphql"


def test_graphql_operations_have_error_schema() -> None:
    extractor = GraphQLExtractor()
    service_ir = extractor.extract(
        SourceConfig(
            file_path=str(GRAPHQL_FIXTURE_PATH),
            url="https://catalog.example.com/graphql",
        )
    )

    assert len(service_ir.operations) >= 1
    for op in service_ir.operations:
        assert op.error_schema is not None
        assert op.error_schema.default_error_schema is not None
        schema = op.error_schema.default_error_schema
        assert schema["type"] == "object"
        errors_prop = schema["properties"]["errors"]
        assert errors_prop["type"] == "array"
        item_props = errors_prop["items"]["properties"]
        assert "message" in item_props
        assert "locations" in item_props
        assert "path" in item_props
        assert errors_prop["items"]["required"] == ["message"]


# ── detect edge cases ──────────────────────────────────────────────────────


def test_detect_returns_zero_when_file_content_is_not_valid_json() -> None:
    """Line 84, 87-88: _get_content returns content but _parse_schema raises."""
    extractor = GraphQLExtractor()
    assert extractor.detect(SourceConfig(file_content="not json at all")) == 0.0


def test_detect_url_with_graphql_in_name() -> None:
    """Lines 91-92: source has only a URL containing 'graphql'."""
    extractor = GraphQLExtractor()
    confidence = extractor.detect(SourceConfig(url="https://api.example.com/graphql"))
    assert confidence == 0.4


def test_detect_url_without_graphql_keyword() -> None:
    """Line 93: source has only a URL not containing 'graphql'."""
    extractor = GraphQLExtractor()
    confidence = extractor.detect(SourceConfig(url="https://api.example.com/api"))
    assert confidence == 0.0


# ── extract edge cases ─────────────────────────────────────────────────────


def test_extract_raises_when_no_content() -> None:
    """Line 98: extract raises ValueError when _get_content returns None."""
    extractor = GraphQLExtractor()
    with patch.object(extractor, "_get_content", return_value=None):
        with pytest.raises(ValueError, match="Could not read GraphQL schema source"):
            extractor.extract(SourceConfig(url="https://api.example.com/nothing"))


# ── _get_content via URL ───────────────────────────────────────────────────


def test_get_content_fetches_from_url() -> None:
    """Lines 148-157: _get_content performs HTTP POST when only URL is given."""
    extractor = GraphQLExtractor()
    schema_json = json.dumps({"data": {"__schema": {"types": []}}})
    mock_response = MagicMock()
    mock_response.text = schema_json
    mock_response.raise_for_status = MagicMock()

    with patch("libs.extractors.graphql.httpx.post", return_value=mock_response) as mock_post:
        content = extractor._get_content(SourceConfig(url="https://api.example.com/graphql"))

    assert content == schema_json
    mock_post.assert_called_once()


# ── _parse_schema variations ──────────────────────────────────────────────


def test_parse_schema_top_level_schema_key() -> None:
    """Line 162: payload has __schema at top level (not under data)."""
    extractor = GraphQLExtractor()
    content = json.dumps({"__schema": {"queryType": {"name": "Query"}, "types": []}})
    schema = extractor._parse_schema(content)
    assert "types" in schema


def test_parse_schema_invalid_payload_raises() -> None:
    """Line 166: payload is not a GraphQL introspection response."""
    extractor = GraphQLExtractor()
    with pytest.raises(ValueError, match="not a GraphQL introspection response"):
        extractor._parse_schema(json.dumps({"something": "else"}))


# ── _extract_root_operations edge cases ───────────────────────────────────


def _make_introspection(query_fields: list, mutation_fields: list | None = None) -> str:
    """Build a minimal introspection JSON with given query/mutation fields."""
    types = [
        {"kind": "OBJECT", "name": "Query", "fields": query_fields},
    ]
    schema: dict = {
        "__schema": {
            "queryType": {"name": "Query"},
            "types": types,
        }
    }
    if mutation_fields is not None:
        types.append({"kind": "OBJECT", "name": "Mutation", "fields": mutation_fields})
        schema["__schema"]["mutationType"] = {"name": "Mutation"}
    return json.dumps(schema)


def test_root_type_not_in_type_index() -> None:
    """Line 206: root type name not in type_index → empty operations."""
    extractor = GraphQLExtractor()
    content = json.dumps(
        {
            "__schema": {
                "queryType": {"name": "MissingType"},
                "types": [],
            }
        }
    )
    ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
    assert ir.operations == []


def test_root_type_fields_not_a_list() -> None:
    """Line 210: fields is not a list → empty operations."""
    extractor = GraphQLExtractor()
    content = json.dumps(
        {
            "__schema": {
                "queryType": {"name": "Query"},
                "types": [{"kind": "OBJECT", "name": "Query", "fields": "not-a-list"}],
            }
        }
    )
    ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
    assert ir.operations == []


def test_field_not_a_dict_is_skipped() -> None:
    """Line 217: non-dict field entries are skipped."""
    extractor = GraphQLExtractor()
    content = _make_introspection(query_fields=["not-a-dict"])
    ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
    assert ir.operations == []


def test_field_name_not_a_string_is_skipped() -> None:
    """Line 220: field with non-string name is skipped."""
    extractor = GraphQLExtractor()
    content = _make_introspection(query_fields=[{"name": 123, "args": []}])
    ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
    assert ir.operations == []


# ── _build_graphql_operation_config edge cases ────────────────────────────


def test_non_dict_argument_in_operation_config_skipped() -> None:
    """Line 293: non-dict argument is skipped in _build_graphql_operation_config."""
    extractor = GraphQLExtractor()
    content = _make_introspection(
        query_fields=[
            {
                "name": "myQuery",
                "description": "",
                "args": [
                    "not-a-dict",
                    {"name": "validArg", "type": {"kind": "SCALAR", "name": "String"}},
                ],
                "type": {"kind": "SCALAR", "name": "String", "ofType": None},
            }
        ]
    )
    ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
    op = next(o for o in ir.operations if o.id == "myQuery")
    assert op.graphql is not None
    assert op.graphql.variable_names == ["validArg"]


def test_argument_name_not_string_skipped() -> None:
    """Line 296: argument with non-string name is skipped."""
    extractor = GraphQLExtractor()
    content = _make_introspection(
        query_fields=[
            {
                "name": "myQuery",
                "description": "",
                "args": [{"name": 999, "type": {"kind": "SCALAR", "name": "Int"}}],
                "type": {"kind": "SCALAR", "name": "String", "ofType": None},
            }
        ]
    )
    ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
    op = next(o for o in ir.operations if o.id == "myQuery")
    assert op.graphql is not None
    assert op.graphql.variable_names == []


# ── _map_type edge cases ─────────────────────────────────────────────────


def test_map_type_list_returns_array() -> None:
    """Lines 334, 360: type containing LIST kind maps to 'array'."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "LIST", "ofType": {"kind": "SCALAR", "name": "String"}}
    assert extractor._map_type(type_ref, {}) == "array"


def test_map_type_object_returns_object() -> None:
    """Lines 346-347: OBJECT kind maps to 'object'."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "OBJECT", "name": "SomeType"}
    assert extractor._map_type(type_ref, {}) == "object"


def test_map_type_referenced_enum_returns_string() -> None:
    """Lines 349-351: unknown kind but type_index has ENUM → 'string'."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "UNKNOWN_KIND", "name": "MyEnum"}
    type_index = {"MyEnum": {"kind": "ENUM", "name": "MyEnum"}}
    assert extractor._map_type(type_ref, type_index) == "string"


def test_map_type_referenced_input_object_returns_object() -> None:
    """Lines 352-353: unknown kind but type_index has INPUT_OBJECT → 'object'."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "UNKNOWN_KIND", "name": "MyInput"}
    type_index = {"MyInput": {"kind": "INPUT_OBJECT", "name": "MyInput"}}
    assert extractor._map_type(type_ref, type_index) == "object"


def test_map_type_unknown_fallback_string() -> None:
    """Line 354: unknown kind not in type_index → 'string'."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "UNION", "name": "Foo"}
    assert extractor._map_type(type_ref, {}) == "string"


# ── _unwrap_named_type edge case ──────────────────────────────────────────


def test_unwrap_named_type_returns_last_dict_when_no_name() -> None:
    """Line 372: type chain with no name returns last dict traversed."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "NON_NULL", "ofType": {"kind": "LIST", "ofType": None}}
    result = extractor._unwrap_named_type(type_ref)
    assert result == {"kind": "LIST", "ofType": None}


# ── _parse_default_value edge cases ──────────────────────────────────────


def test_parse_default_non_string_returned_as_is() -> None:
    """Line 381: non-string default is returned directly."""
    extractor = GraphQLExtractor()
    assert extractor._parse_default_value(42) == 42
    assert extractor._parse_default_value(True) is True


def test_parse_default_invalid_json_strips_quotes() -> None:
    """Lines 384-386: string that isn't valid JSON is stripped of quotes."""
    extractor = GraphQLExtractor()
    assert extractor._parse_default_value('"hello world"') == "hello world"
    assert extractor._parse_default_value("ACTIVE") == "ACTIVE"


# ── _graphql_type_literal edge cases ─────────────────────────────────────


def test_graphql_type_literal_non_dict_returns_string() -> None:
    """Line 390: non-dict type_ref returns 'String'."""
    extractor = GraphQLExtractor()
    assert extractor._graphql_type_literal(None) == "String"
    assert extractor._graphql_type_literal("bogus") == "String"


def test_graphql_type_literal_list_kind() -> None:
    """Line 396: LIST kind wraps inner type in brackets."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "LIST", "ofType": {"kind": "SCALAR", "name": "Int"}}
    assert extractor._graphql_type_literal(type_ref) == "[Int]"


def test_graphql_type_literal_nested_oftype_fallback() -> None:
    """Lines 402-405: no name, has nested ofType → recurse."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "WRAPPER", "ofType": {"kind": "SCALAR", "name": "Boolean"}}
    assert extractor._graphql_type_literal(type_ref) == "Boolean"


def test_graphql_type_literal_no_name_no_oftype() -> None:
    """Lines 402-405 branch: no name, no ofType → 'String'."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "WRAPPER"}
    assert extractor._graphql_type_literal(type_ref) == "String"


# ── _selection_set_for_type edge cases ────────────────────────────────────


def test_selection_set_scalar_returns_none() -> None:
    """Line 420: SCALAR type returns None for selection set."""
    extractor = GraphQLExtractor()
    assert extractor._selection_set_for_type({"kind": "SCALAR", "name": "String"}, {}) is None


def test_selection_set_enum_returns_none() -> None:
    """Line 420: ENUM type returns None."""
    extractor = GraphQLExtractor()
    assert extractor._selection_set_for_type({"kind": "ENUM", "name": "Status"}, {}) is None


def test_selection_set_unreferenced_type_returns_none() -> None:
    """Line 424: named type not in type_index or not OBJECT/INTERFACE → None."""
    extractor = GraphQLExtractor()
    assert extractor._selection_set_for_type({"kind": "OBJECT", "name": "Unknown"}, {}) is None
    # Also INPUT_OBJECT is not OBJECT/INTERFACE
    assert (
        extractor._selection_set_for_type(
            {"kind": "INPUT_OBJECT", "name": "Foo"},
            {"Foo": {"kind": "INPUT_OBJECT", "fields": []}},
        )
        is None
    )


def test_selection_set_visited_type_returns_typename() -> None:
    """Line 428: already-visited type returns { __typename }."""
    extractor = GraphQLExtractor()
    type_index = {
        "Foo": {
            "kind": "OBJECT",
            "fields": [{"name": "bar", "type": {"kind": "SCALAR", "name": "String"}}],
        }
    }
    result = extractor._selection_set_for_type(
        {"kind": "OBJECT", "name": "Foo"}, type_index, visited={"Foo"}
    )
    assert result == "{ __typename }"


def test_selection_set_fields_not_a_list() -> None:
    """Line 433: fields not a list → { __typename }."""
    extractor = GraphQLExtractor()
    type_index = {"Foo": {"kind": "OBJECT", "fields": "not-a-list"}}
    result = extractor._selection_set_for_type({"kind": "OBJECT", "name": "Foo"}, type_index)
    assert result == "{ __typename }"


def test_selection_set_non_dict_child_field_skipped() -> None:
    """Line 438: non-dict child field is skipped."""
    extractor = GraphQLExtractor()
    type_index = {
        "Foo": {
            "kind": "OBJECT",
            "fields": [
                "not-a-dict",
                {"name": "valid", "type": {"kind": "SCALAR", "name": "String"}},
            ],
        }
    }
    result = extractor._selection_set_for_type({"kind": "OBJECT", "name": "Foo"}, type_index)
    assert result is not None
    assert "valid" in result


def test_selection_set_child_name_not_str_skipped() -> None:
    """Line 441: child field with non-string name is skipped."""
    extractor = GraphQLExtractor()
    type_index = {
        "Foo": {
            "kind": "OBJECT",
            "fields": [
                {"name": 123, "type": {"kind": "SCALAR", "name": "Int"}},
                {"name": "ok", "type": {"kind": "SCALAR", "name": "String"}},
            ],
        }
    }
    result = extractor._selection_set_for_type({"kind": "OBJECT", "name": "Foo"}, type_index)
    assert "ok" in result


def test_selection_set_depth_limit_skips_nested_objects() -> None:
    """Lines 446-456: depth >= max_depth skips nested object fields."""
    extractor = GraphQLExtractor()
    type_index = {
        "Root": {
            "kind": "OBJECT",
            "fields": [
                {"name": "nested", "type": {"kind": "OBJECT", "name": "Child"}},
                {"name": "leaf", "type": {"kind": "SCALAR", "name": "String"}},
            ],
        },
        "Child": {
            "kind": "OBJECT",
            "fields": [
                {"name": "deep", "type": {"kind": "SCALAR", "name": "Int"}},
            ],
        },
    }
    result = extractor._selection_set_for_type(
        {"kind": "OBJECT", "name": "Root"}, type_index, depth=0, max_depth=1
    )
    assert "nested" in result
    assert "deep" in result

    # At max_depth, nested objects should be skipped
    result_at_max = extractor._selection_set_for_type(
        {"kind": "OBJECT", "name": "Root"}, type_index, depth=2, max_depth=2
    )
    assert "leaf" in result_at_max
    assert "nested" not in result_at_max


def test_selection_set_no_leaf_selections_returns_typename() -> None:
    """Line 458-459: all children are nested objects beyond max_depth → { __typename }."""
    extractor = GraphQLExtractor()
    type_index = {
        "Root": {
            "kind": "OBJECT",
            "fields": [
                {"name": "nested", "type": {"kind": "OBJECT", "name": "Child"}},
            ],
        },
        "Child": {
            "kind": "OBJECT",
            "fields": [
                {"name": "val", "type": {"kind": "SCALAR", "name": "Int"}},
            ],
        },
    }
    result = extractor._selection_set_for_type(
        {"kind": "OBJECT", "name": "Root"}, type_index, depth=2, max_depth=2
    )
    assert result == "{ __typename }"


# ── _is_leaf_output_type ──────────────────────────────────────────────────


def test_is_leaf_output_type_referenced_enum() -> None:
    """Lines 471-475: type with non-ENUM kind but referencing ENUM in type_index."""
    extractor = GraphQLExtractor()
    type_index = {"StatusEnum": {"kind": "ENUM", "name": "StatusEnum"}}
    # Named type that doesn't have SCALAR/ENUM kind itself but is ENUM in index
    type_ref = {"kind": "UNKNOWN", "name": "StatusEnum"}
    assert extractor._is_leaf_output_type(type_ref, type_index) is True


def test_is_leaf_output_type_non_leaf_returns_false() -> None:
    """Lines 471-475: type that is not leaf and not enum in index."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "OBJECT", "name": "SomeObj"}
    type_index = {"SomeObj": {"kind": "OBJECT"}}
    assert extractor._is_leaf_output_type(type_ref, type_index) is False


def test_is_leaf_output_type_no_name() -> None:
    """Line 475: named type with no string name → False."""
    extractor = GraphQLExtractor()
    type_ref = {"kind": "UNKNOWN"}
    assert extractor._is_leaf_output_type(type_ref, {}) is False


# ── _derive_service_name ──────────────────────────────────────────────────


def test_derive_service_name_from_hints() -> None:
    """Line 480: service name from hints."""
    extractor = GraphQLExtractor()
    source = SourceConfig(
        file_content=json.dumps({"__schema": {"types": []}}),
        hints={"service_name": "My Cool API"},
    )
    assert extractor._derive_service_name(source) == "my-cool-api"


def test_derive_service_name_from_url() -> None:
    """Lines 484-486: service name from URL hostname."""
    extractor = GraphQLExtractor()
    source = SourceConfig(url="https://api.example.com:8080/graphql")
    assert extractor._derive_service_name(source) == "api-example-com"


def test_derive_service_name_fallback() -> None:
    """Line 487: no hints, no file_path, no url → default name."""
    extractor = GraphQLExtractor()
    # Need at least one of url/file_path/file_content for SourceConfig
    source = SourceConfig(file_content="{}")
    assert extractor._derive_service_name(source) == "graphql-service"


# ── _derive_auth ──────────────────────────────────────────────────────────


def test_derive_auth_with_auth_header() -> None:
    """Line 491: auth_header → bearer auth config."""
    extractor = GraphQLExtractor()
    source = SourceConfig(file_content="{}", auth_header="Bearer tok123")
    auth = extractor._derive_auth(source)
    assert auth.type is AuthType.bearer


def test_derive_auth_with_auth_token() -> None:
    """Line 497: auth_token → bearer auth config."""
    extractor = GraphQLExtractor()
    source = SourceConfig(file_content="{}", auth_token="tok123")
    auth = extractor._derive_auth(source)
    assert auth.type is AuthType.bearer


# ── _auth_headers ─────────────────────────────────────────────────────────


def test_auth_headers_with_auth_header() -> None:
    """Lines 505-506: auth_header passed through."""
    extractor = GraphQLExtractor()
    source = SourceConfig(file_content="{}", auth_header="Bearer abc")
    assert extractor._auth_headers(source) == {"Authorization": "Bearer abc"}


def test_auth_headers_with_auth_token() -> None:
    """Lines 507-508: auth_token formatted as Bearer."""
    extractor = GraphQLExtractor()
    source = SourceConfig(file_content="{}", auth_token="xyz")
    assert extractor._auth_headers(source) == {"Authorization": "Bearer xyz"}


def test_auth_headers_no_auth() -> None:
    """Line 509: no auth → empty dict."""
    extractor = GraphQLExtractor()
    source = SourceConfig(file_content="{}")
    assert extractor._auth_headers(source) == {}


# ── _graphql_base_url ─────────────────────────────────────────────────────


def test_graphql_base_url_no_url_returns_localhost() -> None:
    """Line 534: no URL → http://localhost."""
    extractor = GraphQLExtractor()
    source = SourceConfig(file_content="{}")
    assert extractor._graphql_base_url(source) == "http://localhost"


# ── _subscription_field_names edge cases ──────────────────────────────────


def test_subscription_type_not_in_type_index() -> None:
    """Line 546: subscription type name exists but not in type_index."""
    extractor = GraphQLExtractor()
    schema = {"subscriptionType": {"name": "Sub"}}
    assert extractor._subscription_field_names(schema, {}) == []


def test_subscription_type_fields_not_a_list() -> None:
    """Line 549: subscription type fields is not a list."""
    extractor = GraphQLExtractor()
    schema = {"subscriptionType": {"name": "Sub"}}
    type_index = {"Sub": {"kind": "OBJECT", "fields": "not-a-list"}}
    assert extractor._subscription_field_names(schema, type_index) == []
