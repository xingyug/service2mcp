"""Unit tests for apps/mcp_runtime/sql.py helper functions and executor."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from apps.mcp_runtime.sql import (
    SQLRuntimeExecutor,
    _json_safe_row,
    _json_safe_value,
    _required_primary_key_values,
    _resolve_limit,
    _to_async_database_url,
)
from libs.ir.models import (
    Operation,
    ServiceIR,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
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


def _make_service_ir() -> ServiceIR:
    return ServiceIR(
        service_id="test",
        service_name="Test",
        base_url="sqlite:///test.db",
        source_hash="sha256:test",
        protocol="sql",
        operations=[],
    )


def _make_operation() -> Operation:
    return Operation(
        id="op1",
        name="test_op",
        method="sql",
        path="/test",
        description="test operation",
        enabled=True,
    )


def _make_query_config(
    *,
    filterable_columns: list[str] | None = None,
) -> SqlOperationConfig:
    return SqlOperationConfig(
        action=SqlOperationType.query,
        relation_name="users",
        schema_name="main",
        relation_kind=SqlRelationKind.table,
        filterable_columns=filterable_columns or ["id"],
        insertable_columns=[],
        default_limit=10,
        max_limit=100,
    )


def _make_insert_config(
    *,
    insertable_columns: list[str] | None = None,
) -> SqlOperationConfig:
    return SqlOperationConfig(
        action=SqlOperationType.insert,
        relation_name="users",
        schema_name="main",
        relation_kind=SqlRelationKind.table,
        filterable_columns=[],
        insertable_columns=insertable_columns or ["name", "email"],
        default_limit=10,
        max_limit=100,
    )


def _make_update_config(
    *,
    updatable_columns: list[str] | None = None,
    primary_key_columns: list[str] | None = None,
) -> SqlOperationConfig:
    return SqlOperationConfig(
        action=SqlOperationType.update,
        relation_name="users",
        schema_name="main",
        relation_kind=SqlRelationKind.table,
        filterable_columns=[],
        updatable_columns=updatable_columns or ["name", "email"],
        primary_key_columns=primary_key_columns or ["id"],
        default_limit=10,
        max_limit=100,
    )


def _make_delete_config(
    *,
    primary_key_columns: list[str] | None = None,
) -> SqlOperationConfig:
    return SqlOperationConfig(
        action=SqlOperationType.delete,
        relation_name="users",
        schema_name="main",
        relation_kind=SqlRelationKind.table,
        filterable_columns=[],
        primary_key_columns=primary_key_columns or ["id"],
        default_limit=10,
        max_limit=100,
    )


class _AsyncContextManagerMock:
    """Helper to create a mock async context manager."""

    def __init__(self, return_value: Any) -> None:
        self._return_value = return_value

    async def __aenter__(self) -> Any:
        return self._return_value

    async def __aexit__(self, *args: Any) -> None:
        pass


class TestSQLRuntimeExecutorQuery:
    """Tests covering query-path gaps: column not found, IN clause, dict filter, execute+fetch."""

    @pytest.mark.asyncio
    async def test_column_not_in_table_skips_filter(self) -> None:
        """Line 72: column not found in table → skip filter."""
        ir = _make_service_ir()
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value = _AsyncContextManagerMock(mock_conn)
        executor = SQLRuntimeExecutor(ir, engine=mock_engine)

        mock_col = MagicMock()
        mock_table = MagicMock()
        mock_table.c.get = MagicMock(side_effect=lambda col: mock_col if col == "name" else None)
        executor._get_table = AsyncMock(return_value=mock_table)

        mock_row = MagicMock()
        mock_row._mapping = {"id": 1, "name": "Alice"}
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([mock_row]))
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_select_chain = MagicMock()
        mock_select_chain.limit.return_value = mock_select_chain
        mock_select_chain.where.return_value = mock_select_chain

        config = _make_query_config(filterable_columns=["name", "missing_col"])
        with patch("apps.mcp_runtime.sql.select", return_value=mock_select_chain):
            result = await executor.invoke(
                operation=_make_operation(),
                arguments={"name": "Alice", "missing_col": "whatever"},
                config=config,
            )

        assert result["action"] == "query"
        assert result["row_count"] == 1

    @pytest.mark.asyncio
    async def test_list_value_uses_in_clause(self) -> None:
        """Lines 74-75: list value → use IN clause."""
        ir = _make_service_ir()
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value = _AsyncContextManagerMock(mock_conn)
        executor = SQLRuntimeExecutor(ir, engine=mock_engine)

        mock_col = MagicMock()
        mock_col.in_ = MagicMock(return_value=MagicMock())
        mock_table = MagicMock()
        mock_table.c.get = MagicMock(return_value=mock_col)
        executor._get_table = AsyncMock(return_value=mock_table)

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_select_chain = MagicMock()
        mock_select_chain.limit.return_value = mock_select_chain
        mock_select_chain.where.return_value = mock_select_chain

        config = _make_query_config(filterable_columns=["status"])
        with patch("apps.mcp_runtime.sql.select", return_value=mock_select_chain):
            result = await executor.invoke(
                operation=_make_operation(),
                arguments={"status": ["active", "pending"]},
                config=config,
            )

        mock_col.in_.assert_called_once_with(["active", "pending"])
        assert result["row_count"] == 0

    @pytest.mark.asyncio
    async def test_dict_value_raises_tool_error(self) -> None:
        """Line 77: dict value → raise ToolError."""
        ir = _make_service_ir()
        mock_engine = MagicMock()
        executor = SQLRuntimeExecutor(ir, engine=mock_engine)

        mock_col = MagicMock()
        mock_table = MagicMock()
        mock_table.c.get = MagicMock(return_value=mock_col)
        executor._get_table = AsyncMock(return_value=mock_table)

        mock_select_chain = MagicMock()
        mock_select_chain.limit.return_value = mock_select_chain
        mock_select_chain.where.return_value = mock_select_chain

        config = _make_query_config(filterable_columns=["metadata"])
        with patch("apps.mcp_runtime.sql.select", return_value=mock_select_chain):
            with pytest.raises(ToolError, match="does not support object filter values"):
                await executor.invoke(
                    operation=_make_operation(),
                    arguments={"metadata": {"key": "val"}},
                    config=config,
                )

    @pytest.mark.asyncio
    async def test_query_execute_and_fetch_rows(self) -> None:
        """Lines 83-84: execute query and fetch rows with _json_safe_row."""
        from decimal import Decimal

        ir = _make_service_ir()
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value = _AsyncContextManagerMock(mock_conn)
        executor = SQLRuntimeExecutor(ir, engine=mock_engine)

        mock_table = MagicMock()
        mock_table.c.get = MagicMock(return_value=None)
        executor._get_table = AsyncMock(return_value=mock_table)

        mock_row1 = MagicMock()
        mock_row1._mapping = {"id": 1, "price": Decimal("9.99")}
        mock_row2 = MagicMock()
        mock_row2._mapping = {"id": 2, "price": Decimal("19.99")}
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([mock_row1, mock_row2]))
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_select_chain = MagicMock()
        mock_select_chain.limit.return_value = mock_select_chain

        config = _make_query_config()
        with patch("apps.mcp_runtime.sql.select", return_value=mock_select_chain):
            result = await executor.invoke(
                operation=_make_operation(),
                arguments={},
                config=config,
            )

        assert result["row_count"] == 2
        assert result["rows"][0] == {"id": 1, "price": pytest.approx(9.99)}
        assert result["rows"][1] == {"id": 2, "price": pytest.approx(19.99)}
        assert result["limit"] == 10


class TestSQLRuntimeExecutorInsert:
    """Tests covering insert-path gap: returning primary key."""

    @pytest.mark.asyncio
    async def test_insert_returning_primary_key(self) -> None:
        """Lines 118-123: insert returning primary key."""
        ir = _make_service_ir()
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value = _AsyncContextManagerMock(mock_conn)
        executor = SQLRuntimeExecutor(ir, engine=mock_engine)

        mock_pk_col = MagicMock()
        mock_pk = MagicMock()
        mock_pk.columns = [mock_pk_col]
        mock_table = MagicMock()
        mock_table.primary_key = mock_pk
        executor._get_table = AsyncMock(return_value=mock_table)

        returned_row = MagicMock()
        returned_row.__iter__ = MagicMock(return_value=iter([42]))
        mock_result = MagicMock()
        mock_result.first.return_value = returned_row
        mock_result.rowcount = 1
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_insert_chain = MagicMock()
        mock_insert_chain.values.return_value = mock_insert_chain
        mock_insert_chain.returning.return_value = mock_insert_chain

        config = _make_insert_config(insertable_columns=["name"])
        with patch("apps.mcp_runtime.sql.insert", return_value=mock_insert_chain):
            result = await executor.invoke(
                operation=_make_operation(),
                arguments={"name": "Alice"},
                config=config,
            )

        assert result["action"] == "insert"
        assert result["row_count"] == 1
        assert result["inserted_primary_key"] == [42]


class TestRequiredPrimaryKeyValues:
    def test_collects_required_values(self) -> None:
        assert _required_primary_key_values(
            operation_id="delete_users",
            arguments={"id": 42},
            primary_key_columns=["id"],
        ) == {"id": 42}

    def test_raises_when_primary_key_missing(self) -> None:
        with pytest.raises(ToolError, match="requires primary key parameter id"):
            _required_primary_key_values(
                operation_id="delete_users",
                arguments={},
                primary_key_columns=["id"],
            )


class TestSQLRuntimeExecutorUpdateDelete:
    @pytest.mark.asyncio
    async def test_update_uses_primary_key_filter_and_returns_row_count(self) -> None:
        ir = _make_service_ir()
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value = _AsyncContextManagerMock(mock_conn)
        executor = SQLRuntimeExecutor(ir, engine=mock_engine)

        mock_id_col = MagicMock()
        mock_id_col.__eq__ = MagicMock(return_value=MagicMock())
        mock_table = MagicMock()
        mock_table.c.get = MagicMock(side_effect=lambda name: mock_id_col if name == "id" else None)
        executor._get_table = AsyncMock(return_value=mock_table)

        mock_result = MagicMock()
        mock_result.rowcount = 2
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_update_chain = MagicMock()
        mock_update_chain.values.return_value = mock_update_chain
        mock_update_chain.where.return_value = mock_update_chain

        with patch("apps.mcp_runtime.sql.update", return_value=mock_update_chain):
            result = await executor.invoke(
                operation=_make_operation(),
                arguments={"id": 42, "email": "alice@example.com"},
                config=_make_update_config(updatable_columns=["email"]),
            )

        assert result == {
            "relation": "users",
            "action": "update",
            "row_count": 2,
            "updated_primary_key": {"id": 42},
        }

    @pytest.mark.asyncio
    async def test_update_requires_at_least_one_updatable_value(self) -> None:
        ir = _make_service_ir()
        executor = SQLRuntimeExecutor(ir, engine=MagicMock())
        executor._get_table = AsyncMock(return_value=MagicMock())

        with pytest.raises(ToolError, match="requires at least one updatable value"):
            await executor.invoke(
                operation=_make_operation(),
                arguments={"id": 42},
                config=_make_update_config(updatable_columns=["email"]),
            )

    @pytest.mark.asyncio
    async def test_delete_uses_primary_key_filter_and_returns_row_count(self) -> None:
        ir = _make_service_ir()
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value = _AsyncContextManagerMock(mock_conn)
        executor = SQLRuntimeExecutor(ir, engine=mock_engine)

        mock_id_col = MagicMock()
        mock_id_col.__eq__ = MagicMock(return_value=MagicMock())
        mock_table = MagicMock()
        mock_table.c.get = MagicMock(side_effect=lambda name: mock_id_col if name == "id" else None)
        executor._get_table = AsyncMock(return_value=mock_table)

        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_delete_chain = MagicMock()
        mock_delete_chain.where.return_value = mock_delete_chain

        with patch("apps.mcp_runtime.sql.delete", return_value=mock_delete_chain):
            result = await executor.invoke(
                operation=_make_operation(),
                arguments={"id": 42},
                config=_make_delete_config(),
            )

        assert result == {
            "relation": "users",
            "action": "delete",
            "row_count": 1,
            "deleted_primary_key": {"id": 42},
        }
