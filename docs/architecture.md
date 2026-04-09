# Architecture Deep-Dive

> **Audience:** Contributors and operators who need to understand the full system.
> See also: [ADR index](adr/), [Quickstart](quickstart.md).

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Data Flow](#2-data-flow)
3. [Persistence Model](#3-persistence-model)
4. [Messaging](#4-messaging)
5. [IR Lifecycle](#5-ir-lifecycle)
6. [Runtime Architecture](#6-runtime-architecture)
7. [Extension Points](#7-extension-points)
8. [Security Architecture](#8-security-architecture)
9. [Observability Architecture](#9-observability-architecture)

---

## 1. System Overview

service2mcp compiles heterogeneous APIs into governed
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) tool servers.
The system is organised into **three planes**, each with clearly separated
responsibilities.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CONTROL  PLANE                              │
│                                                                    │
│  ┌──────────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │  Compiler API    │  │   Service    │  │  Access Control    │   │
│  │ apps/compiler_   │  │   Registry   │  │ apps/access_       │   │
│  │ api/             │  │  (PostgreSQL)│  │ control/           │   │
│  └────────┬─────────┘  └──────────────┘  └────────────────────┘   │
│           │  enqueue                                               │
├───────────┼────────────────────────────────────────────────────────┤
│           ▼         BUILD  PLANE                                   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Compiler Worker  (apps/compiler_worker/)                    │  │
│  │                                                              │  │
│  │  detect → extract → enhance → validate_ir → generate         │  │
│  │           → deploy → validate_runtime → route → register     │  │
│  │                                                              │  │
│  │  Extractors   Enhancer   Validators   Generator              │  │
│  │  libs/        libs/      libs/        libs/                  │  │
│  │  extractors/  enhancer/  validator/   generator/             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
├────────────────────────────────────────────────────────────────────┤
│                       RUNTIME  PLANE                               │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Generic MCP Runtime  (apps/mcp_runtime/)                    │  │
│  │                                                              │  │
│  │  IR loader → tool/resource/prompt registration               │  │
│  │  RuntimeProxy → upstream dispatch                            │  │
│  │  Circuit breaker, observability, metrics                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Gateway  (API gateway + route binding)                      │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

### Control Plane

| Component | Path | Responsibility |
|-----------|------|----------------|
| Compiler API | `apps/compiler_api/` | Job submission (`POST /api/v1/compilations`), artifact management, service catalog, SSE event streaming, diff API |
| Access Control | `apps/access_control/` | JWT/PAT authentication, RBAC policy evaluation, gateway-binding route sync, audit log |
| PostgreSQL | — | Persistent store for jobs, artifacts, policies, PATs, audit events |

### Build Plane

| Component | Path | Responsibility |
|-----------|------|----------------|
| Compiler Worker | `apps/compiler_worker/` | Celery-backed 9-stage pipeline execution, rollback orchestration |
| Extractors | `libs/extractors/` | Protocol-specific API extraction → `ServiceIR` |
| Enhancer | `libs/enhancer/` | LLM-assisted description enrichment, tool-intent derivation, grouping |
| Validators | `libs/validator/` | Pre-deploy IR validation, post-deploy smoke tests, audit framework |
| Generator | `libs/generator/` | Kubernetes manifest + route config generation |

### Runtime Plane

| Component | Path | Responsibility |
|-----------|------|----------------|
| MCP Runtime | `apps/mcp_runtime/` | Loads `ServiceIR`, registers tools, proxies calls to upstream APIs, exposes `/mcp` endpoint |
| Gateway Binding | `apps/access_control/gateway_binding/` | Synchronises routes, PATs, and policies to an external API gateway |

### Supported Protocol Families

All nine protocol families normalise to the same `ServiceIR` contract
(`libs/ir/models.py`). Downstream components never need to know which
protocol produced the IR.

| Protocol | Extractor | Source |
|----------|-----------|--------|
| OpenAPI (2.0 / 3.x) | `libs/extractors/openapi.py` | Spec URL |
| REST (black-box) | `libs/extractors/rest.py` | Live endpoint |
| GraphQL | `libs/extractors/graphql.py` | Introspection |
| gRPC | `libs/extractors/grpc.py` | Server reflection |
| SOAP / WSDL | `libs/extractors/soap.py` | WSDL URL |
| OData v4 | `libs/extractors/odata.py` | `$metadata` |
| SQL | `libs/extractors/sql.py` | DB connection string |
| JSON-RPC 2.0 | `libs/extractors/jsonrpc.py` | `system.listMethods` |
| SCIM 2.0 | `libs/extractors/scim.py` | RFC 7644 endpoint |

---

## 2. Data Flow

The end-to-end journey from API submission to agent tool invocation passes
through nine pipeline stages. Each stage is an explicit, retryable
activity defined in `apps/compiler_worker/activities/production.py`.

```
 Client
   │
   │  POST /api/v1/compilations  { source_url, options, ... }
   ▼
┌──────────────┐    enqueue     ┌──────────────────────────────────────┐
│ Compiler API ├───────────────►│ Celery Worker                        │
│              │ (Celery task)  │                                      │
│  creates Job │                │  ① detect   ─ TypeDetector picks     │
│  row (pending)                │               protocol by confidence │
│              │                │  ② extract  ─ Extractor → ServiceIR  │
│              │                │  ③ enhance  ─ LLM descriptions +     │
│              │                │               tool-intent derivation  │
│              │                │  ④ validate ─ Pre-deploy IR checks   │
│              │                │  ⑤ generate ─ K8s manifests +        │
│              │                │               route_config            │
│              │                │  ⑥ deploy   ─ kubectl apply          │
│              │                │               (ConfigMap, Deployment, │
│              │                │                Service, NetworkPolicy)│
│              │                │  ⑦ validate ─ Post-deploy health +   │
│              │                │    runtime    smoke-tool invocation   │
│              │                │  ⑧ route    ─ Publish routes to      │
│              │                │               Access Control          │
│              │                │  ⑨ register ─ Persist versioned      │
│              │                │               artifact to registry    │
│              │                └──────────────────────────────────────┘
│              │
│  GET /api/v1/compilations/{id}/events  (SSE stream)
│              │
└──────────────┘
                    After stage ⑥ completes:

                    ┌────────────────────────────┐
                    │ Generic MCP Runtime (Pod)   │
                    │                             │
                    │  Loads ServiceIR from       │
                    │  ConfigMap (gzip + base64)  │
                    │                             │
                    │  /mcp   ← MCP transport     │
                    │  /tools ← tool listing      │
                    │  /healthz, /readyz          │
                    │  /metrics                   │
                    └─────────────┬───────────────┘
                                  │ proxy
                                  ▼
                            Upstream API
```

### Stage Details

| # | Stage | Input | Output | Rollback? |
|---|-------|-------|--------|-----------|
| 1 | `detect` | `SourceConfig` (URL or content) | Protocol name + confidence | No |
| 2 | `extract` | Protocol + source | `ServiceIR` + version + source hash | No |
| 3 | `enhance` | `ServiceIR` | Enhanced IR + token usage metrics | No |
| 4 | `validate_ir` | `ServiceIR` | Pre-deploy validation report | No |
| 5 | `generate` | IR + version metadata | `GeneratedManifestSet` (YAML, route config) | Bookkeeping only |
| 6 | `deploy` | Manifest set | Deployment revision + runtime URL | **Yes** — deletes manifests |
| 7 | `validate_runtime` | IR + runtime URL | Post-deploy report + sample invocations | No |
| 8 | `route` | Route config | Route IDs + previous routes | **Yes** — restores previous routes |
| 9 | `register` | All context | Versioned artifact in registry | No |

### Service Identity Resolution

When creating a compilation job, the service identity is resolved in
priority order:

1. Explicit `service_id` on the request
2. Explicit `service_name` on the request
3. `ServiceIR.service_name` extracted from the source

---

## 3. Persistence Model

PostgreSQL with three logical schemas. Migrations live in `migrations/`
(Alembic-style, numbered `001_initial.py` through `006_add_job_service_id.py`).

```
┌─────────────────────────── compiler schema ──────────────────────────┐
│                                                                      │
│  compilation_jobs                    compilation_events               │
│  ┌──────────────────────┐            ┌──────────────────────┐        │
│  │ id           UUID PK │◄──────────┐│ id           UUID PK │        │
│  │ source_url   text    │           ││ job_id       UUID FK │────┘   │
│  │ source_content text  │           │ sequence_number int   │        │
│  │ source_hash  text    │           │ stage         text    │        │
│  │ protocol     text    │           │ event_type    text    │        │
│  │ service_id   text    │           │ attempt       int     │        │
│  │ service_name text    │           │ detail        jsonb   │        │
│  │ status       text    │           │ error_detail  jsonb   │        │
│  │ current_stage text   │           │ created_at    timestamptz│     │
│  │ error_detail jsonb   │           └──────────────────────┘        │
│  │ created_by   text    │                                            │
│  │ tenant       text    │            review_workflows                │
│  │ environment  text    │            ┌──────────────────────┐        │
│  │ created_at   timestamptz│         │ service_id   text    │        │
│  │ updated_at   timestamptz│         │ version_number int   │        │
│  └──────────────────────┘            │ state        text    │        │
│                                      │ review_notes jsonb   │        │
│  status ∈ {pending, running,         │ transitions  jsonb[] │        │
│            succeeded, failed,        └──────────────────────┘        │
│            rolled_back}                                              │
└──────────────────────────────────────────────────────────────────────┘

┌─────────────────────────── registry schema ──────────────────────────┐
│                                                                      │
│  service_versions                    artifact_records                 │
│  ┌──────────────────────┐            ┌──────────────────────┐        │
│  │ service_id   text    │            │ service_id   text    │        │
│  │ version_number int   │            │ version_number int   │        │
│  │ tenant       text    │            │ content_type text    │        │
│  │ environment  text    │            │ content_hash text    │        │
│  │ ir_json      jsonb   │            │ content      text    │        │
│  │ raw_ir_json  jsonb   │            └──────────────────────┘        │
│  │ compiler_version text│                                            │
│  │ protocol     text    │   content_type ∈ {manifest, ir_blob}       │
│  │ source_url   text    │                                            │
│  │ source_hash  text    │   Invariants:                              │
│  │ is_active    bool    │   • UNIQUE(service_id, version_number,     │
│  │ route_config jsonb   │            tenant, environment)            │
│  │ validation_report jsonb│ • Only one is_active=true per            │
│  │ deployment_revision text│           (service_id, tenant, env)     │
│  │ created_at   timestamptz│                                         │
│  │ updated_at   timestamptz│                                         │
│  └──────────────────────┘                                            │
└──────────────────────────────────────────────────────────────────────┘

┌───────────────────────────── auth schema ─────────────────────────────┐
│                                                                       │
│  users                     pats (Personal Access Tokens)              │
│  ┌─────────────────┐       ┌─────────────────────┐                   │
│  │ id        UUID  │       │ id          UUID     │                   │
│  │ username  text  │       │ user_id     UUID FK  │                   │
│  └─────────────────┘       │ token_hash  text     │                   │
│                            │ prefix      "pat_"   │                   │
│  policies                  │ created_at  timestamptz│                 │
│  ┌─────────────────────┐   │ revoked_at  timestamptz│                 │
│  │ id          UUID    │   └─────────────────────┘                   │
│  │ subject_type text   │                                              │
│  │ subject_id  text    │   audit_log (append-only)                    │
│  │ resource_id text    │   ┌─────────────────────────┐               │
│  │ action_pattern text │   │ id           UUID        │               │
│  │ decision    text    │   │ action       text        │               │
│  │ risk_threshold text │   │ actor        text        │               │
│  └─────────────────────┘   │ resource_type text       │               │
│                            │ resource_id  text        │               │
│  decision ∈ {allow, deny,  │ detail       jsonb       │               │
│   require_approval}        │ created_at   timestamptz │               │
│                            └─────────────────────────┘               │
└───────────────────────────────────────────────────────────────────────┘
```

### Key Invariants

- **Monotonic versions:** Version numbers auto-increment per `service_id`.
- **Single active version:** Only one `is_active=true` row per
  `(service_id, tenant, environment)` scope. Activating a version
  deactivates siblings.
- **Event ordering:** `(job_id, sequence_number)` is unique with retry
  logic (up to 3 attempts on conflict).
- **Append-only audit:** The `audit_log` table is insert-only; no
  updates or deletes.

---

## 4. Messaging

### Celery Task Queue

The Compiler API dispatches compilation jobs to a Celery worker via
`CeleryCompilationDispatcher` (`apps/compiler_worker/celery_app.py`).

| Setting | Value |
|---------|-------|
| Broker | Redis (falls back to `memory://` in dev, logs warning) |
| Result backend | Redis (falls back to `cache+memory://`) |
| Task name | `compiler_worker.execute_compilation` |
| Dispatch | `task.apply_async(...)` |

The worker is started by `apps/compiler_worker/entrypoint.py`, which
waits for Redis broker reachability, then launches the Celery consumer
and an HTTP diagnostics shell side-by-side.

### SSE Event Streaming

Clients observe compilation progress in real time via Server-Sent Events:

```
GET /api/v1/compilations/{job_id}/events
Accept: text/event-stream
Authorization: Bearer <token>
```

Event wire format:

```
event: stage.succeeded
data: {"event_type":"stage.succeeded","job_id":"...","stage":"extract","sequence_number":3,...}

```

The API polls `compilation_events` every 500 ms (with `after_sequence`
cursor) and streams until a terminal job status is reached or the client
disconnects.

### Job Status Transitions

```
pending ──► running ──┬──► succeeded
                      ├──► failed
                      └──► rolled_back
```

### Compilation Event Types

| Category | Events |
|----------|--------|
| Job lifecycle | `job.created`, `job.started`, `job.succeeded`, `job.failed`, `job.rolled_back` |
| Stage | `stage.started`, `stage.succeeded`, `stage.retrying`, `stage.failed` |
| Rollback | `rollback.started`, `rollback.succeeded`, `rollback.failed` |

### Retry & Rollback

- Default retry policy: `max_attempts=3` per stage.
- Retry events include `detail.next_attempt`.
- Only `deploy` and `route` stages have production rollback handlers.
- Deploy rollback: best-effort deletion of created K8s resources in
  reverse order.
- Route rollback: restores previously captured route documents via
  Access Control.

---

## 5. IR Lifecycle

`ServiceIR` (`libs/ir/models.py`) is the **central contract** of the
platform. Every extractor produces it; every downstream component
consumes it. See [ADR-001](adr/001-ir-as-first-class-artifact.md).

### Creation (Extract Stage)

An extractor converts the upstream source into `ServiceIR`:

```python
# Simplified — each extractor follows this contract (libs/extractors/base.py)
class Extractor:
    def detect(self, source: SourceConfig) -> float     # confidence ∈ [0.0, 1.0]
    def extract(self, source: SourceConfig) -> ServiceIR
```

The `TypeDetector` runs `detect()` on all registered extractors, selects
the highest-confidence result (ties broken by registration order), and
delegates `extract()`.

Fields set at creation time:

- `ir_version` — semver (currently `"1.0.0"`)
- `compiler_version` — semver (currently `"0.1.0"`)
- `protocol` — detected protocol name
- `source_hash` — SHA-256 of source input
- `created_at` — UTC timestamp

### Enhancement (Enhance Stage)

The enhancer (`libs/enhancer/enhancer.py`) enriches the IR in two phases:

1. **LLM-assisted** (optional, `skip_enhancement` disables):
   - Rewrites `Operation.description` and `Param.description` where
     existing text is too short (< 20 / < 10 chars).
   - Batch size: 10 operations per LLM call, 50 000 token budget.
   - Providers: OpenAI (default), Anthropic, DeepSeek, Vertex AI.
   - Failures are non-blocking — skipped and logged.
   - Enhanced fields are tagged with `source: SourceType.llm`.

2. **Deterministic post-enhancement** (always runs):
   - Tool-intent derivation (`libs/enhancer/tool_intent.py`):
     `discovery` (read-only) vs `action` (state-mutating).
   - Description bifurcation with `[DISCOVERY]` / `[ACTION]` prefixes.
   - Error-schema normalisation (`libs/enhancer/error_normalizer.py`).
   - Optional: tool grouping (`libs/enhancer/tool_grouping.py`),
     response-example generation (`libs/enhancer/examples_generator.py`).

### Source Tracking

Every field that may be set by different actors carries `source` and
`confidence`:

```python
class SourceType(str, Enum):
    extractor     = "extractor"    # direct extraction from source
    llm           = "llm"          # LLM-generated
    user_override = "user_override" # human operator

# Tracked on: Param, RiskMetadata, Operation, ToolGroup, ResponseExample
```

Invariant: `source=extractor` requires `confidence ≥ 0.8`.

### Validation (Validate Stages)

| Phase | Validator | Checks |
|-------|-----------|--------|
| Pre-deploy (stage ④) | `PreDeployValidator` (`libs/validator/pre_deploy.py`) | IR schema, event support, auth smoke (OAuth2 token-endpoint reachability, secret-ref presence) |
| Post-deploy (stage ⑦) | `PostDeployValidator` (via `libs/validator/post_deploy.py`) | Runtime `/healthz` + `/readyz`, tool listing, representative smoke-tool invocation |
| Audit (optional) | `AuditValidator` (`libs/validator/audit.py`) | Per-tool audit over auditable operations, LLM-as-Judge scoring |

### Persistence (Register Stage)

The completed IR is stored as `ir_json` (JSONB) in
`registry.service_versions`, along with:

- `raw_ir_json` — pre-enhancement backup
- `validation_report` — combined pre/post reports
- `route_config` — gateway routes
- `deployment_revision` — K8s revision marker

Companion `artifact_records` rows store the manifest YAML and IR blob
with content hashes.

### Loading (Runtime Startup)

The MCP Runtime loads `ServiceIR` at startup
(`apps/mcp_runtime/loader.py`):

1. Read `SERVICE_IR_PATH` (default: `/config/service-ir.json.gz`).
2. Detect gzip (magic bytes `\x1f\x8b`) → decompress.
3. Deserialise via `ServiceIR.model_validate(...)` (Pydantic v2).
4. Validate secret-reference name collisions.
5. On failure: boot an HTTP shell with `load_error` set;
   `/readyz` returns 503, `/tools` returns 0 tools.

### Diffing

`libs/ir/diff.py` provides semantic IR comparison, exposed via:

```
GET /api/v1/artifacts/{service_id}/diff?from=<v>&to=<v>
```

Returns `ArtifactDiffResponse` with:

- `added_operations`, `removed_operations`, `changed_operations`
- Per-changed-op: field-level diffs, `added_params`, `removed_params`
- Synthetic sections for `__service__`, `__resource_definitions__`,
  `__prompt_definitions__`, `__event_descriptors__`

### IR Model Hierarchy

```
ServiceIR (root)
├── ir_version, compiler_version, protocol, source_hash
├── service_name, service_description, base_url
├── tenant, environment, created_at
├── auth: AuthConfig
│   ├── type (bearer | basic | api_key | custom_header | oauth2 | none)
│   ├── oauth2: OAuth2ClientCredentialsConfig
│   ├── mtls: MTLSConfig
│   └── request_signing: RequestSigningConfig
├── operations: list[Operation]
│   ├── id, name, description, method, path, enabled
│   ├── params: list[Param]  (name, type, required, source, confidence)
│   ├── risk: RiskMetadata
│   │   └── writes_state, destructive, external_side_effect,
│   │       idempotent, risk_level, confidence, source
│   ├── response_strategy: ResponseStrategy
│   │   └── pagination: PaginationConfig
│   ├── error_schema: ErrorSchema → list[ErrorResponse]
│   ├── response_examples: list[ResponseExample]
│   ├── tool_intent: ToolIntent (discovery | action)
│   └── Protocol-specific configs (mutually exclusive):
│       ├── graphql:  GraphQLOperationConfig
│       ├── sql:      SqlOperationConfig
│       ├── grpc_unary: GrpcUnaryRuntimeConfig
│       ├── soap:     SoapOperationConfig
│       ├── jsonrpc:  JsonRpcOperationConfig
│       └── async_job: AsyncJobConfig
├── operation_chains: list[OperationChain]
├── tool_grouping: list[ToolGroup]
├── event_descriptors: list[EventDescriptor]
│   └── grpc_stream: GrpcStreamRuntimeConfig
├── resource_definitions: list[ResourceDefinition]
└── prompt_definitions: list[PromptDefinition]
    └── arguments: list[PromptArgument]
```

---

## 6. Runtime Architecture

The MCP Runtime is a **single generic server image** that loads any
`ServiceIR` at startup and dynamically registers tools, resources, and
prompts. No per-service code generation is required for the default path.
See [ADR-002](adr/002-generic-runtime-default.md).

### Startup Sequence (`apps/mcp_runtime/main.py`)

```
1.  Load ServiceIR from disk           (loader.load_service_ir)
2.  Validate secret-ref collisions
3.  Configure tracing
4.  Conditionally enable native executors:
      SQL     → SQLRuntimeExecutor       (auto if IR has SQL ops)
      gRPC    → ReflectionGrpcUnary/     (env-gated:
                ReflectionGrpcStream       ENABLE_NATIVE_GRPC_UNARY,
                                           ENABLE_NATIVE_GRPC_STREAM)
5.  Create RuntimeProxy                 (apps/mcp_runtime/proxy.py)
6.  Register tools, resources, prompts  (loader.register_ir_*)
7.  Register runtime-stats resource
8.  Mount /mcp transport, expose HTTP endpoints
```

### Endpoints

| Path | Purpose |
|------|---------|
| `/mcp/mcp` | MCP Streamable HTTP transport (spec-compliant). The FastAPI app mounts the SDK's `streamable_http_app()` at `/mcp`; the SDK itself serves on `/mcp` within that mount, producing the doubled path. This is by design — all internal callers (smoke tests, worker `ToolInvoker`) use `/mcp/mcp`. |
| `/healthz` | Liveness probe — always `{"status":"ok"}` |
| `/readyz` | Readiness — checks IR loaded + upstream ping |
| `/tools` | Lists registered tools with `inputSchema` |
| `/metrics` | Prometheus scrape endpoint |

### RuntimeProxy (`apps/mcp_runtime/proxy.py`)

The proxy translates MCP tool calls into upstream API requests and shapes
responses back.

**Request shaping** (protocol-aware):

| Protocol | Shaping |
|----------|---------|
| REST / OpenAPI | Path-template resolution (`{id}` → URL-encoded), query vs body split, multipart/raw modes |
| GraphQL | `{"query", "variables", "operationName"}` envelope |
| SOAP | XML envelope + `SOAPAction` header |
| JSON-RPC | JSON-RPC 2.0 envelope (positional or named params) |
| OData | Restore `$` system-query-param prefixes |
| gRPC unary | Delegate to `GrpcUnaryRuntimeConfig.rpc_path` via native executor |
| SQL | Delegate to `SqlOperationConfig` via `SQLRuntimeExecutor` |

**Auth & secret resolution** — driven entirely by `ServiceIR.auth`:

- Bearer / OAuth2 → `Authorization` header
- API key → header or query parameter
- Basic auth → username + secret ref
- OAuth2 client-credentials → lazy token fetch + cache until expiry
- mTLS → client cert/key/CA from secret refs
- Request signing → HMAC signature + timestamp headers
- Missing secret ref → **explicit failure** (never silent skip)

**Response shaping:**

- Protocol-specific unwrapping (SOAP envelope, GraphQL `data`/`errors`,
  OData/SCIM collection envelope, JSON-RPC `result`/`error`).
- Field filtering (top-level keys, nested dot-paths, array paths).
- Array truncation, byte truncation, binary → base64 encoding.
- Result shape: `{status, operation_id, upstream_status, result, truncated}`.

**Async-job polling** (for 202-style APIs):

- Configurable via `AsyncJobConfig` on the operation.
- Polls status URL (from `Location` header or response body).
- Respects pending/success/failure status values.
- Same-origin restriction on poll URLs (SSRF mitigation).

### Circuit Breaker (`apps/mcp_runtime/circuit_breaker.py`)

Per-operation circuit breakers protect against cascading upstream
failures:

- **Failure threshold:** 5 consecutive failures (configurable).
- **Behaviour:** Breaker opens immediately once threshold reached.
  Currently no half-open / cooldown — stays open until process restart.
- **Observed metrics:** success/error counts, latency per operation,
  upstream error classification, state transitions.

### Native Executors

For protocols that benefit from native client libraries rather than
HTTP proxying:

| Executor | Path | Auto-enable? | Condition |
|----------|------|-------------|-----------|
| SQL | `apps/mcp_runtime/sql.py` | Yes | IR contains enabled SQL operations |
| gRPC Unary | `apps/mcp_runtime/grpc_unary.py` | No | `ENABLE_NATIVE_GRPC_UNARY=true` + ops with `grpc_unary` |
| gRPC Stream | `apps/mcp_runtime/grpc_stream.py` | No | `ENABLE_NATIVE_GRPC_STREAM=true` + events with `grpc_stream` |

### Tool Registration

For each enabled `Operation` in the IR, `loader.register_ir_tools()`
dynamically builds a Python function whose signature matches the
operation's parameters, then registers it with `FastMCP.add_tool()`.
Reserved-keyword and name-collision handling is built into the parameter
mapper.

```python
# Simplified registration flow (apps/mcp_runtime/loader.py)
for op in service_ir.operations:
    if not op.enabled:
        continue
    fn = build_tool_function(op, handler=proxy.invoke)
    server.add_tool(fn, name=op.id, title=op.name, description=op.description)
```

---

## 7. Extension Points

### Adding a New Protocol Extractor

1. Create `libs/extractors/<protocol>.py` implementing the extractor
   contract:

   ```python
   class MyProtocolExtractor:
       def detect(self, source: SourceConfig) -> float:
           """Return confidence ∈ [0.0, 1.0]."""
           ...
       def extract(self, source: SourceConfig) -> ServiceIR:
           """Normalise source into ServiceIR."""
           ...
   ```

2. Register in the extractor list in
   `apps/compiler_worker/activities/production.py`
   (registration order determines tie-breaking).

3. Add test fixtures under `tests/fixtures/`.

**Rules** (see [ADR-001](adr/001-ir-as-first-class-artifact.md)):

- Extractors **must not** call LLMs. All LLM work lives in
  `libs/enhancer/`.
- Extractor output uses `source: SourceType.extractor`.
- Risk metadata must be set explicitly, not inferred from HTTP verbs
  alone ([ADR-005](adr/005-semantic-risk-classification.md)).

### Adding a New Deployment Target (Generator)

Generators live in `libs/generator/`. The current modes:

| Mode | File | Output |
|------|------|--------|
| `generic` | `libs/generator/generic_mode.py` | ConfigMap + Deployment + Service + NetworkPolicy + route config |
| `codegen` | `libs/generator/codegen_mode.py` | Code-generated runtime variant |

Templates are in `libs/generator/templates/`. To add a target (e.g.,
Docker Compose, serverless), create a new mode module that produces a
`GeneratedManifestSet`.

### Adding a New Auth Scheme

`AuthConfig` in `libs/ir/models.py` already supports seven auth types.
To add a new one:

1. Add the type to the `AuthType` enum.
2. Add optional config fields to `AuthConfig`.
3. Handle resolution in `RuntimeProxy` request shaping.
4. Add validation in the `AuthConfig` model validators.

### Adding New Validators

Validators under `libs/validator/` can be plugged into the
pre-deploy or post-deploy stages. The audit framework
(`libs/validator/audit.py`) supports custom audit policies and
the LLM-as-Judge scorer (`libs/validator/llm_judge.py`).

---

## 8. Security Architecture

### Authentication

Two authentication mechanisms, unified under a single authorization
path. See [ADR-004](adr/004-oidc-jwt-auth-and-pats.md).

```
Client Request
     │
     ▼
┌────────────────────────┐
│   Access Control Svc   │
│                        │
│  ┌──────────────────┐  │
│  │ Bearer Token?    │  │
│  │  ├─ JWT → decode │  │    HS256, enforces exp/nbf/iss/aud
│  │  │   HS256       │  │    Username claim: preferred_username
│  │  │               │  │      → username → cognito:username → login
│  │  └─ PAT → hash   │  │    SHA-256 lookup in DB
│  │     lookup        │  │    prefix: pat_
│  └──────────────────┘  │
│           │             │
│           ▼             │
│  ┌──────────────────┐  │
│  │ Policy Engine    │  │    RBAC: subject × resource × action
│  │ (AuthzService)   │  │    Specificity-ranked matching
│  │                  │  │    Default: DENY
│  └──────────────────┘  │
│           │             │
│           ▼             │
│  ┌──────────────────┐  │
│  │ Audit Log        │  │    Append-only record
│  └──────────────────┘  │
└────────────────────────┘
```

**JWT:**

- Algorithm: HS256 (strict).
- Secret: `JWT_SECRET` env var (required in production).
- Dev/test fallback: `dev-secret`.
- Claims enforced: `exp`, `nbf` (when present), `iss`, `aud` (when
  configured).
- Internal admin tokens: short-lived JWTs minted by Access Control for
  control-plane calls (e.g., route publication).

**Personal Access Tokens (PAT):**

- Prefix: `pat_`.
- Plaintext returned **only at creation**; stored as SHA-256 hash.
- Revocation is idempotent.
- Gateway sync uses the hashed form.

### Authorization (RBAC)

Policy model (`apps/access_control/`):

```python
class Policy:
    subject_type: str      # user, role, service
    subject_id: str        # exact match or wildcard *
    resource_id: str       # fnmatch glob, case-sensitive
    action_pattern: str    # fnmatch glob
    decision: str          # allow | deny | require_approval
    risk_threshold: str    # safe | cautious | dangerous | unknown
```

**Matching rules:**

1. Candidate policies must match `subject_type`.
2. `subject_id`: exact match or wildcard `*`.
3. `resource_id` / `action_pattern`: fnmatch glob matching.
4. Risk ordering: `safe < cautious < dangerous < unknown`.

**Priority:** deny > require\_approval > allow. Default: **deny**.

### Semantic Risk

Every operation carries explicit `RiskMetadata`
([ADR-005](adr/005-semantic-risk-classification.md)):

| Field | Type | Meaning |
|-------|------|---------|
| `writes_state` | `bool?` | Mutates state |
| `destructive` | `bool?` | Irreversible change |
| `external_side_effect` | `bool?` | Triggers effects outside primary service |
| `idempotent` | `bool?` | Safe to retry |
| `risk_level` | enum | `safe`, `cautious`, `dangerous`, `unknown` |

Operations with `risk_level=unknown` are **auto-disabled** until
explicitly reviewed.

### Gateway Binding

`apps/access_control/gateway_binding/` synchronises policies, PATs, and
routes to an external API gateway:

- **Service-route sync:** Computes stale routes, publishes new ones,
  captures previous route documents for rollback.
- **Reconciliation:** Compares expected state against gateway, upserts
  or deletes diffs.
- **Transactional coupling:** PAT/policy mutations + gateway sync in
  same logical unit. Gateway sync failure → DB rollback + 502.

Pluggable client: in-memory (default) or HTTP gateway-admin
(`GATEWAY_ADMIN_URL`).

### Manifest Hardening

Generated K8s manifests (`libs/generator/`) include:

- Non-root UID/GID (10001)
- seccomp `RuntimeDefault` profile
- No privilege escalation
- All capabilities dropped
- Read-only root filesystem + `/tmp` emptyDir
- Liveness / readiness probes
- NetworkPolicy: egress restricted to upstream API + OAuth2 token
  endpoint + DNS

---

## 9. Observability Architecture

### Structured Logging

Single-line JSON format across all components:

```json
{
  "timestamp": "2025-01-15T12:00:00Z",
  "level": "INFO",
  "component": "mcp_runtime",
  "logger": "proxy",
  "message": "Tool invocation succeeded",
  "trace_id": "abc123",
  "span_id": "def456"
}
```

Guaranteed fields: `timestamp`, `level`, `component`, `logger`,
`message`. Optional: `trace_id`, `span_id`, `extra`, `exception`.

### Metrics (Prometheus)

Each MCP Runtime instance exposes `/metrics` in Prometheus exposition
format (`apps/mcp_runtime/observability.py`).

| Metric | Type | Labels |
|--------|------|--------|
| `mcp_runtime_tool_calls_total` | Counter | operation, status |
| `mcp_runtime_tool_latency_seconds` | Histogram | operation |
| `mcp_runtime_upstream_errors_total` | Counter | operation, error\_class |
| `mcp_runtime_circuit_breaker_state` | Gauge | operation, state |

Histogram buckets: `0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
2.5, 5.0, 10.0, 30.0` seconds.

The Compiler Worker also exposes a `/metrics` endpoint on its HTTP
diagnostics shell (`apps/compiler_worker/observability.py`).

### Tracing (OpenTelemetry)

- **Safe by default:** No-op when `OTEL_EXPORTER_ENDPOINT` is absent.
- **Local spans:** Enable via `enable_local=True` for in-process traces.
- **Export:** OTLP gRPC; honours `OTEL_EXPORTER_OTLP_INSECURE`.
- Helper span contexts degrade to no-op when tracing is disabled.

Configuration in `libs/` — shared by Compiler API, Worker, and Runtime.

### Dashboards

Pre-built Grafana dashboards ship in `observability/grafana/`:

| Dashboard | File | Panels |
|-----------|------|--------|
| Runtime | `runtime-dashboard.json` | Tool calls, latency, errors, circuit-breaker state |
| Compilation | `compilation-dashboard.json` | Workflow stage durations, success/failure rates |

Prometheus scrape config: `observability/prometheus/`.
Collector config: `observability/otel/`.

### Health & Readiness Summary

| Component | `/healthz` | `/readyz` |
|-----------|-----------|-----------|
| Compiler API | Always `ok` | DB `SELECT 1` probe |
| Access Control | Always `ok` | DB probe + JWT config + gateway binding |
| Compiler Worker | Always `ok` | Reports config completeness (engine, image, namespace) |
| MCP Runtime | Always `ok` | IR loaded + upstream reachability |

---

*Last updated: DOC-004. See `agent.md` for current project status.*
