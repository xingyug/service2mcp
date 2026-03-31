"""Unit tests for compilation executor adapters."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.compiler_worker.executor import (
    CallbackCompilationExecutor,
    DatabaseWorkflowCompilationExecutor,
    WorkflowCompilationExecutor,
    configure_compilation_executor,
    reset_compilation_executor,
    resolve_compilation_executor,
)
from apps.compiler_worker.models import (
    CompilationEventType,
    CompilationRequest,
    store_compilation_rollback_request,
)


def _request(
    name: str = "test-svc", *, options: dict[str, object] | None = None
) -> CompilationRequest:
    return CompilationRequest(service_name=name, options=options or {})


class TestCallbackCompilationExecutor:
    @pytest.mark.asyncio
    async def test_forwards_to_callback(self) -> None:
        received: list[CompilationRequest] = []

        async def on_execute(req: CompilationRequest) -> None:
            received.append(req)

        executor = CallbackCompilationExecutor(callback=on_execute)
        req = _request()
        await executor.execute(req)
        assert len(received) == 1
        assert received[0] is req

    @pytest.mark.asyncio
    async def test_exception_propagates(self) -> None:
        async def failing(_: CompilationRequest) -> None:
            raise ValueError("boom")

        executor = CallbackCompilationExecutor(callback=failing)
        with pytest.raises(ValueError, match="boom"):
            await executor.execute(_request())


class TestWorkflowCompilationExecutor:
    @pytest.mark.asyncio
    async def test_delegates_to_workflow_run(self) -> None:
        mock_workflow = AsyncMock()
        executor = WorkflowCompilationExecutor(workflow=mock_workflow)
        req = _request()
        await executor.execute(req)
        mock_workflow.run.assert_awaited_once_with(req)


class TestResolveCompilationExecutor:
    def setup_method(self) -> None:
        reset_compilation_executor()

    def teardown_method(self) -> None:
        reset_compilation_executor()

    def test_returns_configured_executor(self) -> None:
        sentinel = CallbackCompilationExecutor(callback=AsyncMock())
        configure_compilation_executor(sentinel)
        assert resolve_compilation_executor() is sentinel

    def test_raises_without_database_url(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            with pytest.raises(RuntimeError, match="DATABASE_URL"):
                resolve_compilation_executor()

    def test_returns_database_executor_when_url_set(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+asyncpg://u:p@h/db"}):
            executor = resolve_compilation_executor()
        assert isinstance(executor, DatabaseWorkflowCompilationExecutor)
        assert executor.database_url == "postgresql+asyncpg://u:p@h/db"

    def test_reset_clears_configured(self) -> None:
        sentinel = CallbackCompilationExecutor(callback=AsyncMock())
        configure_compilation_executor(sentinel)
        assert resolve_compilation_executor() is sentinel
        reset_compilation_executor()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            with pytest.raises(RuntimeError, match="DATABASE_URL"):
                resolve_compilation_executor()

    def test_configure_none_resets(self) -> None:
        sentinel = CallbackCompilationExecutor(callback=AsyncMock())
        configure_compilation_executor(sentinel)
        configure_compilation_executor(None)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            with pytest.raises(RuntimeError, match="DATABASE_URL"):
                resolve_compilation_executor()


class TestDatabaseWorkflowCompilationExecutor:
    def test_get_engine_creates_and_caches(self) -> None:
        executor = DatabaseWorkflowCompilationExecutor(database_url="postgresql+asyncpg://u:p@h/db")
        with patch("apps.compiler_worker.executor.create_async_engine") as mock_create:
            sentinel_engine = object()
            mock_create.return_value = sentinel_engine

            engine1 = executor._get_engine()
            engine2 = executor._get_engine()

        mock_create.assert_called_once_with("postgresql+asyncpg://u:p@h/db", pool_pre_ping=True)
        assert engine1 is sentinel_engine
        assert engine2 is sentinel_engine

    @pytest.mark.asyncio
    async def test_execute_builds_workflow_and_runs(self) -> None:
        mock_engine = AsyncMock()
        mock_workflow_instance = AsyncMock()
        req = _request()

        with (
            patch(
                "apps.compiler_worker.executor.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "apps.compiler_worker.executor.async_sessionmaker",
            ),
            patch(
                "apps.compiler_worker.executor.SQLAlchemyCompilationJobStore",
            ),
            patch(
                "apps.compiler_worker.executor.CompilationWorkflow",
            ) as mock_wf_cls,
            patch(
                "apps.compiler_worker.executor.create_default_activity_registry",
            ),
        ):
            mock_wf_cls.return_value = mock_workflow_instance
            executor = DatabaseWorkflowCompilationExecutor(
                database_url="postgresql+asyncpg://u:p@h/db"
            )
            await executor.execute(req)

            mock_wf_cls.assert_called_once()
            mock_workflow_instance.run.assert_awaited_once_with(req)

    @pytest.mark.asyncio
    async def test_execute_uses_rollback_workflow_for_rollback_jobs(self) -> None:
        mock_engine = AsyncMock()
        mock_rollback_workflow = AsyncMock()
        req = _request(
            options=store_compilation_rollback_request(
                {},
                source_job_id=uuid4(),
                service_id="petstore",
                target_version=2,
            )
        )
        req.job_id = uuid4()

        with (
            patch(
                "apps.compiler_worker.executor.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "apps.compiler_worker.executor.async_sessionmaker",
            ) as _mock_session_factory_cls,
            patch(
                "apps.compiler_worker.executor.SQLAlchemyCompilationJobStore",
            ) as mock_store_cls,
            patch(
                "apps.compiler_worker.executor.create_default_rollback_workflow",
                return_value=mock_rollback_workflow,
            ) as mock_rollback_factory,
            patch(
                "apps.compiler_worker.executor.CompilationWorkflow",
            ) as mock_wf_cls,
        ):
            mock_store = AsyncMock()
            mock_store.create_job.return_value = req.job_id
            mock_store_cls.return_value = mock_store
            mock_rollback_workflow.run.return_value = SimpleNamespace(
                service_id="petstore",
                target_version=2,
                previous_active_version=1,
                deployment_revision="rev-1",
                validation_report={"overall_passed": True},
                protocol="openapi",
            )
            executor = DatabaseWorkflowCompilationExecutor(
                database_url="postgresql+asyncpg://u:p@h/db"
            )
            await executor.execute(req)

            mock_wf_cls.assert_not_called()
            mock_rollback_factory.assert_called_once()
            assert mock_rollback_factory.call_args.kwargs["request_options"] == req.options
            assert mock_rollback_factory.call_args.kwargs["session_factory"] is not None
            mock_rollback_workflow.run.assert_awaited_once()
            rollback_request = mock_rollback_workflow.run.await_args.args[0]
            assert rollback_request.service_id == "petstore"
            assert rollback_request.target_version == 2
            event_types = [
                call.kwargs["event_type"] for call in mock_store.append_event.await_args_list
            ]
            assert CompilationEventType.ROLLBACK_STARTED in event_types
            assert CompilationEventType.ROLLBACK_SUCCEEDED in event_types
