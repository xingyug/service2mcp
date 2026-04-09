#!/usr/bin/env bash
# deploy-all.sh — Build, push, and deploy all protocol test target services.
#
# Usage:
#   ./deploy/k8s/test-targets/deploy-all.sh              # full deploy
#   ./deploy/k8s/test-targets/deploy-all.sh --build-only  # build & push only
#   ./deploy/k8s/test-targets/deploy-all.sh --apply-only  # kubectl apply only
#   ./deploy/k8s/test-targets/deploy-all.sh --teardown    # delete namespace
#
# Environment:
#   REGISTRY   — container registry (default: YOUR_REGISTRY/YOUR_PROJECT)
#   NAMESPACE  — target namespace (default: example-test-targets)
#   TAG        — image tag (default: latest)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="${REGISTRY:-YOUR_REGISTRY/YOUR_PROJECT}"
NAMESPACE="${NAMESPACE:-example-test-targets}"
TAG="${TAG:-latest}"

# Services that require custom image builds (directory → image name)
CUSTOM_BUILDS=(
  "rest-jsonserver:test-target-rest"
  "graphql-server:test-target-graphql"
  "grpc-server:test-target-grpc"
  "soap-server:test-target-soap"
  "odata-server:test-target-odata"
  "scim-server:test-target-scim"
  "jsonrpc-server:test-target-jsonrpc"
)

# ── helpers ──────────────────────────────────────────────────────────

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
  log "Waiting for all pods in ${NAMESPACE} to be Ready (timeout 300s)..."
  local deadline=$((SECONDS + 300))
  while true; do
    local not_ready
    not_ready=$(kubectl get pods -n "${NAMESPACE}" --no-headers 2>/dev/null \
      | grep -cv "Running\|Completed" || true)
    if [[ "${not_ready}" -eq 0 ]]; then
      local total
      total=$(kubectl get pods -n "${NAMESPACE}" --no-headers 2>/dev/null | wc -l)
      if [[ "${total}" -gt 0 ]]; then
        log "All ${total} pods Ready ✓"
        return 0
      fi
    fi
    if [[ ${SECONDS} -ge ${deadline} ]]; then
      log "Timeout waiting for pods — current status:"
      kubectl get pods -n "${NAMESPACE}" --no-headers
      fail "Pod readiness timeout exceeded (300s)"
    fi
    sleep 5
  done
}

# ── actions ──────────────────────────────────────────────────────────

do_build() {
  log "=== Building & pushing custom images ==="
  for entry in "${CUSTOM_BUILDS[@]}"; do
    IFS=':' read -r dir image <<< "${entry}"
    build_and_push "${dir}" "${image}"
  done
  log "=== All images built & pushed ==="
}

do_apply() {
  log "=== Applying Kubernetes manifests ==="

  # Namespace
  kubectl apply -f "${SCRIPT_DIR}/namespace.yaml"

  # Apply all service manifests (configmaps first, then deployments & services)
  for dir in "${SCRIPT_DIR}"/*/; do
    [[ -f "${dir}/configmap.yaml" ]] && kubectl apply -f "${dir}/configmap.yaml"
  done
  for dir in "${SCRIPT_DIR}"/*/; do
    [[ -f "${dir}/deployment.yaml" ]] && kubectl apply -f "${dir}/deployment.yaml"
    [[ -f "${dir}/service.yaml" ]]    && kubectl apply -f "${dir}/service.yaml"
  done

  log "=== Manifests applied ==="
  wait_for_pods
  log "=== Deployment complete ==="
  echo ""
  log "Service endpoints (cluster-internal):"
  echo "  REST:     http://rest-jsonserver.${NAMESPACE}.svc.cluster.local:3000"
  echo "  OpenAPI:  http://openapi-petstore.${NAMESPACE}.svc.cluster.local:8080/api/v3/openapi.json"
  echo "  GraphQL:  http://graphql-server.${NAMESPACE}.svc.cluster.local:4000/graphql"
  echo "  gRPC:     grpc-server.${NAMESPACE}.svc.cluster.local:50051"
  echo "  SOAP:     http://soap-server.${NAMESPACE}.svc.cluster.local:8000/?wsdl"
  echo "  SQL:      postgresql://catalog:catalog@sql-postgres.${NAMESPACE}.svc.cluster.local:5432/catalog"
  echo "  OData:    http://odata-server.${NAMESPACE}.svc.cluster.local:8000/odata/\$metadata"
  echo "  SCIM:     http://scim-server.${NAMESPACE}.svc.cluster.local:8000/scim/v2/Schemas"
  echo "  JSON-RPC: http://jsonrpc-server.${NAMESPACE}.svc.cluster.local:8000/rpc"
}

do_teardown() {
  log "=== Tearing down namespace ${NAMESPACE} ==="
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=false
  log "Namespace deletion initiated (async)"
}

# ── main ─────────────────────────────────────────────────────────────

case "${1:-}" in
  --build-only) do_build ;;
  --apply-only) do_apply ;;
  --teardown)   do_teardown ;;
  "")           do_build; do_apply ;;
  *)            echo "Usage: $0 [--build-only|--apply-only|--teardown]"; exit 1 ;;
esac
