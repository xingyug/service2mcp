"""Integration tests for native SQL runtime execution."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from apps.mcp_runtime import create_app
from libs.extractors.base import SourceConfig
from libs.extractors.sql import SQLExtractor
from libs.ir.schema import serialize_ir


def _initialize_sqlite_catalog(tmp_path: Path) -> tuple[str, Path]:
    database_path = tmp_path / "catalog.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            );

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                total_cents INTEGER NOT NULL,
                notes TEXT,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
            );

            CREATE VIEW order_summaries AS
            SELECT orders.id, customers.name AS customer_name, orders.total_cents
            FROM orders
            JOIN customers ON customers.id = orders.customer_id;

            INSERT INTO customers(name) VALUES ('Acme');
            INSERT INTO orders(customer_id, total_cents, notes) VALUES (1, 1250, 'rush');
            """
        )
        connection.commit()
    finally:
        connection.close()

    return f"sqlite:///{database_path}", database_path


def _extract_sql_ir(tmp_path: Path) -> tuple[Path, Path]:
    database_url, database_path = _initialize_sqlite_catalog(tmp_path)
    service_ir = SQLExtractor().extract(SourceConfig(url=database_url, hints={"schema": "main"}))
    service_ir_path = tmp_path / "sql_runtime_ir.json"
    service_ir_path.write_text(serialize_ir(service_ir), encoding="utf-8")
    return service_ir_path, database_path


@pytest.mark.asyncio
async def test_runtime_tool_call_executes_native_sql_query(tmp_path: Path) -> None:
    service_ir_path, _ = _extract_sql_ir(tmp_path)
    app = create_app(service_ir_path=service_ir_path)

    _, structured = await app.state.runtime_state.mcp_server.call_tool(
        "query_orders",
        {"customer_id": 1, "limit": 1},
    )

    assert structured["status"] == "ok"
    assert structured["result"] == {
        "relation": "orders",
        "action": "query",
        "limit": 1,
        "row_count": 1,
        "rows": [
            {
                "id": 1,
                "customer_id": 1,
                "total_cents": 1250,
                "notes": "rush",
            }
        ],
    }


@pytest.mark.asyncio
async def test_runtime_tool_call_executes_native_sql_insert(tmp_path: Path) -> None:
    service_ir_path, database_path = _extract_sql_ir(tmp_path)
    app = create_app(service_ir_path=service_ir_path)

    _, structured = await app.state.runtime_state.mcp_server.call_tool(
        "insert_orders",
        {"customer_id": 1, "total_cents": 2500, "notes": "priority"},
    )

    assert structured["status"] == "ok"
    assert structured["result"]["relation"] == "orders"
    assert structured["result"]["action"] == "insert"
    assert structured["result"]["row_count"] == 1

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT customer_id, total_cents, notes FROM orders ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [(1, 1250, "rush"), (1, 2500, "priority")]
