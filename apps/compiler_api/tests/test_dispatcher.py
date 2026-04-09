"""Unit tests for compilation dispatcher classes."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI

from apps.compiler_api.dispatcher import (
    CallbackCompilationDispatcher,
    InMemoryCompilationDispatcher,
    UnconfiguredCompilationDispatcher,
    _resolve_default_dispatcher,
    configure_compilation_dispatcher,
    get_compilation_dispatcher,
)
from apps.compiler_worker.models import CompilationRequest


def _request(name: str = "test-svc") -> CompilationRequest:
    return CompilationRequest(service_name=name)


class TestInMemoryCompilationDispatcher:
    @pytest.mark.asyncio
    async def test_enqueue_records_request(self) -> None:
        dispatcher = InMemoryCompilationDispatcher()
        req = _request()
        await dispatcher.enqueue(req)
        assert len(dispatcher.submitted_requests) == 1
        assert dispatcher.submitted_requests[0] is req

    @pytest.mark.asyncio
    async def test_enqueue_multiple(self) -> None:
        dispatcher = InMemoryCompilationDispatcher()
        await dispatcher.enqueue(_request("svc-a"))
        await dispatcher.enqueue(_request("svc-b"))
        assert len(dispatcher.submitted_requests) == 2
        assert dispatcher.submitted_requests[0].service_name == "svc-a"
        assert dispatcher.submitted_requests[1].service_name == "svc-b"


class TestCallbackCompilationDispatcher:
    @pytest.mark.asyncio
    async def test_enqueue_forwards_to_callback(self) -> None:
        received: list[CompilationRequest] = []

        async def on_enqueue(req: CompilationRequest) -> None:
            received.append(req)

        dispatcher = CallbackCompilationDispatcher(callback=on_enqueue)
        req = _request()
        await dispatcher.enqueue(req)
        assert len(received) == 1
        assert received[0] is req

    @pytest.mark.asyncio
    async def test_callback_exception_propagates(self) -> None:
        async def failing_callback(_: CompilationRequest) -> None:
            raise ValueError("boom")

        dispatcher = CallbackCompilationDispatcher(callback=failing_callback)
        with pytest.raises(ValueError, match="boom"):
            await dispatcher.enqueue(_request())


class TestResolveDefaultDispatcher:
    def test_defaults_to_unconfigured_dispatcher(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORKFLOW_ENGINE", None)
            dispatcher = _resolve_default_dispatcher()
        assert isinstance(dispatcher, UnconfiguredCompilationDispatcher)
        assert dispatcher.workflow_engine is None

    def test_celery_engine(self) -> None:
        from apps.compiler_api.dispatcher import CeleryCompilationDispatcher

        with patch.dict(os.environ, {"WORKFLOW_ENGINE": "celery"}):
            dispatcher = _resolve_default_dispatcher()
        assert isinstance(dispatcher, CeleryCompilationDispatcher)

    def test_unknown_engine_returns_unconfigured_dispatcher(self) -> None:
        with patch.dict(os.environ, {"WORKFLOW_ENGINE": "temporal"}):
            dispatcher = _resolve_default_dispatcher()
        assert isinstance(dispatcher, UnconfiguredCompilationDispatcher)
        assert dispatcher.workflow_engine == "temporal"


class TestConfigureAndGetDispatcher:
    def test_configure_attaches_to_app_state(self) -> None:
        app = FastAPI()
        dispatcher = InMemoryCompilationDispatcher()
        configure_compilation_dispatcher(app, dispatcher=dispatcher)
        assert getattr(app.state, "compilation_dispatcher") is dispatcher

    def test_configure_uses_default_when_none(self) -> None:
        app = FastAPI()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORKFLOW_ENGINE", None)
            configure_compilation_dispatcher(app)
        assert isinstance(
            getattr(app.state, "compilation_dispatcher"),
            UnconfiguredCompilationDispatcher,
        )

    def test_get_dispatcher_returns_configured(self) -> None:
        app = FastAPI()
        dispatcher = InMemoryCompilationDispatcher()
        configure_compilation_dispatcher(app, dispatcher=dispatcher)

        from starlette.requests import Request as StarletteRequest

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
        mock_request = StarletteRequest(scope)
        resolved = get_compilation_dispatcher(mock_request)
        assert resolved is dispatcher


class TestCeleryCompilationDispatcher:
    @pytest.mark.asyncio
    async def test_enqueue_raises_when_task_not_registered(self) -> None:
        from unittest.mock import MagicMock

        from apps.compiler_api.dispatcher import CeleryCompilationDispatcher

        fake_celery = MagicMock()
        fake_celery.tasks = {}  # no tasks registered

        dispatcher = CeleryCompilationDispatcher(
            celery_app=fake_celery,
            task_name="nonexistent.task",
            queue_name="default",
        )
        with pytest.raises(RuntimeError, match="nonexistent.task is not registered"):
            await dispatcher.enqueue(_request())


class TestUnconfiguredCompilationDispatcher:
    @pytest.mark.asyncio
    async def test_enqueue_raises_when_workflow_engine_missing(self) -> None:
        dispatcher = UnconfiguredCompilationDispatcher()

        with pytest.raises(RuntimeError, match="WORKFLOW_ENGINE=celery"):
            await dispatcher.enqueue(_request())

    @pytest.mark.asyncio
    async def test_enqueue_raises_for_unsupported_engine(self) -> None:
        dispatcher = UnconfiguredCompilationDispatcher(workflow_engine="temporal")

        with pytest.raises(RuntimeError, match="Unsupported WORKFLOW_ENGINE 'temporal'"):
            await dispatcher.enqueue(_request())


class TestGetDispatcherFallback:
    def test_fallback_creates_and_caches_dispatcher(self) -> None:
        """Dispatcher with no preset state falls back to _resolve_default_dispatcher()."""
        app = FastAPI()
        # Do NOT call configure_compilation_dispatcher — state is empty

        from starlette.requests import Request as StarletteRequest

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
        mock_request = StarletteRequest(scope)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORKFLOW_ENGINE", None)
            dispatcher = get_compilation_dispatcher(mock_request)

        assert isinstance(dispatcher, UnconfiguredCompilationDispatcher)
        # Second call should return same cached instance
        dispatcher2 = get_compilation_dispatcher(mock_request)
        assert dispatcher2 is dispatcher
