"""Unit tests for apps/compiler_worker/repository.py — static DTO transformer methods."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.compiler_worker.models import (
    CompilationEventType,
    CompilationStage,
    CompilationStatus,
)
from apps.compiler_worker.repository import SQLAlchemyCompilationJobStore


def _utcnow() -> datetime:
    return datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC)


def _fake_job(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source_url": "https://example.com/api.yaml",
        "source_hash": "sha256:abc",
        "protocol": "openapi",
        "status": "succeeded",
        "current_stage": "deploy",
        "error_detail": None,
        "options": {"key": "value"},
        "created_by": "user@example.com",
        "service_name": "Test API",
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_event(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "sequence_number": 1,
        "stage": "extract",
        "event_type": "stage.succeeded",
        "attempt": 1,
        "detail": {"operations": 5},
        "error_detail": None,
        "created_at": _utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# Use a dummy store instance (we only need the instance methods, not the DB)
_store = SQLAlchemyCompilationJobStore.__new__(SQLAlchemyCompilationJobStore)


class TestToJobRecord:
    def test_basic_fields(self) -> None:
        job = _fake_job()
        record = _store._to_job_record(job)  # type: ignore[arg-type]
        assert record.id == job.id
        assert record.source_url == "https://example.com/api.yaml"
        assert record.status == CompilationStatus.SUCCEEDED
        assert record.current_stage == CompilationStage.DEPLOY
        assert record.service_name == "Test API"

    def test_null_stage(self) -> None:
        job = _fake_job(current_stage=None)
        record = _store._to_job_record(job)  # type: ignore[arg-type]
        assert record.current_stage is None

    def test_pending_status(self) -> None:
        job = _fake_job(status="pending")
        record = _store._to_job_record(job)  # type: ignore[arg-type]
        assert record.status == CompilationStatus.PENDING


class TestToEventRecord:
    def test_basic_fields(self) -> None:
        event = _fake_event()
        record = _store._to_event_record(event)  # type: ignore[arg-type]
        assert record.id == event.id
        assert record.job_id == event.job_id
        assert record.sequence_number == 1
        assert record.stage == CompilationStage.EXTRACT
        assert record.event_type == CompilationEventType.STAGE_SUCCEEDED
        assert record.attempt == 1
        assert record.detail == {"operations": 5}

    def test_null_stage(self) -> None:
        event = _fake_event(stage=None)
        record = _store._to_event_record(event)  # type: ignore[arg-type]
        assert record.stage is None

    def test_error_event(self) -> None:
        event = _fake_event(
            event_type="stage.failed",
            error_detail="something broke",
        )
        record = _store._to_event_record(event)  # type: ignore[arg-type]
        assert record.event_type == CompilationEventType.STAGE_FAILED
        assert record.error_detail == "something broke"


class TestCreateJobIntegrityError:
    """Test that create_job() handles IntegrityError by rolling back and re-raising."""

    @pytest.mark.asyncio
    async def test_create_job_integrity_error_no_allow_existing(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.exc import IntegrityError

        from apps.compiler_worker.models import CompilationRequest

        request = CompilationRequest(
            service_name="test-svc",
            source_url="https://example.com/api.yaml",
            source_hash="sha256:abc",
        )

        fake_session = AsyncMock()
        fake_session.get = AsyncMock(return_value=None)
        fake_session.add = MagicMock()
        fake_session.commit = AsyncMock(
            side_effect=IntegrityError("dup", params=None, orig=Exception("duplicate key"))
        )
        fake_session.rollback = AsyncMock()

        # Build an async context manager that yields fake_session
        async_ctx = AsyncMock()
        async_ctx.__aenter__ = AsyncMock(return_value=fake_session)
        async_ctx.__aexit__ = AsyncMock(return_value=False)

        fake_factory = MagicMock(return_value=async_ctx)

        store = SQLAlchemyCompilationJobStore(session_factory=fake_factory)

        with pytest.raises(IntegrityError):
            await store.create_job(request)

        fake_session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_job_integrity_error_allow_existing_returns_id(self) -> None:
        """When allow_existing=True and job exists after rollback, return its id."""
        import uuid as _uuid
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.exc import IntegrityError

        from apps.compiler_worker.models import CompilationRequest

        job_id = _uuid.uuid4()
        request = CompilationRequest(
            service_name="test-svc",
            source_url="https://example.com/api.yaml",
            source_hash="sha256:abc",
            job_id=job_id,
        )

        existing_job = SimpleNamespace(id=job_id)

        fake_session = AsyncMock()
        # First get() returns None (pre-insert check), second returns existing_job (post-rollback)
        fake_session.get = AsyncMock(side_effect=[None, existing_job])
        fake_session.add = MagicMock()
        fake_session.commit = AsyncMock(
            side_effect=IntegrityError("dup", params=None, orig=Exception("duplicate key"))
        )
        fake_session.rollback = AsyncMock()

        async_ctx = AsyncMock()
        async_ctx.__aenter__ = AsyncMock(return_value=fake_session)
        async_ctx.__aexit__ = AsyncMock(return_value=False)

        fake_factory = MagicMock(return_value=async_ctx)

        store = SQLAlchemyCompilationJobStore(session_factory=fake_factory)

        result = await store.create_job(request)
        assert result == job_id
        fake_session.rollback.assert_awaited_once()
