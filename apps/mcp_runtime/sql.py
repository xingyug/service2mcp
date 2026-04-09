"""Native SQL runtime executor for reflected CRUD operations."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import MetaData, Table, delete, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from libs.ir.models import Operation, ServiceIR, SqlOperationConfig, SqlOperationType


class SQLRuntimeExecutor:
    """Execute reflected SQL operations against the configured database URL."""

    def __init__(
        self,
        service_ir: ServiceIR,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._database_url = _to_async_database_url(service_ir.base_url)
        self._engine = engine or create_async_engine(self._database_url)
        self._owns_engine = engine is None
        self._table_cache: dict[tuple[str, str], Table] = {}

    async def aclose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def invoke(
        self,
        *,
        operation: Operation,
        arguments: dict[str, Any],
        config: SqlOperationConfig,
    ) -> dict[str, Any]:
        table = await self._get_table(config)
        if config.action is SqlOperationType.query:
            return await self._query(operation, arguments, config, table)
        if config.action is SqlOperationType.insert:
            return await self._insert(operation, arguments, config, table)
        if config.action is SqlOperationType.update:
            return await self._update(operation, arguments, config, table)
        if config.action is SqlOperationType.delete:
            return await self._delete(operation, arguments, config, table)
        raise ToolError(
            f"Unsupported SQL runtime action {config.action.value} for operation {operation.id}."
        )

    async def _query(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        config: SqlOperationConfig,
        table: Table,
    ) -> dict[str, Any]:
        limit = _resolve_limit(
            arguments.get("limit"),
            default_limit=config.default_limit,
            max_limit=config.max_limit,
            operation_id=operation.id,
        )
        statement = select(table).limit(limit)
        for column_name in config.filterable_columns:
            if column_name not in arguments or arguments[column_name] is None:
                continue
            value = arguments[column_name]
            column = table.c.get(column_name)
            if column is None:
                raise ToolError(
                    f"SQL query operation {operation.id} requested filter column {column_name!r} "
                    f"that is not present in relation {config.relation_name}."
                )
            if isinstance(value, list):
                statement = statement.where(column.in_(value))
                continue
            if isinstance(value, dict):
                raise ToolError(
                    f"SQL query operation {operation.id} does not support object filter values."
                )
            statement = statement.where(column == value)

        async with self._engine.connect() as connection:
            result = await connection.execute(statement)
            rows = [_json_safe_row(dict(row._mapping)) for row in result]

        return {
            "relation": config.relation_name,
            "action": config.action.value,
            "limit": limit,
            "row_count": len(rows),
            "rows": rows,
        }

    async def _insert(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        config: SqlOperationConfig,
        table: Table,
    ) -> dict[str, Any]:
        values = {
            column_name: arguments[column_name]
            for column_name in config.insertable_columns
            if column_name in arguments
        }
        if not values:
            raise ToolError(
                f"SQL insert operation {operation.id} requires at least one insertable value."
            )

        primary_key_columns = list(table.primary_key.columns)
        statement = insert(table).values(**values)
        if primary_key_columns:
            statement = statement.returning(*primary_key_columns)

        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
            inserted_primary_key: list[Any] | None = None
            if primary_key_columns:
                returned_row = result.first()
                if returned_row is not None:
                    inserted_primary_key = [_json_safe_value(value) for value in returned_row]
            row_count = (
                result.rowcount if result.rowcount is not None and result.rowcount >= 0 else 1
            )

        return {
            "relation": config.relation_name,
            "action": config.action.value,
            "row_count": row_count,
            "inserted_primary_key": inserted_primary_key,
        }

    async def _update(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        config: SqlOperationConfig,
        table: Table,
    ) -> dict[str, Any]:
        primary_key_values = _required_primary_key_values(
            operation_id=operation.id,
            arguments=arguments,
            primary_key_columns=config.primary_key_columns,
        )
        values = {
            column_name: arguments[column_name]
            for column_name in config.updatable_columns
            if column_name in arguments and arguments[column_name] is not None
        }
        if not values:
            raise ToolError(
                f"SQL update operation {operation.id} requires at least one updatable value."
            )

        statement = update(table).values(**values)
        for column_name, value in primary_key_values.items():
            column = table.c.get(column_name)
            if column is None:
                raise ToolError(
                    f"SQL update operation {operation.id} references unknown column {column_name}."
                )
            statement = statement.where(column == value)

        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
            row_count = (
                result.rowcount if result.rowcount is not None and result.rowcount >= 0 else 0
            )

        return {
            "relation": config.relation_name,
            "action": config.action.value,
            "row_count": row_count,
            "updated_primary_key": _json_safe_row(primary_key_values),
        }

    async def _delete(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        config: SqlOperationConfig,
        table: Table,
    ) -> dict[str, Any]:
        primary_key_values = _required_primary_key_values(
            operation_id=operation.id,
            arguments=arguments,
            primary_key_columns=config.primary_key_columns,
        )
        statement = delete(table)
        for column_name, value in primary_key_values.items():
            column = table.c.get(column_name)
            if column is None:
                raise ToolError(
                    f"SQL delete operation {operation.id} references unknown column {column_name}."
                )
            statement = statement.where(column == value)

        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
            row_count = (
                result.rowcount if result.rowcount is not None and result.rowcount >= 0 else 0
            )

        return {
            "relation": config.relation_name,
            "action": config.action.value,
            "row_count": row_count,
            "deleted_primary_key": _json_safe_row(primary_key_values),
        }

    async def _get_table(self, config: SqlOperationConfig) -> Table:
        cache_key = (config.schema_name, config.relation_name)
        cached = self._table_cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._engine.connect() as connection:
            table = await connection.run_sync(
                lambda sync_connection: _reflect_table(sync_connection, config)
            )
        self._table_cache[cache_key] = table
        return table


def _reflect_table(connection: Connection, config: SqlOperationConfig) -> Table:
    metadata = MetaData()
    schema: str | None = config.schema_name
    if connection.dialect.name == "sqlite" and schema == "main":
        schema = None
    return Table(
        config.relation_name,
        metadata,
        schema=schema,
        autoload_with=connection,
    )


def _to_async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    raise ToolError(f"Unsupported SQL database URL: {database_url}")


def _resolve_limit(
    raw_limit: Any,
    *,
    default_limit: int,
    max_limit: int,
    operation_id: str,
) -> int:
    if raw_limit is None:
        return default_limit
    if isinstance(raw_limit, bool):
        raise ToolError(f"SQL query operation {operation_id} requires an integer limit.")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"SQL query operation {operation_id} requires an integer limit.") from exc
    if limit <= 0:
        raise ToolError(f"SQL query operation {operation_id} requires limit > 0.")
    return min(limit, max_limit)


def _required_primary_key_values(
    *,
    operation_id: str,
    arguments: dict[str, Any],
    primary_key_columns: list[str],
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for column_name in primary_key_columns:
        if column_name not in arguments or arguments[column_name] is None:
            raise ToolError(
                f"SQL operation {operation_id} requires primary key parameter {column_name}."
            )
        values[column_name] = arguments[column_name]
    return values


def _json_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_safe_value(value) for key, value in row.items()}


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            import base64

            return f"base64:{base64.b64encode(value).decode('ascii')}"
    if isinstance(value, float) and (
        value != value or value == float("inf") or value == float("-inf")
    ):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value
