"""Tests for the SQL extractor."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql.sqltypes import (
    ARRAY,
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    Numeric,
    Time,
)
from sqlalchemy.sql.sqltypes import String as SQLString
from sqlalchemy.sql.sqltypes import Uuid as SQLUuid
from testcontainers.postgres import PostgresContainer

from libs.extractors.base import SourceConfig
from libs.extractors.sql import SQLExtractor, _run_coroutine, _slugify
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


def test_sql_query_operations_have_error_schema(initialized_database: str) -> None:
    extractor = SQLExtractor()

    service_ir = extractor.extract(SourceConfig(url=initialized_database))

    query_ops = [op for op in service_ir.operations if op.id.startswith("query_")]
    assert len(query_ops) >= 1
    expected_codes = {"SYNTAX_ERROR", "TIMEOUT"}
    for op in query_ops:
        assert op.error_schema is not None
        assert len(op.error_schema.responses) == 2
        actual_codes = {r.error_code for r in op.error_schema.responses}
        assert actual_codes == expected_codes


def test_sql_insert_operations_have_error_schema(initialized_database: str) -> None:
    extractor = SQLExtractor()

    service_ir = extractor.extract(SourceConfig(url=initialized_database))

    insert_ops = [op for op in service_ir.operations if op.id.startswith("insert_")]
    assert len(insert_ops) >= 1
    expected_codes = {"CONSTRAINT_VIOLATION", "SYNTAX_ERROR", "TIMEOUT"}
    for op in insert_ops:
        assert op.error_schema is not None
        assert len(op.error_schema.responses) == 3
        actual_codes = {r.error_code for r in op.error_schema.responses}
        assert actual_codes == expected_codes


# ── detect edge cases ──────────────────────────────────────────────────────


def test_detect_returns_zero_when_no_database_url() -> None:
    """Line 96: _resolve_database_url returns None → 0.0."""
    extractor = SQLExtractor()
    source = SourceConfig(file_content="just some text without url scheme")
    assert extractor.detect(source) == 0.0


def test_detect_with_protocol_hint() -> None:
    """Line 92: protocol hint 'sql' → 1.0."""
    extractor = SQLExtractor()
    source = SourceConfig(file_content="anything", hints={"protocol": "sql"})
    assert extractor.detect(source) == 1.0


def test_detect_non_database_scheme() -> None:
    """Line 102: URL with non-database scheme → 0.0."""
    extractor = SQLExtractor()
    source = SourceConfig(url="https://example.com/not-a-database")
    assert extractor.detect(source) == 0.0


# ── extract error case ────────────────────────────────────────────────────


def test_extract_raises_when_no_database_url() -> None:
    """Line 107: extract raises ValueError when _resolve_database_url is None."""
    extractor = SQLExtractor()
    source = SourceConfig(file_content="not a url")
    with pytest.raises(ValueError, match="requires a database URL"):
        extractor.extract(source)


# ── _map_column_type edge cases ───────────────────────────────────────────


class TestMapColumnType:
    def setup_method(self) -> None:
        self.extractor = SQLExtractor()

    def test_array_type(self) -> None:
        """Line 383: ARRAY → 'array'."""
        assert self.extractor._map_column_type(ARRAY(Integer)) == "array"

    def test_boolean_type(self) -> None:
        """Line 385: Boolean → 'boolean'."""
        assert self.extractor._map_column_type(Boolean()) == "boolean"

    def test_integer_type(self) -> None:
        assert self.extractor._map_column_type(Integer()) == "integer"

    def test_numeric_type(self) -> None:
        """Line 389: Numeric → 'number'."""
        assert self.extractor._map_column_type(Numeric()) == "number"

    def test_float_type(self) -> None:
        assert self.extractor._map_column_type(Float()) == "number"

    def test_json_type(self) -> None:
        """Line 391: JSON → 'object'."""
        assert self.extractor._map_column_type(JSON()) == "object"

    def test_date_type(self) -> None:
        assert self.extractor._map_column_type(Date()) == "string"

    def test_datetime_type(self) -> None:
        assert self.extractor._map_column_type(DateTime()) == "string"

    def test_time_type(self) -> None:
        assert self.extractor._map_column_type(Time()) == "string"

    def test_uuid_type(self) -> None:
        assert self.extractor._map_column_type(SQLUuid()) == "string"

    def test_string_type(self) -> None:
        assert self.extractor._map_column_type(SQLString()) == "string"

    def test_python_type_bool_fallback(self) -> None:
        """Lines 397-399: python_type fallback for bool."""
        mock_type = MagicMock()
        type(mock_type).python_type = PropertyMock(return_value=bool)
        mock_type.__class__ = type("CustomType", (), {})
        assert self.extractor._map_column_type(mock_type) == "boolean"

    def test_python_type_int_fallback(self) -> None:
        """Lines 400-401: python_type fallback for int."""
        mock_type = MagicMock()
        type(mock_type).python_type = PropertyMock(return_value=int)
        mock_type.__class__ = type("CustomType", (), {})
        assert self.extractor._map_column_type(mock_type) == "integer"

    def test_python_type_float_fallback(self) -> None:
        """Lines 402-403: python_type fallback for float."""
        mock_type = MagicMock()
        type(mock_type).python_type = PropertyMock(return_value=float)
        mock_type.__class__ = type("CustomType", (), {})
        assert self.extractor._map_column_type(mock_type) == "number"

    def test_python_type_dict_fallback(self) -> None:
        """Lines 404-405: python_type dict → 'object'."""
        mock_type = MagicMock()
        type(mock_type).python_type = PropertyMock(return_value=dict)
        mock_type.__class__ = type("CustomType", (), {})
        assert self.extractor._map_column_type(mock_type) == "object"

    def test_python_type_list_fallback(self) -> None:
        """Lines 404-405: python_type list → 'array'."""
        mock_type = MagicMock()
        type(mock_type).python_type = PropertyMock(return_value=list)
        mock_type.__class__ = type("CustomType", (), {})
        assert self.extractor._map_column_type(mock_type) == "array"

    def test_unknown_type_defaults_to_string(self) -> None:
        """Line 406: unknown type with no python_type → 'string'."""
        mock_type = MagicMock()
        type(mock_type).python_type = PropertyMock(return_value=bytes)
        mock_type.__class__ = type("CustomType", (), {})
        assert self.extractor._map_column_type(mock_type) == "string"

    def test_large_binary_defaults_to_string(self) -> None:
        """Line 406: LargeBinary has python_type=bytes → 'string'."""
        assert self.extractor._map_column_type(LargeBinary()) == "string"


# ── _resolve_database_url ─────────────────────────────────────────────────


class TestResolveDatabaseUrl:
    def setup_method(self) -> None:
        self.extractor = SQLExtractor()

    def test_from_source_url(self) -> None:
        source = SourceConfig(url="postgresql://localhost/mydb")
        assert self.extractor._resolve_database_url(source) == "postgresql://localhost/mydb"

    def test_from_file_content_with_scheme(self) -> None:
        """Lines 411-412: file_content containing :// is used as URL."""
        source = SourceConfig(file_content="  postgresql://localhost/mydb  ")
        assert self.extractor._resolve_database_url(source) == "postgresql://localhost/mydb"

    def test_returns_none_when_no_url(self) -> None:
        """Line 413: no url, no :// in file_content → None."""
        source = SourceConfig(file_content="just text")
        assert self.extractor._resolve_database_url(source) is None


# ── _derive_service_name ──────────────────────────────────────────────────


class TestDeriveServiceName:
    def setup_method(self) -> None:
        self.extractor = SQLExtractor()

    def test_from_hints(self) -> None:
        """Line 417: service_name from hints."""
        source = SourceConfig(url="postgresql://localhost/mydb", hints={"service_name": "My DB"})
        assert self.extractor._derive_service_name(source, "postgresql://localhost/mydb") == "my-db"

    def test_from_database_path(self) -> None:
        source = SourceConfig(url="postgresql://localhost/orders_db")
        assert "orders" in self.extractor._derive_service_name(
            source, "postgresql://localhost/orders_db"
        )


# ── _to_async_url conversions ─────────────────────────────────────────────


class TestToAsyncUrl:
    def setup_method(self) -> None:
        self.extractor = SQLExtractor()

    def test_already_asyncpg(self) -> None:
        """Line 451: already async → unchanged."""
        url = "postgresql+asyncpg://localhost/db"
        assert self.extractor._to_async_url(url) == url

    def test_psycopg2_to_asyncpg(self) -> None:
        """Line 454: psycopg2 → asyncpg."""
        url = "postgresql+psycopg2://localhost/db"
        assert self.extractor._to_async_url(url) == "postgresql+asyncpg://localhost/db"

    def test_postgresql_to_asyncpg(self) -> None:
        """Line 455: plain postgresql → asyncpg."""
        url = "postgresql://localhost/db"
        assert self.extractor._to_async_url(url) == "postgresql+asyncpg://localhost/db"

    def test_postgres_to_asyncpg(self) -> None:
        """Line 457: postgres:// → asyncpg."""
        url = "postgres://localhost/db"
        assert self.extractor._to_async_url(url) == "postgresql+asyncpg://localhost/db"

    def test_already_aiosqlite(self) -> None:
        """Line 459: already aiosqlite → unchanged."""
        url = "sqlite+aiosqlite:///test.db"
        assert self.extractor._to_async_url(url) == url

    def test_sqlite_to_aiosqlite(self) -> None:
        """Line 461: sqlite → aiosqlite."""
        url = "sqlite:///test.db"
        assert self.extractor._to_async_url(url) == "sqlite+aiosqlite:///test.db"

    def test_unsupported_scheme_raises(self) -> None:
        """Line 462: unsupported scheme → ValueError."""
        with pytest.raises(ValueError, match="Unsupported SQL database URL"):
            self.extractor._to_async_url("mysql://localhost/db")


# ── _slugify ──────────────────────────────────────────────────────────────


class TestSlugify:
    def test_basic_slugify(self) -> None:
        assert _slugify("Hello World") == "hello-world"

    def test_consecutive_special_chars(self) -> None:
        """Lines 496-499: consecutive non-alnum chars collapse to single dash."""
        assert _slugify("foo---bar") == "foo-bar"
        assert _slugify("   ") == "sql-service"

    def test_empty_string_returns_default(self) -> None:
        """Line 500: empty string → 'sql-service'."""
        assert _slugify("") == "sql-service"
        assert _slugify("---") == "sql-service"


# ── _reflect_database error handling ──────────────────────────────────────


@pytest.mark.asyncio
async def test_reflect_database_sqlalchemy_error() -> None:
    """Lines 144-145: SQLAlchemyError during reflection wraps in ValueError."""
    extractor = SQLExtractor()
    with patch("libs.extractors.sql.create_async_engine") as mock_engine_factory:
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.run_sync.side_effect = SQLAlchemyError("connection refused")

        # engine.connect() returns an async context manager
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = False
        mock_engine.connect.return_value = mock_cm
        mock_engine.dispose = AsyncMock()
        mock_engine_factory.return_value = mock_engine

        with pytest.raises(ValueError, match="Failed to inspect database schema"):
            await extractor._reflect_database("postgresql+asyncpg://localhost/db", "")


# ── _run_coroutine with existing event loop ───────────────────────────────


@pytest.mark.asyncio
async def test_run_coroutine_within_running_loop() -> None:
    """Lines 471-485: _run_coroutine delegates to a thread when a loop is running."""

    async def sample_coro() -> str:
        return "hello from thread"

    result = _run_coroutine(sample_coro())
    assert result == "hello from thread"


@pytest.mark.asyncio
async def test_run_coroutine_within_running_loop_error() -> None:
    """Lines 483-484: error in threaded coroutine is re-raised."""

    async def failing_coro() -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _run_coroutine(failing_coro())
