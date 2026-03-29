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
PROOF_PROFILE="${PROOF_PROFILE:-mock}"
UPSTREAM_NAMESPACE="${UPSTREAM_NAMESPACE:-tc-real-targets}"
AUDIT_ALL_GENERATED_TOOLS="${AUDIT_ALL_GENERATED_TOOLS:-0}"
ENABLE_TOOL_GROUPING="${ENABLE_TOOL_GROUPING:-0}"
ENABLE_LLM_JUDGE="${ENABLE_LLM_JUDGE:-0}"
ENABLE_LLM_ENHANCEMENT="${ENABLE_LLM_ENHANCEMENT:-1}"
CASE_IDS="${CASE_IDS:-}"
IMAGE_REPO_BASE="${IMAGE_REPO_BASE:-us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
COMPILER_API_IMAGE="${COMPILER_API_IMAGE:-${IMAGE_REPO_BASE}/compiler-api:${IMAGE_TAG}}"
ACCESS_CONTROL_IMAGE="${ACCESS_CONTROL_IMAGE:-${IMAGE_REPO_BASE}/access-control:${IMAGE_TAG}}"
COMPILER_WORKER_IMAGE="${COMPILER_WORKER_IMAGE:-${IMAGE_REPO_BASE}/compiler-worker:${IMAGE_TAG}}"
MCP_RUNTIME_IMAGE="${MCP_RUNTIME_IMAGE:-${IMAGE_REPO_BASE}/mcp-runtime:${IMAGE_TAG}}"
PROOF_HELPER_IMAGE="${PROOF_HELPER_IMAGE:-${COMPILER_API_IMAGE}}"
LLM_API_KEY_FILE="${LLM_API_KEY_FILE:-/home/guoxy/esoc-agents/.deepseek_api_key}"
REAL_TARGET_ENV_FILE="${REAL_TARGET_ENV_FILE:-${ROOT_DIR}/.env.real-targets.local}"
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

if [[ ("${ENABLE_LLM_ENHANCEMENT}" == "1" || "${ENABLE_LLM_JUDGE}" == "1") && ! -f "${LLM_API_KEY_FILE}" ]]; then
  echo "DeepSeek API key file ${LLM_API_KEY_FILE} was not found." >&2
  exit 1
fi

case "${PROTOCOL}" in
  all|graphql|rest|openapi|grpc|jsonrpc|odata|scim|soap|sql)
    ;;
  *)
    echo "Unsupported PROTOCOL=${PROTOCOL}. Expected one of: all, graphql, rest, openapi, grpc, jsonrpc, odata, scim, soap, sql." >&2
    exit 1
    ;;
esac

case "${PROOF_PROFILE}" in
  mock|real-targets)
    ;;
  *)
    echo "Unsupported PROOF_PROFILE=${PROOF_PROFILE}. Expected mock or real-targets." >&2
    exit 1
    ;;
esac

image_repo() {
  printf '%s' "${1%:*}"
}

image_tag() {
  printf '%s' "${1##*:}"
}

append_secret_env() {
  local env_name="$1"
  local secret_name="$2"
  local secret_key="$3"
  PROOF_RUNNER_ENV_ENTRIES="${PROOF_RUNNER_ENV_ENTRIES}
            - name: ${env_name}
              valueFrom:
                secretKeyRef:
                  name: ${secret_name}
                  key: ${secret_key}"
}

load_real_target_env_file() {
  if [[ -f "${REAL_TARGET_ENV_FILE}" ]]; then
    echo "Loading local real-target overrides from ${REAL_TARGET_ENV_FILE}"
    set -a
    # shellcheck disable=SC1090
    source "${REAL_TARGET_ENV_FILE}"
    set +a
  fi
}

fetch_directus_access_token() {
  local attempt
  local output
  for attempt in 1 2 3 4 5; do
    if output="$("${KUBECTL}" exec -n "${UPSTREAM_NAMESPACE}" deployment/directus -- \
      node -e '
const namespace = process.argv[1];
const baseUrl = `http://directus.${namespace}.svc.cluster.local:8055`;
fetch(`${baseUrl}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "admin@example.com", password: "Admin123!" }),
})
  .then(async (response) => {
    if (!response.ok) {
      throw new Error(`Directus login failed: ${response.status} ${await response.text()}`);
    }
    const payload = await response.json();
    process.stdout.write(payload.data.access_token);
  })
  .catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
' "${UPSTREAM_NAMESPACE}" | tr -d '\r')"; then
      printf '%s' "${output}"
      return 0
    fi
    sleep $(( attempt * 2 ))
  done
  return 1
}

fetch_pocketbase_access_token() {
  local attempt
  local output
  for attempt in 1 2 3 4 5; do
    if output="$("${KUBECTL}" exec -n "${UPSTREAM_NAMESPACE}" deployment/directus -- \
      node -e '
const namespace = process.argv[1];
const baseUrl = `http://pocketbase.${namespace}.svc.cluster.local:8090`;
fetch(`${baseUrl}/api/collections/_superusers/auth-with-password`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ identity: "admin@example.com", password: "Admin12345!" }),
})
  .then(async (response) => {
    if (!response.ok) {
      throw new Error(`PocketBase login failed: ${response.status} ${await response.text()}`);
    }
    const payload = await response.json();
    process.stdout.write(payload.token);
  })
  .catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
' "${UPSTREAM_NAMESPACE}" | tr -d '\r')"; then
      printf '%s' "${output}"
      return 0
    fi
    sleep $(( attempt * 2 ))
  done
  return 1
}

fetch_jackson_scim_info() {
  local attempt
  local output
  for attempt in 1 2 3 4 5; do
    if output="$("${KUBECTL}" exec -n "${UPSTREAM_NAMESPACE}" deployment/directus -- \
      node -e '
const namespace = process.argv[1];
const product = process.argv[2];
const baseUrl = `http://jackson.${namespace}.svc.cluster.local:5225`;
fetch(`${baseUrl}/api/v1/dsync`, {
  method: "POST",
  headers: {
    "Authorization": "Api-Key tc-test-api-key-12345",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ tenant: "tc", product, type: "generic-scim-v2" }),
})
  .then(async (response) => {
    if (!response.ok) {
      throw new Error(`Jackson directory create failed: ${response.status} ${await response.text()}`);
    }
    const payload = await response.json();
    process.stdout.write(`${payload.data.scim.path}\t${payload.data.scim.secret}`);
  })
  .catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
' "${UPSTREAM_NAMESPACE}" "proof${RUN_ID,,}" | tr -d '\r')"; then
      printf '%s' "${output}"
      return 0
    fi
    sleep $(( attempt * 2 ))
  done
  return 1
}

create_mock_secrets() {
  local secret_args=(
    create secret generic llm-e2e-secrets
    --namespace "${NAMESPACE}"
  )
  if [[ "${ENABLE_LLM_ENHANCEMENT}" == "1" || "${ENABLE_LLM_JUDGE}" == "1" ]]; then
    secret_args+=(--from-file=llm-api-key="${LLM_API_KEY_FILE}")
  fi
  "${KUBECTL}" "${secret_args[@]}" --dry-run=client -o yaml | "${KUBECTL}" apply -f -
}

create_real_target_secrets() {
  local directus_token="${REAL_TARGET_DIRECTUS_ACCESS_TOKEN:-}"
  local pocketbase_token="${REAL_TARGET_POCKETBASE_ACCESS_TOKEN:-}"
  local gitea_basic_auth="${REAL_TARGET_GITEA_BASIC_AUTH:-gitea_admin:Admin123!}"
  local jackson_info=""
  local jackson_scim_path=""
  local jackson_scim_secret="${REAL_TARGET_JACKSON_SCIM_SECRET:-}"
  local jackson_scim_base_url="${REAL_TARGET_JACKSON_SCIM_BASE_URL:-}"

  if [[ -z "${directus_token}" ]]; then
    directus_token="$(fetch_directus_access_token)"
  fi
  if [[ -z "${pocketbase_token}" ]]; then
    pocketbase_token="$(fetch_pocketbase_access_token)"
  fi
  if [[ -z "${jackson_scim_secret}" || -z "${jackson_scim_base_url}" ]]; then
    jackson_info="$(fetch_jackson_scim_info)"
    jackson_scim_path="${jackson_info%%$'\t'*}"
    jackson_scim_secret="${jackson_info#*$'\t'}"
    jackson_scim_base_url="http://jackson.${UPSTREAM_NAMESPACE}.svc.cluster.local:5225${jackson_scim_path}"
  fi

  local llm_secret_args=(
    create secret generic llm-e2e-secrets
    --namespace "${NAMESPACE}"
    --from-literal=directus-access-token="${directus_token}"
    --from-literal=pocketbase-access-token="${pocketbase_token}"
    --from-literal=gitea-basic-auth="${gitea_basic_auth}"
    --from-literal=jackson-scim-base-url="${jackson_scim_base_url}"
    --from-literal=jackson-scim-secret="${jackson_scim_secret}"
  )
  if [[ "${ENABLE_LLM_ENHANCEMENT}" == "1" || "${ENABLE_LLM_JUDGE}" == "1" ]]; then
    llm_secret_args+=(--from-file=llm-api-key="${LLM_API_KEY_FILE}")
  fi
  "${KUBECTL}" "${llm_secret_args[@]}" --dry-run=client -o yaml | "${KUBECTL}" apply -f -

  "${KUBECTL}" create secret generic tool-compiler-runtime-secrets \
    --namespace "${NAMESPACE}" \
    --from-literal=directus-access-token="${directus_token}" \
    --from-literal=pocketbase-access-token="${pocketbase_token}" \
    --from-literal=gitea-basic-auth="${gitea_basic_auth}" \
    --from-literal=jackson-scim-secret="${jackson_scim_secret}" \
    --dry-run=client \
    -o yaml | "${KUBECTL}" apply -f -
}

COMPILER_WORKER_SECRET_ENV_BLOCK=""
if [[ "${ENABLE_LLM_ENHANCEMENT}" == "1" || "${ENABLE_LLM_JUDGE}" == "1" ]]; then
  COMPILER_WORKER_SECRET_ENV_BLOCK=$(cat <<'YAML'
  secretEnv:
    - name: LLM_API_KEY
      secretName: llm-e2e-secrets
      secretKey: llm-api-key
YAML
)
fi

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
      value: "${ENABLE_LLM_ENHANCEMENT}"
    - name: WORKER_ENABLE_TOOL_GROUPING
      value: "${ENABLE_TOOL_GROUPING}"
${COMPILER_WORKER_SECRET_ENV_BLOCK}
YAML

cd "${ROOT_DIR}"

echo "Running GKE proof with profile=${PROOF_PROFILE} protocol=${PROTOCOL} llm_enhancement=${ENABLE_LLM_ENHANCEMENT}"

"${KUBECTL}" get namespace "${NAMESPACE}" >/dev/null 2>&1 || "${KUBECTL}" create namespace "${NAMESPACE}" >/dev/null

if [[ "${PROOF_PROFILE}" == "real-targets" ]]; then
  load_real_target_env_file
  create_real_target_secrets
else
  create_mock_secrets
fi

if [[ "${PROOF_PROFILE}" == "mock" ]]; then
  "${KUBECTL}" create configmap llm-proof-sql-init \
    --namespace "${NAMESPACE}" \
    --from-file=init.sql="${ROOT_DIR}/tests/fixtures/sql_schemas/catalog_live.sql" \
    --dry-run=client \
    -o yaml | "${KUBECTL}" apply -f -
fi

"${HELM_BIN}" upgrade --install "${RELEASE_NAME}" \
  "${ROOT_DIR}/deploy/helm/tool-compiler" \
  --namespace "${NAMESPACE}" \
  -f "${ROOT_DIR}/deploy/helm/tool-compiler/values.yaml" \
  -f "${VALUES_OVERRIDE_PATH}" \
  --wait \
  --timeout "${WAIT_TIMEOUT_SECONDS}s"

if [[ "${PROOF_PROFILE}" == "mock" ]]; then
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
fi

DEPLOYMENTS=(
  "${RELEASE_NAME}-compiler-api"
  "${RELEASE_NAME}-access-control"
  "${RELEASE_NAME}-compiler-worker"
  "${RELEASE_NAME}-gateway-admin-mock"
)
if [[ "${PROOF_PROFILE}" == "mock" ]]; then
  DEPLOYMENTS+=("llm-proof-http" "llm-proof-grpc" "llm-proof-sql")
fi

for deployment in "${DEPLOYMENTS[@]}"; do
  "${KUBECTL}" rollout status -n "${NAMESPACE}" "deployment/${deployment}" --timeout="${WAIT_TIMEOUT_SECONDS}s"
done

if [[ "${PROOF_PROFILE}" == "mock" ]]; then
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
fi

PROOF_JOB_NAME="llm-proof-runner-${RUN_ID}"
EXTRA_PROOF_ARGS=""
PROOF_RUNNER_ENV_ENTRIES=""

if [[ "${AUDIT_ALL_GENERATED_TOOLS}" == "1" ]]; then
  EXTRA_PROOF_ARGS="${EXTRA_PROOF_ARGS}
            - \"--audit-all-generated-tools\""
fi

if [[ "${ENABLE_LLM_ENHANCEMENT}" != "1" ]]; then
  EXTRA_PROOF_ARGS="${EXTRA_PROOF_ARGS}
            - \"--skip-llm-artifact-checks\""
fi

if [[ "${ENABLE_LLM_JUDGE}" == "1" ]]; then
  EXTRA_PROOF_ARGS="${EXTRA_PROOF_ARGS}
            - \"--enable-llm-judge\""
  PROOF_RUNNER_ENV_ENTRIES="${PROOF_RUNNER_ENV_ENTRIES}
            - name: LLM_PROVIDER
              value: deepseek
            - name: LLM_MODEL
              value: deepseek-chat"
  append_secret_env "LLM_API_KEY" "llm-e2e-secrets" "llm-api-key"
fi

if [[ "${PROOF_PROFILE}" == "real-targets" ]]; then
  append_secret_env "PROOF_DIRECTUS_ACCESS_TOKEN" "llm-e2e-secrets" "directus-access-token"
  append_secret_env "PROOF_POCKETBASE_ACCESS_TOKEN" "llm-e2e-secrets" "pocketbase-access-token"
  append_secret_env "PROOF_GITEA_BASIC_AUTH" "llm-e2e-secrets" "gitea-basic-auth"
  append_secret_env "PROOF_JACKSON_SCIM_BASE_URL" "llm-e2e-secrets" "jackson-scim-base-url"
  append_secret_env "PROOF_JACKSON_SCIM_SECRET" "llm-e2e-secrets" "jackson-scim-secret"
fi

if [[ -n "${CASE_IDS}" ]]; then
  IFS=',' read -r -a requested_case_ids <<< "${CASE_IDS}"
  for case_id in "${requested_case_ids[@]}"; do
    case_id="${case_id// /}"
    if [[ -z "${case_id}" ]]; then
      continue
    fi
    EXTRA_PROOF_ARGS="${EXTRA_PROOF_ARGS}
            - \"--case-id\"
            - \"${case_id}\""
  done
fi

PROOF_RUNNER_ENV=""
if [[ -n "${PROOF_RUNNER_ENV_ENTRIES}" ]]; then
  PROOF_RUNNER_ENV="          env:${PROOF_RUNNER_ENV_ENTRIES}"
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
            - "--profile"
            - "${PROOF_PROFILE}"
            - "--upstream-namespace"
            - "${UPSTREAM_NAMESPACE}"
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
