"""Unit tests for apps/compiler_api/routes/compilations.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from apps.access_control.authn.models import TokenPrincipalResponse
from apps.compiler_api.models import CompilationCreateRequest, CompilationJobResponse
from apps.compiler_api.routes.compilations import (
    _format_sse_event,
    _not_found,
    create_compilation,
    get_compilation,
    list_compilations,
    retry_compilation,
    rollback_compilation,
    stream_compilation_events,
)
from apps.compiler_worker.models import CompilationStatus


def _caller(subject: str = "operator") -> TokenPrincipalResponse:
    return TokenPrincipalResponse(
        subject=subject,
        username=None,
        token_type="jwt",
        claims={"sub": subject},
    )


class TestNotFound:
    def test_creates_404_exception(self) -> None:
        job_id = uuid4()
        exc = _not_found(job_id)
        assert exc.status_code == 404
        assert str(job_id) in exc.detail
        assert "not found" in exc.detail


class TestFormatSseEvent:
    def test_formats_event_correctly(self) -> None:
        event_name = "test.event"
        payload = {"key": "value", "number": 42}

        result = _format_sse_event(event_name, payload)

        expected = 'event: test.event\ndata: {"key":"value","number":42}\n\n'
        assert result == expected


class TestCreateCompilation:
    async def test_successful_creation(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        mock_payload = MagicMock(spec=CompilationCreateRequest)
        mock_payload.created_by = "test-user"
        mock_payload.source_url = "https://example.com/spec.yaml"
        mock_payload.service_id = "test-service-id"
        mock_payload.service_name = "test-service"

        mock_workflow_request = MagicMock()
        mock_payload.to_workflow_request.return_value = mock_workflow_request

        mock_job = MagicMock(spec=CompilationJobResponse)
        mock_job.id = uuid4()
        mock_job.service_id = "test-service-id"
        mock_job.service_name = "test-service"

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.create_job.return_value = mock_job

            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit

            result = await create_compilation(mock_payload, mock_session, mock_dispatcher, caller)

            assert result == mock_job
            assert mock_workflow_request.job_id == mock_job.id

            mock_repo.create_job.assert_called_once_with(mock_workflow_request)
            mock_audit.append_entry.assert_called_once_with(
                actor="operator",
                action="compilation.triggered",
                resource="test-service-id",
                detail={
                    "job_id": str(mock_job.id),
                    "source_url": "https://example.com/spec.yaml",
                    "service_id": "test-service-id",
                    "service_name": "test-service",
                },
                commit=False,
            )
            mock_dispatcher.enqueue.assert_called_once_with(mock_workflow_request)

    async def test_dispatcher_failure_deletes_job(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        mock_dispatcher.enqueue.side_effect = Exception("Dispatch failed")
        caller = _caller("operator")

        mock_payload = MagicMock(spec=CompilationCreateRequest)
        mock_payload.created_by = None  # Test system actor
        mock_payload.source_url = "https://example.com/spec.yaml"
        mock_payload.service_id = None
        mock_payload.service_name = None  # Test fallback to job ID

        mock_workflow_request = MagicMock()
        mock_payload.to_workflow_request.return_value = mock_workflow_request

        mock_job = MagicMock(spec=CompilationJobResponse)
        mock_job.id = uuid4()
        mock_job.service_id = None
        mock_job.service_name = None

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.create_job.return_value = mock_job

            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit

            with pytest.raises(HTTPException) as exc_info:
                await create_compilation(mock_payload, mock_session, mock_dispatcher, caller)

            assert exc_info.value.status_code == 503
            assert exc_info.value.detail == "Compilation worker dispatch failed: Dispatch failed"

            # Verify job was deleted
            mock_repo.delete_job.assert_called_once_with(mock_job.id)

            # Verify audit entry was attempted with job ID as resource
            mock_audit.append_entry.assert_called_once_with(
                actor="operator",
                action="compilation.triggered",
                resource=str(mock_job.id),
                detail={
                    "job_id": str(mock_job.id),
                    "source_url": "https://example.com/spec.yaml",
                    "service_id": None,
                    "service_name": None,
                },
                commit=False,
            )


class TestGetCompilation:
    async def test_successful_get(self) -> None:
        mock_session = AsyncMock()
        job_id = uuid4()
        mock_job = MagicMock(spec=CompilationJobResponse)

        with patch(
            "apps.compiler_api.routes.compilations.CompilationRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = mock_job

            result = await get_compilation(job_id, mock_session)

            assert result == mock_job
            mock_repo.get_job.assert_called_once_with(job_id)

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        job_id = uuid4()

        with patch(
            "apps.compiler_api.routes.compilations.CompilationRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await get_compilation(job_id, mock_session)

            assert exc_info.value.status_code == 404


class TestListCompilations:
    async def test_returns_repository_jobs(self) -> None:
        mock_session = AsyncMock()
        mock_jobs = [
            MagicMock(spec=CompilationJobResponse),
            MagicMock(spec=CompilationJobResponse),
        ]

        with patch(
            "apps.compiler_api.routes.compilations.CompilationRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.list_jobs.return_value = mock_jobs

            result = await list_compilations(mock_session)

            assert result == mock_jobs
            mock_repo.list_jobs.assert_called_once_with()


class TestStreamCompilationEvents:
    async def test_job_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        mock_request = AsyncMock(spec=Request)
        job_id = uuid4()

        with patch(
            "apps.compiler_api.routes.compilations.CompilationRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await stream_compilation_events(job_id, mock_request, mock_session)

            assert exc_info.value.status_code == 404

    async def test_successful_streaming(self) -> None:
        mock_session = AsyncMock()
        mock_request = AsyncMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)
        job_id = uuid4()

        # Mock job and events
        mock_job = MagicMock()
        mock_job.status = CompilationStatus.SUCCEEDED.value

        mock_event = MagicMock()
        mock_event.sequence_number = 1
        mock_event.event_type = "stage.started"
        mock_event.model_dump.return_value = {"stage": "extract"}

        # Create a proper session factory mock that supports async context manager
        mock_session_factory = MagicMock()
        mock_session_instance = AsyncMock()

        # Mock the async context manager behavior
        async_context_mock = AsyncMock()
        async_context_mock.__aenter__.return_value = mock_session_instance
        async_context_mock.__aexit__.return_value = None
        mock_session_factory.return_value = async_context_mock

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.resolve_session_factory"
            ) as mock_resolve_factory,
            patch("asyncio.sleep", return_value=None),
        ):  # Mock sleep to avoid waiting
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo

            # Initial job check
            mock_repo.get_job.return_value = mock_job

            # Event polling setup
            mock_repo.list_events.return_value = [mock_event]

            mock_resolve_factory.return_value = mock_session_factory

            # Call the function
            response = await stream_compilation_events(job_id, mock_request, mock_session)

            assert isinstance(response, StreamingResponse)
            assert response.media_type == "text/event-stream"
            assert response.headers["Cache-Control"] == "no-cache"

            # Test the async generator by consuming it
            events = []
            async for event in response.body_iterator:
                events.append(event)
                break  # Only get the first event to avoid infinite loop

            assert len(events) == 1
            assert "event: stage.started" in events[0]
            assert '{"stage":"extract"}' in events[0]

    async def test_stream_stops_on_terminal_status(self) -> None:
        mock_session = AsyncMock()
        mock_request = AsyncMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)
        job_id = uuid4()

        mock_job = MagicMock()
        mock_job.status = CompilationStatus.SUCCEEDED.value

        # Create proper session factory mock
        mock_session_factory = MagicMock()
        mock_session_instance = AsyncMock()
        async_context_mock = AsyncMock()
        async_context_mock.__aenter__.return_value = mock_session_instance
        async_context_mock.__aexit__.return_value = None
        mock_session_factory.return_value = async_context_mock

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.resolve_session_factory"
            ) as mock_resolve_factory,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = mock_job
            mock_repo.list_events.return_value = []

            mock_resolve_factory.return_value = mock_session_factory

            response = await stream_compilation_events(job_id, mock_request, mock_session)

            # Consume the generator - should terminate due to terminal status
            events = []
            async for event in response.body_iterator:
                events.append(event)
                # Should terminate naturally without events since status is terminal
                if len(events) > 10:  # Safety break
                    break

            # Should have terminated without infinite loop
            assert len(events) == 0

    async def test_stream_stops_on_job_none(self) -> None:
        mock_session = AsyncMock()
        mock_request = AsyncMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)
        job_id = uuid4()

        # Create proper session factory mock
        mock_session_factory = MagicMock()
        mock_session_instance = AsyncMock()
        async_context_mock = AsyncMock()
        async_context_mock.__aenter__.return_value = mock_session_instance
        async_context_mock.__aexit__.return_value = None
        mock_session_factory.return_value = async_context_mock

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.resolve_session_factory"
            ) as mock_resolve_factory,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo

            # Initial check passes, then job becomes None in polling
            initial_job = MagicMock()
            initial_job.status = CompilationStatus.PENDING.value
            mock_repo.get_job.side_effect = [
                initial_job,
                None,
            ]  # First call returns job, second returns None
            mock_repo.list_events.return_value = []

            mock_resolve_factory.return_value = mock_session_factory

            response = await stream_compilation_events(job_id, mock_request, mock_session)

            # Consume the generator - should terminate when job becomes None
            events = []
            async for event in response.body_iterator:
                events.append(event)
                if len(events) > 10:  # Safety break
                    break

            assert len(events) == 0

    async def test_stream_emits_error_event_on_serialization_failure(self) -> None:
        mock_session = AsyncMock()
        mock_request = AsyncMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)
        job_id = uuid4()

        mock_job = MagicMock()
        mock_job.status = CompilationStatus.SUCCEEDED.value

        mock_event = MagicMock()
        mock_event.sequence_number = 1
        mock_event.event_type = "stage.started"
        mock_event.model_dump.return_value = {"detail": object()}

        mock_session_factory = MagicMock()
        mock_session_instance = AsyncMock()
        async_context_mock = AsyncMock()
        async_context_mock.__aenter__.return_value = mock_session_instance
        async_context_mock.__aexit__.return_value = None
        mock_session_factory.return_value = async_context_mock

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.resolve_session_factory"
            ) as mock_resolve_factory,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = mock_job
            mock_repo.list_events.return_value = [mock_event]
            mock_resolve_factory.return_value = mock_session_factory

            response = await stream_compilation_events(job_id, mock_request, mock_session)

            events = []
            async for event in response.body_iterator:
                events.append(event)

            assert events == [
                'event: stream.error\ndata: {"message":"Failed to serialize compilation event '
                'stage.started: Object of type object is not JSON serializable"}\n\n'
            ]

    async def test_stream_stops_on_disconnect(self) -> None:
        mock_session = AsyncMock()
        mock_request = AsyncMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=True)
        job_id = uuid4()

        mock_job = MagicMock()
        mock_job.status = CompilationStatus.PENDING.value

        # Create proper session factory mock
        mock_session_factory = MagicMock()
        mock_session_instance = AsyncMock()
        async_context_mock = AsyncMock()
        async_context_mock.__aenter__.return_value = mock_session_instance
        async_context_mock.__aexit__.return_value = None
        mock_session_factory.return_value = async_context_mock

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.resolve_session_factory"
            ) as mock_resolve_factory,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = mock_job
            mock_repo.list_events.return_value = []

            mock_resolve_factory.return_value = mock_session_factory

            response = await stream_compilation_events(job_id, mock_request, mock_session)

            # Consume the generator - should terminate due to disconnection
            events = []
            async for event in response.body_iterator:
                events.append(event)
                if len(events) > 10:  # Safety break
                    break

            assert len(events) == 0


class TestRetryCompilation:
    """BUG-100: POST /api/v1/compilations/{jobId}/retry must exist."""

    async def test_retry_creates_new_job(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")
        original_id = uuid4()

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.source_url = "https://example.com/spec.yaml"
        original_job.source_hash = "abc123"
        original_job.created_by = "alice"
        original_job.service_id = "pet-store"
        original_job.service_name = "pet-store"
        original_job.options = {"force_protocol": "openapi"}
        original_job.id = original_id

        new_job = MagicMock(spec=CompilationJobResponse)
        new_job.id = uuid4()
        new_job.service_name = "pet-store"

        with (
            patch(
                "apps.compiler_api.routes.compilations.CompilationRepository"
            ) as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.AuditLogService"
            ) as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job
            mock_repo.create_job.return_value = new_job

            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit

            result = await retry_compilation(
                original_id, "extract", mock_session, mock_dispatcher, caller
            )

            assert result == new_job
            mock_dispatcher.enqueue.assert_called_once()
            mock_audit.append_entry.assert_called_once()
            audit_call = mock_audit.append_entry.call_args
            assert audit_call.kwargs["action"] == "compilation.retried"

    async def test_retry_not_found(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        with patch(
            "apps.compiler_api.routes.compilations.CompilationRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await retry_compilation(
                    uuid4(), None, mock_session, mock_dispatcher, caller
                )
            assert exc_info.value.status_code == 404

    async def test_retry_includes_from_stage(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.source_url = "https://example.com/spec.yaml"
        original_job.source_hash = None
        original_job.created_by = "bob"
        original_job.service_id = "svc"
        original_job.service_name = "svc"
        original_job.options = {
            "__compiler_resume_checkpoint": {
                "payload": {
                    "service_ir": {"service_name": "svc"},
                    "source_url": "https://example.com/spec.yaml",
                },
                "protocol": "openapi",
                "service_name": "svc",
                "completed_stage": "enhance",
            }
        }
        original_job.id = uuid4()

        new_job = MagicMock(spec=CompilationJobResponse)
        new_job.id = uuid4()
        new_job.service_name = "svc"

        with (
            patch(
                "apps.compiler_api.routes.compilations.CompilationRepository"
            ) as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.AuditLogService"
            ) as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job
            mock_repo.create_job.return_value = new_job

            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit

            await retry_compilation(
                original_job.id,
                "validate_ir",
                mock_session,
                mock_dispatcher,
                caller,
            )

            created_req = mock_repo.create_job.call_args[0][0]
            assert created_req.options["from_stage"] == "validate_ir"

    async def test_retry_restores_inline_source_content_and_filename(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.source_url = None
        original_job.source_hash = "sha256:abc"
        original_job.created_by = "alice"
        original_job.service_id = None
        original_job.service_name = "Billing API"
        original_job.options = {
            "__compiler_request_replay": {
                "source_content": "openapi: 3.0.0",
                "filename": "billing.yaml",
                "service_id": "billing-api",
            }
        }
        original_job.id = uuid4()

        new_job = MagicMock(spec=CompilationJobResponse)
        new_job.id = uuid4()
        new_job.service_name = "Billing API"

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job
            mock_repo.create_job.return_value = new_job

            mock_audit_class.return_value = AsyncMock()

            await retry_compilation(
                original_job.id,
                None,
                mock_session,
                mock_dispatcher,
                caller,
            )

            created_req = mock_repo.create_job.call_args[0][0]
            assert created_req.source_content == "openapi: 3.0.0"
            assert created_req.filename == "billing.yaml"
            assert created_req.service_id == "billing-api"

    async def test_retry_dispatcher_failure_deletes_job(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        mock_dispatcher.enqueue.side_effect = Exception("Dispatch failed")
        caller = _caller("operator")
        original_id = uuid4()

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.source_url = "https://example.com/spec.yaml"
        original_job.source_hash = "abc123"
        original_job.created_by = "alice"
        original_job.service_id = "pet-store"
        original_job.service_name = "pet-store"
        original_job.options = {}
        original_job.id = original_id

        new_job = MagicMock(spec=CompilationJobResponse)
        new_job.id = uuid4()
        new_job.service_name = "pet-store"

        with (
            patch(
                "apps.compiler_api.routes.compilations.CompilationRepository"
            ) as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.AuditLogService"
            ) as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job
            mock_repo.create_job.return_value = new_job

            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit

            with pytest.raises(HTTPException) as exc_info:
                await retry_compilation(
                    original_id, None, mock_session, mock_dispatcher, caller
                )

            assert exc_info.value.status_code == 503
            assert "Dispatch failed" in exc_info.value.detail
            mock_repo.delete_job.assert_called_once_with(new_job.id)

    async def test_retry_rejects_unknown_from_stage(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.id = uuid4()
        original_job.options = {}

        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job

            with pytest.raises(HTTPException) as exc_info:
                await retry_compilation(
                    original_job.id,
                    "not-a-stage",
                    mock_session,
                    mock_dispatcher,
                    caller,
                )

        assert exc_info.value.status_code == 422

    async def test_retry_rejects_missing_resume_checkpoint(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.id = uuid4()
        original_job.options = {}

        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job

            with pytest.raises(HTTPException) as exc_info:
                await retry_compilation(
                    original_job.id,
                    "validate_ir",
                    mock_session,
                    mock_dispatcher,
                    caller,
                )

        assert exc_info.value.status_code == 409


class TestRollbackCompilation:
    """BUG-101: POST /api/v1/compilations/{jobId}/rollback."""

    async def test_rollback_creates_new_job(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")
        original_id = uuid4()

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.source_url = "https://example.com/spec.yaml"
        original_job.source_hash = "abc"
        original_job.created_by = "alice"
        original_job.service_id = "pet-store"
        original_job.service_name = "pet-store"
        original_job.options = {
            "__compiler_resume_checkpoint": {
                "payload": {"registered_version": 2},
                "protocol": "openapi",
                "service_name": "pet-store",
                "completed_stage": "register",
            }
        }
        original_job.tenant = None
        original_job.environment = None
        original_job.id = original_id
        original_job.status = CompilationStatus.SUCCEEDED.value

        new_job = MagicMock(spec=CompilationJobResponse)
        new_job.id = uuid4()
        new_job.service_name = "pet-store"

        with (
            patch(
                "apps.compiler_api.routes.compilations.CompilationRepository"
            ) as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.ArtifactRegistryRepository"
            ) as mock_artifact_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.AuditLogService"
            ) as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job
            mock_repo.list_events.return_value = []
            mock_repo.create_job.return_value = new_job
            mock_artifact_repo = AsyncMock()
            mock_artifact_repo_class.return_value = mock_artifact_repo
            active_version = MagicMock()
            active_version.version_number = 2
            previous_version = MagicMock()
            previous_version.version_number = 1
            mock_artifact_repo.get_active_version.return_value = active_version
            mock_artifact_repo.list_versions.return_value = MagicMock(
                versions=[active_version, previous_version]
            )

            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit

            result = await rollback_compilation(
                original_id, mock_session, mock_dispatcher, caller
            )

            assert result == new_job
            mock_dispatcher.enqueue.assert_called_once()
            created_req = mock_repo.create_job.call_args[0][0]
            assert created_req.options["__compiler_rollback_request"]["target_version"] == 1
            audit_kw = mock_audit.append_entry.call_args.kwargs
            assert audit_kw["action"] == "compilation.rollback_requested"
            assert audit_kw["detail"]["target_version"] == 1

    async def test_rollback_restores_inline_source_content_and_filename(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")
        original_id = uuid4()

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.source_url = None
        original_job.source_hash = "sha256:abc"
        original_job.created_by = "alice"
        original_job.service_id = None
        original_job.service_name = "Billing API"
        original_job.options = {
            "__compiler_request_replay": {
                "source_content": "openapi: 3.0.0",
                "filename": "billing.yaml",
                "service_id": "billing-api",
            },
            "__compiler_resume_checkpoint": {
                "payload": {"registered_version": 3},
                "protocol": "openapi",
                "service_name": "Billing API",
                "completed_stage": "register",
            },
        }
        original_job.tenant = None
        original_job.environment = None
        original_job.id = original_id
        original_job.status = CompilationStatus.SUCCEEDED.value

        new_job = MagicMock(spec=CompilationJobResponse)
        new_job.id = uuid4()
        new_job.service_name = "Billing API"

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.ArtifactRegistryRepository"
            ) as mock_artifact_repo_class,
            patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job
            mock_repo.list_events.return_value = []
            mock_repo.create_job.return_value = new_job
            mock_artifact_repo = AsyncMock()
            mock_artifact_repo_class.return_value = mock_artifact_repo
            active_version = MagicMock()
            active_version.version_number = 3
            previous_version = MagicMock()
            previous_version.version_number = 2
            mock_artifact_repo.get_active_version.return_value = active_version
            mock_artifact_repo.list_versions.return_value = MagicMock(
                versions=[active_version, previous_version]
            )

            mock_audit_class.return_value = AsyncMock()

            await rollback_compilation(
                original_id,
                mock_session,
                mock_dispatcher,
                caller,
            )

            created_req = mock_repo.create_job.call_args[0][0]
            assert created_req.source_content == "openapi: 3.0.0"
            assert created_req.filename == "billing.yaml"
            assert created_req.service_id == "billing-api"
            assert created_req.options["__compiler_rollback_request"]["target_version"] == 2

    async def test_rollback_not_found(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        with patch(
            "apps.compiler_api.routes.compilations.CompilationRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                await rollback_compilation(
                    uuid4(), mock_session, mock_dispatcher, caller
                )
            assert exc_info.value.status_code == 404

    async def test_rollback_rejects_non_succeeded(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.status = CompilationStatus.FAILED.value

        with patch(
            "apps.compiler_api.routes.compilations.CompilationRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job

            with pytest.raises(HTTPException) as exc_info:
                await rollback_compilation(
                    uuid4(), mock_session, mock_dispatcher, caller
                )
            assert exc_info.value.status_code == 409
            assert "succeeded" in exc_info.value.detail

    async def test_rollback_rejects_non_active_deployment(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        caller = _caller("operator")

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.id = uuid4()
        original_job.status = CompilationStatus.SUCCEEDED.value
        original_job.service_id = "pet-store"
        original_job.service_name = "pet-store"
        original_job.tenant = None
        original_job.environment = None
        original_job.options = {
            "__compiler_resume_checkpoint": {
                "payload": {"registered_version": 1},
                "protocol": "openapi",
                "service_name": "pet-store",
                "completed_stage": "register",
            }
        }

        with (
            patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.ArtifactRegistryRepository"
            ) as mock_artifact_repo_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job
            mock_repo.list_events.return_value = []
            mock_artifact_repo = AsyncMock()
            mock_artifact_repo_class.return_value = mock_artifact_repo
            active_version = MagicMock()
            active_version.version_number = 2
            mock_artifact_repo.get_active_version.return_value = active_version

            with pytest.raises(HTTPException) as exc_info:
                await rollback_compilation(
                    uuid4(), mock_session, mock_dispatcher, caller
                )
            assert exc_info.value.status_code == 409
            assert "active deployment" in exc_info.value.detail

    async def test_rollback_dispatcher_failure_deletes_job(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        mock_dispatcher.enqueue.side_effect = Exception("Dispatch failed")
        caller = _caller("operator")
        original_id = uuid4()

        original_job = MagicMock(spec=CompilationJobResponse)
        original_job.source_url = "https://example.com/spec.yaml"
        original_job.source_hash = "abc"
        original_job.created_by = "alice"
        original_job.service_id = "pet-store"
        original_job.service_name = "pet-store"
        original_job.options = {
            "__compiler_resume_checkpoint": {
                "payload": {"registered_version": 2},
                "protocol": "openapi",
                "service_name": "pet-store",
                "completed_stage": "register",
            }
        }
        original_job.tenant = None
        original_job.environment = None
        original_job.id = original_id
        original_job.status = CompilationStatus.SUCCEEDED.value

        new_job = MagicMock(spec=CompilationJobResponse)
        new_job.id = uuid4()
        new_job.service_name = "pet-store"

        with (
            patch(
                "apps.compiler_api.routes.compilations.CompilationRepository"
            ) as mock_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.ArtifactRegistryRepository"
            ) as mock_artifact_repo_class,
            patch(
                "apps.compiler_api.routes.compilations.AuditLogService"
            ) as mock_audit_class,
        ):
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = original_job
            mock_repo.list_events.return_value = []
            mock_repo.create_job.return_value = new_job
            mock_artifact_repo = AsyncMock()
            mock_artifact_repo_class.return_value = mock_artifact_repo
            active_version = MagicMock()
            active_version.version_number = 2
            previous_version = MagicMock()
            previous_version.version_number = 1
            mock_artifact_repo.get_active_version.return_value = active_version
            mock_artifact_repo.list_versions.return_value = MagicMock(
                versions=[active_version, previous_version]
            )

            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit

            with pytest.raises(HTTPException) as exc_info:
                await rollback_compilation(
                    original_id, mock_session, mock_dispatcher, caller
                )

            assert exc_info.value.status_code == 503
            assert "Dispatch failed" in exc_info.value.detail
            mock_repo.delete_job.assert_called_once_with(new_job.id)
