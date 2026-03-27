"""Unit tests for apps/compiler_worker/celery_app.py."""

from __future__ import annotations

from apps.compiler_worker.celery_app import (
    COMPILATION_TASK_NAME,
    DEFAULT_COMPILATION_QUEUE,
    _run_coro,
    create_celery_app,
)


class TestCreateCeleryApp:
    def test_defaults_to_memory_broker(self) -> None:
        app = create_celery_app()
        assert "memory" in app.conf.broker_url

    def test_explicit_broker_url(self) -> None:
        app = create_celery_app(broker_url="redis://localhost:6379/0")
        assert app.conf.broker_url == "redis://localhost:6379/0"

    def test_explicit_result_backend(self) -> None:
        app = create_celery_app(result_backend="redis://localhost:6379/1")
        assert app.conf.result_backend == "redis://localhost:6379/1"

    def test_default_queue(self) -> None:
        app = create_celery_app()
        assert app.conf.task_default_queue == DEFAULT_COMPILATION_QUEUE

    def test_custom_queue(self) -> None:
        app = create_celery_app(queue_name="custom.queue")
        assert app.conf.task_default_queue == "custom.queue"

    def test_compilation_task_registered(self) -> None:
        app = create_celery_app()
        assert COMPILATION_TASK_NAME in app.tasks

    def test_json_serializer_configured(self) -> None:
        app = create_celery_app()
        assert app.conf.task_serializer == "json"
        assert app.conf.result_serializer == "json"
        assert "json" in app.conf.accept_content


class TestRunCoro:
    def test_runs_simple_coroutine(self) -> None:
        async def _coro() -> int:
            return 42

        result = _run_coro(_coro())
        assert result == 42

    def test_propagates_exception(self) -> None:
        async def _failing() -> None:
            raise ValueError("test error")

        try:
            _run_coro(_failing())
            assert False, "Should have raised"
        except ValueError as exc:
            assert "test error" in str(exc)
