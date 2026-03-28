"""Unit tests for apps/mcp_runtime/sql.py helper functions and executor."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.sql import (
    _json_safe_row,
    _json_safe_value,
    _resolve_limit,
    _to_async_database_url,
)


class TestToAsyncDatabaseUrl:
    def test_postgresql_asyncpg_passthrough(self) -> None:
        url = "postgresql+asyncpg://user:pass@host/db"
        assert _to_async_database_url(url) == url

    def test_postgresql_psycopg2_converted(self) -> None:
        url = "postgresql+psycopg2://user:pass@host/db"
        assert _to_async_database_url(url) == "postgresql+asyncpg://user:pass@host/db"

    def test_postgresql_plain_converted(self) -> None:
        url = "postgresql://user:pass@host/db"
        assert _to_async_database_url(url) == "postgresql+asyncpg://user:pass@host/db"

    def test_postgres_plain_converted(self) -> None:
        url = "postgres://user:pass@host/db"
        assert _to_async_database_url(url) == "postgresql+asyncpg://user:pass@host/db"

    def test_sqlite_aiosqlite_passthrough(self) -> None:
        url = "sqlite+aiosqlite:///test.db"
        assert _to_async_database_url(url) == url

    def test_sqlite_plain_converted(self) -> None:
        url = "sqlite:///test.db"
        assert _to_async_database_url(url) == "sqlite+aiosqlite:///test.db"

    def test_unsupported_raises(self) -> None:
        with pytest.raises(ToolError, match="Unsupported SQL database URL"):
            _to_async_database_url("mysql://user:pass@host/db")

    def test_empty_raises(self) -> None:
        with pytest.raises(ToolError, match="Unsupported SQL database URL"):
            _to_async_database_url("")


class TestResolveLimit:
    def test_none_returns_default(self) -> None:
        assert _resolve_limit(None, default_limit=50, max_limit=100, operation_id="op1") == 50

    def test_valid_int(self) -> None:
        assert _resolve_limit(25, default_limit=50, max_limit=100, operation_id="op1") == 25

    def test_string_int_coerced(self) -> None:
        assert _resolve_limit("10", default_limit=50, max_limit=100, operation_id="op1") == 10

    def test_clamped_to_max(self) -> None:
        assert _resolve_limit(200, default_limit=50, max_limit=100, operation_id="op1") == 100

    def test_zero_raises(self) -> None:
        with pytest.raises(ToolError, match="requires limit > 0"):
            _resolve_limit(0, default_limit=50, max_limit=100, operation_id="op1")

    def test_negative_raises(self) -> None:
        with pytest.raises(ToolError, match="requires limit > 0"):
            _resolve_limit(-5, default_limit=50, max_limit=100, operation_id="op1")

    def test_bool_raises(self) -> None:
        with pytest.raises(ToolError, match="requires an integer limit"):
            _resolve_limit(True, default_limit=50, max_limit=100, operation_id="op1")

    def test_non_numeric_string_raises(self) -> None:
        with pytest.raises(ToolError, match="requires an integer limit"):
            _resolve_limit("abc", default_limit=50, max_limit=100, operation_id="op1")

    def test_exact_max(self) -> None:
        assert _resolve_limit(100, default_limit=50, max_limit=100, operation_id="op1") == 100


class TestJsonSafeValue:
    def test_string_passthrough(self) -> None:
        assert _json_safe_value("hello") == "hello"

    def test_int_passthrough(self) -> None:
        assert _json_safe_value(42) == 42

    def test_none_passthrough(self) -> None:
        assert _json_safe_value(None) is None

    def test_decimal_to_float(self) -> None:
        assert _json_safe_value(Decimal("3.14")) == pytest.approx(3.14)

    def test_datetime_to_isoformat(self) -> None:
        dt = datetime(2026, 1, 15, 10, 30, 0)
        assert _json_safe_value(dt) == "2026-01-15T10:30:00"

    def test_date_to_isoformat(self) -> None:
        d = date(2026, 1, 15)
        assert _json_safe_value(d) == "2026-01-15"

    def test_time_to_isoformat(self) -> None:
        t = time(10, 30, 0)
        assert _json_safe_value(t) == "10:30:00"

    def test_uuid_to_string(self) -> None:
        u = UUID("12345678-1234-5678-1234-567812345678")
        assert _json_safe_value(u) == "12345678-1234-5678-1234-567812345678"

    def test_nested_dict(self) -> None:
        result = _json_safe_value({"a": Decimal("1.5"), "b": {"c": 2}})
        assert result == {"a": 1.5, "b": {"c": 2}}

    def test_list_values(self) -> None:
        result = _json_safe_value([Decimal("1.1"), "text", 3])
        assert result == [pytest.approx(1.1), "text", 3]

    def test_tuple_becomes_list(self) -> None:
        result = _json_safe_value((1, 2, 3))
        assert result == [1, 2, 3]


class TestJsonSafeRow:
    def test_converts_all_values(self) -> None:
        row = {
            "id": UUID("12345678-1234-5678-1234-567812345678"),
            "price": Decimal("9.99"),
            "name": "test",
            "created_at": datetime(2026, 1, 15),
        }
        result = _json_safe_row(row)
        assert result == {
            "id": "12345678-1234-5678-1234-567812345678",
            "price": pytest.approx(9.99),
            "name": "test",
            "created_at": "2026-01-15T00:00:00",
        }

    def test_empty_row(self) -> None:
        assert _json_safe_row({}) == {}


# Test coverage for SQLRuntimeExecutor - focus on specific uncovered lines
from unittest.mock import AsyncMock, MagicMock, patch
from apps.mcp_runtime.sql import SQLRuntimeExecutor
from libs.ir.models import ServiceIR, Operation, SqlOperationConfig, SqlOperationType, SqlRelationKind


class TestSQLRuntimeExecutorCoverage:
    """Tests to cover specific uncovered lines in SQLRuntimeExecutor."""

    async def test_aclose_with_owned_engine(self) -> None:
        """Test line 33-34: aclose disposes owned engine."""
        ir = ServiceIR(
            service_id="test",
            service_name="Test",
            base_url="sqlite:///test.db",
            source_hash="sha256:test",
            protocol="sql",
            operations=[],
        )
        # Create without providing engine - should own it
        executor = SQLRuntimeExecutor(ir)
        executor._engine = AsyncMock()
        
        await executor.aclose()
        executor._engine.dispose.assert_called_once()

    async def test_aclose_with_unowned_engine(self) -> None:
        """Test that unowned engine is not disposed."""
        ir = ServiceIR(
            service_id="test",
            service_name="Test",
            base_url="sqlite:///test.db",
            source_hash="sha256:test",
            protocol="sql",
            operations=[],
        )
        mock_engine = AsyncMock()
        executor = SQLRuntimeExecutor(ir, engine=mock_engine)
        
        await executor.aclose()
        mock_engine.dispose.assert_not_called()

    async def test_invoke_unsupported_operation(self) -> None:
        """Test line 48: Unsupported SQL runtime action."""
        ir = ServiceIR(
            service_id="test",
            service_name="Test",
            base_url="sqlite:///test.db",
            source_hash="sha256:test",
            protocol="sql",
            operations=[],
        )
        executor = SQLRuntimeExecutor(ir)
        executor._get_table = AsyncMock(return_value=MagicMock())
        
        op = Operation(
            id="op1",
            name="test",
            method="sql",
            path="/test",
            description="test",
            enabled=True,
        )
        
        # Mock config with unsupported action
        config = MagicMock()
        config.action = MagicMock()
        config.action.value = "unsupported"
        
        with pytest.raises(ToolError, match="Unsupported SQL runtime action"):
            await executor.invoke(operation=op, arguments={}, config=config)

    async def test_insert_no_values_error(self) -> None:
        """Test line 107: insert with no values raises error."""
        ir = ServiceIR(
            service_id="test",
            service_name="Test",
            base_url="sqlite:///test.db",
            source_hash="sha256:test",
            protocol="sql",
            operations=[],
        )
        executor = SQLRuntimeExecutor(ir)
        
        op = Operation(
            id="op1",
            name="test",
            method="sql",
            path="/test",
            description="test",
            enabled=True,
        )
        
        config = SqlOperationConfig(
            action=SqlOperationType.insert,
            relation_name="users",
            schema_name="main",
            relation_kind=SqlRelationKind.table,
            filterable_columns=[],
            insertable_columns=["name", "email"],
            default_limit=10,
            max_limit=100,
        )
        
        executor._get_table = AsyncMock(return_value=MagicMock())
        
        # Pass arguments that don't match insertable columns or are None
        with pytest.raises(ToolError, match="requires at least one insertable value"):
            await executor.invoke(
                operation=op,
                arguments={"other_field": "value", "name": None},
                config=config,
            )

    async def test_get_table_cache_hit(self) -> None:
        """Test line 140: table cache hit returns cached table."""
        ir = ServiceIR(
            service_id="test",
            service_name="Test",
            base_url="sqlite:///test.db",
            source_hash="sha256:test",
            protocol="sql",
            operations=[],
        )
        executor = SQLRuntimeExecutor(ir)
        
        config = SqlOperationConfig(
            action=SqlOperationType.query,
            relation_name="users",
            schema_name="main",
            relation_kind=SqlRelationKind.table,
            filterable_columns=["id"],
            insertable_columns=[],
            default_limit=10,
            max_limit=100,
        )
        
        # Add a table to cache
        mock_table = MagicMock()
        cache_key = ("main", "users")
        executor._table_cache[cache_key] = mock_table
        
        result = await executor._get_table(config)
        assert result is mock_table

    def test_additional_resolve_limit_coverage(self) -> None:
        """Additional tests to cover edge cases in _resolve_limit."""
        # Test negative limit
        with pytest.raises(ToolError, match="requires limit > 0"):
            _resolve_limit(-1, default_limit=50, max_limit=100, operation_id="test")
        
        # Test zero limit  
        with pytest.raises(ToolError, match="requires limit > 0"):
            _resolve_limit(0, default_limit=50, max_limit=100, operation_id="test")