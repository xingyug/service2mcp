"""SQL schema extractor based on SQLAlchemy reflection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from threading import Thread
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql.sqltypes import (
    ARRAY,
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    Time,
)
from sqlalchemy.sql.sqltypes import String as SQLString
from sqlalchemy.sql.sqltypes import Uuid as SQLUuid

from libs.extractors.base import SourceConfig
from libs.ir.models import (
    AuthConfig,
    AuthType,
    ErrorResponse,
    ErrorSchema,
    Operation,
    Param,
    RiskLevel,
    RiskMetadata,
    ServiceIR,
    SourceType,
    SqlOperationConfig,
    SqlOperationType,
    SqlRelationKind,
)

logger = logging.getLogger(__name__)

_DATABASE_SCHEMES = {"postgres", "postgresql", "mysql", "mariadb", "sqlite"}


@dataclass(frozen=True)
class ReflectedColumn:
    """Normalized reflected column metadata."""

    name: str
    ir_type: str
    nullable: bool
    description: str
    has_default: bool
    autoincrement: bool
    primary_key: bool
    insertable: bool


@dataclass(frozen=True)
class ReflectedRelation:
    """Normalized reflected relation metadata."""

    name: str
    kind: str
    columns: tuple[ReflectedColumn, ...]


@dataclass(frozen=True)
class ReflectedDatabase:
    """Reflected database schema used to derive a ServiceIR."""

    schema: str
    relations: tuple[ReflectedRelation, ...]


class SQLExtractor:
    """Extract SQL table and view metadata into ServiceIR."""

    protocol_name: str = "sql"

    def detect(self, source: SourceConfig) -> float:
        if source.hints.get("protocol") == "sql":
            return 1.0

        database_url = self._resolve_database_url(source)
        if database_url is None:
            return 0.0

        parsed = urlparse(database_url)
        scheme = parsed.scheme.split("+", 1)[0]
        if scheme in _DATABASE_SCHEMES:
            return 0.95
        return 0.0

    def extract(self, source: SourceConfig) -> ServiceIR:
        database_url = self._resolve_database_url(source)
        if database_url is None:
            raise ValueError(
                "SQLExtractor requires a database URL in source.url or source.file_content"
            )

        schema_name = source.hints.get("schema", "")
        reflected = _run_coroutine(self._reflect_database(database_url, schema_name))
        service_name = self._derive_service_name(source, database_url)
        operations = self._build_operations(reflected)
        fingerprint = self._schema_fingerprint(database_url, reflected)

        return ServiceIR(
            source_url=database_url,
            source_hash=fingerprint,
            protocol="sql",
            service_name=service_name,
            service_description=f"SQL schema reflection for {service_name}",
            base_url=database_url,
            auth=AuthConfig(type=AuthType.none),
            operations=operations,
            metadata={
                "database_schema": reflected.schema,
                "tables": [
                    relation.name for relation in reflected.relations if relation.kind == "table"
                ],
                "views": [
                    relation.name for relation in reflected.relations if relation.kind == "view"
                ],
            },
        )

    async def _reflect_database(self, database_url: str, schema_name: str) -> ReflectedDatabase:
        engine = create_async_engine(self._to_async_url(database_url))
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync_connection: self._reflect_sync(sync_connection, schema_name)
                )
        except SQLAlchemyError as exc:
            raise ValueError(f"Failed to inspect database schema: {exc}") from exc
        finally:
            await engine.dispose()

    def _reflect_sync(self, connection: Connection, schema_name: str) -> ReflectedDatabase:
        inspector = inspect(connection)
        resolved_schema = schema_name or inspector.default_schema_name or "public"

        relations: list[ReflectedRelation] = []
        for table_name in inspector.get_table_names(schema=resolved_schema):
            relations.append(
                self._reflect_relation(
                    inspector,
                    schema=resolved_schema,
                    relation_name=table_name,
                    kind="table",
                )
            )
        for view_name in inspector.get_view_names(schema=resolved_schema):
            relations.append(
                self._reflect_relation(
                    inspector,
                    schema=resolved_schema,
                    relation_name=view_name,
                    kind="view",
                )
            )

        relations.sort(key=lambda relation: (relation.kind, relation.name))
        return ReflectedDatabase(schema=resolved_schema, relations=tuple(relations))

    def _reflect_relation(
        self,
        inspector: Inspector,
        *,
        schema: str,
        relation_name: str,
        kind: str,
    ) -> ReflectedRelation:
        columns = inspector.get_columns(relation_name, schema=schema)
        primary_key_columns = set(
            inspector.get_pk_constraint(relation_name, schema=schema).get("constrained_columns", [])
        )
        foreign_key_descriptions = self._foreign_key_descriptions(
            inspector,
            schema=schema,
            relation_name=relation_name,
        )

        reflected_columns: list[ReflectedColumn] = []
        for column in columns:
            column_name = str(column["name"])
            ir_type = self._map_column_type(column.get("type"))
            description_parts = [str(column.get("comment") or "").strip()]
            foreign_key_description = foreign_key_descriptions.get(column_name)
            if foreign_key_description:
                description_parts.append(foreign_key_description)
            description = " ".join(part for part in description_parts if part)
            has_default = (
                column.get("default") is not None
                or column.get("server_default") is not None
                or column.get("identity") is not None
            )
            autoincrement = bool(column.get("autoincrement")) and column_name in primary_key_columns
            primary_key = column_name in primary_key_columns
            insertable = kind == "table" and not (primary_key and (autoincrement or has_default))
            reflected_columns.append(
                ReflectedColumn(
                    name=column_name,
                    ir_type=ir_type,
                    nullable=bool(column.get("nullable", True)),
                    description=description,
                    has_default=has_default,
                    autoincrement=autoincrement,
                    primary_key=primary_key,
                    insertable=insertable,
                )
            )

        return ReflectedRelation(
            name=relation_name,
            kind=kind,
            columns=tuple(reflected_columns),
        )

    def _foreign_key_descriptions(
        self,
        inspector: Inspector,
        *,
        schema: str,
        relation_name: str,
    ) -> dict[str, str]:
        descriptions: dict[str, str] = {}
        for foreign_key in inspector.get_foreign_keys(relation_name, schema=schema):
            constrained_columns = foreign_key.get("constrained_columns") or []
            referred_table = foreign_key.get("referred_table")
            referred_columns = foreign_key.get("referred_columns") or []
            if len(constrained_columns) != len(referred_columns):
                logger.warning(
                    "FK column count mismatch for %s: constrained=%s, referred=%s; pairing only matched columns.",
                    relation_name,
                    constrained_columns,
                    referred_columns,
                )
            for column_name, referred_column in zip(constrained_columns, referred_columns):
                descriptions[str(column_name)] = f"References {referred_table}.{referred_column}."
        return descriptions

    def _build_operations(self, reflected: ReflectedDatabase) -> list[Operation]:
        operations: list[Operation] = []
        for relation in reflected.relations:
            operations.append(self._build_query_operation(reflected.schema, relation))
            if relation.kind == "table":
                operations.append(self._build_insert_operation(reflected.schema, relation))
        return operations

    def _build_query_operation(self, schema: str, relation: ReflectedRelation) -> Operation:
        return Operation(
            id=f"query_{relation.name}",
            name=f"Query {relation.name}",
            description=f"Query rows from {relation.kind} {relation.name}.",
            method="GET",
            path=f"/sql/{schema}/{relation.name}",
            params=[
                Param(
                    name=column.name,
                    type=column.ir_type,
                    required=False,
                    description=column.description,
                    source=SourceType.extractor,
                    confidence=0.95,
                )
                for column in relation.columns
            ]
            + [
                Param(
                    name="limit",
                    type="integer",
                    required=False,
                    description="Maximum rows to return.",
                    default=50,
                    source=SourceType.extractor,
                    confidence=0.95,
                )
            ],
            risk=RiskMetadata(
                writes_state=False,
                destructive=False,
                external_side_effect=False,
                idempotent=True,
                risk_level=RiskLevel.safe,
                confidence=0.95,
                source=SourceType.extractor,
            ),
            sql=SqlOperationConfig(
                schema_name=schema,
                relation_name=relation.name,
                relation_kind=SqlRelationKind(relation.kind),
                action=SqlOperationType.query,
                filterable_columns=[column.name for column in relation.columns],
            ),
            tags=["sql", relation.kind],
            source=SourceType.extractor,
            confidence=0.95,
            enabled=True,
            error_schema=ErrorSchema(
                responses=[
                    ErrorResponse(
                        error_code="SYNTAX_ERROR",
                        description="SQL syntax error in generated query.",
                    ),
                    ErrorResponse(
                        error_code="TIMEOUT",
                        description="Query execution exceeded timeout.",
                    ),
                ]
            ),
        )

    def _build_insert_operation(self, schema: str, relation: ReflectedRelation) -> Operation:
        insert_params = [
            Param(
                name=column.name,
                type=column.ir_type,
                required=(
                    not column.nullable and not column.has_default and not column.autoincrement
                ),
                description=column.description,
                source=SourceType.extractor,
                confidence=0.95,
            )
            for column in relation.columns
            if column.insertable
        ]
        return Operation(
            id=f"insert_{relation.name}",
            name=f"Insert {relation.name}",
            description=f"Insert a row into table {relation.name}.",
            method="POST",
            path=f"/sql/{schema}/{relation.name}",
            params=insert_params,
            risk=RiskMetadata(
                writes_state=True,
                destructive=False,
                external_side_effect=True,
                idempotent=False,
                risk_level=RiskLevel.cautious,
                confidence=0.95,
                source=SourceType.extractor,
            ),
            sql=SqlOperationConfig(
                schema_name=schema,
                relation_name=relation.name,
                relation_kind=SqlRelationKind.table,
                action=SqlOperationType.insert,
                filterable_columns=[column.name for column in relation.columns],
                insertable_columns=[
                    column.name for column in relation.columns if column.insertable
                ],
            ),
            tags=["sql", "table", "insert"],
            source=SourceType.extractor,
            confidence=0.95,
            enabled=True,
            error_schema=ErrorSchema(
                responses=[
                    ErrorResponse(
                        error_code="CONSTRAINT_VIOLATION",
                        description="Insert violates a database constraint.",
                    ),
                    ErrorResponse(
                        error_code="SYNTAX_ERROR",
                        description="SQL syntax error in generated statement.",
                    ),
                    ErrorResponse(
                        error_code="TIMEOUT",
                        description="Insert execution exceeded timeout.",
                    ),
                ]
            ),
        )

    def _map_column_type(self, sql_type: Any) -> str:
        if isinstance(sql_type, ARRAY):
            return "array"
        if isinstance(sql_type, (Boolean,)):
            return "boolean"
        if isinstance(sql_type, (Integer,)):
            return "integer"
        if isinstance(sql_type, (Numeric, Float)):
            return "number"
        if isinstance(sql_type, (JSON,)):
            return "object"
        if isinstance(sql_type, (Date, DateTime, Time, SQLUuid)):
            return "string"
        if isinstance(sql_type, (SQLString,)):
            return "string"

        python_type = getattr(sql_type, "python_type", None)
        if python_type is bool:
            return "boolean"
        if python_type is int:
            return "integer"
        if python_type is float:
            return "number"
        if python_type in {dict, list}:
            return "object" if python_type is dict else "array"
        return "string"

    def _resolve_database_url(self, source: SourceConfig) -> str | None:
        if source.url:
            return source.url
        if source.file_content and "://" in source.file_content:
            return source.file_content.strip()
        return None

    def _derive_service_name(self, source: SourceConfig, database_url: str) -> str:
        if source.hints.get("service_name"):
            return _slugify(source.hints["service_name"])

        parsed = urlparse(database_url)
        database_name = (
            parsed.path.rsplit("/", 1)[-1].strip("/") or parsed.hostname or "sql-service"
        )
        return _slugify(database_name)

    def _schema_fingerprint(self, database_url: str, reflected: ReflectedDatabase) -> str:
        payload = {
            "database_url": database_url,
            "schema": reflected.schema,
            "relations": [
                {
                    "name": relation.name,
                    "kind": relation.kind,
                    "columns": [
                        {
                            "name": column.name,
                            "type": column.ir_type,
                            "nullable": column.nullable,
                            "insertable": column.insertable,
                            "description": column.description,
                        }
                        for column in relation.columns
                    ],
                }
                for relation in reflected.relations
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _to_async_url(self, database_url: str) -> str:
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
        raise ValueError(f"Unsupported SQL database URL: {database_url}")


def _run_coroutine[T](coroutine: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    result: list[T] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            error.append(exc)

    thread = Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def _slugify(value: str) -> str:
    slug = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum():
            slug.append(char)
            previous_dash = False
            continue
        if previous_dash:
            continue
        slug.append("-")
        previous_dash = True
    return "".join(slug).strip("-") or "sql-service"


__all__ = ["SQLExtractor"]
