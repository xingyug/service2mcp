#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/deploy/docker-compose.yaml"

docker compose -f "${COMPOSE_FILE}" ps >/dev/null

python3 - <<'PY'
from __future__ import annotations

import socket
import time
import urllib.request

PORTS = [5432, 6379, 7233, 8000, 8001, 8002, 8003, 8004]
HTTP_ENDPOINTS = [
    "http://127.0.0.1:8000/healthz",
    "http://127.0.0.1:8001/healthz",
    "http://127.0.0.1:8002/healthz",
    "http://127.0.0.1:8003/healthz",
    "http://127.0.0.1:8004/healthz",
]


def wait_for_port(port: int) -> None:
    deadline = time.time() + 90
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for port {port}.")


for port in PORTS:
    wait_for_port(port)

for endpoint in HTTP_ENDPOINTS:
    with urllib.request.urlopen(endpoint, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"Health check failed for {endpoint}: {response.status}")

print("Development smoke checks passed.")
PY
