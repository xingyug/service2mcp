"""Corpus-driven extractor conformance tests for messy real-world fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer

from libs.extractors import (
    GraphQLExtractor,
    GrpcProtoExtractor,
    OpenAPIExtractor,
    SOAPWSDLExtractor,
    SQLExtractor,
)
from libs.extractors.base import SourceConfig
from libs.ir.models import AuthType, EventDescriptor, RequestBodyMode

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures"
CORPUS_MANIFEST_PATH = FIXTURES_DIR / "conformance" / "corpus.yaml"
SQL_EDGE_FIXTURE_PATH = FIXTURES_DIR / "conformance" / "sql" / "analytics_edge.sql"


@dataclass(frozen=True)
class CorpusCase:
    id: str
    protocol: str
    source: dict[str, Any]
    expect: dict[str, Any]


def _load_corpus_cases() -> list[CorpusCase]:
    payload = yaml.safe_load(CORPUS_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("Corpus manifest must be a mapping.")
    raw_cases = payload.get("cases", [])
    if not isinstance(raw_cases, list):
        raise AssertionError("Corpus manifest cases must be a list.")
    cases: list[CorpusCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise AssertionError("Each corpus case must be a mapping.")
        cases.append(
            CorpusCase(
                id=str(raw_case["id"]),
                protocol=str(raw_case["protocol"]),
                source=dict(raw_case["source"]),
                expect=dict(raw_case["expect"]),
            )
        )
    return cases


CORPUS_CASES = _load_corpus_cases()


def _to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgres://"):
        return connection_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres connection URL: {connection_url}")


def _build_extractor(protocol: str) -> Any:
    if protocol == "openapi":
        return OpenAPIExtractor()
    if protocol == "graphql":
        return GraphQLExtractor()
    if protocol == "grpc":
        return GrpcProtoExtractor()
    if protocol == "soap":
        return SOAPWSDLExtractor()
    raise AssertionError(f"Unsupported corpus protocol: {protocol}")


def _build_source(source_config: dict[str, Any]) -> SourceConfig:
    kwargs = dict(source_config)
    file_path = kwargs.get("file_path")
    if isinstance(file_path, str):
        kwargs["file_path"] = str(FIXTURES_DIR / file_path)
    hints = kwargs.get("hints")
    if hints is None:
        kwargs["hints"] = {}
    return SourceConfig(**kwargs)


def _project_event_descriptor(descriptor: EventDescriptor) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "id": descriptor.id,
        "transport": descriptor.transport.value,
        "direction": descriptor.direction.value,
        "support": descriptor.support.value,
    }
    if descriptor.channel is not None:
        projected["channel"] = descriptor.channel
    if descriptor.operation_id is not None:
        projected["operation_id"] = descriptor.operation_id
    if descriptor.metadata:
        projected["metadata"] = descriptor.metadata
    return projected


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(scope="module")
async def analytics_edge_database(
    postgres_container: PostgresContainer,
) -> AsyncIterator[str]:
    engine: AsyncEngine = create_async_engine(
        _to_asyncpg_url(postgres_container.get_connection_url())
    )

    async with engine.begin() as connection:
        sql_statements = SQL_EDGE_FIXTURE_PATH.read_text(encoding="utf-8")
        for statement in sql_statements.split(";\n"):
            candidate = statement.strip()
            if candidate:
                await connection.execute(text(candidate))

    try:
        yield postgres_container.get_connection_url()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("case", CORPUS_CASES, ids=lambda case: case.id)
def test_extractor_conformance_corpus(case: CorpusCase) -> None:
    extractor = _build_extractor(case.protocol)
    source = _build_source(case.source)
    expected = case.expect

    confidence = extractor.detect(source)
    outcome = expected["outcome"]
    if outcome == "fail":
        assert confidence <= float(expected.get("max_detect_confidence", 0.0))
        with pytest.raises(Exception):
            extractor.extract(source)
        return

    assert confidence >= float(expected.get("min_detect_confidence", 0.1))
    service_ir = extractor.extract(source)

    assert service_ir.protocol == case.protocol
    if "service_name" in expected:
        assert service_ir.service_name == expected["service_name"]
    if "auth_type" in expected:
        assert service_ir.auth.type is AuthType(expected["auth_type"])
    if "operations_count" in expected:
        assert len(service_ir.operations) == int(expected["operations_count"])
    if "operation_ids" in expected:
        assert {operation.id for operation in service_ir.operations} == set(
            expected["operation_ids"]
        )
    if "metadata" in expected:
        for key, value in expected["metadata"].items():
            assert service_ir.metadata.get(key) == value
    if "request_body_modes" in expected:
        for operation_id, mode in expected["request_body_modes"].items():
            operation = next(op for op in service_ir.operations if op.id == operation_id)
            assert operation.request_body_mode is RequestBodyMode(mode)
    if "body_param_names" in expected:
        for operation_id, body_param_name in expected["body_param_names"].items():
            operation = next(op for op in service_ir.operations if op.id == operation_id)
            assert operation.body_param_name == body_param_name
    if "event_descriptors" in expected:
        assert [_project_event_descriptor(d) for d in service_ir.event_descriptors] == expected[
            "event_descriptors"
        ]


def test_sql_edge_conformance_fixture_uses_explicit_expected_outcomes(
    analytics_edge_database: str,
) -> None:
    extractor = SQLExtractor()
    source = SourceConfig(
        url=analytics_edge_database,
        hints={"service_name": "analytics-edge"},
    )

    confidence = extractor.detect(source)
    assert confidence >= 0.9

    service_ir = extractor.extract(source)

    assert service_ir.protocol == "sql"
    assert service_ir.service_name == "analytics-edge"
    assert service_ir.metadata["tables"] == ["audit_events"]
    assert service_ir.metadata["views"] == ["audit_event_rollups"]
    assert {operation.id for operation in service_ir.operations} == {
        "query_audit_events",
        "insert_audit_events",
        "update_audit_events",
        "delete_audit_events",
        "query_audit_event_rollups",
    }

    query_events = next(
        operation for operation in service_ir.operations if operation.id == "query_audit_events"
    )
    query_types = {param.name: param.type for param in query_events.params}
    assert query_types["external_id"] == "string"
    assert query_types["success"] == "boolean"
    assert query_types["payload"] == "object"
    assert query_types["amount"] == "number"
    assert query_types["occurred_at"] == "string"

    insert_events = next(
        operation for operation in service_ir.operations if operation.id == "insert_audit_events"
    )
    insert_required = {param.name: param.required for param in insert_events.params}
    assert insert_required["external_id"] is True
    assert insert_required["amount"] is True
    assert insert_required["occurred_at"] is True
    assert insert_required["payload"] is False

    update_events = next(
        operation for operation in service_ir.operations if operation.id == "update_audit_events"
    )
    update_required = {param.name: param.required for param in update_events.params}
    assert update_required["id"] is True
    assert update_required["payload"] is False

    delete_events = next(
        operation for operation in service_ir.operations if operation.id == "delete_audit_events"
    )
    delete_required = {param.name: param.required for param in delete_events.params}
    assert delete_required == {"id": True}

    query_rollups = next(
        operation
        for operation in service_ir.operations
        if operation.id == "query_audit_event_rollups"
    )
    assert all(param.required is False for param in query_rollups.params)
