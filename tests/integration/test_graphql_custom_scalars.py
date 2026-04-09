"""Integration tests for GraphQL custom scalar type mapping."""

from __future__ import annotations

import json
from typing import Any

import pytest

from libs.extractors.base import SourceConfig
from libs.extractors.graphql import _SCALAR_TYPE_MAP, GraphQLExtractor, _infer_scalar_type


def _make_introspection(
    query_fields: list[dict[str, Any]],
    mutation_fields: list[dict[str, Any]] | None = None,
    extra_types: list[dict[str, Any]] | None = None,
) -> str:
    """Build a minimal introspection JSON with given query/mutation fields."""
    types: list[dict[str, Any]] = [
        {"kind": "OBJECT", "name": "Query", "fields": query_fields},
    ]
    if extra_types:
        types.extend(extra_types)
    schema: dict[str, Any] = {
        "__schema": {
            "queryType": {"name": "Query"},
            "types": types,
        },
    }
    if mutation_fields is not None:
        types.append({"kind": "OBJECT", "name": "Mutation", "fields": mutation_fields})
        schema["__schema"]["mutationType"] = {"name": "Mutation"}
    return json.dumps(schema)


def _scalar_arg(name: str, scalar_name: str, *, required: bool = False) -> dict[str, Any]:
    """Build an introspection argument referencing a SCALAR type."""
    type_ref: dict[str, Any] = {"kind": "SCALAR", "name": scalar_name, "ofType": None}
    if required:
        type_ref = {"kind": "NON_NULL", "name": None, "ofType": type_ref}
    return {"name": name, "type": type_ref, "description": "", "defaultValue": None}


def _simple_query(name: str, args: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a simple query field returning a String."""
    return {
        "name": name,
        "description": "",
        "args": args,
        "type": {"kind": "SCALAR", "name": "String", "ofType": None},
    }


class TestStandardScalarsMapped:
    """Standard GraphQL scalars are correctly mapped in _SCALAR_TYPE_MAP."""

    @pytest.mark.parametrize(
        ("scalar", "expected"),
        [
            ("Int", "integer"),
            ("Float", "number"),
            ("Boolean", "boolean"),
            ("ID", "string"),
            ("String", "string"),
        ],
    )
    def test_standard_scalars_mapped(self, scalar: str, expected: str) -> None:
        assert _SCALAR_TYPE_MAP[scalar] == expected

    @pytest.mark.parametrize(
        ("scalar", "expected"),
        [
            ("Int", "integer"),
            ("Float", "number"),
            ("Boolean", "boolean"),
            ("ID", "string"),
            ("String", "string"),
        ],
    )
    def test_standard_scalars_via_extraction(self, scalar: str, expected: str) -> None:
        extractor = GraphQLExtractor()
        content = _make_introspection(
            query_fields=[_simple_query("myField", [_scalar_arg("arg1", scalar)])]
        )
        ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
        param = ir.operations[0].params[0]
        assert param.type == expected


class TestKnownCustomScalarsMapped:
    """Well-known custom scalars map to the correct IR types."""

    @pytest.mark.parametrize(
        ("scalar", "expected"),
        [
            ("DateTime", "string"),
            ("Date", "string"),
            ("Time", "string"),
            ("Timestamp", "string"),
            ("JSON", "object"),
            ("JSONObject", "object"),
            ("JSONString", "string"),
            ("Upload", "string"),
            ("File", "string"),
            ("Email", "string"),
            ("URL", "string"),
            ("URI", "string"),
            ("UUID", "string"),
            ("GUID", "string"),
            ("BigInt", "integer"),
            ("Long", "integer"),
            ("Decimal", "number"),
            ("BigDecimal", "number"),
            ("Byte", "integer"),
            ("Short", "integer"),
            ("PositiveInt", "integer"),
            ("NegativeInt", "integer"),
            ("NonNegativeInt", "integer"),
            ("NonPositiveInt", "integer"),
            ("PositiveFloat", "number"),
            ("NegativeFloat", "number"),
            ("NonNegativeFloat", "number"),
            ("NonPositiveFloat", "number"),
            ("Void", "string"),
        ],
    )
    def test_known_custom_scalars_mapped(self, scalar: str, expected: str) -> None:
        assert _SCALAR_TYPE_MAP[scalar] == expected

    @pytest.mark.parametrize(
        ("scalar", "expected"),
        [
            ("DateTime", "string"),
            ("JSON", "object"),
            ("BigInt", "integer"),
            ("Upload", "string"),
            ("Decimal", "number"),
        ],
    )
    def test_known_custom_scalars_via_extraction(self, scalar: str, expected: str) -> None:
        extractor = GraphQLExtractor()
        content = _make_introspection(
            query_fields=[_simple_query("myField", [_scalar_arg("arg1", scalar)])]
        )
        ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
        param = ir.operations[0].params[0]
        assert param.type == expected


class TestUnknownScalarInferredByName:
    """Unknown scalars are inferred from naming conventions via _infer_scalar_type."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("ProductCount", "integer"),
            ("PageSize", "integer"),
            ("PortNumber", "integer"),
            ("TotalPrice", "number"),
            ("ExchangeRate", "number"),
            ("DiscountPercent", "number"),
            ("ConfigMap", "object"),
            ("MetadataJson", "object"),
            ("SettingsDict", "object"),
            ("IsActiveFlag", "boolean"),
            ("EnabledBool", "boolean"),
        ],
    )
    def test_unknown_scalar_inferred_by_name(self, name: str, expected: str) -> None:
        assert _infer_scalar_type(name) == expected

    @pytest.mark.parametrize(
        ("scalar", "expected"),
        [
            ("ProductCount", "integer"),
            ("TotalPrice", "number"),
            ("ConfigMap", "object"),
        ],
    )
    def test_unknown_scalar_inferred_via_extraction(self, scalar: str, expected: str) -> None:
        extractor = GraphQLExtractor()
        content = _make_introspection(
            query_fields=[_simple_query("myField", [_scalar_arg("arg1", scalar)])]
        )
        ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
        param = ir.operations[0].params[0]
        assert param.type == expected


class TestUnknownScalarDefaultsToString:
    """Completely unknown scalars with no heuristic match default to string."""

    @pytest.mark.parametrize("name", ["FooBar", "Quux", "Widget", "Bleep"])
    def test_unknown_scalar_defaults_to_string(self, name: str) -> None:
        assert _infer_scalar_type(name) == "string"

    def test_unknown_scalar_defaults_to_string_via_extraction(self) -> None:
        extractor = GraphQLExtractor()
        content = _make_introspection(
            query_fields=[_simple_query("myField", [_scalar_arg("arg1", "FooBar")])]
        )
        ir = extractor.extract(SourceConfig(file_content=content, url="https://x.com/graphql"))
        param = ir.operations[0].params[0]
        assert param.type == "string"


class TestFullExtractionWithCustomScalars:
    """Full introspection response with custom scalar fields yields correct IR types."""

    def test_full_extraction_with_custom_scalars(self) -> None:
        extractor = GraphQLExtractor()
        content = _make_introspection(
            query_fields=[
                {
                    "name": "getEvent",
                    "description": "Fetch a calendar event",
                    "args": [
                        _scalar_arg("id", "UUID", required=True),
                        _scalar_arg("after", "DateTime"),
                        _scalar_arg("limit", "PositiveInt"),
                    ],
                    "type": {"kind": "SCALAR", "name": "String", "ofType": None},
                },
            ],
            mutation_fields=[
                {
                    "name": "uploadFile",
                    "description": "Upload a document",
                    "args": [
                        _scalar_arg("file", "Upload", required=True),
                        _scalar_arg("metadata", "JSON"),
                        _scalar_arg("tags", "JSONString"),
                    ],
                    "type": {"kind": "SCALAR", "name": "String", "ofType": None},
                },
            ],
        )
        ir = extractor.extract(
            SourceConfig(file_content=content, url="https://api.example.com/graphql")
        )

        assert len(ir.operations) == 2

        get_event = next(op for op in ir.operations if op.id == "getEvent")
        param_types = {p.name: p.type for p in get_event.params}
        assert param_types == {
            "id": "string",  # UUID → string
            "after": "string",  # DateTime → string
            "limit": "integer",  # PositiveInt → integer
        }

        upload_file = next(op for op in ir.operations if op.id == "uploadFile")
        param_types = {p.name: p.type for p in upload_file.params}
        assert param_types == {
            "file": "string",  # Upload → string
            "metadata": "object",  # JSON → object
            "tags": "string",  # JSONString → string
        }
