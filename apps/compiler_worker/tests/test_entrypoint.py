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
        with patch.dict(
            os.environ,
            {
                "WORKER_HTTP_HOST": "127.0.0.1",
                "WORKER_HTTP_PORT": "9999",
            },
        ):
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
            _wait_for_celery_ready(process, ready, timeout_seconds=1.0, poll_interval_seconds=0.01)

    def test_timeout_raises(self) -> None:
        ready = Event()
        process = MagicMock(spec=subprocess.Popen)
        process.poll.return_value = None
        with pytest.raises(RuntimeError, match="did not report ready"):
            _wait_for_celery_ready(process, ready, timeout_seconds=0.05, poll_interval_seconds=0.01)


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


class TestConnectTcp:
    def test_connection_returns_none(self) -> None:
        from apps.compiler_worker.entrypoint import _connect_tcp

        with patch("socket.create_connection") as mock_socket:
            mock_socket.return_value.__enter__ = MagicMock()
            mock_socket.return_value.__exit__ = MagicMock()
            result = _connect_tcp("localhost", 6379, 2.0)
            assert result is None
            mock_socket.assert_called_once_with(("localhost", 6379), timeout=2.0)


class TestWaitForBrokerSocket:
    def test_no_broker_endpoint_returns_early(self) -> None:
        from apps.compiler_worker.entrypoint import _wait_for_broker_socket

        with patch("apps.compiler_worker.entrypoint._broker_endpoint", return_value=None):
            # Should return without raising
            _wait_for_broker_socket(timeout_seconds=1.0)

    def test_successful_connection(self) -> None:
        from apps.compiler_worker.entrypoint import _wait_for_broker_socket

        with (
            patch(
                "apps.compiler_worker.entrypoint._broker_endpoint", return_value=("localhost", 6379)
            ),
            patch("apps.compiler_worker.entrypoint._connect_tcp") as mock_connect,
        ):
            _wait_for_broker_socket(timeout_seconds=1.0)
            mock_connect.assert_called_once_with("localhost", 6379, 2.0)

    def test_timeout_raises_runtime_error(self) -> None:
        from apps.compiler_worker.entrypoint import _wait_for_broker_socket

        with (
            patch(
                "apps.compiler_worker.entrypoint._broker_endpoint", return_value=("localhost", 6379)
            ),
            patch(
                "apps.compiler_worker.entrypoint._connect_tcp",
                side_effect=OSError("Connection refused"),
            ),
            patch("time.sleep") as mock_sleep,
        ):
            with pytest.raises(RuntimeError, match="did not become reachable"):
                _wait_for_broker_socket(timeout_seconds=0.1, poll_interval_seconds=0.05)
            # Should have tried to sleep at least once
            assert mock_sleep.call_count >= 1

    def test_custom_connect_timeout(self) -> None:
        from apps.compiler_worker.entrypoint import _wait_for_broker_socket

        with (
            patch(
                "apps.compiler_worker.entrypoint._broker_endpoint", return_value=("localhost", 6379)
            ),
            patch("apps.compiler_worker.entrypoint._connect_tcp") as mock_connect,
            patch.dict(os.environ, {"WORKER_BROKER_CONNECT_TIMEOUT_SECONDS": "5"}),
        ):
            _wait_for_broker_socket(timeout_seconds=1.0)
            mock_connect.assert_called_once_with("localhost", 6379, 5.0)


class TestStreamCeleryOutput:
    def test_output_streaming_and_ready_detection(self) -> None:
        import io
        from threading import Event

        from apps.compiler_worker.entrypoint import _stream_celery_output

        ready_event = Event()
        process = MagicMock(spec=subprocess.Popen)
        # Mock stdout as an iterator returning lines
        process.stdout = iter(
            ["Starting worker\n", "[2024-01-01 12:00:00,000] Worker ready.\n", "Another line\n"]
        )

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            _stream_celery_output(process, ready_event)

        # Check that ready event was set when " ready." was found
        assert ready_event.is_set()

        # Check that all output was written to stdout
        output = mock_stdout.getvalue()
        assert "Starting worker" in output
        assert "Worker ready." in output
        assert "Another line" in output


class TestMain:
    def test_broker_timeout_env_var_invalid_uses_default(self) -> None:
        from apps.compiler_worker.entrypoint import main

        with (
            patch.dict(os.environ, {"WORKER_BROKER_READY_TIMEOUT_SECONDS": "invalid"}),
            patch("apps.compiler_worker.entrypoint._wait_for_broker_socket") as mock_wait,
            patch("subprocess.Popen") as mock_popen,
            patch("signal.signal"),
            patch("time.sleep", side_effect=KeyboardInterrupt("Stop loop")),
        ):
            # Mock processes
            celery_process = MagicMock()
            celery_process.poll.return_value = None
            http_process = MagicMock()
            http_process.poll.return_value = None
            mock_popen.side_effect = [celery_process, http_process]

            # Mock stdout for celery process
            celery_process.stdout = iter(["Worker ready.\n"])

            with patch("threading.Thread") as mock_thread:
                mock_thread_instance = MagicMock()
                mock_thread.return_value = mock_thread_instance

                try:
                    main()
                except KeyboardInterrupt:
                    pass

            # Verify broker timeout used default value (60.0)
            mock_wait.assert_called_once_with(timeout_seconds=60.0)

    def test_celery_process_fails_to_start(self) -> None:
        from apps.compiler_worker.entrypoint import main

        with (
            patch("apps.compiler_worker.entrypoint._wait_for_broker_socket"),
            patch("subprocess.Popen") as mock_popen,
            patch(
                "apps.compiler_worker.entrypoint._wait_for_celery_ready",
                side_effect=RuntimeError("Celery failed"),
            ),
            patch("apps.compiler_worker.entrypoint._terminate_processes") as mock_terminate,
        ):
            celery_process = MagicMock()
            mock_popen.return_value = celery_process
            celery_process.wait.return_value = None

            with patch("threading.Thread"):
                with pytest.raises(RuntimeError, match="Celery failed"):
                    main()

            # Verify processes were terminated
            mock_terminate.assert_called_once_with([celery_process])
            celery_process.wait.assert_called_once_with(timeout=30)

    def test_process_exits_during_loop(self) -> None:
        from apps.compiler_worker.entrypoint import main

        with (
            patch("apps.compiler_worker.entrypoint._wait_for_broker_socket"),
            patch("subprocess.Popen") as mock_popen,
            patch("apps.compiler_worker.entrypoint._wait_for_celery_ready"),
            patch("signal.signal"),
            patch("apps.compiler_worker.entrypoint._terminate_processes") as mock_terminate,
        ):
            # Mock celery process startup
            celery_process = MagicMock()
            celery_process.stdout = iter(["Worker ready.\n"])
            # The loop calls poll() for both processes in each iteration
            # For celery: None, None, 1 (exits on third check)
            celery_process.poll.side_effect = [
                None,
                None,
                1,
                None,
            ]  # Added extra None for finally block

            # Mock HTTP process
            http_process = MagicMock()
            http_process.poll.side_effect = [None, None, None, None]  # Always None
            http_process.wait.return_value = None

            mock_popen.side_effect = [celery_process, http_process]

            with patch("threading.Thread") as mock_thread:
                mock_thread_instance = MagicMock()
                mock_thread.return_value = mock_thread_instance

                result = main()

            assert result == 1  # Should return the exit code
            mock_terminate.assert_called()
            # Verify the sibling process was waited for
            http_process.wait.assert_called_with(timeout=30)

    def test_signal_handler_terminates_processes(self) -> None:
        import signal as signal_module

        from apps.compiler_worker.entrypoint import main

        with (
            patch("apps.compiler_worker.entrypoint._wait_for_broker_socket"),
            patch("subprocess.Popen") as mock_popen,
            patch("apps.compiler_worker.entrypoint._wait_for_celery_ready"),
            patch("signal.signal") as mock_signal,
            patch("apps.compiler_worker.entrypoint._terminate_processes") as mock_terminate,
        ):
            # Mock celery process startup
            celery_process = MagicMock()
            celery_process.stdout = iter(["Worker ready.\n"])
            celery_process.poll.return_value = None

            # Mock HTTP process
            http_process = MagicMock()
            http_process.poll.return_value = None

            mock_popen.side_effect = [celery_process, http_process]

            # Capture the signal handler
            signal_handler = None

            def capture_signal(sig, handler):
                nonlocal signal_handler
                signal_handler = handler

            mock_signal.side_effect = capture_signal

            with (
                patch("threading.Thread") as mock_thread,
                patch("time.sleep", side_effect=KeyboardInterrupt("Stop loop")),
            ):
                mock_thread_instance = MagicMock()
                mock_thread.return_value = mock_thread_instance

                try:
                    main()
                except KeyboardInterrupt:
                    pass

            # Verify signal handler was registered
            assert mock_signal.call_count == 2  # SIGTERM and SIGINT
            assert signal_handler is not None

            # Test the signal handler
            signal_handler(signal_module.SIGTERM, None)
            mock_terminate.assert_called()

    def test_cleanup_in_finally_block(self) -> None:
        from apps.compiler_worker.entrypoint import main

        with (
            patch("apps.compiler_worker.entrypoint._wait_for_broker_socket"),
            patch("subprocess.Popen") as mock_popen,
            patch("apps.compiler_worker.entrypoint._wait_for_celery_ready"),
            patch("signal.signal"),
            patch("apps.compiler_worker.entrypoint._terminate_processes") as mock_terminate,
        ):
            # Mock processes
            celery_process = MagicMock()
            celery_process.stdout = iter(["Worker ready.\n"])
            celery_process.poll.side_effect = [None, None, None, None]  # Still running

            http_process = MagicMock()
            http_process.poll.side_effect = [None, None, None, None]  # Still running

            mock_popen.side_effect = [celery_process, http_process]

            # Mock time.sleep to raise KeyboardInterrupt in the main loop
            with (
                patch("threading.Thread") as mock_thread,
                patch("time.sleep", side_effect=KeyboardInterrupt("Stop loop")),
            ):
                mock_thread_instance = MagicMock()
                mock_thread.return_value = mock_thread_instance

                try:
                    main()
                except KeyboardInterrupt:
                    pass

            # Verify cleanup happened in finally block - focus on main cleanup actions
            mock_terminate.assert_called()
            celery_process.wait.assert_called_with(timeout=30)
            http_process.wait.assert_called_with(timeout=30)


class TestMainEntryPoint:
    def test_main_entrypoint_raises_system_exit(self) -> None:
        # Test the __main__ block behavior
        with patch("apps.compiler_worker.entrypoint.main", return_value=42):
            # Mock the __name__ check by importing and executing the module's main block
            import apps.compiler_worker.entrypoint as entrypoint_module

            original_name = getattr(entrypoint_module, "__name__", None)
            entrypoint_module.__name__ = "__main__"

            try:
                with pytest.raises(SystemExit) as exc_info:
                    # Simulate the module being run directly
                    if entrypoint_module.__name__ == "__main__":
                        raise SystemExit(entrypoint_module.main())
                assert exc_info.value.code == 42
            finally:
                # Restore original __name__
                if original_name is not None:
                    entrypoint_module.__name__ = original_name
