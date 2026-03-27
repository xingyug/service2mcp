"""Tests for the GraphQL extractor."""

from __future__ import annotations

from pathlib import Path

from libs.extractors.base import SourceConfig
from libs.extractors.graphql import GraphQLExtractor
from libs.ir.models import (
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
