"""Shared fixtures for performance tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

OPENAPI_FIXTURE = FIXTURES_DIR / "openapi_specs" / "large_surface_api.yaml"
GRAPHQL_FIXTURE = FIXTURES_DIR / "graphql_schemas" / "catalog_introspection.json"
GRPC_FIXTURE = FIXTURES_DIR / "grpc_protos" / "inventory.proto"
WSDL_FIXTURE = FIXTURES_DIR / "wsdl" / "order_service.wsdl"


@pytest.fixture()
def openapi_fixture_path() -> Path:
    if not OPENAPI_FIXTURE.exists():
        pytest.skip(f"Fixture missing: {OPENAPI_FIXTURE}")
    return OPENAPI_FIXTURE


@pytest.fixture()
def graphql_fixture_path() -> Path:
    if not GRAPHQL_FIXTURE.exists():
        pytest.skip(f"Fixture missing: {GRAPHQL_FIXTURE}")
    return GRAPHQL_FIXTURE


@pytest.fixture()
def grpc_fixture_path() -> Path:
    if not GRPC_FIXTURE.exists():
        pytest.skip(f"Fixture missing: {GRPC_FIXTURE}")
    return GRPC_FIXTURE


@pytest.fixture()
def wsdl_fixture_path() -> Path:
    if not WSDL_FIXTURE.exists():
        pytest.skip(f"Fixture missing: {WSDL_FIXTURE}")
    return WSDL_FIXTURE
