"""Unit tests to improve coverage of apps/compiler_api/repository.py.

Covers async methods (get_job found, list_jobs, list_events, get_service),
DTO edge cases, and update_version field-mapping branches.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from apps.compiler_api.repository import (
    ArtifactRegistryRepository,
    CompilationRepository,
    ServiceCatalogRepository,
)
from libs.db_models import CompilationJob
from libs.ir.models import ServiceIR


def _utcnow() -> datetime:
    return datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC)


def _minimal_ir_json() -> dict[str, Any]:
    return ServiceIR(
        service_id="test-svc",
        service_name="Test Service",
        base_url="https://example.com",
        source_hash="sha256:abc",
        protocol="openapi",
        operations=[],
    ).model_dump(mode="json")


def _fake_job(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source_url": "https://example.com/api.yaml",
        "source_hash": "sha256:abc",
        "protocol": "openapi",
        "status": "completed",
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


def _fake_service_version(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "service_id": "test-svc",
        "version_number": 1,
        "is_active": True,
        "ir_json": _minimal_ir_json(),
        "raw_ir_json": None,
        "compiler_version": "0.1.0",
        "source_url": "https://example.com/api.yaml",
        "source_hash": "sha256:abc",
        "protocol": "openapi",
        "validation_report": None,
        "deployment_revision": "rev-1",
        "route_config": None,
        "tenant": None,
        "environment": None,
        "created_at": _utcnow(),
        "artifacts": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_artifact(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "artifact_type": "image",
        "content_hash": "sha256:img",
        "storage_path": "/artifacts/image.tar",
        "metadata_json": {"tag": "latest"},
        "created_at": _utcnow(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# CompilationRepository: get_job (found), list_jobs, list_events
# ---------------------------------------------------------------------------


class TestGetJobFound:
    """Cover line 74: get_job returns a response when job exists."""

    async def test_returns_job_response(self) -> None:
        fake = _fake_job()
        mock_session = AsyncMock()
        mock_session.get.return_value = fake

        repo = CompilationRepository(mock_session)
        result = await repo.get_job(fake.id)

        assert result is not None
        assert result.id == fake.id
        assert result.status == "completed"
        mock_session.get.assert_called_once_with(CompilationJob, fake.id)


class TestListJobs:
    """Cover lines 77-80: list_jobs returns mapped results."""

    async def test_empty_list(self) -> None:
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.scalars.return_value = mock_result

        repo = CompilationRepository(mock_session)
        result = await repo.list_jobs()

        assert result == []

    async def test_multiple_jobs(self) -> None:
        jobs = [_fake_job(status="pending"), _fake_job(status="completed")]
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = jobs
        mock_session.scalars.return_value = mock_result

        repo = CompilationRepository(mock_session)
        result = await repo.list_jobs(limit=10)

        assert len(result) == 2
        assert result[0].status == "pending"
        assert result[1].status == "completed"

    async def test_jobs_with_none_optional_fields(self) -> None:
        job = _fake_job(
            source_url=None,
            source_hash=None,
            protocol=None,
            current_stage=None,
            error_detail=None,
            options=None,
            created_by=None,
            service_name=None,
        )
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [job]
        mock_session.scalars.return_value = mock_result

        repo = CompilationRepository(mock_session)
        result = await repo.list_jobs()

        assert len(result) == 1
        r = result[0]
        assert r.source_url is None
        assert r.source_hash is None
        assert r.protocol is None
        assert r.current_stage is None
        assert r.options is None
        assert r.created_by is None
        assert r.service_name is None


class TestListEvents:
    """Cover lines 88-94: list_events returns mapped event results."""

    async def test_empty_events(self) -> None:
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.scalars.return_value = mock_result

        repo = CompilationRepository(mock_session)
        result = await repo.list_events(uuid.uuid4())

        assert result == []

    async def test_multiple_events(self) -> None:
        job_id = uuid.uuid4()
        events = [
            _fake_event(job_id=job_id, sequence_number=1, event_type="stage.started"),
            _fake_event(job_id=job_id, sequence_number=2, event_type="stage.succeeded"),
        ]
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = events
        mock_session.scalars.return_value = mock_result

        repo = CompilationRepository(mock_session)
        result = await repo.list_events(job_id, after_sequence=0)

        assert len(result) == 2
        assert result[0].event_type == "stage.started"
        assert result[1].event_type == "stage.succeeded"

    async def test_events_with_error_detail(self) -> None:
        event = _fake_event(
            event_type="stage.failed",
            error_detail="Connection timed out",
            detail={"stage": "deploy", "reason": "timeout"},
        )
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [event]
        mock_session.scalars.return_value = mock_result

        repo = CompilationRepository(mock_session)
        result = await repo.list_events(event.job_id)

        assert len(result) == 1
        assert result[0].error_detail == "Connection timed out"
        assert result[0].detail == {"stage": "deploy", "reason": "timeout"}

    async def test_events_with_none_optional_fields(self) -> None:
        event = _fake_event(stage=None, attempt=None, detail=None, error_detail=None)
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [event]
        mock_session.scalars.return_value = mock_result

        repo = CompilationRepository(mock_session)
        result = await repo.list_events(event.job_id)

        assert len(result) == 1
        assert result[0].stage is None
        assert result[0].attempt is None
        assert result[0].detail is None
        assert result[0].error_detail is None


# ---------------------------------------------------------------------------
# ServiceCatalogRepository: get_service with filters and not-found
# ---------------------------------------------------------------------------


class TestGetService:
    """Cover lines 159-175: get_service with tenant/environment filters."""

    async def test_not_found_returns_none(self) -> None:
        mock_session = AsyncMock()
        mock_session.scalar.return_value = None

        repo = ServiceCatalogRepository(mock_session)
        result = await repo.get_service("nonexistent")

        assert result is None

    async def test_found_returns_summary(self) -> None:
        version = _fake_service_version()
        mock_session = AsyncMock()
        mock_session.scalar.return_value = version

        repo = ServiceCatalogRepository(mock_session)
        result = await repo.get_service("test-svc")

        assert result is not None
        assert result.service_id == "test-svc"
        assert result.service_name == "Test Service"
        assert result.tool_count == 0

    async def test_with_tenant_filter(self) -> None:
        mock_session = AsyncMock()
        mock_session.scalar.return_value = None

        repo = ServiceCatalogRepository(mock_session)
        result = await repo.get_service("svc", tenant="acme")

        assert result is None
        mock_session.scalar.assert_called_once()

    async def test_with_environment_filter(self) -> None:
        mock_session = AsyncMock()
        mock_session.scalar.return_value = None

        repo = ServiceCatalogRepository(mock_session)
        result = await repo.get_service("svc", environment="staging")

        assert result is None
        mock_session.scalar.assert_called_once()

    async def test_with_both_filters(self) -> None:
        version = _fake_service_version(tenant="acme", environment="prod")
        mock_session = AsyncMock()
        mock_session.scalar.return_value = version

        repo = ServiceCatalogRepository(mock_session)
        result = await repo.get_service("test-svc", tenant="acme", environment="prod")

        assert result is not None
        assert result.service_id == "test-svc"


# ---------------------------------------------------------------------------
# ArtifactRegistryRepository: update_version field-mapping branches
# ---------------------------------------------------------------------------


class TestUpdateVersionFields:
    """Cover lines 292-319: update_version applies individual field updates."""

    async def test_update_single_field(self) -> None:
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.id = uuid.uuid4()
        mock_record.service_id = "svc"
        mock_record.version_number = 1
        mock_record.is_active = True
        mock_record.ir_json = _minimal_ir_json()
        mock_record.raw_ir_json = None
        mock_record.compiler_version = "0.1.0"
        mock_record.source_url = None
        mock_record.source_hash = None
        mock_record.protocol = None
        mock_record.validation_report = None
        mock_record.deployment_revision = None
        mock_record.route_config = None
        mock_record.tenant = None
        mock_record.environment = None
        mock_record.created_at = _utcnow()
        mock_record.artifacts = []

        payload = MagicMock()
        payload.ir_json = None
        payload.raw_ir_json = None
        payload.compiler_version = "0.2.0"
        payload.source_url = None
        payload.source_hash = None
        payload.protocol = None
        payload.validation_report = None
        payload.deployment_revision = None
        payload.route_config = None
        payload.tenant = None
        payload.environment = None
        payload.artifacts = None

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
        ):
            repo = ArtifactRegistryRepository(mock_session)
            result = await repo.update_version("svc", 1, payload)

        assert result is not None
        assert mock_record.compiler_version == "0.2.0"

    async def test_update_all_simple_fields(self) -> None:
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.id = uuid.uuid4()
        mock_record.service_id = "svc"
        mock_record.version_number = 1
        mock_record.is_active = True
        mock_record.ir_json = _minimal_ir_json()
        mock_record.raw_ir_json = None
        mock_record.compiler_version = "0.1.0"
        mock_record.source_url = None
        mock_record.source_hash = None
        mock_record.protocol = None
        mock_record.validation_report = None
        mock_record.deployment_revision = None
        mock_record.route_config = None
        mock_record.tenant = None
        mock_record.environment = None
        mock_record.created_at = _utcnow()
        mock_record.artifacts = []

        payload = MagicMock()
        payload.ir_json = None
        payload.raw_ir_json = None
        payload.compiler_version = "1.0.0"
        payload.source_url = "https://new.example.com"
        payload.source_hash = "sha256:new"
        payload.protocol = "grpc"
        payload.validation_report = {"valid": True}
        payload.deployment_revision = "rev-99"
        payload.route_config = {"prefix": "/v2"}
        payload.tenant = "acme"
        payload.environment = "prod"
        payload.artifacts = None

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
        ):
            repo = ArtifactRegistryRepository(mock_session)
            result = await repo.update_version("svc", 1, payload)

        assert result is not None
        assert mock_record.compiler_version == "1.0.0"
        assert mock_record.source_url == "https://new.example.com"
        assert mock_record.source_hash == "sha256:new"
        assert mock_record.protocol == "grpc"
        assert mock_record.validation_report == {"valid": True}
        assert mock_record.deployment_revision == "rev-99"
        assert mock_record.route_config == {"prefix": "/v2"}
        assert mock_record.tenant == "acme"
        assert mock_record.environment == "prod"

    async def test_update_ir_json_fields(self) -> None:
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.id = uuid.uuid4()
        mock_record.service_id = "svc"
        mock_record.version_number = 1
        mock_record.is_active = True
        mock_record.ir_json = _minimal_ir_json()
        mock_record.raw_ir_json = None
        mock_record.compiler_version = "0.1.0"
        mock_record.source_url = None
        mock_record.source_hash = None
        mock_record.protocol = None
        mock_record.validation_report = None
        mock_record.deployment_revision = None
        mock_record.route_config = None
        mock_record.tenant = None
        mock_record.environment = None
        mock_record.created_at = _utcnow()
        mock_record.artifacts = []

        new_ir = _minimal_ir_json()
        payload = MagicMock()
        payload.ir_json = new_ir
        payload.raw_ir_json = new_ir
        payload.compiler_version = None
        payload.source_url = None
        payload.source_hash = None
        payload.protocol = None
        payload.validation_report = None
        payload.deployment_revision = None
        payload.route_config = None
        payload.tenant = None
        payload.environment = None
        payload.artifacts = None

        with patch.object(
            ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
        ):
            repo = ArtifactRegistryRepository(mock_session)
            result = await repo.update_version("svc", 1, payload)

        assert result is not None
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once_with(mock_record)

    async def test_update_with_artifacts_replacement(self) -> None:
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.id = uuid.uuid4()
        mock_record.service_id = "svc"
        mock_record.version_number = 1
        mock_record.is_active = True
        mock_record.ir_json = _minimal_ir_json()
        mock_record.raw_ir_json = None
        mock_record.compiler_version = "0.1.0"
        mock_record.source_url = None
        mock_record.source_hash = None
        mock_record.protocol = None
        mock_record.validation_report = None
        mock_record.deployment_revision = None
        mock_record.route_config = None
        mock_record.tenant = None
        mock_record.environment = None
        mock_record.created_at = _utcnow()
        mock_record.artifacts = []

        new_artifacts = [MagicMock()]
        payload = MagicMock()
        payload.ir_json = None
        payload.raw_ir_json = None
        payload.compiler_version = None
        payload.source_url = None
        payload.source_hash = None
        payload.protocol = None
        payload.validation_report = None
        payload.deployment_revision = None
        payload.route_config = None
        payload.tenant = None
        payload.environment = None
        payload.artifacts = new_artifacts

        with (
            patch.object(
                ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
            ),
            patch.object(
                ArtifactRegistryRepository, "_replace_artifacts", new_callable=AsyncMock
            ) as mock_replace,
        ):
            repo = ArtifactRegistryRepository(mock_session)
            await repo.update_version("svc", 1, payload)

        mock_replace.assert_called_once_with(mock_record.id, new_artifacts)


# ---------------------------------------------------------------------------
# ArtifactRegistryRepository: activate_version success path
# ---------------------------------------------------------------------------


class TestActivateVersionSuccess:
    """Cover lines 332-338: activate_version when record exists."""

    async def test_activate_sets_is_active(self) -> None:
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.id = uuid.uuid4()
        mock_record.service_id = "svc"
        mock_record.version_number = 1
        mock_record.is_active = False
        mock_record.ir_json = _minimal_ir_json()
        mock_record.raw_ir_json = None
        mock_record.compiler_version = "0.1.0"
        mock_record.source_url = None
        mock_record.source_hash = None
        mock_record.protocol = None
        mock_record.validation_report = None
        mock_record.deployment_revision = None
        mock_record.route_config = None
        mock_record.tenant = None
        mock_record.environment = None
        mock_record.created_at = _utcnow()
        mock_record.artifacts = []

        with (
            patch.object(
                ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
            ),
            patch.object(
                ArtifactRegistryRepository, "_deactivate_service_versions", new_callable=AsyncMock
            ) as mock_deactivate,
        ):
            repo = ArtifactRegistryRepository(mock_session)
            result = await repo.activate_version("svc", 1)

        assert result is not None
        assert mock_record.is_active is True
        mock_deactivate.assert_called_once_with(
            "svc", tenant=None, environment=None,
        )
        mock_session.flush.assert_called_once()
        mock_session.commit.assert_called_once()

    async def test_activate_without_commit(self) -> None:
        mock_session = AsyncMock()
        mock_record = MagicMock()
        mock_record.id = uuid.uuid4()
        mock_record.service_id = "svc"
        mock_record.version_number = 2
        mock_record.is_active = False
        mock_record.ir_json = _minimal_ir_json()
        mock_record.raw_ir_json = None
        mock_record.compiler_version = "0.1.0"
        mock_record.source_url = None
        mock_record.source_hash = None
        mock_record.protocol = None
        mock_record.validation_report = None
        mock_record.deployment_revision = None
        mock_record.route_config = None
        mock_record.tenant = None
        mock_record.environment = None
        mock_record.created_at = _utcnow()
        mock_record.artifacts = []

        with (
            patch.object(
                ArtifactRegistryRepository, "_get_version_record", return_value=mock_record
            ),
            patch.object(
                ArtifactRegistryRepository, "_deactivate_service_versions", new_callable=AsyncMock
            ),
        ):
            repo = ArtifactRegistryRepository(mock_session)
            result = await repo.activate_version("svc", 2, commit=False)

        assert result is not None
        mock_session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# DTO edge cases: _to_response with fully-populated optional fields
# ---------------------------------------------------------------------------


class TestToResponseEdgeCases:
    """Cover additional branches for _to_response with populated optional fields."""

    def test_with_all_optional_fields_populated(self) -> None:
        art = _fake_artifact(storage_path=None, metadata_json=None)
        version = _fake_service_version(
            raw_ir_json=_minimal_ir_json(),
            validation_report={"errors": [], "warnings": ["deprecated"]},
            route_config={"prefix": "/v1"},
            tenant="acme",
            environment="prod",
            artifacts=[art],
        )
        result = ArtifactRegistryRepository._to_response(version)  # type: ignore[arg-type]

        assert result.raw_ir_json is not None
        assert result.validation_report == {"errors": [], "warnings": ["deprecated"]}
        assert result.route_config == {"prefix": "/v1"}
        assert result.tenant == "acme"
        assert result.environment == "prod"
        assert len(result.artifacts) == 1
        assert result.artifacts[0].storage_path is None
        assert result.artifacts[0].metadata_json is None

    def test_with_multiple_artifacts(self) -> None:
        arts = [
            _fake_artifact(artifact_type="image"),
            _fake_artifact(artifact_type="manifest", content_hash="sha256:mfst"),
            _fake_artifact(artifact_type="ir", storage_path=None),
        ]
        version = _fake_service_version(artifacts=arts)
        result = ArtifactRegistryRepository._to_response(version)  # type: ignore[arg-type]

        assert len(result.artifacts) == 3
        assert result.artifacts[0].artifact_type == "image"
        assert result.artifacts[1].artifact_type == "manifest"
        assert result.artifacts[2].artifact_type == "ir"
        assert result.artifacts[2].storage_path is None


class TestToServiceSummaryEdgeCases:
    """Cover _to_service_summary with various protocol/field combinations."""

    def test_version_protocol_overrides_ir(self) -> None:
        version = _fake_service_version(protocol="grpc")
        result = ServiceCatalogRepository._to_service_summary(version)  # type: ignore[arg-type]
        assert result.protocol == "grpc"

    def test_tenant_and_environment_populated(self) -> None:
        version = _fake_service_version(
            tenant="acme",
            environment="staging",
            deployment_revision="deploy-42",
        )
        result = ServiceCatalogRepository._to_service_summary(version)  # type: ignore[arg-type]
        assert result.tenant == "acme"
        assert result.environment == "staging"
        assert result.deployment_revision == "deploy-42"


class TestToEventResponseEdgeCases:
    """Cover _to_event_response with error details and populated metadata."""

    def test_event_with_error_detail(self) -> None:
        event = _fake_event(
            event_type="stage.failed",
            error_detail="timeout after 30s",
            attempt=3,
        )
        result = CompilationRepository._to_event_response(event)  # type: ignore[arg-type]
        assert result.error_detail == "timeout after 30s"
        assert result.attempt == 3
        assert result.event_type == "stage.failed"

    def test_event_with_empty_detail(self) -> None:
        event = _fake_event(detail={})
        result = CompilationRepository._to_event_response(event)  # type: ignore[arg-type]
        assert result.detail == {}
