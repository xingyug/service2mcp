# Production Deployment Guide

Deploy **service2mcp** to Google Kubernetes Engine (GKE) or any
conformant Kubernetes cluster. This guide covers every step from infrastructure
provisioning through day-two operations.

> **Related docs:**
> [`docs/quickstart.md`](quickstart.md) — local development setup
> [`docs/architecture.md`](architecture.md) — component deep-dive
> [`deploy/helm/tool-compiler/`](../deploy/helm/tool-compiler/) — Helm chart source

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Infrastructure Setup](#2-infrastructure-setup)
3. [Configuration](#3-configuration)
4. [Deployment Steps](#4-deployment-steps)
5. [Database Migrations](#5-database-migrations)
6. [Authentication Setup](#6-authentication-setup)
7. [TLS and Networking](#7-tls-and-networking)
8. [Monitoring and Observability](#8-monitoring-and-observability)
9. [Scaling](#9-scaling)
10. [Backup and Recovery](#10-backup-and-recovery)
11. [Upgrade Procedures](#11-upgrade-procedures)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

### 1.1 Cluster Requirements

| Requirement | Minimum |
|---|---|
| Kubernetes version | 1.27+ |
| Node pool vCPUs | 4 vCPU (e2-standard-4 or larger) |
| Node pool memory | 16 GiB |
| Node count | 3 (for HA across zones) |
| Storage class | `standard-rwo` or equivalent for PVCs |

### 1.2 Required Tooling

```bash
# GKE-specific
gcloud version    # >= 450.0.0
gcloud components install gke-gcloud-auth-plugin

# Kubernetes
kubectl version --client   # >= 1.27
helm version               # >= 3.12

# Container builds
docker version             # >= 24.0 (or podman)

# Optional
gitleaks --version         # secrets scanning (required before git push)
```

### 1.3 GCP Service Accounts and IAM Roles

Create a dedicated GCP service account for the deployment pipeline:

```bash
PROJECT_ID="your-gcp-project"
SA_NAME="service2mcp-deployer"

gcloud iam service-accounts create ${SA_NAME} \
  --display-name="service2mcp deployer"

# Grant required roles
for ROLE in \
  roles/container.developer \
  roles/artifactregistry.writer \
  roles/cloudsql.client \
  roles/secretmanager.secretAccessor \
  roles/redis.editor; do
  gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="${ROLE}"
done
```

For the **compiler-worker** pod (which deploys MCP runtime pods at runtime), the
Kubernetes service account needs RBAC permissions for ConfigMaps, Deployments,
Services, and NetworkPolicies. These are provisioned automatically by
[`deploy/helm/tool-compiler/templates/rbac.yaml`](../deploy/helm/tool-compiler/templates/rbac.yaml).

### 1.4 GKE Cluster Creation (if needed)

```bash
CLUSTER_NAME="service2mcp-prod"
REGION="us-central1"

gcloud container clusters create ${CLUSTER_NAME} \
  --region=${REGION} \
  --num-nodes=1 \
  --machine-type=e2-standard-4 \
  --enable-ip-alias \
  --enable-network-policy \
  --workload-pool="${PROJECT_ID}.svc.id.goog" \
  --release-channel=regular

gcloud container clusters get-credentials ${CLUSTER_NAME} --region=${REGION}
```

---

## 2. Infrastructure Setup

### 2.1 PostgreSQL (Cloud SQL)

service2mcp requires PostgreSQL 16+ with the `asyncpg` driver.

```bash
INSTANCE_NAME="service2mcp-db"
DB_PASSWORD=$(openssl rand -base64 32)

gcloud sql instances create ${INSTANCE_NAME} \
  --database-version=POSTGRES_16 \
  --tier=db-custom-2-7680 \
  --region=${REGION} \
  --availability-type=REGIONAL \
  --storage-type=SSD \
  --storage-size=20GB \
  --backup-start-time="02:00" \
  --enable-bin-log

gcloud sql databases create toolcompiler --instance=${INSTANCE_NAME}
gcloud sql users set-password postgres \
  --instance=${INSTANCE_NAME} \
  --password="${DB_PASSWORD}"
```

**Connection:** Use [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/connect-kubernetes-engine)
as a sidecar or connect via private IP within the same VPC.

### 2.2 Redis (Memorystore)

Redis is used as the Celery broker and result backend.

```bash
gcloud redis instances create service2mcp-redis \
  --size=1 \
  --region=${REGION} \
  --redis-version=redis_7_0 \
  --tier=STANDARD_HA \
  --connect-mode=PRIVATE_SERVICE_ACCESS

REDIS_HOST=$(gcloud redis instances describe service2mcp-redis \
  --region=${REGION} --format='value(host)')
```

The resulting URL: `redis://${REDIS_HOST}:6379/0`

### 2.3 Container Registry (Artifact Registry)

```bash
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/service2mcp"

gcloud artifacts repositories create service2mcp \
  --repository-format=docker \
  --location=${REGION} \
  --description="service2mcp container images"
```

---

## 3. Configuration

### 3.1 Environment Variables Reference

All application services read configuration exclusively from environment
variables. The Helm chart wires these from `values.yaml` and Kubernetes Secrets.

#### Core Infrastructure

| Variable | Component(s) | Required | Description |
|---|---|---|---|
| `DATABASE_URL` | API, Worker, Access Control | Yes | PostgreSQL connection (`postgresql+asyncpg://user:pass@host:5432/db`) |
| `REDIS_URL` | API, Worker | Yes | Redis URL — Celery broker/backend (`redis://host:6379/0`) |
| `CELERY_BROKER_URL` | Worker | No | Override broker (falls back to `REDIS_URL`) |
| `CELERY_RESULT_BACKEND` | Worker | No | Override result backend (falls back to `REDIS_URL`) |

#### Authentication & Security

| Variable | Component(s) | Required | Description |
|---|---|---|---|
| `ACCESS_CONTROL_JWT_SECRET` | API, Worker, Access Control | **Yes** | HS256 signing key (min 32 bytes recommended) |
| `ACCESS_CONTROL_JWT_ISSUER` | Access Control | No | Validate `iss` claim if set |
| `ACCESS_CONTROL_JWT_AUDIENCE` | Access Control | No | Validate `aud` claim if set |
| `ACCESS_CONTROL_URL` | API, Worker | Yes | Internal URL of access-control service |
| `GATEWAY_ADMIN_URL` | Access Control | Conditional | APISIX gateway admin API URL |
| `GATEWAY_ADMIN_TOKEN` | Access Control | Conditional | APISIX admin auth token |
| `GATEWAY_ADMIN_TIMEOUT_SECONDS` | Access Control | No | Gateway admin HTTP timeout (default: `10.0`) |

#### Compiler Worker

| Variable | Component | Required | Description |
|---|---|---|---|
| `WORKFLOW_ENGINE` | API, Worker | No | Task engine — `celery` (default) |
| `COMPILATION_TASK_QUEUE` | Worker | No | Celery queue name (default: `compiler.jobs`) |
| `ROUTE_PUBLISH_MODE` | Worker | No | `deferred` or `access-control` (default: `deferred`) |
| `MCP_RUNTIME_IMAGE` | Worker | No | Runtime container image for deployed tools |
| `COMPILER_TARGET_NAMESPACE` | Worker | No | K8s namespace for runtime pods (default: release namespace) |
| `MCP_RUNTIME_IMAGE_PULL_POLICY` | Worker | No | Pull policy for runtime pods (default: `IfNotPresent`) |
| `CELERY_WORKER_CONCURRENCY` | Worker | No | Celery worker process count |
| `CELERY_WORKER_POOL` | Worker | No | Pool type: `prefork`, `solo` (default: `prefork`) |
| `CELERY_WORKER_LOGLEVEL` | Worker | No | Log level (default: `INFO`) |

#### MCP Runtime

| Variable | Component | Required | Description |
|---|---|---|---|
| `SERVICE_IR_PATH` | MCP Runtime | Yes | Path to compiled ServiceIR JSON/GZ |
| `ENABLE_NATIVE_GRPC_UNARY` | MCP Runtime | No | Enable native gRPC unary (default: `false`) |
| `ENABLE_NATIVE_GRPC_STREAM` | MCP Runtime | No | Enable native gRPC streaming (default: `false`) |
| `MCP_DISABLE_DNS_REBINDING_PROTECTION` | MCP Runtime | No | Disable DNS rebinding protection (default: `false`) |
| `MCP_ALLOWED_HOSTS` | MCP Runtime | No | Comma-separated allowed DNS names |
| `MCP_ALLOWED_ORIGINS` | MCP Runtime | No | Comma-separated allowed CORS origins |

#### LLM Enhancement (Optional)

| Variable | Component | Required | Description |
|---|---|---|---|
| `WORKER_ENABLE_LLM_ENHANCEMENT` | Worker | No | Enable LLM IR enrichment (default: `false`) |
| `WORKER_ENABLE_TOOL_GROUPING` | Worker | No | Enable tool categorization (default: `false`) |
| `LLM_PROVIDER` | Worker | Conditional | `openai`, `anthropic`, `deepseek`, `vertexai` |
| `LLM_MODEL` | Worker | No | Model name (provider-specific defaults) |
| `LLM_API_KEY` | Worker | Conditional | API key for LLM provider |
| `LLM_API_BASE_URL` | Worker | No | Custom API endpoint |
| `VERTEX_PROJECT_ID` | Worker | Conditional | GCP project for Vertex AI |
| `VERTEX_LOCATION` | Worker | No | GCP region for Vertex AI (default: `us-central1`) |
| `LLM_MAX_TOKENS_PER_JOB` | Worker | No | Token budget per job (default: `50000`) |

#### Observability

| Variable | Component(s) | Required | Description |
|---|---|---|---|
| `OTEL_EXPORTER_ENDPOINT` | All | No | OpenTelemetry gRPC collector endpoint |
| `OTEL_EXPORTER_OTLP_INSECURE` | All | No | Skip TLS for OTel exporter (default: `false`) |

### 3.2 Secrets Management

#### Kubernetes Secrets (Helm-managed)

The Helm chart creates a `tool-compiler-secrets` Secret
([`templates/secret.yaml`](../deploy/helm/tool-compiler/templates/secret.yaml)) containing:

| Key | Source value |
|---|---|
| `jwt-secret` | `global.jwtSecret` |
| `postgres-password` | `global.database.password` |
| `billing-secret` | `global.billingSecret` |

In production, set these values via `--set` or use an external secrets operator:

```bash
# Option A: --set at install time (secrets in shell history — use with care)
helm install ... --set global.jwtSecret="$(openssl rand -base64 32)"

# Option B: External Secrets Operator (recommended)
# Use external-secrets.io to sync from Google Secret Manager / Vault
```

#### Google Secret Manager Integration

```bash
# Store secrets
echo -n "$(openssl rand -base64 32)" | \
  gcloud secrets create service2mcp-jwt-secret --data-file=-

echo -n "${DB_PASSWORD}" | \
  gcloud secrets create service2mcp-db-password --data-file=-
```

Then use the [External Secrets Operator](https://external-secrets.io/) to
project these into Kubernetes Secrets automatically.

### 3.3 Helm Values Reference

The chart ships two value files:

| File | Purpose |
|---|---|
| [`values.yaml`](../deploy/helm/tool-compiler/values.yaml) | Development defaults (in-cluster Postgres/Redis, dev secrets) |
| [`values-production.yaml`](../deploy/helm/tool-compiler/values-production.yaml) | Production overlay (empty secrets, `Always` pull, HPA, PDB, security contexts) |

**Production overlay** (`values-production.yaml`) enables:

- **Image pull policy:** `Always` for all application images
- **Security contexts:** `runAsNonRoot`, `readOnlyRootFilesystem`, `drop: ALL`
- **HPA:** Compiler API (2–8 replicas), MCP Runtime (2–10 replicas) at 70% CPU
- **PDB:** `minAvailable: 1` for API, Access Control, and Runtime
- **Probe tuning:** Adjusted `initialDelaySeconds` and `periodSeconds`

---

## 4. Deployment Steps

### 4.1 Build Container Images

The single Dockerfile at [`deploy/docker/Dockerfile.app`](../deploy/docker/Dockerfile.app)
builds all services via the `APP_MODULE` build arg:

```bash
IMAGE_TAG="$(date +%Y%m%d)-$(git rev-parse --short HEAD)"
REGISTRY="us-central1-docker.pkg.dev/${PROJECT_ID}/service2mcp"

# Compiler API
docker build -f deploy/docker/Dockerfile.app \
  --build-arg APP_MODULE=apps.compiler_api.main:app \
  --build-arg APP_PORT=8000 \
  --build-arg INSTALL_EXTRAS=extractors,enhancer,observability \
  -t ${REGISTRY}/compiler-api:${IMAGE_TAG} .

# Access Control
docker build -f deploy/docker/Dockerfile.app \
  --build-arg APP_MODULE=apps.access_control.main:app \
  --build-arg APP_PORT=8001 \
  --build-arg INSTALL_EXTRAS=extractors,enhancer,observability \
  -t ${REGISTRY}/access-control:${IMAGE_TAG} .

# Compiler Worker
docker build -f deploy/docker/Dockerfile.app \
  --build-arg APP_MODULE=apps.compiler_worker.entrypoint \
  --build-arg APP_PORT=8002 \
  --build-arg INSTALL_EXTRAS=extractors,enhancer,observability \
  -t ${REGISTRY}/compiler-worker:${IMAGE_TAG} .

# MCP Runtime
docker build -f deploy/docker/Dockerfile.app \
  --build-arg APP_MODULE=apps.mcp_runtime.main:app \
  --build-arg APP_PORT=8003 \
  --build-arg INSTALL_EXTRAS=extractors,enhancer,observability \
  -t ${REGISTRY}/mcp-runtime:${IMAGE_TAG} .
```

### 4.2 Push Images

```bash
gcloud auth configure-docker ${REGION}-docker.pkg.dev

for IMG in compiler-api access-control compiler-worker mcp-runtime; do
  docker push ${REGISTRY}/${IMG}:${IMAGE_TAG}
done
```

### 4.3 Create Namespace and Secrets

```bash
NAMESPACE="service2mcp"
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -

# Pre-create secret (if not using Helm-managed secrets)
kubectl -n ${NAMESPACE} create secret generic tool-compiler-secrets \
  --from-literal=jwt-secret="$(openssl rand -base64 32)" \
  --from-literal=postgres-password="${DB_PASSWORD}" \
  --from-literal=billing-secret="$(openssl rand -base64 16)" \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 4.4 Helm Install

```bash
helm upgrade --install tool-compiler ./deploy/helm/tool-compiler \
  --namespace ${NAMESPACE} \
  -f deploy/helm/tool-compiler/values.yaml \
  -f deploy/helm/tool-compiler/values-production.yaml \
  --set global.database.host="${CLOUDSQL_PRIVATE_IP}" \
  --set global.database.password="${DB_PASSWORD}" \
  --set global.redisUrl="redis://${REDIS_HOST}:6379/0" \
  --set global.jwtSecret="$(kubectl -n ${NAMESPACE} get secret tool-compiler-secrets -o jsonpath='{.data.jwt-secret}' | base64 -d)" \
  --set images.compilerApi.repository="${REGISTRY}/compiler-api" \
  --set images.compilerApi.tag="${IMAGE_TAG}" \
  --set images.accessControl.repository="${REGISTRY}/access-control" \
  --set images.accessControl.tag="${IMAGE_TAG}" \
  --set images.compilerWorker.repository="${REGISTRY}/compiler-worker" \
  --set images.compilerWorker.tag="${IMAGE_TAG}" \
  --set images.mcpRuntime.repository="${REGISTRY}/mcp-runtime" \
  --set images.mcpRuntime.tag="${IMAGE_TAG}" \
  --wait --timeout 5m
```

### 4.5 Verify Deployment

```bash
# All pods running
kubectl -n ${NAMESPACE} get pods -l app.kubernetes.io/instance=tool-compiler

# Health checks
for SVC in compiler-api:8000 access-control:8001 compiler-worker:8002; do
  NAME=${SVC%%:*}
  PORT=${SVC##*:}
  kubectl -n ${NAMESPACE} exec deploy/tool-compiler-${NAME} -- \
    curl -sf http://localhost:${PORT}/healthz
  echo " ← ${NAME} /healthz OK"
done

# Readiness checks
for SVC in compiler-api:8000 access-control:8001 compiler-worker:8002; do
  NAME=${SVC%%:*}
  PORT=${SVC##*:}
  kubectl -n ${NAMESPACE} exec deploy/tool-compiler-${NAME} -- \
    curl -sf http://localhost:${PORT}/readyz
  echo " ← ${NAME} /readyz OK"
done

# Migration job completed
kubectl -n ${NAMESPACE} get jobs -l app.kubernetes.io/component=migrations
```

---

## 5. Database Migrations

### 5.1 How Migrations Work

Migrations are managed by **Alembic** with configuration at
[`migrations/alembic.ini`](../migrations/alembic.ini). The Helm chart runs
migrations automatically as a `post-install,post-upgrade` Job
([`templates/migration-job.yaml`](../deploy/helm/tool-compiler/templates/migration-job.yaml)).

The `env.py` translates the async `postgresql+asyncpg://` URL to the sync
`postgresql+psycopg://` driver that Alembic requires.

### 5.2 Manual Migration

```bash
# Forward the database port
kubectl -n ${NAMESPACE} port-forward svc/tool-compiler-postgres 5432:5432 &

# Run migrations from a local environment
DATABASE_URL="postgresql+asyncpg://toolcompiler:${DB_PASSWORD}@localhost:5432/toolcompiler" \
  alembic -c migrations/alembic.ini upgrade head
```

Or run inside the cluster:

```bash
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  alembic -c migrations/alembic.ini upgrade head
```

### 5.3 Check Current Revision

```bash
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  alembic -c migrations/alembic.ini current
```

### 5.4 Rollback

```bash
# Roll back one revision
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  alembic -c migrations/alembic.ini downgrade -1

# Roll back to a specific revision
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  alembic -c migrations/alembic.ini downgrade <revision_id>
```

### 5.5 Migration History

Current migrations (in [`migrations/versions/`](../migrations/versions/)):

| Revision | Description |
|---|---|
| `001` | Initial schema (compiler jobs, events, registry, auth) |
| `002` | Add review workflows |
| `003` | Scope review workflow constraints |
| `004` | Add user roles for RBAC |
| `005` | Harden service version uniqueness |
| `006` | Add job→service_id index |

---

## 6. Authentication Setup

### 6.1 JWT Configuration

The access-control service uses **HS256** (HMAC-SHA256) for JWT validation.
All services that validate tokens share the same secret via the
`ACCESS_CONTROL_JWT_SECRET` environment variable.

```bash
# Generate a production JWT secret (32+ bytes)
openssl rand -base64 32
```

**JWT claims** recognized by access-control:

| Claim | Required | Description |
|---|---|---|
| `sub` | Yes | Subject identifier (user ID) |
| `exp` | Yes | Expiration timestamp (UNIX epoch) |
| `nbf` | No | Not-before timestamp |
| `iss` | No | Issuer (validated if `ACCESS_CONTROL_JWT_ISSUER` is set) |
| `aud` | No | Audience (validated if `ACCESS_CONTROL_JWT_AUDIENCE` is set) |
| `preferred_username` | No | Username (first priority for extraction) |
| `username` | No | Username (second priority) |
| `roles` | No | Array of role strings, synced to local user record |

**Username extraction order:** `preferred_username` → `username` →
`cognito:username` → `login` → falls back to `sub`.

### 6.2 Personal Access Tokens (PATs)

PATs allow programmatic access with fine-grained control. They are prefixed
with `pat_` and stored as HMAC hashes in the database.

```bash
API_URL="https://service2mcp.example.com"

# Create a PAT (requires a valid JWT)
curl -X POST ${API_URL}/api/v1/authn/pats \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "ci-pipeline"}'

# Response includes the plaintext token (shown only once)
# { "id": "...", "token": "pat_xxxxxxxx...", "name": "ci-pipeline" }

# List PATs
curl ${API_URL}/api/v1/authn/pats \
  -H "Authorization: Bearer ${JWT_TOKEN}"

# Revoke a PAT
curl -X DELETE ${API_URL}/api/v1/authn/pats/${PAT_ID} \
  -H "Authorization: Bearer ${JWT_TOKEN}"
```

### 6.3 OIDC Integration

To integrate with an external identity provider (Keycloak, Auth0, Okta):

1. Configure the IdP to issue JWTs with `sub`, `exp`, and a username claim.
2. Set the shared HMAC secret (or configure the IdP to sign with the same HS256
   key as `ACCESS_CONTROL_JWT_SECRET`).
3. Optionally set `ACCESS_CONTROL_JWT_ISSUER` and `ACCESS_CONTROL_JWT_AUDIENCE`
   for additional validation.

```yaml
# In your Helm values override:
compilerWorker:
  extraEnv:
    - name: ACCESS_CONTROL_JWT_ISSUER
      value: "https://idp.example.com/realms/service2mcp"
    - name: ACCESS_CONTROL_JWT_AUDIENCE
      value: "service2mcp-api"
```

### 6.4 RBAC Policies

The access-control service supports policy-based authorization:

```bash
# Create an allow policy
curl -X POST ${API_URL}/api/v1/authz/policies \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "subject_type": "user",
    "subject_id": "ci-bot",
    "resource_id": "service:billing-api",
    "action_pattern": "tool:invoke:*",
    "decision": "allow"
  }'
```

Supported decisions: `allow`, `deny`, `require_approval`.

---

## 7. TLS and Networking

### 7.1 Ingress Configuration

Deploy an Ingress resource to expose the Compiler API and MCP Runtime
externally:

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: service2mcp-ingress
  namespace: service2mcp
  annotations:
    kubernetes.io/ingress.class: "nginx"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
spec:
  tls:
    - hosts:
        - service2mcp.example.com
      secretName: service2mcp-tls
  rules:
    - host: service2mcp.example.com
      http:
        paths:
          - path: /api/
            pathType: Prefix
            backend:
              service:
                name: tool-compiler-compiler-api
                port:
                  number: 8000
          - path: /mcp
            pathType: Prefix
            backend:
              service:
                name: tool-compiler-mcp-runtime
                port:
                  number: 8003
```

### 7.2 cert-manager Setup

```bash
# Install cert-manager
helm repo add jetstack https://charts.jetstack.io
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=true

# Create ClusterIssuer
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ops@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
EOF
```

### 7.3 Internal Service Communication

All internal services communicate over ClusterIP services (no TLS by default).
For zero-trust networking, enable a service mesh (Istio, Linkerd) and enforce
mTLS between pods.

**Service ports** (internal):

| Service | ClusterIP Port |
|---|---|
| `tool-compiler-compiler-api` | 8000 |
| `tool-compiler-access-control` | 8001 |
| `tool-compiler-compiler-worker` | 8002 |
| `tool-compiler-mcp-runtime` | 8003 |
| `tool-compiler-postgres` | 5432 |
| `tool-compiler-redis` | 6379 |

### 7.4 Network Policies

The compiler-worker automatically creates `NetworkPolicy` resources for
deployed MCP runtime pods (see RBAC in
[`templates/rbac.yaml`](../deploy/helm/tool-compiler/templates/rbac.yaml)).
For cluster-wide policies, restrict inter-namespace traffic:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-external
  namespace: service2mcp
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: service2mcp
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
```

---

## 8. Monitoring and Observability

### 8.1 Prometheus Scraping

The compiler-worker and MCP runtime expose a `/metrics` endpoint in Prometheus
text format. Configure scraping via `ServiceMonitor` or pod annotations:

```yaml
# ServiceMonitor (requires prometheus-operator)
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: service2mcp
  namespace: service2mcp
spec:
  selector:
    matchLabels:
      app.kubernetes.io/instance: tool-compiler
  endpoints:
    - port: http
      path: /metrics
      interval: 15s
```

Or use pod annotations:

```yaml
# Add to pod template metadata in a values override
podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8002"
  prometheus.io/path: "/metrics"
```

### 8.2 Key Metrics

Metrics are defined in [`libs/observability/metrics.py`](../libs/observability/metrics.py):

| Metric | Type | Description |
|---|---|---|
| Compilation job counters | Counter | Jobs started, succeeded, failed, rolled back |
| Stage latency | Histogram | Per-stage execution time (buckets: 5ms–30s) |
| Tool invocations | Counter | MCP tool calls by operation ID |
| Upstream latency | Histogram | Proxy call latency per upstream |
| Circuit breaker trips | Counter | Consecutive failures triggering circuit break |

### 8.3 Grafana Dashboards

Pre-built dashboards are in [`observability/grafana/`](../observability/grafana/):

| Dashboard | File |
|---|---|
| Compilation Pipeline | `observability/grafana/compilation-dashboard.json` |
| Runtime Operations | `observability/grafana/runtime-dashboard.json` |

Import via Grafana UI → Dashboards → Import → Upload JSON, or use a
`ConfigMap`-based provisioner:

```bash
kubectl -n monitoring create configmap service2mcp-dashboards \
  --from-file=observability/grafana/compilation-dashboard.json \
  --from-file=observability/grafana/runtime-dashboard.json
```

### 8.4 Structured Logging

All services emit JSON logs via [`libs/observability/logging.py`](../libs/observability/logging.py):

```json
{
  "timestamp": "2025-03-29T10:15:30.123456+00:00",
  "level": "INFO",
  "component": "compiler-api",
  "logger": "apps.compiler_api.main",
  "message": "Compilation started",
  "trace_id": "abc123...",
  "span_id": "def456..."
}
```

Use Cloud Logging filters:

```
resource.type="k8s_container"
resource.labels.namespace_name="service2mcp"
jsonPayload.level="ERROR"
```

### 8.5 Distributed Tracing (OpenTelemetry)

Enable by setting `OTEL_EXPORTER_ENDPOINT` to your collector:

```yaml
compilerWorker:
  extraEnv:
    - name: OTEL_EXPORTER_ENDPOINT
      value: "otel-collector.observability:4317"
    - name: OTEL_EXPORTER_OTLP_INSECURE
      value: "true"
```

Traces use W3C Trace Context propagation and are correlated with structured
log fields (`trace_id`, `span_id`).

### 8.6 Alerting

Example PrometheusRule for critical alerts:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: service2mcp-alerts
  namespace: service2mcp
spec:
  groups:
    - name: service2mcp
      rules:
        - alert: CompilationFailureRate
          expr: |
            rate(compilation_jobs_failed_total[5m])
            / rate(compilation_jobs_started_total[5m]) > 0.1
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "Compilation failure rate > 10%"
        - alert: PodNotReady
          expr: |
            kube_pod_status_ready{namespace="service2mcp",condition="true"} == 0
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "Pod {{ $labels.pod }} not ready for 5m"
```

---

## 9. Scaling

### 9.1 Horizontal Pod Autoscaler (HPA)

The production values overlay
([`values-production.yaml`](../deploy/helm/tool-compiler/values-production.yaml))
enables HPA for the Compiler API and MCP Runtime, rendered by
[`templates/production.yaml`](../deploy/helm/tool-compiler/templates/production.yaml):

| Component | Min Replicas | Max Replicas | Target CPU |
|---|---|---|---|
| Compiler API | 2 | 8 | 70% |
| MCP Runtime | 2 | 10 | 70% |

Customize in your values override:

```yaml
autoscaling:
  compilerApi:
    enabled: true
    minReplicas: 3
    maxReplicas: 12
    targetCPUUtilizationPercentage: 60
  mcpRuntime:
    enabled: true
    minReplicas: 3
    maxReplicas: 20
    targetCPUUtilizationPercentage: 60
```

### 9.2 Pod Disruption Budgets (PDB)

Enabled by the production overlay:

| Component | minAvailable |
|---|---|
| Compiler API | 1 |
| Access Control | 1 |
| MCP Runtime | 1 |

### 9.3 Resource Requests and Limits

**Production resource allocation**
(from [`values-production.yaml`](../deploy/helm/tool-compiler/values-production.yaml)):

| Component | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---|---|---|---|---|
| Compiler API | 250m | 1 | 256Mi | 512Mi |
| Access Control | 100m | 500m | 128Mi | 256Mi |
| Compiler Worker | 500m | 2 | 512Mi | 1Gi |
| MCP Runtime | 100m | 500m | 128Mi | 256Mi |

**Tuning guidance:**

- **Compiler Worker** is CPU-bound during extraction/enhancement; increase CPU
  limit if compilation latency is high.
- **MCP Runtime** is I/O-bound; memory matters more than CPU.
- Enable LLM enhancement (`WORKER_ENABLE_LLM_ENHANCEMENT=true`) increases
  worker memory needs — consider 1Gi request / 2Gi limit.

### 9.4 Connection Pool Tuning

PostgreSQL connection pools are managed by SQLAlchemy with `pool_pre_ping=True`.
For high-throughput deployments:

```yaml
compilerWorker:
  extraEnv:
    - name: SQLALCHEMY_POOL_SIZE
      value: "20"
    - name: SQLALCHEMY_MAX_OVERFLOW
      value: "10"
```

Redis connection pools are managed by the `redis` library defaults. For Celery,
tune via:

```yaml
compilerWorker:
  extraEnv:
    - name: CELERY_WORKER_CONCURRENCY
      value: "4"
    - name: CELERY_WORKER_POOL
      value: "prefork"
```

---

## 10. Backup and Recovery

### 10.1 Database Backups

#### Cloud SQL Automated Backups

If using Cloud SQL, automated backups are configured at instance creation
(`--backup-start-time`). Additionally, enable point-in-time recovery:

```bash
gcloud sql instances patch ${INSTANCE_NAME} \
  --enable-point-in-time-recovery \
  --retained-transaction-log-days=7
```

#### Manual Backup

```bash
# Export database
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  pg_dump -h ${DB_HOST} -U toolcompiler -d toolcompiler \
  --format=custom --file=/tmp/backup.dump

# Or via Cloud SQL export
gcloud sql export sql ${INSTANCE_NAME} \
  gs://${BACKUP_BUCKET}/backup-$(date +%Y%m%d).sql.gz \
  --database=toolcompiler
```

### 10.2 Service IR Backups

Compiled ServiceIR artifacts are stored in the `service_versions` table
(`ir_json` column) and as ConfigMaps in the cluster. Back up critical IRs:

```bash
# Export all active service versions
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  python -c "
import asyncio, json
from libs.db import get_async_session
# ... query service_versions where is_active=True
"
```

### 10.3 Disaster Recovery

**RPO/RTO targets:**

| Component | RPO | RTO |
|---|---|---|
| Database (Cloud SQL HA) | ~0 (sync replication) | < 5 min (automatic failover) |
| Redis (Memorystore HA) | seconds | < 1 min |
| Application pods | N/A (stateless) | < 2 min (HPA + PDB) |

**Recovery procedure:**

1. Ensure database is accessible (Cloud SQL failover is automatic with HA).
2. Verify Redis connectivity (`redis-cli ping`).
3. Run migrations if schema may be behind: `alembic upgrade head`.
4. Restart deployments: `kubectl -n ${NAMESPACE} rollout restart deploy`.
5. Verify health: check `/readyz` on all services.

---

## 11. Upgrade Procedures

### 11.1 Rolling Updates (Default)

Helm `upgrade` performs a rolling update by default:

```bash
IMAGE_TAG="20250401-abc1234"

helm upgrade tool-compiler ./deploy/helm/tool-compiler \
  --namespace ${NAMESPACE} \
  -f deploy/helm/tool-compiler/values.yaml \
  -f deploy/helm/tool-compiler/values-production.yaml \
  --set images.compilerApi.tag="${IMAGE_TAG}" \
  --set images.accessControl.tag="${IMAGE_TAG}" \
  --set images.compilerWorker.tag="${IMAGE_TAG}" \
  --set images.mcpRuntime.tag="${IMAGE_TAG}" \
  --reuse-values \
  --wait --timeout 5m
```

The migration Job runs automatically as a `post-upgrade` hook.

### 11.2 Blue-Green Deployment

For zero-downtime major upgrades:

```bash
# Deploy to a parallel namespace
BLUE_NS="service2mcp"
GREEN_NS="service2mcp-green"

kubectl create namespace ${GREEN_NS}

helm install tool-compiler-green ./deploy/helm/tool-compiler \
  --namespace ${GREEN_NS} \
  -f deploy/helm/tool-compiler/values.yaml \
  -f deploy/helm/tool-compiler/values-production.yaml \
  --set global.database.host="${CLOUDSQL_PRIVATE_IP}" \
  # ... same config as blue

# Validate green
kubectl -n ${GREEN_NS} exec deploy/tool-compiler-compiler-api -- \
  curl -sf http://localhost:8000/readyz

# Switch Ingress to green
kubectl patch ingress service2mcp-ingress -n ${BLUE_NS} \
  --type=json \
  -p='[{"op":"replace","path":"/spec/rules/0/http/paths/0/backend/service/name","value":"tool-compiler-compiler-api.'${GREEN_NS}'.svc.cluster.local"}]'

# Tear down blue after verification
helm uninstall tool-compiler --namespace ${BLUE_NS}
```

### 11.3 Canary Deployment

Use Ingress annotations to split traffic:

```yaml
# canary-ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: service2mcp-canary
  annotations:
    nginx.ingress.kubernetes.io/canary: "true"
    nginx.ingress.kubernetes.io/canary-weight: "10"
spec:
  rules:
    - host: service2mcp.example.com
      http:
        paths:
          - path: /api/
            pathType: Prefix
            backend:
              service:
                name: tool-compiler-compiler-api-canary
                port:
                  number: 8000
```

### 11.4 Rollback

```bash
# Helm rollback to previous release
helm rollback tool-compiler 0 --namespace ${NAMESPACE}

# Check release history
helm history tool-compiler --namespace ${NAMESPACE}

# Rollback database if needed (see §5.4)
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  alembic -c migrations/alembic.ini downgrade -1
```

---

## 12. Troubleshooting

### 12.1 Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| Pods in `CrashLoopBackOff` | Missing `DATABASE_URL` or invalid secret | Check `kubectl describe pod` and env vars |
| `/readyz` returns 503 | Database unreachable or JWT secret missing | Verify DB connectivity, check `ACCESS_CONTROL_JWT_SECRET` |
| Migration job fails | DB credentials wrong or schema conflict | Check job logs; run `alembic current` to diagnose |
| Compilation stuck in `pending` | Redis unreachable or no worker running | Verify `REDIS_URL`, check worker `/readyz` |
| Worker cannot deploy runtimes | Missing RBAC permissions | Check ServiceAccount binding in `rbac.yaml` |
| `memory://` warning in worker logs | No Redis URL configured | Set `REDIS_URL` (Celery falls back to ephemeral in-memory broker) |

### 12.2 Debug Commands

```bash
# Pod status overview
kubectl -n ${NAMESPACE} get pods -o wide

# Describe a failing pod
kubectl -n ${NAMESPACE} describe pod <pod-name>

# Application logs (structured JSON)
kubectl -n ${NAMESPACE} logs deploy/tool-compiler-compiler-api --tail=100

# Follow logs across all compiler components
kubectl -n ${NAMESPACE} logs -l app.kubernetes.io/instance=tool-compiler \
  --all-containers -f --tail=50

# Check events for scheduling issues
kubectl -n ${NAMESPACE} get events --sort-by='.lastTimestamp' | tail -20

# Interactive debug shell
kubectl -n ${NAMESPACE} exec -it deploy/tool-compiler-compiler-api -- /bin/bash

# Test internal connectivity
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  curl -sf http://tool-compiler-access-control:8001/healthz

# Check Celery worker status
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-worker -- \
  curl -sf http://localhost:8002/readyz | python -m json.tool

# Database connectivity test
kubectl -n ${NAMESPACE} exec deploy/tool-compiler-compiler-api -- \
  python -c "import asyncio; from sqlalchemy.ext.asyncio import create_async_engine; \
  e = create_async_engine('$DATABASE_URL'); \
  asyncio.run(e.dispose())" && echo "DB OK"

# Check Helm release status
helm status tool-compiler --namespace ${NAMESPACE}
helm get values tool-compiler --namespace ${NAMESPACE}
```

### 12.3 Log Locations

| Component | Log Source | Key Fields |
|---|---|---|
| Compiler API | `kubectl logs deploy/tool-compiler-compiler-api` | `component: compiler-api` |
| Access Control | `kubectl logs deploy/tool-compiler-access-control` | `component: access-control` |
| Compiler Worker | `kubectl logs deploy/tool-compiler-compiler-worker` | `component: compiler-worker` |
| MCP Runtime | `kubectl logs deploy/tool-compiler-mcp-runtime` | `component: mcp-runtime` |
| Migration Job | `kubectl logs job/tool-compiler-migrations` | Alembic output |
| Celery tasks | Embedded in worker logs | `stage`, `job_id` fields |

### 12.4 Health Endpoint Reference

| Service | Liveness | Readiness | Notes |
|---|---|---|---|
| Compiler API | `GET /healthz` → 200 | `GET /readyz` → 200/503 | Readiness checks DB |
| Access Control | `GET /healthz` → 200 | `GET /readyz` → 200/503 | Readiness checks DB + JWT config |
| Compiler Worker | `GET /healthz` → 200 | `GET /readyz` → 200/503 | Readiness checks broker + config |
| MCP Runtime | `GET /healthz` → 200 | `GET /readyz` → 200/503 | Readiness checks IR loaded + upstreams |
| MCP Runtime | — | `GET /tools` → 200/503 | Lists registered MCP tools |
| Worker/Runtime | — | `GET /metrics` | Prometheus metrics endpoint |

### 12.5 Security Headers

All HTTP responses from the Compiler API include security headers injected by
`SecurityHeadersMiddleware`:

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 0
Referrer-Policy: strict-origin-when-cross-origin
Cache-Control: no-store
```

Requests are tagged with `X-Request-ID` for correlation across services.
