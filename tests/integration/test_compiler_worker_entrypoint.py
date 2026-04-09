"""Regression tests for deployed compiler worker process startup flags."""

from __future__ import annotations

import sys
from threading import Event

import pytest

from apps.compiler_worker.entrypoint import (
    _broker_endpoint,
    _build_celery_command,
    _wait_for_broker_socket,
    _wait_for_celery_ready,
)


def test_build_celery_command_honors_worker_runtime_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPILATION_TASK_QUEUE", "compiler.jobs")
    monkeypatch.setenv("CELERY_WORKER_LOGLEVEL", "INFO")
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "1")
    monkeypatch.setenv("CELERY_WORKER_POOL", "solo")

    command = _build_celery_command()

    assert command[:6] == [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "apps.compiler_worker.celery_app:celery_app",
        "worker",
    ]
    assert "--queues" in command
    assert command[command.index("--queues") + 1] == "compiler.jobs"
    assert "--loglevel" in command
    assert command[command.index("--loglevel") + 1] == "info"
    assert "--concurrency" in command
    assert command[command.index("--concurrency") + 1] == "1"
    assert "--pool" in command
    assert command[command.index("--pool") + 1] == "solo"


def test_broker_endpoint_reads_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://tool-compiler-redis:6379/0")

    assert _broker_endpoint() == ("tool-compiler-redis", 6379)


def test_wait_for_broker_socket_retries_until_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://tool-compiler-redis:6379/0")

    attempts: list[tuple[str, int, float]] = []

    def flaky_connect(host: str, port: int, timeout_seconds: float) -> None:
        attempts.append((host, port, timeout_seconds))
        if len(attempts) < 3:
            raise OSError("connection refused")

    monkeypatch.setattr(
        "apps.compiler_worker.entrypoint._connect_tcp",
        flaky_connect,
    )

    _wait_for_broker_socket(timeout_seconds=1, poll_interval_seconds=0)

    assert attempts == [
        ("tool-compiler-redis", 6379, 2.0),
        ("tool-compiler-redis", 6379, 2.0),
        ("tool-compiler-redis", 6379, 2.0),
    ]


class _FakeProcess:
    def __init__(self, poll_values: list[int | None]) -> None:
        self._poll_values = list(poll_values)

    def poll(self) -> int | None:
        if len(self._poll_values) == 1:
            return self._poll_values[0]
        return self._poll_values.pop(0)


def test_wait_for_celery_ready_returns_when_ready_event_is_set() -> None:
    ready_event = Event()
    ready_event.set()

    _wait_for_celery_ready(
        _FakeProcess([None]),  # type: ignore[arg-type]
        ready_event,
        timeout_seconds=1,
    )


def test_wait_for_celery_ready_raises_when_process_exits_early() -> None:
    ready_event = Event()

    with pytest.raises(RuntimeError, match="exited before becoming ready"):
        _wait_for_celery_ready(
            _FakeProcess([2]),  # type: ignore[arg-type]
            ready_event,
            timeout_seconds=1,
        )
