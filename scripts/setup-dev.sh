#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

cd "${ROOT_DIR}"

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -e ".[all]"

docker compose -f deploy/docker-compose.yaml config >/dev/null

echo "Local development environment is prepared."
echo "Use 'make dev-up' to start PostgreSQL, Redis, Temporal, and the local services."
