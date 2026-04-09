"""Unit tests for apps/access_control/audit/service.py — DTO transformer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from apps.access_control.audit.service import AuditLogService


def _utcnow() -> datetime:
    return datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC)


def _fake_entry(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "actor": "admin@example.com",
        "action": "create_pat",
        "resource": "service:petstore",
        "detail": {"pat_id": "pat-123"},
        "timestamp": _utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestToResponse:
    def test_basic_fields(self) -> None:
        entry = _fake_entry()
        result = AuditLogService._to_response(entry)
        assert result.id == entry.id
        assert result.actor == "admin@example.com"
        assert result.action == "create_pat"
        assert result.resource == "service:petstore"
        assert result.detail == {"pat_id": "pat-123"}
        assert result.timestamp == _utcnow()

    def test_null_optional_fields(self) -> None:
        entry = _fake_entry(resource=None, detail=None)
        result = AuditLogService._to_response(entry)
        assert result.resource is None
        assert result.detail is None

    def test_different_action(self) -> None:
        entry = _fake_entry(action="revoke_pat", detail={"reason": "expired"})
        result = AuditLogService._to_response(entry)
        assert result.action == "revoke_pat"
        assert result.detail == {"reason": "expired"}


# Additional tests to cover uncovered lines in audit/service.py


def _mock_session() -> AsyncMock:
    """Create a mock AsyncSession with coroutine stubs."""
    session = AsyncMock()
    # session.add is a sync method on AsyncSession
    session.add = MagicMock()
    return session


class TestAppendEntry:
    @pytest.mark.asyncio
    async def test_append_entry_with_commit(self) -> None:
        session = _mock_session()
        entry_id = uuid.uuid4()
        ts = _utcnow()

        # Make refresh populate the entry with db-generated fields
        async def _fake_refresh(obj: Any, **_kw: Any) -> None:
            obj.id = entry_id
            obj.timestamp = ts

        session.refresh.side_effect = _fake_refresh
        svc = AuditLogService(session)

        result = await svc.append_entry(
            actor="admin@example.com",
            action="create_pat",
            resource="service:petstore",
            detail={"pat_id": "pat-123"},
        )

        # Verify session interactions
        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once()

        # Verify correct call order: add → flush → commit → refresh
        assert session.flush.await_args_list[0] == call()
        assert session.commit.await_args_list[0] == call()

        # Verify returned response
        assert result.id == entry_id
        assert result.actor == "admin@example.com"
        assert result.action == "create_pat"
        assert result.resource == "service:petstore"
        assert result.detail == {"pat_id": "pat-123"}
        assert result.timestamp == ts

    @pytest.mark.asyncio
    async def test_append_entry_without_commit(self) -> None:
        session = _mock_session()
        entry_id = uuid.uuid4()
        ts = _utcnow()

        async def _fake_refresh(obj: Any, **_kw: Any) -> None:
            obj.id = entry_id
            obj.timestamp = ts

        session.refresh.side_effect = _fake_refresh
        svc = AuditLogService(session)

        result = await svc.append_entry(
            actor="admin@example.com",
            action="revoke_pat",
            commit=False,
        )

        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        session.commit.assert_not_awaited()
        session.refresh.assert_awaited_once()

        assert result.id == entry_id
        assert result.action == "revoke_pat"
        assert result.resource is None
        assert result.detail is None


class TestListEntries:
    @pytest.mark.asyncio
    async def test_list_entries_applies_default_limit(self) -> None:
        session = _mock_session()
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = []
        session.scalars.return_value = mock_scalars_result
        svc = AuditLogService(session)

        await svc.list_entries()

        query = session.scalars.await_args.args[0]
        assert query._limit_clause is not None
        assert query._limit_clause.value == 1000

    @pytest.mark.asyncio
    async def test_list_entries_can_disable_limit(self) -> None:
        session = _mock_session()
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = []
        session.scalars.return_value = mock_scalars_result
        svc = AuditLogService(session)

        await svc.list_entries(limit=None)

        query = session.scalars.await_args.args[0]
        assert query._limit_clause is None


class TestGetEntry:
    @pytest.mark.asyncio
    async def test_returns_matching_entry(self) -> None:
        session = _mock_session()
        entry = _fake_entry()
        session.get.return_value = entry
        svc = AuditLogService(session)

        result = await svc.get_entry(entry.id)

        session.get.assert_awaited_once()
        assert result is not None
        assert result.id == entry.id
        assert result.action == entry.action

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self) -> None:
        session = _mock_session()
        session.get.return_value = None
        svc = AuditLogService(session)

        result = await svc.get_entry(uuid.uuid4())

        assert result is None
