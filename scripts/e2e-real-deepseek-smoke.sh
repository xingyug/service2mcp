#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTEST_BIN="${ROOT_DIR}/.venv/bin/pytest"
LLM_API_KEY_FILE="${LLM_API_KEY_FILE:-/home/guoxy/esoc-agents/.deepseek_api_key}"
DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-chat}"
DEEPSEEK_API_BASE_URL="${DEEPSEEK_API_BASE_URL:-https://api.deepseek.com}"
PYTEST_K_EXPR="${PYTEST_K_EXPR:-graphql_introspection_compiles_to_running_runtime_and_tool_invocation or sql_schema_compiles_to_running_runtime_and_tool_invocation}"

if [[ ! -x "${PYTEST_BIN}" ]]; then
  echo "Pytest executable ${PYTEST_BIN} was not found. Run setup first." >&2
  exit 1
fi

if [[ ! -f "${LLM_API_KEY_FILE}" ]]; then
  echo "DeepSeek API key file ${LLM_API_KEY_FILE} was not found." >&2
  exit 1
fi

DEEPSEEK_API_KEY="$(tr -d '\r\n' < "${LLM_API_KEY_FILE}")"
if [[ -z "${DEEPSEEK_API_KEY}" ]]; then
  echo "DeepSeek API key file ${LLM_API_KEY_FILE} is empty." >&2
  exit 1
fi

echo "Running local real DeepSeek smoke for GraphQL + SQL E2E proofs..."
echo "Using test selector: ${PYTEST_K_EXPR}"

cd "${ROOT_DIR}"

exec env \
  DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}" \
  ENABLE_REAL_DEEPSEEK_E2E=1 \
  DEEPSEEK_MODEL="${DEEPSEEK_MODEL}" \
  DEEPSEEK_API_BASE_URL="${DEEPSEEK_API_BASE_URL}" \
  "${PYTEST_BIN}" -vv tests/e2e/test_full_compilation_flow.py -k "${PYTEST_K_EXPR}" "$@"
