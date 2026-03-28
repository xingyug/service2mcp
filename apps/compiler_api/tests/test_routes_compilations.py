"""Unit tests for apps/compiler_api/routes/compilations.py."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from apps.compiler_api.models import CompilationCreateRequest, CompilationJobResponse
from apps.compiler_api.routes.compilations import (
    _format_sse_event,
    _not_found,
    create_compilation,
    get_compilation,
    stream_compilation_events,
)
from apps.compiler_worker.models import CompilationStatus


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
        
        mock_payload = MagicMock(spec=CompilationCreateRequest)
        mock_payload.created_by = "test-user"
        mock_payload.source_url = "https://example.com/spec.yaml"
        mock_payload.service_name = "test-service"
        
        mock_workflow_request = MagicMock()
        mock_payload.to_workflow_request.return_value = mock_workflow_request
        
        mock_job = MagicMock(spec=CompilationJobResponse)
        mock_job.id = uuid4()
        mock_job.service_name = "test-service"
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class, \
             patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit_class:
            
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.create_job.return_value = mock_job
            
            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit
            
            result = await create_compilation(mock_payload, mock_session, mock_dispatcher)
            
            assert result == mock_job
            assert mock_workflow_request.job_id == mock_job.id
            
            mock_repo.create_job.assert_called_once_with(mock_workflow_request)
            mock_audit.append_entry.assert_called_once_with(
                actor="test-user",
                action="compilation.triggered",
                resource="test-service",
                detail={
                    "job_id": str(mock_job.id),
                    "source_url": "https://example.com/spec.yaml",
                    "service_name": "test-service",
                },
            )
            mock_dispatcher.enqueue.assert_called_once_with(mock_workflow_request)

    async def test_dispatcher_failure_deletes_job(self) -> None:
        mock_session = AsyncMock()
        mock_dispatcher = AsyncMock()
        mock_dispatcher.enqueue.side_effect = Exception("Dispatch failed")
        
        mock_payload = MagicMock(spec=CompilationCreateRequest)
        mock_payload.created_by = None  # Test system actor
        mock_payload.source_url = "https://example.com/spec.yaml"
        mock_payload.service_name = None  # Test fallback to job ID
        
        mock_workflow_request = MagicMock()
        mock_payload.to_workflow_request.return_value = mock_workflow_request
        
        mock_job = MagicMock(spec=CompilationJobResponse)
        mock_job.id = uuid4()
        mock_job.service_name = None
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class, \
             patch("apps.compiler_api.routes.compilations.AuditLogService") as mock_audit_class:
            
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.create_job.return_value = mock_job
            
            mock_audit = AsyncMock()
            mock_audit_class.return_value = mock_audit
            
            with pytest.raises(HTTPException) as exc_info:
                await create_compilation(mock_payload, mock_session, mock_dispatcher)
            
            assert exc_info.value.status_code == 503
            assert "dispatch failed" in exc_info.value.detail.lower()
            
            # Verify job was deleted
            mock_repo.delete_job.assert_called_once_with(mock_job.id)
            
            # Verify audit entry was attempted with job ID as resource
            mock_audit.append_entry.assert_called_once_with(
                actor="system",
                action="compilation.triggered", 
                resource=str(mock_job.id),
                detail={
                    "job_id": str(mock_job.id),
                    "source_url": "https://example.com/spec.yaml",
                    "service_name": None,
                },
            )


class TestGetCompilation:
    async def test_successful_get(self) -> None:
        mock_session = AsyncMock()
        job_id = uuid4()
        mock_job = MagicMock(spec=CompilationJobResponse)
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = mock_job
            
            result = await get_compilation(job_id, mock_session)
            
            assert result == mock_job
            mock_repo.get_job.assert_called_once_with(job_id)

    async def test_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        job_id = uuid4()
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_job.return_value = None
            
            with pytest.raises(HTTPException) as exc_info:
                await get_compilation(job_id, mock_session)
            
            assert exc_info.value.status_code == 404


class TestStreamCompilationEvents:
    async def test_job_not_found_raises_404(self) -> None:
        mock_session = AsyncMock()
        mock_request = AsyncMock(spec=Request)
        job_id = uuid4()
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class:
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
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class, \
             patch("apps.compiler_api.routes.compilations.resolve_session_factory") as mock_resolve_factory, \
             patch("asyncio.sleep", return_value=None):  # Mock sleep to avoid waiting
            
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
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class, \
             patch("apps.compiler_api.routes.compilations.resolve_session_factory") as mock_resolve_factory:
            
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
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class, \
             patch("apps.compiler_api.routes.compilations.resolve_session_factory") as mock_resolve_factory:
            
            mock_repo = AsyncMock()
            mock_repo_class.return_value = mock_repo
            
            # Initial check passes, then job becomes None in polling
            initial_job = MagicMock()
            initial_job.status = CompilationStatus.PENDING.value
            mock_repo.get_job.side_effect = [initial_job, None]  # First call returns job, second returns None
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
        
        with patch("apps.compiler_api.routes.compilations.CompilationRepository") as mock_repo_class, \
             patch("apps.compiler_api.routes.compilations.resolve_session_factory") as mock_resolve_factory:
            
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