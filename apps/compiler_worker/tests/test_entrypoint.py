"""Unit tests for apps/compiler_worker/entrypoint.py helper functions."""

from __future__ import annotations

import os
import subprocess
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from apps.compiler_worker.entrypoint import (
    _broker_endpoint,
    _build_celery_command,
    _build_http_command,
    _terminate_processes,
    _wait_for_celery_ready,
)


class TestBuildHttpCommand:
    def test_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("WORKER_HTTP_HOST", None)
            os.environ.pop("WORKER_HTTP_PORT", None)
            cmd = _build_http_command()
            assert "uvicorn" in cmd
            assert "apps.compiler_worker.main:app" in cmd
            assert "0.0.0.0" in cmd
            assert "8002" in cmd

    def test_custom_host_port(self) -> None:
        with patch.dict(os.environ, {
            "WORKER_HTTP_HOST": "127.0.0.1",
            "WORKER_HTTP_PORT": "9999",
        }):
            cmd = _build_http_command()
            assert "127.0.0.1" in cmd
            assert "9999" in cmd


class TestBuildCeleryCommand:
    def test_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CELERY_WORKER_LOGLEVEL", None)
            os.environ.pop("COMPILATION_TASK_QUEUE", None)
            os.environ.pop("CELERY_WORKER_CONCURRENCY", None)
            os.environ.pop("CELERY_WORKER_POOL", None)
            cmd = _build_celery_command()
            assert "celery" in cmd
            assert "worker" in cmd
            assert "info" in cmd  # default loglevel lowered

    def test_with_concurrency(self) -> None:
        with patch.dict(os.environ, {"CELERY_WORKER_CONCURRENCY": "4"}):
            cmd = _build_celery_command()
            assert "--concurrency" in cmd
            assert "4" in cmd

    def test_with_pool(self) -> None:
        with patch.dict(os.environ, {"CELERY_WORKER_POOL": "solo"}):
            cmd = _build_celery_command()
            assert "--pool" in cmd
            assert "solo" in cmd


class TestBrokerEndpoint:
    def test_no_env_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CELERY_BROKER_URL", None)
            os.environ.pop("REDIS_URL", None)
            assert _broker_endpoint() is None

    def test_redis_url(self) -> None:
        with patch.dict(os.environ, {"REDIS_URL": "redis://myhost:6380/0"}):
            result = _broker_endpoint()
            assert result == ("myhost", 6380)

    def test_redis_default_port(self) -> None:
        with patch.dict(os.environ, {"CELERY_BROKER_URL": "redis://myhost/0"}):
            result = _broker_endpoint()
            assert result == ("myhost", 6379)

    def test_rediss_default_port(self) -> None:
        with patch.dict(os.environ, {"CELERY_BROKER_URL": "rediss://myhost/0"}):
            result = _broker_endpoint()
            assert result == ("myhost", 6380)

    def test_non_redis_scheme_returns_none(self) -> None:
        with patch.dict(os.environ, {"CELERY_BROKER_URL": "amqp://rabbit:5672"}):
            assert _broker_endpoint() is None

    def test_memory_broker_returns_none(self) -> None:
        with patch.dict(os.environ, {"CELERY_BROKER_URL": "memory://"}):
            assert _broker_endpoint() is None


class TestWaitForCeleryReady:
    def test_already_ready(self) -> None:
        ready = Event()
        ready.set()
        process = MagicMock(spec=subprocess.Popen)
        process.poll.return_value = None
        _wait_for_celery_ready(process, ready, timeout_seconds=1.0)

    def test_process_exits_before_ready_raises(self) -> None:
        ready = Event()
        process = MagicMock(spec=subprocess.Popen)
        process.poll.return_value = 1
        with pytest.raises(RuntimeError, match="exited before becoming ready"):
            _wait_for_celery_ready(
                process, ready, timeout_seconds=1.0, poll_interval_seconds=0.01
            )

    def test_timeout_raises(self) -> None:
        ready = Event()
        process = MagicMock(spec=subprocess.Popen)
        process.poll.return_value = None
        with pytest.raises(RuntimeError, match="did not report ready"):
            _wait_for_celery_ready(
                process, ready, timeout_seconds=0.05, poll_interval_seconds=0.01
            )


class TestTerminateProcesses:
    def test_terminates_running_processes(self) -> None:
        p1 = MagicMock(spec=subprocess.Popen)
        p1.poll.return_value = None
        p2 = MagicMock(spec=subprocess.Popen)
        p2.poll.return_value = 0  # already exited
        _terminate_processes([p1, p2])
        p1.terminate.assert_called_once()
        p2.terminate.assert_not_called()

    def test_empty_list_is_noop(self) -> None:
        _terminate_processes([])
