#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

cd "${ROOT_DIR}"

if command -v uv &>/dev/null; then
    echo "Using uv for environment management."
    uv sync --extra all
else
    echo "uv not found — falling back to pip + venv."
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --upgrade pip
    "${VENV_DIR}/bin/pip" install -e ".[all]"
fi

docker compose -f deploy/docker-compose.yaml config >/dev/null

echo "Local development environment is prepared."
echo "Use 'make dev-up' to start PostgreSQL, Redis, Temporal, and the local services."
