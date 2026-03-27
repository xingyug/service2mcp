#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
HELM_BIN="${HELM_BIN:-/tmp/linux-amd64/helm}"
NAMESPACE="${NAMESPACE:-tool-compiler-llm-e2e}"
RELEASE_NAME="${RELEASE_NAME:-tool-compiler}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-900}"
KEEP_NAMESPACE="${KEEP_NAMESPACE:-0}"
RUN_ID="${RUN_ID:-$(date +%H%M%S)}"
PROTOCOL="${PROTOCOL:-all}"
AUDIT_ALL_GENERATED_TOOLS="${AUDIT_ALL_GENERATED_TOOLS:-0}"
ENABLE_TOOL_GROUPING="${ENABLE_TOOL_GROUPING:-0}"
ENABLE_LLM_JUDGE="${ENABLE_LLM_JUDGE:-0}"
IMAGE_REPO_BASE="${IMAGE_REPO_BASE:-us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
COMPILER_API_IMAGE="${COMPILER_API_IMAGE:-${IMAGE_REPO_BASE}/compiler-api:${IMAGE_TAG}}"
ACCESS_CONTROL_IMAGE="${ACCESS_CONTROL_IMAGE:-${IMAGE_REPO_BASE}/access-control:${IMAGE_TAG}}"
COMPILER_WORKER_IMAGE="${COMPILER_WORKER_IMAGE:-${IMAGE_REPO_BASE}/compiler-worker:${IMAGE_TAG}}"
MCP_RUNTIME_IMAGE="${MCP_RUNTIME_IMAGE:-${IMAGE_REPO_BASE}/mcp-runtime:${IMAGE_TAG}}"
PROOF_HELPER_IMAGE="${PROOF_HELPER_IMAGE:-${COMPILER_API_IMAGE}}"
LLM_API_KEY_FILE="${LLM_API_KEY_FILE:-/home/guoxy/esoc-agents/.deepseek_api_key}"
TMP_DIR="$(mktemp -d)"
VALUES_OVERRIDE_PATH="${TMP_DIR}/values.override.yaml"
RESULTS_PATH="${TMP_DIR}/proof-results.json"

cleanup() {
  rm -rf "${TMP_DIR}"
  if [[ "${KEEP_NAMESPACE}" == "1" ]]; then
    echo "Keeping namespace ${NAMESPACE}"
    return
  fi
  "${KUBECTL}" delete namespace "${NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
}

trap cleanup EXIT

if [[ ! -x "${HELM_BIN}" ]]; then
  echo "Helm binary ${HELM_BIN} is not executable." >&2
  exit 1
fi

if [[ ! -f "${LLM_API_KEY_FILE}" ]]; then
  echo "DeepSeek API key file ${LLM_API_KEY_FILE} was not found." >&2
  exit 1
fi

case "${PROTOCOL}" in
  all|graphql|rest|grpc|soap|sql)
    ;;
  *)
    echo "Unsupported PROTOCOL=${PROTOCOL}. Expected one of: all, graphql, rest, grpc, soap, sql." >&2
    exit 1
    ;;
esac

image_repo() {
  printf '%s' "${1%:*}"
}

image_tag() {
  printf '%s' "${1##*:}"
}

cat > "${VALUES_OVERRIDE_PATH}" <<YAML
images:
  compilerApi:
    repository: $(image_repo "${COMPILER_API_IMAGE}")
    tag: $(image_tag "${COMPILER_API_IMAGE}")
    pullPolicy: Always
  migrations:
    repository: $(image_repo "${COMPILER_API_IMAGE}")
    tag: $(image_tag "${COMPILER_API_IMAGE}")
    pullPolicy: Always
  accessControl:
    repository: $(image_repo "${ACCESS_CONTROL_IMAGE}")
    tag: $(image_tag "${ACCESS_CONTROL_IMAGE}")
    pullPolicy: Always
  compilerWorker:
    repository: $(image_repo "${COMPILER_WORKER_IMAGE}")
    tag: $(image_tag "${COMPILER_WORKER_IMAGE}")
    pullPolicy: Always
  mcpRuntime:
    repository: $(image_repo "${MCP_RUNTIME_IMAGE}")
    tag: $(image_tag "${MCP_RUNTIME_IMAGE}")
    pullPolicy: Always
gatewayAdminMock:
  enabled: true
mcpRuntime:
  enabled: false
compilerWorker:
  extraEnv:
    - name: LLM_PROVIDER
      value: deepseek
    - name: LLM_MODEL
      value: deepseek-chat
    - name: LLM_SKIP_IF_DESCRIPTION_EXISTS
      value: "false"
    - name: WORKER_ENABLE_LLM_ENHANCEMENT
      value: "true"
    - name: WORKER_ENABLE_TOOL_GROUPING
      value: "${ENABLE_TOOL_GROUPING}"
  secretEnv:
    - name: LLM_API_KEY
      secretName: llm-e2e-secrets
      secretKey: llm-api-key
YAML

cd "${ROOT_DIR}"

echo "Running GKE LLM-enabled proof with protocol=${PROTOCOL}"

"${KUBECTL}" get namespace "${NAMESPACE}" >/dev/null 2>&1 || "${KUBECTL}" create namespace "${NAMESPACE}" >/dev/null

"${KUBECTL}" create secret generic llm-e2e-secrets \
  --namespace "${NAMESPACE}" \
  --from-file=llm-api-key="${LLM_API_KEY_FILE}" \
  --dry-run=client \
  -o yaml | "${KUBECTL}" apply -f -

"${KUBECTL}" create configmap llm-proof-sql-init \
  --namespace "${NAMESPACE}" \
  --from-file=init.sql="${ROOT_DIR}/tests/fixtures/sql_schemas/catalog_live.sql" \
  --dry-run=client \
  -o yaml | "${KUBECTL}" apply -f -

"${HELM_BIN}" upgrade --install "${RELEASE_NAME}" \
  "${ROOT_DIR}/deploy/helm/tool-compiler" \
  --namespace "${NAMESPACE}" \
  -f "${ROOT_DIR}/deploy/helm/tool-compiler/values.yaml" \
  -f "${VALUES_OVERRIDE_PATH}" \
  --wait \
  --timeout "${WAIT_TIMEOUT_SECONDS}s"

"${KUBECTL}" apply -n "${NAMESPACE}" -f - <<YAML
apiVersion: v1
kind: Service
metadata:
  name: llm-proof-http
spec:
  selector:
    app: llm-proof-http
  ports:
    - name: http
      port: 8080
      targetPort: http
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llm-proof-http
spec:
  replicas: 1
  selector:
    matchLabels:
      app: llm-proof-http
  template:
    metadata:
      labels:
        app: llm-proof-http
    spec:
      containers:
        - name: http-mock
          image: ${PROOF_HELPER_IMAGE}
          imagePullPolicy: Always
          command:
            - sh
            - -lc
            - python -m uvicorn apps.proof_runner.http_mock:app --host 0.0.0.0 --port 8080
          ports:
            - name: http
              containerPort: 8080
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
---
apiVersion: v1
kind: Service
metadata:
  name: llm-proof-grpc
spec:
  selector:
    app: llm-proof-grpc
  ports:
    - name: grpc
      port: 50051
      targetPort: grpc
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llm-proof-grpc
spec:
  replicas: 1
  selector:
    matchLabels:
      app: llm-proof-grpc
  template:
    metadata:
      labels:
        app: llm-proof-grpc
    spec:
      containers:
        - name: grpc-mock
          image: ${PROOF_HELPER_IMAGE}
          imagePullPolicy: Always
          command:
            - python
            - -m
            - apps.proof_runner.grpc_mock
          env:
            - name: GRPC_PORT
              value: "50051"
          ports:
            - name: grpc
              containerPort: 50051
          readinessProbe:
            tcpSocket:
              port: grpc
          livenessProbe:
            tcpSocket:
              port: grpc
---
apiVersion: v1
kind: Service
metadata:
  name: llm-proof-sql
spec:
  selector:
    app: llm-proof-sql
  ports:
    - name: postgres
      port: 5432
      targetPort: postgres
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llm-proof-sql
spec:
  replicas: 1
  selector:
    matchLabels:
      app: llm-proof-sql
  template:
    metadata:
      labels:
        app: llm-proof-sql
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          env:
            - name: POSTGRES_DB
              value: proofsql
            - name: POSTGRES_USER
              value: proofsql
            - name: POSTGRES_PASSWORD
              value: proofsql
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          ports:
            - name: postgres
              containerPort: 5432
          startupProbe:
            tcpSocket:
              port: postgres
            periodSeconds: 5
            failureThreshold: 60
          readinessProbe:
            tcpSocket:
              port: postgres
          livenessProbe:
            tcpSocket:
              port: postgres
          volumeMounts:
            - name: sql-data
              mountPath: /var/lib/postgresql/data
            - name: sql-init
              mountPath: /docker-entrypoint-initdb.d
      volumes:
        - name: sql-data
          emptyDir: {}
        - name: sql-init
          configMap:
            name: llm-proof-sql-init
YAML

for deployment in \
  "${RELEASE_NAME}-compiler-api" \
  "${RELEASE_NAME}-access-control" \
  "${RELEASE_NAME}-compiler-worker" \
  "${RELEASE_NAME}-gateway-admin-mock" \
  "llm-proof-http" \
  "llm-proof-grpc" \
  "llm-proof-sql"
do
  "${KUBECTL}" rollout status -n "${NAMESPACE}" "deployment/${deployment}" --timeout="${WAIT_TIMEOUT_SECONDS}s"
done

deadline=$(( $(date +%s) + WAIT_TIMEOUT_SECONDS ))
until "${KUBECTL}" exec -n "${NAMESPACE}" deployment/llm-proof-sql -- sh -lc \
  'PGPASSWORD=proofsql psql -U proofsql -d proofsql -c "SELECT count(*) FROM order_summaries;" >/dev/null'
do
  if [[ "$(date +%s)" -ge "${deadline}" ]]; then
    echo "Timed out waiting for SQL proof database initialization." >&2
    exit 1
  fi
  sleep 2
done

PROOF_JOB_NAME="llm-proof-runner-${RUN_ID}"
EXTRA_PROOF_ARGS=""

if [[ "${AUDIT_ALL_GENERATED_TOOLS}" == "1" ]]; then
  EXTRA_PROOF_ARGS="${EXTRA_PROOF_ARGS}
            - \"--audit-all-generated-tools\""
fi

if [[ "${ENABLE_LLM_JUDGE}" == "1" ]]; then
  EXTRA_PROOF_ARGS="${EXTRA_PROOF_ARGS}
            - \"--enable-llm-judge\""
fi

# The proof runner needs LLM credentials when judge evaluation is enabled.
PROOF_RUNNER_ENV=""
if [[ "${ENABLE_LLM_JUDGE}" == "1" ]]; then
  PROOF_RUNNER_ENV='          env:
            - name: LLM_PROVIDER
              value: deepseek
            - name: LLM_MODEL
              value: deepseek-chat
            - name: LLM_API_KEY
              valueFrom:
                secretKeyRef:
                  name: llm-e2e-secrets
                  key: llm-api-key'
fi

"${KUBECTL}" delete job -n "${NAMESPACE}" "${PROOF_JOB_NAME}" --ignore-not-found >/dev/null 2>&1 || true

"${KUBECTL}" apply -n "${NAMESPACE}" -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: ${PROOF_JOB_NAME}
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: ${PROOF_JOB_NAME}
    spec:
      restartPolicy: Never
      containers:
        - name: proof-runner
          image: ${PROOF_HELPER_IMAGE}
          imagePullPolicy: Always
${PROOF_RUNNER_ENV}
          command:
            - "python"
            - "-m"
            - "apps.proof_runner.live_llm_e2e"
            - "--api-base-url"
            - "http://${RELEASE_NAME}-compiler-api:8000"
            - "--namespace"
            - "${NAMESPACE}"
            - "--protocol"
            - "${PROTOCOL}"
            - "--timeout-seconds"
            - "${WAIT_TIMEOUT_SECONDS}"
            - "--run-id"
            - "${RUN_ID}"
${EXTRA_PROOF_ARGS}
YAML

if ! "${KUBECTL}" wait -n "${NAMESPACE}" --for=condition=complete "job/${PROOF_JOB_NAME}" --timeout="${WAIT_TIMEOUT_SECONDS}s"; then
  "${KUBECTL}" logs -n "${NAMESPACE}" "job/${PROOF_JOB_NAME}" || true
  echo "Proof runner job ${PROOF_JOB_NAME} failed." >&2
  exit 1
fi

"${KUBECTL}" logs -n "${NAMESPACE}" "job/${PROOF_JOB_NAME}" | tee "${RESULTS_PATH}"

echo "Stored proof results at ${RESULTS_PATH}"
