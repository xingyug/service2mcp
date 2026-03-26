"""Tests for the SQL extractor."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer

from libs.extractors.base import SourceConfig
from libs.extractors.sql import SQLExtractor
from libs.ir.models import RiskLevel

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures"
SQL_SCHEMA_PATH = FIXTURES_DIR / "sql_schemas" / "catalog.sql"


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(scope="module")
async def initialized_database(postgres_container: PostgresContainer) -> AsyncIterator[str]:
    engine: AsyncEngine = create_async_engine(
        _to_asyncpg_url(postgres_container.get_connection_url())
    )

    async with engine.begin() as connection:
        sql_statements = SQL_SCHEMA_PATH.read_text(encoding="utf-8")
        for statement in sql_statements.split(";\n"):
            candidate = statement.strip()
            if candidate:
                await connection.execute(text(candidate))

    try:
        yield postgres_container.get_connection_url()
    finally:
        await engine.dispose()


def test_detects_postgres_connection_url(initialized_database: str) -> None:
    extractor = SQLExtractor()

    confidence = extractor.detect(SourceConfig(url=initialized_database))

    assert confidence >= 0.9


def test_extracts_tables_foreign_keys_and_views(initialized_database: str) -> None:
    extractor = SQLExtractor()

    service_ir = extractor.extract(SourceConfig(url=initialized_database))

    assert service_ir.protocol == "sql"
    assert service_ir.metadata["tables"] == ["customers", "orders"]
    assert service_ir.metadata["views"] == ["order_summaries"]
    assert len(service_ir.operations) == 5

    query_orders = next(
        operation for operation in service_ir.operations if operation.id == "query_orders"
    )
    assert query_orders.risk.risk_level is RiskLevel.safe
    assert query_orders.method == "GET"
    assert query_orders.sql is not None
    assert query_orders.sql.action.value == "query"
    assert query_orders.sql.relation_name == "orders"
    query_order_params = {param.name: param for param in query_orders.params}
    assert query_order_params["customer_id"].type == "integer"
    assert "customers.id" in query_order_params["customer_id"].description
    assert query_order_params["limit"].default == 50

    insert_orders = next(
        operation for operation in service_ir.operations if operation.id == "insert_orders"
    )
    assert insert_orders.risk.risk_level is RiskLevel.cautious
    assert insert_orders.method == "POST"
    assert insert_orders.sql is not None
    assert insert_orders.sql.action.value == "insert"
    assert insert_orders.sql.insertable_columns == [
        "customer_id",
        "total_cents",
        "notes",
        "created_at",
    ]
    insert_param_required = {param.name: param.required for param in insert_orders.params}
    assert insert_param_required["customer_id"] is True
    assert insert_param_required["total_cents"] is True
    assert insert_param_required["notes"] is False
    assert "id" not in insert_param_required

    query_view = next(
        operation for operation in service_ir.operations if operation.id == "query_order_summaries"
    )
    assert query_view.risk.risk_level is RiskLevel.safe
    assert all(param.required is False for param in query_view.params)
    assert not any(operation.id == "insert_order_summaries" for operation in service_ir.operations)
