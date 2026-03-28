"""Process supervisor for the compiler worker HTTP shell and Celery consumer."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from threading import Event, Thread
from typing import Any
from urllib.parse import urlsplit

from apps.compiler_worker.celery_app import DEFAULT_COMPILATION_QUEUE


def _build_http_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "apps.compiler_worker.main:app",
        "--host",
        os.getenv("WORKER_HTTP_HOST", "0.0.0.0"),
        "--port",
        os.getenv("WORKER_HTTP_PORT", "8002"),
    ]


def _build_celery_command() -> list[str]:
    command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "apps.compiler_worker.celery_app:celery_app",
        "worker",
        "--loglevel",
        os.getenv("CELERY_WORKER_LOGLEVEL", "INFO").lower(),
        "--queues",
        os.getenv("COMPILATION_TASK_QUEUE", DEFAULT_COMPILATION_QUEUE),
    ]
    concurrency = os.getenv("CELERY_WORKER_CONCURRENCY")
    if concurrency:
        command.extend(["--concurrency", concurrency])
    pool = os.getenv("CELERY_WORKER_POOL")
    if pool:
        command.extend(["--pool", pool])
    return command


def _broker_endpoint() -> tuple[str, int] | None:
    broker_url = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL")
    if not broker_url:
        return None
    parsed = urlsplit(broker_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        return None
    default_port = 6380 if parsed.scheme == "rediss" else 6379
    return parsed.hostname, parsed.port or default_port


def _connect_tcp(host: str, port: int, timeout_seconds: float) -> None:
    with socket.create_connection((host, port), timeout=timeout_seconds):
        return None


def _wait_for_broker_socket(
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 1.0,
) -> None:
    endpoint = _broker_endpoint()
    if endpoint is None:
        return

    host, port = endpoint
    connect_timeout_seconds = float(
        os.getenv("WORKER_BROKER_CONNECT_TIMEOUT_SECONDS", "2")
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            _connect_tcp(host, port, connect_timeout_seconds)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Broker {host}:{port} did not become reachable within "
                    f"{timeout_seconds:.0f}s."
                ) from exc
            time.sleep(poll_interval_seconds)


def _stream_celery_output(process: subprocess.Popen[str], ready_event: Event) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if " ready." in line:
            ready_event.set()


def _wait_for_celery_ready(
    process: subprocess.Popen[str],
    ready_event: Event,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.2,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if ready_event.is_set():
            return
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                "Celery worker exited before becoming ready "
                f"(exit code {return_code})."
            )
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Celery worker did not report ready within {timeout_seconds:.0f}s."
            )
        time.sleep(poll_interval_seconds)


def _terminate_processes(processes: list[subprocess.Popen[Any]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()


def main() -> int:
    _wait_for_broker_socket(
        timeout_seconds=float(os.getenv("WORKER_BROKER_READY_TIMEOUT_SECONDS", "60"))
    )

    celery_process = subprocess.Popen(
        _build_celery_command(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    celery_ready = Event()
    celery_output_thread = Thread(
        target=_stream_celery_output,
        args=(celery_process, celery_ready),
        daemon=True,
    )
    celery_output_thread.start()

    try:
        _wait_for_celery_ready(
            celery_process,
            celery_ready,
            timeout_seconds=float(
                os.getenv("WORKER_CELERY_READY_TIMEOUT_SECONDS", "60")
            ),
        )
    except Exception:
        _terminate_processes([celery_process])
        celery_process.wait(timeout=30)
        raise

    processes: list[subprocess.Popen[Any]] = [
        subprocess.Popen(_build_http_command()),
        celery_process,
    ]

    def handle_signal(signum: int, _frame: object) -> None:
        del signum
        _terminate_processes(processes)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is None:
                    continue
                _terminate_processes(processes)
                for sibling in processes:
                    if sibling is process:
                        continue
                    sibling.wait(timeout=30)
                return int(return_code)
            time.sleep(1)
    finally:
        _terminate_processes(processes)
        for process in processes:
            if process.poll() is None:
                process.wait(timeout=30)
        celery_output_thread.join(timeout=1)


if __name__ == "__main__":
    raise SystemExit(main())
