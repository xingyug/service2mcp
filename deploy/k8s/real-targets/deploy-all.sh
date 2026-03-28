#!/usr/bin/env bash
# deploy-all.sh — Build, push, and deploy all REAL protocol test target services.
#
# Usage:
#   ./deploy/k8s/real-targets/deploy-all.sh              # full deploy (build + apply)
#   ./deploy/k8s/real-targets/deploy-all.sh --build-only
#   ./deploy/k8s/real-targets/deploy-all.sh --apply-only
#   ./deploy/k8s/real-targets/deploy-all.sh --teardown
#
# Environment:
#   REGISTRY  — container registry (default: us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler)
#   NAMESPACE — target namespace (default: tc-real-targets)
#   TAG       — image tag (default: latest)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="${REGISTRY:-us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler}"
NAMESPACE="${NAMESPACE:-tc-real-targets}"
TAG="${TAG:-latest}"

# Custom images: directory:image-name
CUSTOM_BUILDS=(
  "aria2:real-target-aria2"
  "pocketbase:real-target-pocketbase"
  "soap-cxf:real-target-soap-cxf"
)

log()  { echo "[$(date +%H:%M:%S)] $*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

build_and_push() {
  local dir="$1" image="$2"
  local full_image="${REGISTRY}/${image}:${TAG}"
  log "Building ${full_image} from ${dir}/"
  docker build -t "${full_image}" "${SCRIPT_DIR}/${dir}" || fail "Build failed: ${dir}"
  log "Pushing ${full_image}"
  docker push "${full_image}" || fail "Push failed: ${full_image}"
}

wait_for_pods() {
  log "Waiting for all pods in ${NAMESPACE} to be Ready (timeout 600s)..."
  local deadline=$((SECONDS + 600))
  while true; do
    local total not_ready
    total=$(kubectl get pods -n "${NAMESPACE}" --no-headers 2>/dev/null | wc -l || echo 0)
    not_ready=$(kubectl get pods -n "${NAMESPACE}" --no-headers 2>/dev/null \
      | grep -cv "Running\|Completed" || true)
    if [[ "${total}" -gt 0 && "${not_ready}" -eq 0 ]]; then
      log "All ${total} pods Ready ✓"
      return 0
    fi
    if [[ ${SECONDS} -ge ${deadline} ]]; then
      log "Timeout — current pod status:"
      kubectl get pods -n "${NAMESPACE}"
      fail "Pod readiness timeout (600s). Check logs: kubectl logs -n ${NAMESPACE} <pod>"
    fi
    log "  Waiting... ${not_ready}/${total} not ready"
    sleep 10
  done
}

do_build() {
  log "=== Building & pushing custom images ==="
  # Authenticate to Artifact Registry
  gcloud auth configure-docker us-central1-docker.pkg.dev --quiet 2>/dev/null || true
  for entry in "${CUSTOM_BUILDS[@]}"; do
    IFS=':' read -r dir image <<< "${entry}"
    build_and_push "${dir}" "${image}"
  done
  log "=== All images built & pushed ==="
}

do_apply() {
  log "=== Applying Kubernetes manifests ==="

  # Namespace first
  kubectl apply -f "${SCRIPT_DIR}/namespace.yaml"

  # ConfigMaps before Deployments
  for dir in "${SCRIPT_DIR}"/*/; do
    [[ -f "${dir}/configmap.yaml" ]] && kubectl apply -f "${dir}/configmap.yaml"
  done

  # Shared postgres first (others depend on it)
  kubectl apply -f "${SCRIPT_DIR}/shared-postgres/deployment.yaml"
  kubectl apply -f "${SCRIPT_DIR}/shared-postgres/service.yaml"

  log "Waiting for shared-postgres to be ready..."
  kubectl rollout status deployment/real-postgres -n "${NAMESPACE}" --timeout=180s

  # All remaining deployments and services
  for dir in "${SCRIPT_DIR}"/*/; do
    local basename
    basename=$(basename "${dir}")
    [[ "${basename}" == "shared-postgres" ]] && continue
    [[ -f "${dir}/deployment.yaml" ]] && kubectl apply -f "${dir}/deployment.yaml"
    [[ -f "${dir}/service.yaml" ]]    && kubectl apply -f "${dir}/service.yaml"
  done

  log "=== All manifests applied ==="
  wait_for_pods
  log ""
  log "=== Deployment complete! Service endpoints (cluster-internal): ==="
  print_endpoints
}

do_teardown() {
  log "=== Tearing down namespace ${NAMESPACE} ==="
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=false
  log "Namespace deletion initiated"
}

print_endpoints() {
  cat <<ENDPOINTS
  GraphQL+REST+OpenAPI  : http://directus.${NAMESPACE}.svc.cluster.local:8055
    ├─ GraphQL          : /graphql
    ├─ REST API         : /items/<collection>
    └─ OpenAPI spec     : /server/specs/oas

  gRPC (OpenFGA)        : openfga.${NAMESPACE}.svc.cluster.local:8081
    ├─ HTTP API         : http://openfga.${NAMESPACE}.svc.cluster.local:8080
    └─ Playground       : http://openfga.${NAMESPACE}.svc.cluster.local:3000

  JSON-RPC (aria2)      : http://aria2.${NAMESPACE}.svc.cluster.local:6800/jsonrpc
    └─ Secret           : token:test-secret

  OData V4 (Northbreeze): http://northbreeze.${NAMESPACE}.svc.cluster.local:4004
    ├─ Metadata         : /odata/v4/northbreeze/\$metadata
    └─ Service doc      : /odata/v4/northbreeze

  OpenAPI (Gitea)       : http://gitea.${NAMESPACE}.svc.cluster.local:3000
    └─ OpenAPI spec     : /swagger.v1.json

  REST/JSON (PocketBase): http://pocketbase.${NAMESPACE}.svc.cluster.local:8090
    └─ API              : /api/collections/<name>/records

  SCIM (Jackson)        : http://jackson.${NAMESPACE}.svc.cluster.local:5225
    └─ SCIM base        : /api/scim/v2.0/<tenant>/<product>/Users
    └─ API Key          : tc-test-api-key-12345  # gitleaks:allow

  SOAP (Spring CXF)     : http://soap-cxf.${NAMESPACE}.svc.cluster.local:8080
    └─ WSDL             : /services/OrderService?wsdl

  SQL (PostgreSQL)      : postgresql://catalog:catalog@real-postgres.${NAMESPACE}.svc.cluster.local:5432/catalog_v2
ENDPOINTS
}

case "${1:-}" in
  --build-only) do_build ;;
  --apply-only) do_apply ;;
  --teardown)   do_teardown ;;
  --endpoints)  print_endpoints ;;
  "")           do_build; do_apply ;;
  *)            echo "Usage: $0 [--build-only|--apply-only|--teardown|--endpoints]"; exit 1 ;;
esac
