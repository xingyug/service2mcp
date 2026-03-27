"""Unit tests for apps/access_control/audit/service.py — DTO transformer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

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
        result = AuditLogService._to_response(entry)  # type: ignore[arg-type]
        assert result.id == entry.id
        assert result.actor == "admin@example.com"
        assert result.action == "create_pat"
        assert result.resource == "service:petstore"
        assert result.detail == {"pat_id": "pat-123"}
        assert result.timestamp == _utcnow()

    def test_null_optional_fields(self) -> None:
        entry = _fake_entry(resource=None, detail=None)
        result = AuditLogService._to_response(entry)  # type: ignore[arg-type]
        assert result.resource is None
        assert result.detail is None

    def test_different_action(self) -> None:
        entry = _fake_entry(action="revoke_pat", detail={"reason": "expired"})
        result = AuditLogService._to_response(entry)  # type: ignore[arg-type]
        assert result.action == "revoke_pat"
        assert result.detail == {"reason": "expired"}
