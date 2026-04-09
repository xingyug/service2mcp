#!/usr/bin/env bash
# seed-all.sh — Seed mock data into all real test target services after deployment.
#
# Usage:
#   ./deploy/k8s/real-targets/seed-all.sh [--via-port-forward]
#
# Requires: kubectl, curl, jq
# Seeds: Directus collections, OpenFGA store+model+tuples, Gitea users+repos+tokens, PocketBase collections+records

set -euo pipefail

NAMESPACE="${NAMESPACE:-example-targets}"
USE_PORT_FORWARD=false
[[ "${1:-}" == "--via-port-forward" ]] && USE_PORT_FORWARD=true
DIRECTUS_URL=""
OPENFGA_URL=""
GITEA_URL=""
POCKETBASE_URL=""

log()     { echo "[$(date +%H:%M:%S)] $*"; }
log_ok()  { echo "[$(date +%H:%M:%S)] ✓ $*"; }
log_err() { echo "[$(date +%H:%M:%S)] ✗ $*" >&2; }
fail()    { log_err "$*"; exit 1; }

# ── Port-forward helpers ──────────────────────────────────────────────────────
PF_PIDS=()
cleanup() { for pid in "${PF_PIDS[@]:-}"; do kill "${pid}" 2>/dev/null || true; done; }
trap cleanup EXIT

pf_start() {
  local svc="$1" local_port="$2" remote_port="$3"
  local pf_log="/tmp/seed-port-forward-${svc}-${local_port}.log"
  : > "${pf_log}"
  kubectl port-forward "svc/${svc}" "${local_port}:${remote_port}" -n "${NAMESPACE}" >"${pf_log}" 2>&1 &
  local pf_pid=$!
  PF_PIDS+=(${pf_pid})
  for _ in $(seq 1 30); do
    if grep -q "Forwarding from" "${pf_log}" 2>/dev/null; then
      log "Port-forward: ${svc}:${remote_port} → localhost:${local_port}"
      return 0
    fi
    if ! kill -0 "${pf_pid}" 2>/dev/null; then
      cat "${pf_log}" >&2 || true
      fail "Port-forward for ${svc}:${remote_port} exited before becoming ready"
    fi
    sleep 1
  done
  cat "${pf_log}" >&2 || true
  fail "Port-forward for ${svc}:${remote_port} did not become ready within 30s"
}

setup_port_forward_urls() {
  pf_start directus  18055 8055
  pf_start openfga   18080 8080
  pf_start gitea     13000 3000
  pf_start pocketbase 18090 8090

  DIRECTUS_URL="http://localhost:18055"
  OPENFGA_URL="http://localhost:18080"
  GITEA_URL="http://localhost:13000"
  POCKETBASE_URL="http://localhost:18090"
}

configure_base_urls() {
  if [[ "${USE_PORT_FORWARD}" == "true" ]]; then
    setup_port_forward_urls
    return
  fi

  DIRECTUS_URL="http://directus.${NAMESPACE}.svc.cluster.local:8055"
  if ! curl -sf --connect-timeout 2 --max-time 5 "${DIRECTUS_URL}/server/health" >/dev/null 2>&1; then
    log "Cluster-internal service DNS is not reachable from this shell; falling back to port-forward mode"
    USE_PORT_FORWARD=true
    setup_port_forward_urls
    return
  fi

  # Run seeds inside a kubectl exec pod (e.g., a busybox debug pod in the namespace)
  DIRECTUS_URL="http://directus.${NAMESPACE}.svc.cluster.local:8055"
  OPENFGA_URL="http://openfga.${NAMESPACE}.svc.cluster.local:8080"
  GITEA_URL="http://gitea.${NAMESPACE}.svc.cluster.local:3000"
  POCKETBASE_URL="http://pocketbase.${NAMESPACE}.svc.cluster.local:8090"
}

# ── Base URLs ─────────────────────────────────────────────────────────────────
configure_base_urls

curl_json() {
  curl -sf -H 'Content-Type: application/json' "$@"
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. DIRECTUS — seed collections and items
# ─────────────────────────────────────────────────────────────────────────────
seed_directus() {
  log "=== Seeding Directus ==="

  # Get auth token
  local token
  token=$(curl_json -d '{"email":"admin@example.com","password":"Admin123!"}' \
    "${DIRECTUS_URL}/auth/login" | jq -r '.data.access_token')
  [[ -z "${token}" || "${token}" == "null" ]] && fail "Directus: could not get auth token"
  log_ok "Directus: authenticated as admin"

  local AUTH="Authorization: Bearer ${token}"

  # Create collections
  for coll in products customers orders; do
    local exists
    exists=$(curl -sf -H "${AUTH}" "${DIRECTUS_URL}/collections/${coll}" 2>/dev/null | jq -r '.data.collection // empty' || true)
    if [[ -z "${exists}" ]]; then
      curl_json -H "${AUTH}" -d "{\"collection\":\"${coll}\",\"meta\":{\"icon\":\"box\"},\"schema\":{}}" \
        "${DIRECTUS_URL}/collections" > /dev/null
      log_ok "Directus: created collection ${coll}"
    else
      log "Directus: collection ${coll} already exists, skipping"
    fi
  done

  # Add fields to products
  for field_json in \
    '{"field":"name","type":"string","schema":{"is_nullable":false},"meta":{"interface":"input"}}' \
    '{"field":"sku","type":"string","schema":{"is_nullable":false},"meta":{"interface":"input"}}' \
    '{"field":"price","type":"decimal","schema":{"is_nullable":true},"meta":{"interface":"input"}}' \
    '{"field":"category","type":"string","schema":{"is_nullable":true},"meta":{"interface":"input"}}' \
    '{"field":"in_stock","type":"boolean","schema":{"default_value":true},"meta":{"interface":"boolean"}}' \
    '{"field":"description","type":"text","schema":{"is_nullable":true},"meta":{"interface":"textarea"}}'; do
    curl_json -H "${AUTH}" -d "${field_json}" "${DIRECTUS_URL}/fields/products" > /dev/null 2>&1 || true
  done

  # Add fields to customers
  for field_json in \
    '{"field":"full_name","type":"string","schema":{"is_nullable":false},"meta":{"interface":"input"}}' \
    '{"field":"email","type":"string","schema":{"is_nullable":false},"meta":{"interface":"input"}}' \
    '{"field":"tier","type":"string","schema":{"default_value":"standard"},"meta":{"interface":"input"}}' \
    '{"field":"active","type":"boolean","schema":{"default_value":true},"meta":{"interface":"boolean"}}'; do
    curl_json -H "${AUTH}" -d "${field_json}" "${DIRECTUS_URL}/fields/customers" > /dev/null 2>&1 || true
  done

  # Add fields to orders
  for field_json in \
    '{"field":"order_number","type":"string","schema":{"is_nullable":false},"meta":{"interface":"input"}}' \
    '{"field":"status","type":"string","schema":{"default_value":"pending"},"meta":{"interface":"input"}}' \
    '{"field":"total_amount","type":"decimal","schema":{"is_nullable":true},"meta":{"interface":"input"}}' \
    '{"field":"customer_id","type":"integer","schema":{"is_nullable":true},"meta":{"interface":"input"}}'; do
    curl_json -H "${AUTH}" -d "${field_json}" "${DIRECTUS_URL}/fields/orders" > /dev/null 2>&1 || true
  done
  log_ok "Directus: fields created"

  # Seed products
  curl_json -H "${AUTH}" -d '{"name":"Galaxy Pro Max","sku":"PHONE-001","price":999.00,"category":"Electronics","in_stock":true,"description":"Flagship smartphone 256GB"}' \
    "${DIRECTUS_URL}/items/products" > /dev/null
  curl_json -H "${AUTH}" -d '{"name":"ThinkPad X1 Carbon","sku":"LAPTOP-001","price":1699.00,"category":"Electronics","in_stock":true,"description":"Ultra-light business laptop"}' \
    "${DIRECTUS_URL}/items/products" > /dev/null
  curl_json -H "${AUTH}" -d '{"name":"The Pragmatic Programmer","sku":"BOOK-001","price":49.99,"category":"Books","in_stock":true,"description":"20th Anniversary Edition"}' \
    "${DIRECTUS_URL}/items/products" > /dev/null
  curl_json -H "${AUTH}" -d '{"name":"UltraBoost Running","sku":"SHOE-001","price":180.00,"category":"Sports","in_stock":true,"description":"Responsive running shoes"}' \
    "${DIRECTUS_URL}/items/products" > /dev/null
  curl_json -H "${AUTH}" -d '{"name":"Robot Vacuum Pro","sku":"HOME-002","price":499.00,"category":"Home","in_stock":false,"description":"LiDAR robot vacuum with mop"}' \
    "${DIRECTUS_URL}/items/products" > /dev/null
  log_ok "Directus: 5 products seeded"

  # Seed customers
  curl_json -H "${AUTH}" -d '{"full_name":"Alice Johnson","email":"alice@example.com","tier":"platinum","active":true}' \
    "${DIRECTUS_URL}/items/customers" > /dev/null
  curl_json -H "${AUTH}" -d '{"full_name":"Bob Smith","email":"bob@example.com","tier":"gold","active":true}' \
    "${DIRECTUS_URL}/items/customers" > /dev/null
  curl_json -H "${AUTH}" -d '{"full_name":"Carol Williams","email":"carol@example.com","tier":"standard","active":true}' \
    "${DIRECTUS_URL}/items/customers" > /dev/null
  curl_json -H "${AUTH}" -d '{"full_name":"Dave Brown","email":"dave@example.com","tier":"enterprise","active":true}' \
    "${DIRECTUS_URL}/items/customers" > /dev/null
  log_ok "Directus: 4 customers seeded"

  # Seed orders
  curl_json -H "${AUTH}" -d '{"order_number":"ORD-D-001","status":"delivered","total_amount":999.00,"customer_id":1}' \
    "${DIRECTUS_URL}/items/orders" > /dev/null
  curl_json -H "${AUTH}" -d '{"order_number":"ORD-D-002","status":"shipped","total_amount":1699.00,"customer_id":1}' \
    "${DIRECTUS_URL}/items/orders" > /dev/null
  curl_json -H "${AUTH}" -d '{"order_number":"ORD-D-003","status":"processing","total_amount":49.99,"customer_id":2}' \
    "${DIRECTUS_URL}/items/orders" > /dev/null
  curl_json -H "${AUTH}" -d '{"order_number":"ORD-D-004","status":"pending","total_amount":499.00,"customer_id":3}' \
    "${DIRECTUS_URL}/items/orders" > /dev/null
  log_ok "Directus: 4 orders seeded"

  log_ok "=== Directus seeding complete ==="
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. OPENFGA — create store, write model, write tuples
# ─────────────────────────────────────────────────────────────────────────────
seed_openfga() {
  log "=== Seeding OpenFGA ==="

  # Create a store
  local store_id
  store_id=$(curl_json -d '{"name":"tool-compiler-test"}' \
    "${OPENFGA_URL}/stores" | jq -r '.id')
  [[ -z "${store_id}" || "${store_id}" == "null" ]] && fail "OpenFGA: could not create store"
  log_ok "OpenFGA: store created → ${store_id}"

  # Write authorization model
  local model
  model=$(cat /dev/stdin << 'MODELEOF'
{
  "schema_version": "1.1",
  "type_definitions": [
    { "type": "user" },
    {
      "type": "organization",
      "relations": {
        "owner":  { "this": {} },
        "admin":  { "this": {} },
        "member": { "union": { "child": [{"this":{}},{"computedUserset":{"relation":"admin"}},{"computedUserset":{"relation":"owner"}}] } }
      },
      "metadata": {
        "relations": {
          "owner":  { "directly_related_user_types": [{"type":"user"}] },
          "admin":  { "directly_related_user_types": [{"type":"user"}] },
          "member": { "directly_related_user_types": [{"type":"user"}] }
        }
      }
    },
    {
      "type": "document",
      "relations": {
        "owner":  { "this": {} },
        "editor": { "union": { "child": [{"this":{}},{"computedUserset":{"relation":"owner"}}] } },
        "viewer": { "union": { "child": [{"this":{}},{"computedUserset":{"relation":"editor"}}] } }
      },
      "metadata": {
        "relations": {
          "owner":  { "directly_related_user_types": [{"type":"user"}] },
          "editor": { "directly_related_user_types": [{"type":"user"}] },
          "viewer": { "directly_related_user_types": [{"type":"user"},{"type":"organization","relation":"member"}] }
        }
      }
    }
  ]
}
MODELEOF
)

  local model_id
  model_id=$(curl_json -d "${model}" \
    "${OPENFGA_URL}/stores/${store_id}/authorization-models" | jq -r '.authorization_model_id')
  [[ -z "${model_id}" || "${model_id}" == "null" ]] && fail "OpenFGA: could not write authorization model"
  log_ok "OpenFGA: authorization model written → ${model_id}"

  # Write relationship tuples
  curl_json -d '{
    "writes": {
      "tuple_keys": [
        {"object":"organization:acme","relation":"owner","user":"user:alice"},
        {"object":"organization:acme","relation":"admin","user":"user:bob"},
        {"object":"organization:acme","relation":"member","user":"user:carol"},
        {"object":"organization:globex","relation":"owner","user":"user:dave"},
        {"object":"document:readme","relation":"owner","user":"user:alice"},
        {"object":"document:readme","relation":"viewer","user":"organization:acme#member"},
        {"object":"document:roadmap","relation":"editor","user":"user:bob"},
        {"object":"document:roadmap","relation":"viewer","user":"user:carol"},
        {"object":"document:secret","relation":"owner","user":"user:dave"},
        {"object":"document:shared","relation":"viewer","user":"user:alice"},
        {"object":"document:shared","relation":"viewer","user":"user:bob"}
      ]
    }
  }' "${OPENFGA_URL}/stores/${store_id}/write" > /dev/null

  log_ok "OpenFGA: 11 relationship tuples written"

  # Save store_id to a ConfigMap for reference
  kubectl create configmap openfga-seed-info \
    --from-literal=store_id="${store_id}" \
    --from-literal=model_id="${model_id}" \
    -n "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
  log_ok "OpenFGA: store/model IDs saved to ConfigMap openfga-seed-info"
  log_ok "=== OpenFGA seeding complete ==="
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. GITEA — create admin, orgs, repos, API token
# ─────────────────────────────────────────────────────────────────────────────
seed_gitea() {
  log "=== Seeding Gitea ==="

  # Create admin user via CLI (must run as 'git' user, not root)
  local admin_created=false
  if kubectl exec -n "${NAMESPACE}" deploy/gitea -- \
      su git -c 'gitea admin user create --username gitea_admin --password "Admin123!" --email admin@example.com --admin --must-change-password=false' 2>/dev/null; then
    admin_created=true
    log_ok "Gitea: admin user 'gitea_admin' created via CLI"
  else
    log "Gitea: admin may already exist, continuing with API"
  fi

  local BASIC="gitea_admin:Admin123!"

  # Create API token
  local token
  token=$(curl -sf -u "${BASIC}" -d '{"name":"tc-seed-token","scopes":["write:repository","write:user","write:organization"]}' \
    -H 'Content-Type: application/json' \
    "${GITEA_URL}/api/v1/users/gitea_admin/tokens" | jq -r '.sha1 // empty' || true)
  [[ -z "${token}" ]] && token="seed-token-not-available"

  local AUTH_HEADER="Authorization: token ${token}"
  [[ "${token}" == "seed-token-not-available" ]] && AUTH_HEADER="Authorization: Basic $(echo -n "${BASIC}" | base64)"

  # Create additional users
  for userdata in \
    '{"username":"alice","password":"Test123!","email":"alice@example.com","full_name":"Alice Johnson"}' \
    '{"username":"bob","password":"Test123!","email":"bob@example.com","full_name":"Bob Smith"}' \
    '{"username":"carol","password":"Test123!","email":"carol@example.com","full_name":"Carol Williams"}'; do
    curl -sf -u "${BASIC}" -H 'Content-Type: application/json' \
      -d "${userdata}" "${GITEA_URL}/api/v1/admin/users" > /dev/null 2>&1 || true
  done
  log_ok "Gitea: users alice/bob/carol created"

  # Create organisations
  for org_data in \
    '{"username":"acme-corp","visibility":"public","full_name":"Acme Corporation","description":"Enterprise test org"}' \
    '{"username":"toolcompiler","visibility":"public","full_name":"service2mcp","description":"Internal tooling org"}'; do
    curl -sf -u "${BASIC}" -H 'Content-Type: application/json' \
      -d "${org_data}" "${GITEA_URL}/api/v1/orgs" > /dev/null 2>&1 || true
  done
  log_ok "Gitea: orgs created"

  # Create repos under tc-admin
  for repo_data in \
    '{"name":"service-api","description":"Main REST API service with OpenAPI spec","private":false,"auto_init":true,"default_branch":"main"}' \
    '{"name":"data-pipeline","description":"ETL pipeline configuration","private":false,"auto_init":true,"default_branch":"main"}' \
    '{"name":"infra-configs","description":"Infrastructure as code","private":true,"auto_init":true,"default_branch":"main"}'; do
    curl -sf -u "${BASIC}" -H 'Content-Type: application/json' \
      -d "${repo_data}" "${GITEA_URL}/api/v1/user/repos" > /dev/null 2>&1 || true
  done
  log_ok "Gitea: 3 repos created"

  # Create org repo
  curl -sf -u "${BASIC}" -H 'Content-Type: application/json' \
    -d '{"name":"platform-services","description":"Platform-level microservices","private":false,"auto_init":true,"default_branch":"main"}' \
    "${GITEA_URL}/api/v1/orgs/acme-corp/repos" > /dev/null 2>&1 || true
  log_ok "Gitea: org repo created"

  # Save API token to a secret for compiler use
  kubectl create secret generic gitea-api-token \
    --from-literal=token="${token}" \
    --from-literal=url="${GITEA_URL}" \
    -n "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
  log_ok "Gitea: API token saved to Secret gitea-api-token"
  log_ok "=== Gitea seeding complete ==="
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. POCKETBASE — bootstrap admin, create collections and records
# ─────────────────────────────────────────────────────────────────────────────
seed_pocketbase() {
  log "=== Seeding PocketBase ==="

  if kubectl -n "${NAMESPACE}" exec deploy/pocketbase -- sh -lc \
    '/pb/pocketbase superuser update admin@example.com Admin12345! >/dev/null 2>&1 || /pb/pocketbase superuser create admin@example.com Admin12345! >/dev/null 2>&1'
  then
    log_ok "PocketBase: ensured superuser credentials"
  else
    log "PocketBase: could not refresh superuser credentials, trying existing account"
  fi

  # Authenticate as superuser (created by PocketBase entrypoint script)
  local token
  token=$(curl_json \
    -d '{"identity":"admin@example.com","password":"Admin12345!"}' \
    "${POCKETBASE_URL}/api/collections/_superusers/auth-with-password" 2>/dev/null | jq -r '.token // empty' || true)
  [[ -z "${token}" ]] && { log_err "PocketBase: could not get auth token — skipping"; return; }
  log_ok "PocketBase: authenticated"

  local AUTH="Authorization: Bearer ${token}"

  # Create products collection
  curl_json -H "${AUTH}" -d '{
    "name": "products",
    "type": "base",
    "fields": [
      {"name":"sku","type":"text","required":true},
      {"name":"name","type":"text","required":true},
      {"name":"description","type":"editor"},
      {"name":"price","type":"number","required":true},
      {"name":"currency","type":"text","required":false},
      {"name":"category","type":"text"},
      {"name":"stock_quantity","type":"number"},
      {"name":"is_active","type":"bool","required":false},
      {"name":"tags","type":"json"}
    ]
  }' "${POCKETBASE_URL}/api/collections" > /dev/null 2>&1 || log "PocketBase: products collection may already exist"

  # Create customers collection
  curl_json -H "${AUTH}" -d '{
    "name": "customers",
    "type": "base",
    "fields": [
      {"name":"full_name","type":"text","required":true},
      {"name":"email","type":"text","required":true},
      {"name":"tier","type":"text"},
      {"name":"phone","type":"text"},
      {"name":"active","type":"bool"}
    ]
  }' "${POCKETBASE_URL}/api/collections" > /dev/null 2>&1 || log "PocketBase: customers collection may already exist"

  # Create orders collection
  curl_json -H "${AUTH}" -d '{
    "name": "orders",
    "type": "base",
    "fields": [
      {"name":"order_number","type":"text","required":true},
      {"name":"customer_email","type":"text"},
      {"name":"status","type":"text"},
      {"name":"total_amount","type":"number"},
      {"name":"currency","type":"text"},
      {"name":"notes","type":"text"},
      {"name":"items","type":"json"}
    ]
  }' "${POCKETBASE_URL}/api/collections" > /dev/null 2>&1 || log "PocketBase: orders collection may already exist"

  log_ok "PocketBase: collections created"

  # Seed products (auth via admin)
  curl_json -H "${AUTH}" \
    -d '{"sku":"PHONE-001","name":"Galaxy Pro Max","price":999.00,"currency":"USD","category":"Electronics","stock_quantity":150,"is_active":true,"description":"Flagship smartphone 256GB","tags":["bestseller","premium"]}' \
    "${POCKETBASE_URL}/api/collections/products/records" > /dev/null 2>&1 || true
  curl_json -H "${AUTH}" \
    -d '{"sku":"LAPTOP-001","name":"ThinkPad X1 Carbon","price":1699.00,"currency":"USD","category":"Electronics","stock_quantity":45,"is_active":true,"description":"Ultra-light business laptop 32GB RAM","tags":["premium"]}' \
    "${POCKETBASE_URL}/api/collections/products/records" > /dev/null 2>&1 || true
  curl_json -H "${AUTH}" \
    -d '{"sku":"BOOK-001","name":"The Pragmatic Programmer","price":49.99,"currency":"USD","category":"Books","stock_quantity":500,"is_active":true,"description":"Classic software engineering book","tags":["bestseller"]}' \
    "${POCKETBASE_URL}/api/collections/products/records" > /dev/null 2>&1 || true
  curl_json -H "${AUTH}" \
    -d '{"sku":"SHOE-001","name":"UltraBoost Running","price":180.00,"currency":"USD","category":"Sports","stock_quantity":120,"is_active":true,"description":"Responsive running shoes with Boost midsole","tags":["new-arrival"]}' \
    "${POCKETBASE_URL}/api/collections/products/records" > /dev/null 2>&1 || true
  curl_json -H "${AUTH}" \
    -d '{"sku":"HOME-002","name":"Robot Vacuum Pro","price":499.00,"currency":"USD","category":"Home","stock_quantity":60,"is_active":true,"description":"LiDAR navigation robot vacuum","tags":["premium","new-arrival"]}' \
    "${POCKETBASE_URL}/api/collections/products/records" > /dev/null 2>&1 || true
  log_ok "PocketBase: 5 products seeded"

  # Seed customers (auth collection)
  for customer_data in \
    '{"email":"alice@example.com","full_name":"Alice Johnson","tier":"platinum","phone":"+1-555-0101","active":true}' \
    '{"email":"bob@example.com","full_name":"Bob Smith","tier":"gold","phone":"+1-555-0102","active":true}' \
    '{"email":"carol@example.com","full_name":"Carol Williams","tier":"standard","phone":"+1-555-0103","active":true}'; do
    curl_json -H "${AUTH}" -d "${customer_data}" \
      "${POCKETBASE_URL}/api/collections/customers/records" > /dev/null 2>&1 || true
  done
  log_ok "PocketBase: 3 customers seeded"

  for order_data in \
    '{"order_number":"ORD-PB-001","customer_email":"alice@example.com","status":"delivered","total_amount":999.00,"currency":"USD","notes":"Priority customer","items":[{"sku":"PHONE-001","qty":1}]}' \
    '{"order_number":"ORD-PB-002","customer_email":"bob@example.com","status":"processing","total_amount":1699.00,"currency":"USD","notes":"Awaiting shipment","items":[{"sku":"LAPTOP-001","qty":1}]}' \
    '{"order_number":"ORD-PB-003","customer_email":"carol@example.com","status":"pending","total_amount":49.99,"currency":"USD","notes":"Gift wrap requested","items":[{"sku":"BOOK-001","qty":1}]}'; do
    curl_json -H "${AUTH}" -d "${order_data}" \
      "${POCKETBASE_URL}/api/collections/orders/records" > /dev/null 2>&1 || true
  done
  log_ok "PocketBase: 3 orders seeded"

  log_ok "=== PocketBase seeding complete ==="
}

# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
main() {
  log "Starting seed process for namespace: ${NAMESPACE}"
  log "Mode: $([ "${USE_PORT_FORWARD}" == "true" ] && echo "port-forward" || echo "cluster-internal")"

  seed_directus
  seed_openfga
  seed_gitea
  seed_pocketbase

  log ""
  log_ok "=== All services seeded successfully ==="
  log ""
  log "Compiler source URLs:"
  cat <<URLS
  GraphQL     : http://directus.${NAMESPACE}.svc.cluster.local:8055/graphql
  REST        : http://directus.${NAMESPACE}.svc.cluster.local:8055/items/products
  OpenAPI spec: http://directus.${NAMESPACE}.svc.cluster.local:8055/server/specs/oas
  Gitea spec  : http://gitea.${NAMESPACE}.svc.cluster.local:3000/swagger.v1.json
  PocketBase  : http://pocketbase.${NAMESPACE}.svc.cluster.local:8090/api/collections/products/records
  gRPC        : openfga.${NAMESPACE}.svc.cluster.local:8081  (gRPC reflection enabled)
  JSON-RPC    : http://aria2.${NAMESPACE}.svc.cluster.local:6800/jsonrpc  (secret: token:test-secret)
  OData       : http://northbreeze.${NAMESPACE}.svc.cluster.local:4004/odata/v4/northbreeze/\$metadata
  SCIM        : http://jackson.${NAMESPACE}.svc.cluster.local:5225/api/scim/v2.0/<tenant>/<product>
  SOAP WSDL   : http://soap-cxf.${NAMESPACE}.svc.cluster.local:8080/services/OrderService?wsdl
  SQL         : postgresql://catalog:catalog@real-postgres.${NAMESPACE}.svc.cluster.local:5432/catalog_v2
URLS
}

main
