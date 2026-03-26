# Tool Compiler v2 — Development Log

> Chronological record of implementation progress. Updated after each task completion.

---

## 2026-03-24 — Phase 1 Kickoff (T-001 → T-005)

### T-001: Initialize repository structure ✅

**Commit:** `9d427b9`

Set up the full monorepo directory structure per the SDD:
- `libs/` — shared libraries (ir, extractors, enhancer, validator, generator, registry_client, observability)
- `apps/` — deployable services (compiler_api, compiler_worker, access_control, mcp_runtime)
- `tests/`, `migrations/`, `deploy/`, `observability/`, `specs/`, `docs/`, `scripts/`
- `pyproject.toml` with hatchling build, all dependency groups (dev, extractors, enhancer, observability)
- `Makefile` with setup, test, lint, typecheck targets
- `.gitignore` for Python projects

Created venv, installed all deps. Verified all packages importable.

---

### T-002: Define IR Pydantic models ✅

**Commit:** `b7db355`

Implemented the core data model in `libs/ir/models.py`:
- **Enums:** `RiskLevel` (safe/cautious/dangerous/unknown), `SourceType` (extractor/llm/user_override), `AuthType`, `TruncationPolicy`
- **Models:** `Param`, `RiskMetadata`, `PaginationConfig`, `ResponseStrategy`, `Operation`, `AuthConfig`, `OperationChain`, `ServiceIR`
- **Validators:** extractor confidence ≥ 0.8, unknown risk → disabled, unique operation IDs, valid chain step references
- **Schema utils** in `libs/ir/schema.py`: `generate_json_schema()`, `serialize_ir()`, `deserialize_ir()`, `ir_to_dict()`, `ir_from_dict()`

**Tests:** 26/26 passing in `libs/ir/tests/test_models.py`
- Validation acceptance/rejection tests for all invariants
- JSON round-trip serialization
- JSON Schema generation
- Hypothesis property-based tests for round-trip fidelity

---

### T-003: Implement IR diff computation ✅

**Commit:** `bc862b2`

Implemented `libs/ir/diff.py`:
- `compute_diff(old, new)` → `IRDiff` with `added`, `removed`, `changed` operations
- `OperationDiff` captures field-level changes between operations
- Detects changes in `description`, `method`, `path`, `risk.risk_level`, `params`, `enabled`

**Tests:** 12/12 passing in `libs/ir/tests/test_diff.py`
- Identical IRs → empty diff
- Added/removed/changed operations detected correctly
- Risk level changes, param changes, description changes all captured
- Multiple simultaneous changes handled

---

### T-004: Implement extractor base protocol and type detector ✅

**Commit:** `b58f4ae`

Implemented `libs/extractors/base.py`:
- `ExtractorProtocol` — runtime-checkable Protocol with `detect(source) → float` and `extract(source) → ServiceIR`
- `SourceConfig` — dataclass holding URL, file path, content, auth headers, hints
- `TypeDetector` — accepts registered extractors, runs detection in parallel, returns highest-confidence extractor
  - Configurable `min_confidence` threshold (default 0.3)
  - Supports `source.hints["protocol"]` for explicit override

**Tests:** 13/13 passing in `libs/extractors/tests/test_detection.py`
- Protocol compliance tests
- TypeDetector selection logic (highest confidence wins)
- Hint-based override
- Below-threshold rejection
- Empty registry handling

---

### T-005: Implement OpenAPI extractor ✅

**Commit:** `9596656`

Implemented `libs/extractors/openapi.py` (364 lines):
- **Detection:** content-based analysis for `openapi`, `swagger`, `paths` keys with confidence scoring
- **Parsing:** Swagger 2.0, OpenAPI 3.0, and 3.1 specs (YAML and JSON)
- **$ref resolution:** recursive in-place resolution of JSON `$ref` pointers
- **Operations:** method + path → operation ID, with all parameters extracted
- **Risk classification:** `GET` → safe, `POST/PUT/PATCH` → cautious, `DELETE` → dangerous
- **Auth extraction:** securitySchemes parsing for bearer, apiKey, basic, oauth2
- **Parameters:**
  - Path, query, header params from `parameters` array
  - Request body flattened to top-level params (OpenAPI 3.x `requestBody`)
  - Swagger 2.0 `body` params with schema flattening
- **Base URL:** from `servers[0].url` (3.x) or `host + basePath` (2.0)

**Test fixtures:**
- `tests/fixtures/openapi_specs/petstore_3_0.yaml` — OpenAPI 3.0.3 with CRUD operations
- `tests/fixtures/openapi_specs/petstore_swagger_2_0.json` — Swagger 2.0 equivalent

**Tests:** 22/22 passing in `libs/extractors/tests/test_openapi.py`
- Swagger 2.0 and OpenAPI 3.0 full extraction
- Operation count, names, methods, paths
- Parameter extraction (path params, query params, request body)
- Risk classification per HTTP method
- Auth detection (bearer, apiKey)
- Empty spec handling
- Detection confidence scoring

---

## 2026-03-24 — Phase 1 Continuation (T-006 → T-008)

### T-006: Implement basic LLM enhancer ✅

**Commit:** pending

Implemented `libs/enhancer/enhancer.py`:
- `IREnhancer` batches operations, builds enhancement prompts, parses JSON responses, and applies description-only updates back onto `ServiceIR`
- Preserves all structural fields (`id`, `name`, `method`, `path`, param types/required flags)
- Tags LLM-contributed descriptions with `source="llm"` and confidence scores
- Tracks per-job token usage via `TokenUsage`
- Supports provider factories for Anthropic, OpenAI, and Vertex AI
- Skips already well-described operations when configured

**Tests:** 13/13 passing in `libs/enhancer/tests/test_enhancer.py`
- Description enhancement and structural preservation
- LLM source/confidence tagging
- Failure fallback and malformed-response handling
- Markdown-fenced JSON parsing
- Batch processing and metadata preservation
- Vertex AI factory path

### T-007: Implement shared observability utilities ✅

**Commit:** pending

Implemented shared observability modules:
- `libs/observability/metrics.py` — counter/histogram/gauge factories with per-registry dedupe protection
- `libs/observability/tracing.py` — OpenTelemetry setup with safe no-op fallback when exporter config is absent
- `libs/observability/logging.py` — structured JSON formatter with component name, exception details, and trace/span context support
- `libs/observability/__init__.py` — shared export surface for app components

**Tests:** 13/13 passing in `libs/observability/tests/test_observability.py`
- Metric creation across counters, histograms, gauges
- Same metric name isolation across registries
- No-op tracing behavior
- Structured JSON logging, exception serialization, trace ID propagation

### T-008: Set up PostgreSQL schema and Alembic migrations ✅

**Commit:** pending

Implemented database schema and migration scaffolding:
- `libs/db_models.py` defines all 7 ORM models across `compiler`, `registry`, and `auth` schemas
- `migrations/alembic.ini` and `migrations/env.py` configure Alembic for PostgreSQL with async URL compatibility
- `migrations/versions/001_initial.py` creates all control-plane schemas, tables, indices, and foreign keys
- Added `.hypothesis/` to `.gitignore` to keep the worktree clean during property-based testing

**Tests:** 22/22 passing in `libs/tests/test_db_models.py`
- Table presence and schema assignment
- Required columns, indices, and foreign keys
- Migration module importability and revision metadata

### T-009: Implement artifact registry data layer ✅

**Commit:** pending

Implemented artifact registry persistence and API surface:
- `apps/compiler_api/main.py` now exposes the compiler API FastAPI app and wires async DB lifecycle
- `apps/compiler_api/db.py` provides injected or environment-based SQLAlchemy async session management
- `apps/compiler_api/repository.py` implements service-version CRUD, activation, tenant/environment filtering, and IR diff generation
- `apps/compiler_api/routes/artifacts.py` adds registry endpoints for create/list/get/update/delete/activate/diff
- `libs/registry_client/models.py` defines the shared request/response contract for the registry API
- `libs/registry_client/client.py` adds an async client for downstream consumers
- `libs/db_models.py` relationship settings now correctly handle artifact deletion with parent service-version removal

**Tests:** 2/2 passing in `tests/integration/test_artifact_registry.py`
- Testcontainers PostgreSQL CRUD flow for create/get/list/filter/update/activate/delete/diff
- Async `RegistryClient` round-trip against the FastAPI app backed by real PostgreSQL

### T-010: Implement generic MCP runtime — IR loader and tool registration ✅

**Commit:** pending

Implemented the generic runtime startup path:
- `apps/mcp_runtime/loader.py` loads and validates `ServiceIR` JSON from disk, maps IR params to Python signatures, and registers enabled operations as FastMCP tools
- `apps/mcp_runtime/main.py` creates the runtime FastAPI app, mounts the MCP HTTP app at `/mcp`, and exposes `/healthz`, `/readyz`, and `/tools`
- `apps/mcp_runtime/__init__.py` exports the runtime app, loader, and state helpers
- Added IR fixtures in `tests/fixtures/ir/` for valid and invalid startup scenarios

**Tests:** 4/4 passing in `tests/integration/test_mcp_runtime.py`
- Valid IR fixture loads successfully and registers only enabled operations
- Invalid IR fixture fails validation cleanly
- Health endpoints return `200` when loaded and `503` when startup IR load fails
- Runtime tool listing matches the enabled operations in the IR fixture

### T-011: Implement generic MCP runtime — upstream proxy and response handling ✅

**Commit:** pending

Implemented runtime upstream execution:
- `apps/mcp_runtime/proxy.py` proxies MCP tool invocations to upstream HTTP endpoints, injects auth from environment, sanitizes responses, and applies truncation rules
- `apps/mcp_runtime/circuit_breaker.py` adds a per-operation circuit breaker that opens after 5 consecutive failures
- `apps/mcp_runtime/main.py` now wires a `RuntimeProxy` into `RuntimeState`, supports injected `httpx` clients for tests, and passes proxy configuration into dynamic tool registration
- Added proxy-focused IR fixture coverage in `tests/fixtures/ir/service_ir_proxy.json`

**Tests:** 4/4 passing in `tests/integration/test_mcp_runtime_proxy.py`
- MCP tool call proxies a GET request with the correct URL, query string, and bearer auth header
- MCP tool call proxies a POST request with the correct JSON body and truncates oversized responses
- Missing required tool params fail validation before any upstream request is sent
- Upstream timeout returns an error and repeated upstream failures open the circuit breaker for fast-fail behavior

### T-012: Implement runtime metrics and tracing ✅

**Commit:** pending

Implemented runtime observability:
- `apps/mcp_runtime/observability.py` creates a per-runtime Prometheus registry with tool call, latency, upstream error, and circuit breaker metrics
- `apps/mcp_runtime/main.py` now exposes `/metrics` and wires runtime observability into `RuntimeState`
- `apps/mcp_runtime/proxy.py` records metrics on success/failure, logs structured invocation events, and wraps each tool invocation in a trace span
- `libs/observability/tracing.py` now supports local in-process span creation for runtime tracing even when no exporter endpoint is configured

**Tests:** 2/2 passing in `tests/integration/test_mcp_runtime_observability.py`
- `/metrics` reports incremented tool call, latency, upstream error, and circuit breaker metrics after runtime invocations
- Structured runtime logs include trace and span IDs during a traced tool invocation

---

### T-013: Implement Kubernetes manifest generator (generic mode) ✅

**Commit:** pending

Implemented generic-mode artifact generation:
- `libs/generator/generic_mode.py` now renders a four-document Kubernetes bundle from `ServiceIR`
- Added Jinja templates in `libs/generator/templates/` for `ConfigMap`, `Deployment`, `Service`, and `NetworkPolicy`
- `libs/generator/__init__.py` now exports the manifest generator API and config types
- Generated manifests mount IR JSON at `/config/service-ir.json`, reference the generic runtime image, and apply secure-by-default workload settings
- Deployment defaults include non-root execution, read-only root filesystem, dropped Linux capabilities, `RuntimeDefault` seccomp, health probes, and a writable `/tmp` `emptyDir`
- NetworkPolicy defaults to egress-only rules for upstream API port plus DNS

**Tests:** 2/2 passing in `libs/generator/tests/test_generic_mode.py`
- Fixture IR generates valid YAML documents with the expected kinds, image reference, ConfigMap mount, and security context
- Service name sanitization and optional name suffix generation behave as expected

---

### T-014: Implement compilation pipeline state machine ✅

**Commit:** pending

Implemented the durable compilation workflow core for the compiler worker:
- Added `apps/compiler_worker/workflows/compile_workflow.py` with the ordered stage machine (`detect → extract → enhance → validate_ir → generate → deploy → validate_runtime → route → register`)
- Added configurable per-stage retry handling, persisted stage/job events, and reverse-order rollback across completed side-effect stages
- Added `apps/compiler_worker/models.py` for workflow status, stage, event, and result models
- Added `apps/compiler_worker/repository.py` with `SQLAlchemyCompilationJobStore` for persisted compilation job state and ordered event streams
- Added `apps/compiler_worker/activities/pipeline.py` with an activity registry abstraction so Celery/Redis wrappers can bind concrete stage handlers later
- Extended `libs/db_models.py` and `migrations/versions/001_initial.py` with `compiler.compilation_events`, including per-job sequence numbers for stable event ordering

**Tests:** 12/12 passing in `tests/integration/test_compile_workflow.py`
- Happy-path workflow execution records all stages in order
- Failure at each stage triggers the expected reverse-order rollback for completed side-effect stages
- Retryable stage failures are retried and then succeed without losing job state
- Workflow state and ordered events persist correctly through the SQLAlchemy/PostgreSQL store

---

### T-015: Implement Compiler API (HTTP endpoints) ✅

**Commit:** pending

Implemented the compiler API surface needed to submit and monitor compilation jobs:
- Added `apps/compiler_api/models.py` with request/response models for compilation jobs, workflow events, and compiled service summaries
- Added `apps/compiler_api/dispatcher.py` with a pluggable dispatch abstraction and an in-memory default queue so API submission is decoupled from the worker engine binding
- Extended `apps/compiler_api/repository.py` with compilation job persistence queries and active-service discovery queries while keeping artifact registry behavior intact
- Added `apps/compiler_api/routes/compilations.py` implementing `POST /api/v1/compilations`, `GET /api/v1/compilations/{job_id}`, and `GET /api/v1/compilations/{job_id}/events` with SSE event streaming
- Added `apps/compiler_api/routes/services.py` implementing `GET /api/v1/services` with tenant/environment filtering over active service versions
- Updated `apps/compiler_api/main.py` and `apps/compiler_api/routes/__init__.py` to wire the new routes and dispatcher configuration into the FastAPI app

**Tests:** 4/4 passing in `tests/integration/test_compiler_api.py`
- OpenAPI schema exposes the required compilation and service endpoints
- Submitting a compilation request creates a persisted pending job and enqueues it through the configured dispatcher
- SSE event streaming returns ordered workflow events for terminal jobs
- Service listing returns active compiled services and honors tenant filters

---

### T-016: Implement pre-deploy validation harness ✅

**Commit:** pending

Implemented the initial pre-deploy validation layer for compiled IR artifacts:
- Added `libs/validator/pre_deploy.py` with `ValidationResult`, `ValidationReport`, and `PreDeployValidator`
- Implemented IR schema validation by validating raw payloads into `ServiceIR`, so structural and semantic model invariants fail fast before deployment
- Implemented auth smoke validation with no-auth pass-through, secret-reference checks for credentialed auth, and live OAuth2 token-endpoint reachability probing via `httpx`
- Updated `libs/validator/__init__.py` to export the pre-deploy validator API for later workflow integration at the `validate_ir` stage

**Tests:** 3/3 passing in `libs/validator/tests/test_pre_deploy.py`
- Valid IR payloads pass schema and auth smoke validation
- Invalid IR payloads fail schema validation and produce a structured skipped auth result
- Unreachable OAuth2 token endpoints fail auth smoke validation with a clear error in the report

---

### T-017: Implement post-deploy validation harness ✅

**Commit:** pending

Implemented runtime publication checks for deployed MCP services:
- Added `libs/validator/post_deploy.py` with `PostDeployValidator`, built on the shared `ValidationResult` and `ValidationReport` primitives from the pre-deploy validator
- Implemented runtime health validation against `/healthz` and `/readyz`
- Implemented tool-listing validation against `/tools`, ensuring the deployed runtime exposes exactly the enabled operations expected by the `ServiceIR`
- Implemented invocation smoke validation using an injected tool invoker, with explicit skip/failure reasons when health, tool listing, or sample input prerequisites are not met
- Updated `libs/validator/__init__.py` to export the post-deploy validator for later workflow integration at the `validate_runtime` stage

**Tests:** 2/2 passing in `libs/validator/tests/test_post_deploy.py`
- Healthy runtime passes health, tool listing, and invocation smoke validation end to end
- Runtime/IR mismatches fail the tool-listing check and prevent false-positive publication

---

### T-018: Implement GraphQL extractor ✅

**Commit:** pending

Implemented GraphQL schema extraction from introspection payloads:
- Added `libs/extractors/graphql.py` with detection and extraction support for introspection JSON from file content, file paths, or live endpoint queries
- Extracted query and mutation root fields into `ServiceIR` operations, mapping GraphQL arguments into IR params with extractor source/confidence metadata
- Added scalar, enum, list, and input-object type handling so GraphQL arguments normalize cleanly into IR parameter types
- Derived GraphQL operation risk metadata semantically: queries as `safe`, mutations as `cautious`
- Added `tests/fixtures/graphql_schemas/catalog_introspection.json` as a canonical fixture covering nested input types, enums, defaults, and mixed query/mutation schemas
- Updated `libs/extractors/__init__.py` to export the GraphQL extractor

**Tests:** 2/2 passing in `libs/extractors/tests/test_graphql.py`
- Detection recognizes GraphQL introspection fixtures with high confidence
- Extraction produces the expected operations, parameter types, defaults, and risk classification for queries and mutations

---

### T-019: Implement SQL extractor ✅

**Commit:** pending

Implemented SQL schema extraction from live database metadata:
- Added `libs/extractors/sql.py` with SQLAlchemy-backed schema reflection over database URLs, including async inspection support from synchronous extractor entrypoints
- Implemented relation extraction for PostgreSQL tables and views, generating safe query operations for both and cautious insert operations for tables only
- Mapped reflected SQL column types into IR param types and carried foreign-key references into parameter descriptions
- Derived insert parameter requiredness from nullability, defaults, identity, and autoincrement metadata so generated write operations remain parameterized and explicit
- Added `tests/fixtures/sql_schemas/catalog.sql` as a canonical schema fixture covering foreign keys and a derived view
- Updated `libs/extractors/__init__.py` to export the SQL extractor

**Tests:** 2/2 passing in `libs/extractors/tests/test_sql.py`
- Detection recognizes PostgreSQL connection URLs with high confidence
- Extraction reflects tables, foreign keys, and views into the expected operation set and risk classifications

---

### T-020: Implement REST extractor (with LLM-assisted discovery) ✅

**Commit:** pending

Implemented discovery-first REST extraction for APIs without formal specs:
- Added `libs/extractors/rest.py` with crawl-based endpoint discovery from HTML links, forms, and JSON link payloads rooted at a base URL
- Added an endpoint-classifier abstraction so discovery output can be normalized by an LLM-backed classifier, while keeping a deterministic heuristic fallback for local/test execution
- Implemented optional method probing with `OPTIONS`, path/query parameter inference, and confidence scoring based on how an endpoint was discovered
- Normalized classified endpoints into `ServiceIR` operations with semantic risk classification derived from the resolved HTTP methods
- Updated `libs/extractors/__init__.py` to export the REST extractor

**Tests:** 2/2 passing in `libs/extractors/tests/test_rest.py`
- Discovery finds endpoints exposed through links and forms, then respects classifier-provided operation metadata
- Heuristic fallback still produces valid operations and correct risk levels when no custom classifier is injected

---

### T-021: Implement Access Control Service - AuthN module ✅

**Commit:** pending

Implemented the first access-control service slice on top of the existing auth schema:
- Added `apps/access_control/main.py` and `apps/access_control/db.py` to expose a dedicated FastAPI service with shared SQLAlchemy session wiring
- Added `apps/access_control/authn/service.py` with HS256 JWT validation, PAT generation, PAT revocation, PAT listing, and token-type-aware authentication dispatch
- Added `apps/access_control/authn/routes.py` and `apps/access_control/authn/models.py` with `POST /api/v1/authn/validate`, `POST /api/v1/authn/pats`, `GET /api/v1/authn/pats`, and `POST /api/v1/authn/pats/{pat_id}/revoke`
- Reused the existing `auth.users` and `auth.pats` tables so PAT lifecycle is fully backed by PostgreSQL without additional migrations
- Updated `apps/access_control/__init__.py` and `apps/access_control/authn/__init__.py` to export the new service and authn module surface

**Tests:** 3/3 passing in `tests/integration/test_access_control_authn.py`
- Valid JWTs pass validation and resolve the expected subject
- Expired JWTs are rejected with `401`
- PAT creation, listing, validation, and revocation all work end to end against PostgreSQL

---

### T-022: Implement Access Control Service - AuthZ module ✅

**Commit:** pending

Implemented policy management and semantic authorization evaluation:
- Added `apps/access_control/authz/models.py`, `apps/access_control/authz/service.py`, and `apps/access_control/authz/routes.py`
- Implemented full policy CRUD over the existing `auth.policies` table: create, list, get, update, delete
- Implemented authorization evaluation for `(subject, resource, action, risk_level)` with wildcard subject/resource/action support and default-deny behavior
- Enforced risk-threshold matching so policies scoped to `safe` operations do not implicitly allow `cautious` or `dangerous` actions
- Wired the authz router into `apps/access_control/main.py` and exported the module surface from `apps/access_control/authz/__init__.py`

**Tests:** 3/3 passing in `tests/integration/test_access_control_authz.py`
- Policy CRUD works end to end through the HTTP API
- Wildcard action policies allow matching operations while default deny still applies to unmatched subjects
- Risk-threshold enforcement blocks cautious operations without an explicit policy that covers them

---

### T-023: Implement Access Control Service - Gateway Binding module ✅

**Commit:** pending

Implemented gateway-side synchronization for access-control state:
- Added `apps/access_control/gateway_binding/client.py` with a gateway admin client protocol plus an in-memory APISIX-style test client
- Added `apps/access_control/gateway_binding/service.py` with PAT consumer sync, policy-binding sync, and full drift reconciliation against the PostgreSQL-backed source of truth
- Added `apps/access_control/gateway_binding/routes.py` exposing `POST /api/v1/gateway-binding/reconcile`
- Wired gateway binding into PAT creation/revocation and policy create/update/delete flows so gateway state updates happen as part of the auth lifecycle
- Updated `apps/access_control/main.py` to accept an injected gateway admin client and initialize the binding service on app state

**Tests:** 3/3 passing in `tests/integration/test_access_control_gateway_binding.py`
- Creating a PAT creates the corresponding gateway consumer
- Revoking a PAT deletes the gateway consumer
- Reconciliation restores drifted consumers and policy bindings from database state

---

### T-024: Implement audit logging ✅

**Commit:** pending

Implemented append-only audit logging across access control and compilation entrypoints:
- Added `apps/access_control/audit/models.py`, `apps/access_control/audit/service.py`, and `apps/access_control/audit/routes.py`
- Implemented queryable audit log reads by actor, action, resource, and time range over the existing `auth.audit_log` table
- Wired policy create/update/delete flows in authz routes to emit audit entries
- Wired compiler job submission in `apps/compiler_api/routes/compilations.py` to emit `compilation.triggered` audit entries into the shared audit log
- Mounted the audit router in `apps/access_control/main.py`

**Tests:** 2/2 passing in `tests/integration/test_access_control_audit.py`
- Permission changes produce audit log entries that are queryable by actor
- Compilation submissions produce audit log entries visible through the audit API

---

### T-025: Implement rollback workflow ✅

**Commit:** pending

Implemented rollback orchestration for previously compiled service versions:
- Added `apps/compiler_worker/workflows/rollback_workflow.py` with `RollbackRequest`, `RollbackResult`, and a workflow that redeploys a target version, waits for rollout, validates the runtime, persists the new deployment metadata, and reactivates the target version in the registry
- Introduced explicit rollback store, deployer, and validator protocols so the workflow stays decoupled from concrete worker, deployment, and validation engines
- Extended `apps/compiler_api/repository.py` with `ArtifactRegistryRepository.get_active_version(...)` so rollback can resolve the currently active version before switching traffic back
- Reused the existing artifact registry as the rollback source of truth, so rollback execution stays aligned with persisted IR, artifact, deployment, and activation metadata

**Tests:** 1/1 passing in `tests/integration/test_rollback_workflow.py`
- Compile/store v1 and v2, roll back to v1, and verify v1 becomes active again with the expected tool set served by the fake deployer

---

### T-026: Implement version coexistence support ✅

**Commit:** pending

Implemented version-aware runtime deployment planning and gateway route metadata:
- Extended `libs/generator/generic_mode.py` with `service_id` and `version_number` support so generated deployment, service, and ConfigMap names can coexist as `...-vN`
- Added generated route metadata for both stable traffic and version-pinned traffic, so default routing can switch atomically while old versions remain addressable
- Exported the route-config helper from `libs/generator/__init__.py`
- Added `tests/integration/test_version_coexistence.py` to prove v1 and v2 coexist in the registry and that active-version switching selects the new stable route target without deleting the old version

**Tests:** 2/2 passing in `libs/generator/tests/test_generic_mode.py` and `tests/integration/test_version_coexistence.py`
- Versioned manifests generate distinct `v1` / `v2` resource names and stable plus pinned route metadata
- Registry activation switches the active route target to the new version while preserving version-specific reachability for the old one

---

### T-027: Set up local development environment ✅

**Commit:** pending

Implemented the local development bootstrap and service topology:
- Added `deploy/docker-compose.yaml` covering PostgreSQL, Redis, Temporal dev server, Compiler API, Access Control, Compiler Worker shell, and Generic MCP Runtime shell
- Added `scripts/setup-dev.sh` for repeatable venv setup and compose validation
- Added `scripts/smoke-dev.sh` for local port and health-endpoint smoke checks
- Updated `Makefile` with `setup`, `test-integration`, and `dev-smoke` targets
- Added `apps/compiler_worker/main.py` and `apps/compiler_worker/observability.py` so the worker has health and metrics endpoints for local dev and later dashboarding

**Tests:** 3/3 passing in `tests/integration/test_compiler_worker_app.py` and `tests/contract/test_local_dev_assets.py`
- Worker shell exposes `/healthz`, `/readyz`, and `/metrics`
- Compose assets and Makefile targets include the required services, health checks, and local-dev commands

---

### T-028: Set up CI pipeline ✅

**Commit:** pending

Implemented a repository CI workflow with real quality gates:
- Added `.github/workflows/ci.yaml` with pull-request jobs for compose validation, `ruff`, `mypy`, unit tests, contract tests, and coverage reporting
- Added main-branch jobs for integration plus end-to-end tests and component image builds
- Added `deploy/docker/Dockerfile.app` and `.dockerignore` to support repeatable container builds for Compiler API, Access Control, Compiler Worker, and MCP Runtime
- Added `tests/contract/test_api_contracts.py` so CI runs actual OpenAPI contract validation against the FastAPI apps

**Tests:** 2/2 passing in `tests/contract/test_api_contracts.py`
- Compiler API OpenAPI schema validates and exposes the expected paths
- Access Control OpenAPI schema validates and exposes the expected paths

---

### T-029: Implement Grafana dashboard templates ✅

**Commit:** pending

Implemented dashboard templates for operator-facing observability:
- Added `observability/grafana/compilation-dashboard.json` covering jobs by status, stage durations, extractor success rate, and LLM token usage
- Added `observability/grafana/runtime-dashboard.json` covering tool-call QPS, latency percentiles, upstream errors, and circuit breaker state
- Aligned dashboard queries with the runtime metrics already exposed in `apps/mcp_runtime/observability.py` and the new compilation metrics exposed in `apps/compiler_worker/observability.py`

**Tests:** covered in `tests/contract/test_observability_and_helm_assets.py`
- Dashboard queries reference expected metrics

---

### T-030: Implement Helm chart for full platform deployment ✅

**Commit:** pending

Implemented the first full-platform Helm chart:
- Added `deploy/helm/tool-compiler/Chart.yaml` and `deploy/helm/tool-compiler/values.yaml`
- Added templates for shared secrets, PostgreSQL, Redis, Temporal, Compiler API, Access Control, Compiler Worker, MCP Runtime, and a post-install/post-upgrade migration job
- Wired the migration job to `alembic -c migrations/alembic.ini upgrade head`
- Used the same image contract as the CI build step so chart values map cleanly onto component images

**Tests:** covered in `tests/contract/test_observability_and_helm_assets.py`
- Helm chart structure and migration hook wiring are validated by contract tests; `helm` is not installed locally, so no live render/install verification was run

---

### T-031: Write ADRs for major design decisions ✅

**Commit:** pending

Added ADRs for the core architectural decisions:
- `docs/adr/001-ir-as-first-class-artifact.md`
- `docs/adr/002-generic-runtime-default.md`
- `docs/adr/003-pipeline-orchestration-abstraction.md`
- `docs/adr/004-oidc-jwt-auth-and-pats.md`
- `docs/adr/005-semantic-risk-classification.md`

Each ADR follows the standard structure: Title, Status, Context, Decision, Consequences.

---

### T-032: Write quickstart guide ✅

**Commit:** pending

Added `docs/quickstart.md` with prerequisites, `make dev-up` / `make dev-smoke`, sample compilation submission, runtime inspection and end-to-end verification commands, quality-gate commands, and teardown steps.

---

### T-033: End-to-end test: OpenAPI spec → running tool → agent invocation ✅

**Commit:** pending

Implemented the repository-level end-to-end acceptance test:
- Added `tests/e2e/test_full_compilation_flow.py`
- The test submits the Petstore spec to Compiler API, executes the workflow through an embedded Celery worker, generates manifests, deploys the generic runtime in an in-memory harness, registers the active artifact version, streams final job events, and performs a real MCP tool call
- Uses the real OpenAPI extractor, pre-deploy validator, manifest generator, post-deploy validator, artifact registry repository, Compiler API, and generic runtime

**Tests:** 1/1 passing in `tests/e2e/test_full_compilation_flow.py`
- Petstore submission completes successfully and an MCP tool invocation returns a valid upstream response

---

### Post-backlog hardening: real Helm / GKE validation ✅

Validated the packaged deployment path against the live test GKE cluster instead of stopping at contract-level chart checks.

Implemented and verified the following fixes during the live validation pass:
- Fixed Helm template rendering in `deploy/helm/tool-compiler/templates/apps.yaml` and `deploy/helm/tool-compiler/templates/migration-job.yaml` by removing incorrectly escaped `include "tool-compiler.fullname"` expressions that caused `helm upgrade --install` parse failures
- Added `psycopg[binary]` to the runtime dependency set in `pyproject.toml` and made `migrations/env.py` translate the application URL from `+asyncpg` to `+psycopg` so Alembic can run inside the container image
- Updated `deploy/docker/Dockerfile.app` to install `.[extractors,observability]` by default so the shipped images include `prometheus-client`, OpenTelemetry, and extractor-side runtime dependencies that are imported by the worker and MCP runtime
- Extended `tests/contract/test_observability_and_helm_assets.py` and `tests/contract/test_local_dev_assets.py` so future regressions in Helm template quoting, migration driver support, or Dockerfile packaging are caught locally
- Created a dedicated Artifact Registry repository at `us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler` and pushed validated `r3` application images for the four app services

Live cluster verification:
- Downloaded a temporary Helm `v3.18.6` binary to `/tmp/linux-amd64/helm` on the VM and used it for the real install/upgrade check
- Successfully deployed the chart to namespace `tool-compiler-gke-test-r3` on cluster `esoc-agents-dev` in `us-central1-a`
- Confirmed Helm release state `STATUS: deployed`
- Confirmed all seven pods reached `Running` and `Ready`
- Ran in-cluster HTTP smoke checks from the `compiler-api` pod and verified `200` responses from:
  - `http://tool-compiler-compiler-api:8000/healthz`
  - `http://tool-compiler-access-control:8001/healthz`
  - `http://tool-compiler-compiler-worker:8002/readyz`
  - `http://tool-compiler-mcp-runtime:8003/tools`
- Cleaned up the earlier failed namespaces `tool-compiler-gke-test` and `tool-compiler-gke-test-r2`; the healthy `tool-compiler-gke-test-r3` namespace was left running as the validated deployment target

---

### Post-backlog hardening: production queue / dispatcher binding ✅

Bound the compiler API submission path to a real Celery queue consumer instead of stopping at the old in-memory or callback-only shell.

Implemented and verified the following changes:
- Added JSON-safe task serialization helpers to `apps/compiler_worker/models.py` so `CompilationRequest` can move through Celery without losing `UUID` or option payloads
- Added `apps/compiler_worker/celery_app.py` with a real `compiler_worker.execute_compilation` Celery task and queue defaults, plus event-loop-safe task execution helpers for sync Celery workers
- Added `apps/compiler_worker/executor.py` and split execution into explicit adapters; the default database-backed executor now creates and disposes its own async engine per task execution to avoid `asyncpg` cross-event-loop failures
- Added `apps/compiler_worker/entrypoint.py` so the deployed worker container runs both the HTTP health shell and the Celery consumer in one supervised process tree
- Extended `apps/compiler_api/dispatcher.py` with `CeleryCompilationDispatcher` and environment-driven dispatcher selection so `WORKFLOW_ENGINE=celery` makes API submission enqueue onto the real worker path by default
- Made `apps/compiler_worker/repository.py` create-job behavior idempotent when the API has already persisted the job row, removing the earlier end-to-end delete-and-recreate workaround
- Updated `deploy/docker-compose.yaml` and `deploy/helm/tool-compiler/templates/apps.yaml` so `compiler-api` advertises `WORKFLOW_ENGINE=celery` and `compiler-worker` starts through the new queue-capable entrypoint
- Updated queue-path coverage in `tests/integration/test_compiler_api.py`, `tests/integration/test_compiler_worker_app.py`, `tests/e2e/test_full_compilation_flow.py`, `tests/contract/test_local_dev_assets.py`, and `tests/contract/test_observability_and_helm_assets.py`

Live cluster verification for the queue path:
- Built and pushed `r4` images tagged `20260325-b0e27e6-r4` for `compiler-api`, `access-control`, `compiler-worker`, and `mcp-runtime` into `us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler`
- Installed a fresh Helm release into namespace `tool-compiler-gke-test-r4` on cluster `esoc-agents-dev` in `us-central1-a`; Helm reported `STATUS: deployed`
- Confirmed all platform pods reached `Running` / `Ready` in the new namespace
- Submitted an in-cluster `POST /api/v1/compilations` request from the deployed `compiler-api` pod and observed the job transition from `pending` to `failed` at stage `detect`
- Confirmed the event stream persisted `job.created` → `job.started` → `stage.started` / `stage.retrying` → `stage.failed` → `job.failed`, proving the real API → Redis → Celery worker → PostgreSQL event path is functioning in-cluster
- Confirmed worker logs show the task being consumed and failing with `No activity handler registered for stage detect.`; this is the expected current behavior because the queue binding is now real, but the deployed worker still does not wire real production activity handlers

---

### Post-backlog hardening: deployed production activities, runtime validation stability, Helm cold-start hardening, and clean live GKE compilation ✅

Extended the deployed worker path beyond `detect` and fixed the concrete issues exposed by repeated GKE runs (`r6` through `r10`).

Completed code and test changes:
- Swapped eager package exports in `apps/compiler_api/__init__.py` and `apps/compiler_worker/__init__.py` for lazy `__getattr__` loading so the deployed worker no longer forms a circular import through `apps.compiler_api.repository`
- Added `tests/integration/test_package_imports.py` to lock the worker/API import-graph regression down in a fresh interpreter
- Changed generic runtime manifest defaults in `libs/generator/generic_mode.py` from port `8000` to `8003`, and updated `libs/generator/tests/test_generic_mode.py` plus `tests/integration/test_compiler_worker_activities.py`
- Updated `apps/mcp_runtime/main.py` so the mounted streamable HTTP app runs under `runtime_state.mcp_server.session_manager.run()`, fixing the real HTTP MCP transport lifecycle bug discovered after rollout succeeded but post-deploy invocation failed
- Added `tests/integration/test_streamable_http_tool_invoker.py` to exercise the runtime over a real local HTTP server instead of the in-memory shortcut path
- Added runtime startup waiting to `apps/compiler_worker/activities/production.py` via `ProductionActivitySettings.runtime_startup_timeout_seconds` / `runtime_startup_poll_seconds` and `_wait_for_runtime_http_ready(...)`, so `validate_runtime` now tolerates short-lived DNS / Service endpoint propagation lag instead of performing three zero-delay connection attempts
- Added `test_default_activity_registry_waits_for_runtime_readiness_before_validation` to `tests/integration/test_compiler_worker_activities.py` to prove the new wait loop survives transient connection failures that would previously have exhausted workflow retries
- Hardened `deploy/helm/tool-compiler/templates/apps.yaml` by adding `startupProbe` blocks for `compiler-api`, `access-control`, `compiler-worker`, and `mcp-runtime`
- Extended `tests/contract/test_observability_and_helm_assets.py` so the Helm assets now fail contract validation if the `startupProbe` scaffolding disappears
- Hardened `deploy/helm/tool-compiler/templates/infra.yaml` with `PGDATA=/var/lib/postgresql/data/pgdata` plus `startupProbe` coverage for `postgres`, `redis`, and `temporal`, preventing repeated partial-init failures on cold nodes
- Extended `apps/compiler_worker/entrypoint.py` to honor `CELERY_WORKER_POOL`, and updated both `deploy/docker-compose.yaml` and `deploy/helm/tool-compiler/templates/apps.yaml` to deploy the worker in `solo` mode with `CELERY_WORKER_CONCURRENCY=1`
- Extended `tests/integration/test_compiler_worker_entrypoint.py`, `tests/contract/test_local_dev_assets.py`, and `tests/contract/test_observability_and_helm_assets.py` so the worker-process and Helm/compose runtime env expectations stay locked down
- Updated `apps/mcp_runtime/loader.py` to build generated runtimes with `TransportSecuritySettings(enable_dns_rebinding_protection=False)`, which allows live MCP calls addressed through Kubernetes service hosts like `*.svc.cluster.local`
- Added a second regression in `tests/integration/test_streamable_http_tool_invoker.py` that proves post-deploy validation and tool invocation succeed when the runtime is called through a cluster-style service hostname

Live GKE outcomes:
- Namespace `tool-compiler-gke-test-r6`: fixed the deployed worker crash caused by package import cycles; the first real live compile then exposed a generated runtime port mismatch (`8000` vs real `8003`)
- Namespace `tool-compiler-gke-test-r7`: fixed the generated runtime port mismatch; the next live compile reached `validate_runtime` and exposed the missing streamable HTTP session-manager lifecycle in `apps.mcp_runtime.main`
- Namespace `tool-compiler-gke-test-r8`: fixed the MCP HTTP lifecycle and pushed `compiler-worker:r8` plus `mcp-runtime:r8`; the real live compile reached `validate_runtime` again and failed with `All connection attempts failed`, showing the stage was racing the just-created runtime Service / endpoint availability
- Namespace `tool-compiler-gke-test-r9`: pushed `compiler-worker:r9` with the runtime readiness wait; this removed the worker-side validation race, but repeated GKE cold starts showed the Helm chart itself needed `startupProbe` protection because liveness was killing pods before they had fully started
- Namespace `tool-compiler-gke-test-r10`: deployed the updated Helm chart and confirmed `helm upgrade --install ...` reached `STATUS: deployed`; the initial application set came up successfully, then a test-cluster node (`gke-esoc-agents-dev-default-pool-8147dedb-mz95`) went `NotReady`, controllers started rescheduling the namespace, and the remaining verification became blocked by cluster churn rather than by a still-open application defect
- Resized the `esoc-agents-dev` GKE node pool to three nodes and brought up a fresh validation namespace `tool-compiler-gke-test-r11`; the steady-state layout kept infra (`postgres`, `redis`, `temporal`) on `gke-esoc-agents-dev-default-pool-8147dedb-sg6b` and the application layer on `gke-esoc-agents-dev-default-pool-8147dedb-b354`
- The first `r11` live compile (`job_id=2a14cb8a-afae-4d81-b16e-8d822a7a6890`) reached `validate_runtime` and exposed a new defect: generated runtimes returned `421 Invalid Host header` for `POST /mcp/mcp` when addressed as `petstore-live-r11-v1.tool-compiler-gke-test-r11.svc.cluster.local`, which traced directly to FastMCP transport security defaults rejecting non-localhost host headers
- Built and pushed `mcp-runtime:20260325-b0e27e6-r12`, upgraded the Helm release in `tool-compiler-gke-test-r11`, and reran the live submission as `job_id=6a44723f-73ab-4a46-9c8f-f05cd63fa7bf`
- The final `r11` job succeeded end-to-end: stages `detect` → `extract` → `enhance` → `validate_ir` → `generate` → `deploy` → `validate_runtime` → `route` → `register` all emitted `stage.succeeded`, the worker log showed successful `POST /mcp/mcp` traffic against `petstore-live-r11-r12-v1.tool-compiler-gke-test-r11.svc.cluster.local:8003`, and the compiled service was registered as `petstore-live-r11-r12` with `active_version=1`
- Verified the deployed runtime resources remain present in-cluster after success: `service/petstore-live-r11-r12-v1`, `deployment.apps/petstore-live-r11-r12-v1`, `configmap/petstore-live-r11-r12-v1-ir`, and `networkpolicy.networking.k8s.io/petstore-live-r11-r12-v1`

Artifacts pushed during this hardening pass:
- `us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-worker:20260325-b0e27e6-r9`
- Digest: `sha256:46558e76339d9be0ea557bd5d995463943547635eec59c2f61d647994f76cf07`
- `us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/mcp-runtime:20260325-b0e27e6-r12`
- Digest: `sha256:e3e01b9bb5340ff9aad1e319b4d0745b73fd83de3dadb8ef2c1db2612af216bf`

Local verification completed after the code and chart fixes:
- `ruff check /home/guoxy/esoc-agents/tool-compiler-v2`
- `mypy /home/guoxy/esoc-agents/tool-compiler-v2/libs /home/guoxy/esoc-agents/tool-compiler-v2/apps /home/guoxy/esoc-agents/tool-compiler-v2/tests/integration /home/guoxy/esoc-agents/tool-compiler-v2/tests/contract /home/guoxy/esoc-agents/tool-compiler-v2/tests/e2e`
- `pytest -q /home/guoxy/esoc-agents/tool-compiler-v2` → `197 passed, 6 warnings`
- `docker compose -f /home/guoxy/esoc-agents/tool-compiler-v2/deploy/docker-compose.yaml config`

---

### Post-backlog hardening: access-control-backed route publication foundation ✅

Implemented gateway route publication as a real local control-plane path rather than a deferred placeholder:
- Added `apps/access_control/gateway_binding/client.py` with a richer gateway-admin protocol, in-memory APISIX-style client, and HTTP-backed client for mock/live admin APIs
- Extended `apps/access_control/gateway_binding/service.py` and `apps/access_control/gateway_binding/routes.py` with service-route sync, delete, rollback, and reconciliation support for persisted `route_config`
- Wired `apps.compiler_worker.activities.production.AccessControlRoutePublisher` into the worker production activity path so compiled services can publish stable and version-pinned routes through Access Control
- Added `apps/gateway_admin_mock/main.py` as a lightweight HTTP gateway-admin surface for compose, Helm, integration coverage, and future live smoke runs
- Updated local and Helm deployment assets so `access-control`, `compiler-worker`, and the optional `gateway-admin-mock` service are wired together consistently

**Tests:** expanded passing coverage in:
- `tests/integration/test_access_control_gateway_binding.py`
- `tests/integration/test_compiler_worker_activities.py`
- `tests/integration/test_compiler_worker_app.py`
- `tests/contract/test_api_contracts.py`
- `tests/contract/test_local_dev_assets.py`
- `tests/contract/test_observability_and_helm_assets.py`

### Planning: post-SDD modular expansion plan and context-engineering guide ✅

Added explicit post-SDD planning and agent-context documents:
- `docs/post-sdd-modular-expansion-plan.md` now defines the modular H-001 through H-008 backlog, recommended execution order, delivery shape, and exit criteria
- `docs/context-engineering.md` now defines the required context template (`Goal`, `Non-goals`, `Inputs`, `Outputs`, `Invariants`, `Tests`), write-set discipline, unsupported-feature rules, and documentation / quality-gate expectations for future agents
- Updated `tool-compiler-v2-sdd.md` so the post-SDD backlog numbering matches the modular plan and records the current status snapshot (`H-001` complete, `H-002` complete, `H-008` in progress)

### H-001: gRPC proto extraction foundation ✅

Implemented the first protocol-expansion slice:
- Added `libs/extractors/grpc.py` with `GrpcProtoExtractor`
- Detects `.proto` sources and extracts unary RPCs into `ServiceIR(protocol="grpc")`
- Preserves package/service metadata, maps request message fields into IR params, derives basic response schemas, and records streaming RPCs in `metadata["ignored_streaming_rpcs"]` instead of silently claiming support
- Wired the extractor into `libs/extractors/__init__.py` and `apps/compiler_worker/activities/production.py`
- Added fixture `tests/fixtures/grpc_protos/inventory.proto`

**Tests:** `3/3` passing in `libs/extractors/tests/test_grpc.py`
- Proto detection
- Unary RPC extraction
- Explicit unsupported streaming metadata

### H-002: SOAP / WSDL extraction foundation ✅

Implemented the second protocol-expansion slice:
- Added `libs/extractors/soap.py` with `SOAPWSDLExtractor`
- Detects WSDL 1.1 definitions documents and extracts services, bindings, port types, SOAP actions, and inline/simple XSD field structure into `ServiceIR(protocol="soap")`
- Preserves SOAP metadata such as target namespace, service, port, binding, port type, and action mapping
- Wired the extractor into `libs/extractors/__init__.py` and `apps/compiler_worker/activities/production.py`
- Added fixture `tests/fixtures/wsdl/order_service.wsdl`
- Fixed `mypy` typing boundaries in the new extractor so repo-wide static checks stay green

**Tests:** `3/3` passing in `libs/extractors/tests/test_soap.py`
- WSDL detection
- Operation extraction
- Metadata and parameter mapping

### H-008: Live GKE gateway reconcile and rollout smoke ✅

Implemented a lighter hardening slice for live gateway reconciliation that does not depend on the full compiler/runtime stack:
- Added `scripts/smoke-gke-gateway-routes.sh` to stand up a temporary namespace with PostgreSQL, a one-shot Alembic migration job, Access Control, Gateway Admin Mock, and a smoke job that inserts a service version directly into `registry.service_versions`
- Added `make gke-gateway-smoke` and extended `docs/quickstart.md` so the minimal live control-plane path is documented and runnable without a full Helm release
- Extended `tests/contract/test_local_dev_assets.py` so the new GKE smoke harness, Makefile target, and quickstart instructions stay locked down
- Adjusted the harness away from kubelet health probes after repeated GKE probe / exec instability; the smoke jobs now wait on PostgreSQL and HTTP health directly, which keeps the validation focused on route publication semantics instead of cluster probe noise

Live GKE validation completed in namespace `tool-compiler-gateway-smoke-r2`:
- `gateway-smoke-migrate` completed successfully against in-cluster PostgreSQL
- `gateway-smoke-runner` inserted `service_id=gateway-smoke-gke`, called `POST /api/v1/gateway-binding/service-routes/sync`, deleted `gateway-smoke-gke-active` from the live Gateway Admin Mock, then called `POST /api/v1/gateway-binding/reconcile`
- The final live result was `status=ok` with restored route IDs `gateway-smoke-gke-active` and `gateway-smoke-gke-v1`, and the stable route target remained `gateway-smoke-runtime-v1`
- Extended the same harness with `SMOKE_MODE=rollout` plus a new integration test so the stable route target is exercised across forward rollout and rollback semantics
- Live rollout validation in `tool-compiler-gateway-smoke-r2` proved: initial stable target `v1`, forward rollout changed the stable route target to `gateway-smoke-runtime-v2`, and rollback restored the stable route target to `gateway-smoke-runtime-v1` while preserving pinned routes `gateway-smoke-gke-v1` and `gateway-smoke-gke-v2`
- A fresh namespace attempt in `tool-compiler-gateway-smoke-r3` hit a cluster-side migration hang on node `gke-esoc-agents-dev-default-pool-8147dedb-6j4l`; to keep H-008 focused on route semantics instead of transient kubelet/debuggability noise, the final rollout proof reused the already-prepared namespace with `SKIP_MIGRATION=1`

This completes `H-008` for the current repo scope: local and integration coverage exist for route publication / rollback / reconciliation, live GKE coverage exists for drift reconciliation, and live GKE coverage now also exists for rollout forward/rollback behavior through the APISIX-style control-plane path.

### H-006: Advanced auth schema, validation, and runtime adapters ✅

Implemented the first non-trivial auth-expansion slice without forking the pipeline or inventing a second runtime path:
- Extended `libs/ir/models.py` so `AuthConfig` can now represent nested OAuth2 client-credentials settings, mTLS certificate/key/CA references, and request-signing metadata while preserving backward compatibility with the earlier auth fields
- Added coherence validation to the IR layer so nested OAuth2 config is only accepted when `auth.type == oauth2`, and custom-header auth still requires an explicit header name
- Expanded `libs/validator/pre_deploy.py` so auth smoke validation now recognizes advanced auth modes, rejects incomplete configurations early, and checks OAuth2 client-credentials token endpoints for reachability before deployment
- Extended `apps/mcp_runtime/proxy.py` with runtime-side OAuth2 client-credentials token exchange and caching, HMAC request-signing header generation, and mTLS-capable HTTP client creation
- Kept the scope intentionally narrow for this slice: no new deployment-time secret-mount contract was added here; the runtime resolves secret references through the existing environment-backed mechanism and fails explicitly when referenced secrets are missing

**Tests:** expanded passing coverage in:
- `libs/ir/tests/test_models.py`
- `libs/validator/tests/test_pre_deploy.py`
- `tests/integration/test_mcp_runtime_proxy.py`

Covered scenarios include:
- Advanced auth IR round-trip and schema-coherence validation
- Pre-deploy success for reachable OAuth2 client-credentials endpoints
- Pre-deploy failure for incomplete advanced-auth configuration
- Runtime token acquisition with `client_secret_basic`
- Runtime request signing over a stable canonical payload
- Runtime mTLS client configuration using secret-backed file paths

### H-004: Multipart, binary-safe, and async job runtime foundations ✅

Implemented the first runtime/data-plane expansion slice without widening into streaming protocols:
- Extended `libs/ir/models.py` with explicit `request_body_mode` support (`json`, `multipart`, `raw`) and typed `AsyncJobConfig` polling metadata
- Extended `apps/mcp_runtime/proxy.py` so the runtime can now build multipart form/file uploads, send raw binary request bodies from base64-wrapped tool arguments, wrap non-text upstream responses as base64-safe payload objects, and poll async job endpoints until terminal success/failure states
- Kept the async polling contract intentionally narrow: the current slice supports `Location` header or response-body status URL discovery plus terminal-state polling, but does not claim streaming or callback/webhook execution support
- Updated `libs/validator/post_deploy.py` so invocation-smoke validation now chooses the first available tool that also has a provided sample invocation instead of failing on the first enabled operation unconditionally

**Tests:** expanded passing coverage in:
- `libs/ir/tests/test_models.py`
- `libs/validator/tests/test_post_deploy.py`
- `tests/integration/test_mcp_runtime_proxy.py`

Covered scenarios include:
- IR acceptance of multipart/raw request-body modes and async job polling config
- Validation failure for incomplete async job response-body config
- Multipart upload proxying with form fields plus file payloads
- Raw binary request-body proxying from base64 input
- Binary-safe response wrapping for `application/octet-stream`
- Async job polling from an initial submission response through terminal completion
- Post-deploy validation picking the first available sample-backed tool

### H-007: Messy-spec conformance corpus ✅

Built a corpus-driven regression layer so support claims are backed by explicit fixtures instead of ad hoc examples:
- Added `tests/fixtures/conformance/corpus.yaml` as the manifest of expected `pass` / `fail` / `unsupported` outcomes
- Added new messy fixtures for OpenAPI, GraphQL, and SQL under `tests/fixtures/conformance/`
- Added `libs/extractors/tests/test_conformance_corpus.py` to execute the corpus as a single regression suite
- Extended `libs/extractors/openapi.py` so auth-heavy multipart specs map request-body mode into the IR and unsupported `callbacks` / `webhooks` are recorded in metadata instead of silently disappearing
- Extended `libs/extractors/graphql.py` so unsupported subscription roots are recorded in metadata via `ignored_subscriptions`
- Added a live-reflection SQL edge case using a disposable PostgreSQL container and a deliberately mixed-type schema (`JSONB`, `NUMERIC`, `TIMESTAMPTZ`, view reflection)

Covered corpus expectations now include:
- malformed OpenAPI documents that must fail cleanly
- auth-heavy OpenAPI specs with multipart request bodies and explicit unsupported callback/webhook reporting
- GraphQL introspection payloads with unsupported subscriptions that must be reported, not implied as supported
- gRPC proto fixtures with unary support plus explicit streaming deferral
- SOAP / WSDL regression fixtures
- SQL reflection against non-trivial schema shapes with explicit expected parameter typing

### H-003: Streaming / event protocol descriptors ✅

Completed the descriptor-only streaming/event slice without widening into runtime execution support:
- Added typed IR models in `libs/ir/models.py`: `EventDescriptor`, `EventTransport`, `EventDirection`, and `EventSupportLevel`, plus `ServiceIR.event_descriptors`
- Added a new `ServiceIR` invariant so descriptor-to-operation references stay valid instead of drifting into loose metadata
- Extended `libs/extractors/openapi.py` so unsupported OpenAPI `callbacks` and top-level `webhooks` now emit explicit descriptors while preserving `ignored_callbacks` / `ignored_webhooks` metadata for compatibility
- Extended `libs/extractors/graphql.py` so unsupported subscription roots now emit explicit `graphql_subscription` descriptors instead of only loose metadata
- Extended `libs/extractors/grpc.py` so deferred streaming RPCs now emit explicit `grpc_stream` descriptors with directionality and client/server-streaming metadata
- Extended `libs/validator/pre_deploy.py` with an `event_support` validation stage that passes only when event descriptors remain explicit unsupported markers; any false `planned` / `supported` runtime claim now fails before deployment

This slice intentionally stops at normalized representation and validation:
- The IR can now describe event/stream contracts explicitly, including future transport enums for `websocket`, `sse`, and generic async-event descriptors
- The current runtime still does not claim to execute streaming/webhook/session semantics

### H-005: Approved streaming runtime support ✅

Completed the runtime-side streaming slice for the currently approved transports:
- Extended `apps/mcp_runtime/proxy.py` so operations linked to supported `event_descriptors` now open bounded upstream stream sessions instead of silently falling back to unary request/response behavior
- Added bounded `sse` session handling with explicit lifecycle metadata, `max_events`, and idle-timeout controls
- Added bounded `websocket` session handling with explicit lifecycle metadata, `max_messages`, outbound initial-message support, and idle-timeout controls
- Kept native `grpc_stream` and other non-approved transports explicit: the runtime now raises a clear `ToolError` instead of pretending those descriptors are executable
- Relaxed `libs/validator/pre_deploy.py` so only approved `supported` transports with an `operation_id` can pass pre-deploy validation; `planned` and non-approved transports are still rejected
- Extended post-deploy/runtime coverage so supported streaming tools pass smoke validation and unsupported transport combinations fail clearly

### Toolchain policy alignment for future agent work ✅

Updated the project guidance documents so future work follows a stricter, standardized quality loop:
- `agent.md` now records the preferred long-term toolchain (`uv`, `ruff`, `basedpyright`, `pytest`, `coverage`, `hypothesis`, `pre-commit`, `nox`, `semgrep`, `deptry`, `pip-audit`, `import-linter`)
- `agent.md` and `docs/context-engineering.md` now both define the required execution order: lint → typecheck → tests → security / dependency / architecture checks
- The same documents now also record the transition rule for the current repository state: until the repo is actually migrated to `uv` / `nox` / `basedpyright`, agents must run the `.venv`-backed `ruff`, `mypy`, and `pytest` equivalents in that order and must not claim target-stack results that were not executed

### R-001: Real DeepSeek endpoint validation ✅

Completed the first post-H follow-on roadmap item against the real provider rather than mocks:
- Extended `libs/enhancer/enhancer.py` with an explicit `deepseek` provider, provider-specific default model selection, and configurable OpenAI-compatible `api_base_url` handling
- Added `scripts/validate_deepseek_enhancer.py` as a reproducible harness that reads the VM-local key file from `/home/guoxy/esoc-agents/.deepseek_api_key`, uses it only as runtime input, and never persists the secret into repository state
- Added `make deepseek-validate` as the local operator entrypoint for this live-provider check
- Expanded `libs/enhancer/tests/test_enhancer.py` to cover DeepSeek default-base-url behavior, env-driven defaults, and explicit OpenAI-compatible base URL propagation
- Observed and corrected a local VM drift issue before the live run: the checked-in dependency graph already declared enhancer support, but the current `.venv` was missing the `openai` SDK and required a one-time local reinstall

Live validation results captured on `2026-03-25`:
- Success path against the official DeepSeek endpoint returned a real enhancement for a sample `ServiceIR`, with `309` input tokens, `125` output tokens, and `1` provider call
- Failure-path validation with an intentionally invalid key returned the expected provider `AuthenticationError` with a real `401` response
- No secret value was written into repository files, fixtures, logs, or generated artifacts

### R-002: Live gateway/data-plane hardening beyond the mock control plane ✅

Completed the second follow-on roadmap item by turning the previous route-publication proof into a real forwarded data-plane path:
- Extended `apps/gateway_admin_mock/main.py` so the gateway admin mock now also exposes `/gateway/{service_id}/...` forwarding routes backed by the published route documents, with explicit active-route selection, version-pinned selection via `x-tool-compiler-version`, and clear `404` / `502` failure surfaces
- Expanded `tests/integration/test_access_control_gateway_binding.py` so the route-publication integration flow now proves stable-route forwarding, pinned-route forwarding, missing-route failure visibility during drift, stable-route recovery after reconcile, forward rollout to `v2`, rollback to `v1`, and pinned `v2` continuity after rollback
- Extended `scripts/smoke-gke-gateway-routes.sh` so the live GKE harness deploys two lightweight versioned runtime services, validates actual forwarded gateway responses, and separates `MIGRATION_IMAGE` from the app images for more reliable live execution
- Updated `docs/quickstart.md` and the local-asset contract checks so the live smoke instructions now describe real data-plane verification rather than control-plane-only route listing
- Found and fixed two live-harness issues while hardening the path: the original migration wait probe could silently hang under `python - <<'PY'`, and the older `compiler-api:20260325-b0e27e6-r4` image remained a bad migration carrier in this environment, so the authoritative successful run used `MIGRATION_IMAGE=ACCESS_CONTROL_IMAGE=us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/access-control:20260325-b0e27e6-r5`

Live validation results captured on `2026-03-25`:
- Built and pushed `access-control:20260325-b0e27e6-r5` (`sha256:1978018c63d11481002186c5f6ab530f98ad9675c0c32c651fd994180d0922b6`)
- Live GKE rollout/data-plane smoke succeeded in namespace `tool-compiler-gateway-smoke-r7`
- Stable route moved to `gateway-smoke-runtime-v2` during rollout and back to `gateway-smoke-runtime-v1` during rollback
- Pinned `v1` and pinned `v2` traffic continued to reach their respective versioned runtimes through the gateway entrypoint
- Final live route set remained `gateway-smoke-gke-active`, `gateway-smoke-gke-v1`, and `gateway-smoke-gke-v2`

### R-001 follow-through: full platform DeepSeek compile/deploy/register proof ✅

Closed the gap between "real provider call works" and "the whole platform really uses it in production mode":
- Extended the Helm chart so `compiler-worker` can receive explicit extra env vars plus secret-backed env vars, and so the migration job image can be overridden independently when the access-control image is the safer migration carrier for live GKE runs
- Updated `deploy/docker/Dockerfile.app` so the shared application image installs `.[extractors,enhancer,observability]` by default; without that, the deployed worker could recognize the `deepseek` provider but still fail at runtime because the OpenAI-compatible SDK was missing
- Hardened `libs/enhancer/enhancer.py` so env and secret-file sourced values are whitespace-trimmed before provider selection and header construction, fixing the real GKE failure mode where the mounted DeepSeek key carried a trailing newline and produced an illegal `Authorization` header
- Expanded `tests/contract/test_local_dev_assets.py`, `tests/contract/test_observability_and_helm_assets.py`, and `libs/enhancer/tests/test_enhancer.py` to lock in the new Helm/env/image behavior and the secret-file whitespace regression fix
- Scaled the GKE cluster back to `3` nodes and deleted stale test namespaces before the authoritative rerun so the proof used a clean, schedulable live environment rather than inheriting earlier churn

Live platform proof captured on `2026-03-25`:
- Fresh release: Helm release `tool-compiler-deepseek` in namespace `tool-compiler-gke-test-r13`
- Final worker image: `us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/compiler-worker:20260325-deepseek-r16`
- Final successful job: `23de5a4b-48be-4d9b-b127-2d1c928a74f9`
- Final registered service: `deepseek-live-r13-r16`
- The worker log recorded a real `POST https://api.deepseek.com/chat/completions` → `HTTP/1.1 200 OK`
- PostgreSQL-backed compilation events recorded `enhance -> stage.succeeded` with `{"model":"deepseek-chat","operations_enhanced":2,"operations_skipped":0}`
- The generated IR ConfigMap `deepseek-live-r13-r16-v1-ir` contains LLM-authored operation and parameter descriptions with `source: "llm"` and non-zero confidence values
- The same job then completed `deploy`, `validate_runtime`, `route`, and `register`, and `GET /api/v1/services` exposed `deepseek-live-r13-r16` with `active_version=1`

Important live failure discoveries fixed along the way:
- The earlier deployed worker image still predated the `deepseek` enum branch and failed with `'deepseek' is not a valid LLMProvider`
- The next worker image recognized `deepseek` but lacked the OpenAI-compatible client dependency and fell back after `ModuleNotFoundError: No module named 'openai'`
- The final live-only failure was the newline-bearing secret-file key; trimming env/secret values in `EnhancerConfig.from_env()` fixed that path cleanly

### R-003: Native `grpc_stream` first concrete server-stream slice with live proof ✅

Extended the earlier foundation work into the first concrete native gRPC streaming slice without overstating support:
- Kept the earlier IR/extractor/validator groundwork in place: `GrpcStreamRuntimeConfig` / `GrpcStreamMode`, strict `EventDescriptor` validation, proto-extracted native stream config, and default pre-deploy rejection unless native grpc support is explicitly enabled
- Added `grpcio-reflection` to `pyproject.toml` so the runtime can resolve upstream gRPC descriptors dynamically through server reflection
- Added `apps/mcp_runtime/grpc_stream.py` with `ReflectionGrpcStreamExecutor`, a concrete native executor that resolves protobuf descriptors through reflection and executes server-stream RPCs as bounded MCP results
- Tightened the executor implementation so request serialization uses the actual protobuf message instance, bounded sessions cancel the underlying stream when `max_messages` is reached, and transport failures surface as explicit `ToolError` exceptions
- Extended `apps/mcp_runtime/main.py` so the runtime can auto-wire the concrete executor only when `ENABLE_NATIVE_GRPC_STREAM=true` and the loaded IR actually declares supported `grpc_stream` descriptors
- Extended `libs/validator/post_deploy.py` so invocation smoke now validates the returned transport and streaming payload shape for supported event descriptors, including native `grpc_stream`
- Preserved the default invariant: without explicit native opt-in, deployed runtime behavior remains a clear rejection rather than a silent downgrade into the HTTP-native `sse` / `websocket` path

Tests added or expanded in this slice:
- `tests/integration/test_mcp_runtime_grpc_stream.py` now covers the opt-in runtime auto-wiring path plus the concrete reflection-backed executor behavior against real protobuf descriptor data
- `libs/validator/tests/test_post_deploy.py` now covers successful native `grpc_stream` post-deploy validation and a mismatched-transport failure path
- `tests/integration/test_streamable_http_tool_invoker.py` now proves the production streamable HTTP invoker can drive post-deploy validation for a native `grpc_stream` runtime
- `tests/integration/test_mcp_runtime_proxy.py` continues to prove the generic runtime can route a `grpc_stream` tool call through a native executor boundary and return `transport="grpc_stream"` without touching the HTTP-native streaming path
- `libs/validator/tests/test_pre_deploy.py` remains the guardrail that keeps supported `grpc_stream` descriptors in explicit opt-in mode instead of false-by-default runtime claims

Live-proof follow-through completed on `2026-03-25`:
- Added `scripts/smoke-gke-grpc-stream.sh`, a minimal live GKE harness that generates a generic runtime manifest from a hand-authored `ServiceIR`, deploys a reflection-enabled gRPC upstream mock, waits for rollout convergence down to a single ready runtime/upstream pod, then runs `PostDeployValidator` plus a direct MCP tool invocation inside the cluster
- Tightened that harness after two real failures: it now defaults to `.venv/bin/python` when available so generator imports do not depend on the host Python, and it serializes runner output safely when MCP content includes non-JSON helper objects
- Fixed a real runtime bug in `apps/mcp_runtime/grpc_stream.py`: reflection-backed execution now primes the service descriptor into the protobuf `DescriptorPool` before `FindMethodByName`, matching the behavior required by the live reflection server and preventing the earlier `"Couldn't find method catalog.v1.InventoryService.WatchInventory"` failure
- Added a dedicated regression test in `tests/integration/test_mcp_runtime_grpc_stream.py` for the descriptor-priming path, plus contract coverage for the new live smoke asset in `tests/contract/test_local_dev_assets.py`
- Built and pushed runtime image `us-central1-docker.pkg.dev/insightcompass-465300/tool-compiler/mcp-runtime:20260325-b0e27e6-r19` (`sha256:cb3e1d73241fc9ea7b0df962788a45b7797abb6a5676621971a5188351505fd7`) for the final live pass
- The authoritative successful live run used namespace `tool-compiler-grpc-stream-smoke-r1` with `KEEP_NAMESPACE=1`; the runner job completed successfully and logged `status="ok"`, `transport="grpc_stream"`, one reflected protobuf event for `watchInventory`, and a passing `PostDeployValidator` report

Current boundary after this slice:
- The repository now has a concrete native `grpc_stream` executor for the server-stream mode
- Native runtime wiring is opt-in via `ENABLE_NATIVE_GRPC_STREAM`
- The repository now has post-deploy validation coverage for native `grpc_stream` through both direct runtime invocation and the production streamable HTTP tool-invoker path
- The repository now also has an authoritative live GKE proof for the native server-stream path
- Broader client-stream / bidirectional modes remain future work unless separately proven

### Planning: protocol-completion track before cross-protocol `LLM-enabled E2E` ✅

Defined the next ordered roadmap after `R-003` and wrote it into `agent.md`, `docs/post-sdd-modular-expansion-plan.md`, and `tool-compiler-v2-sdd.md`.

The new sequencing is:
- `P-001` GraphQL runtime/data-plane completion
- `P-002` REST discovered-API runtime hardening
- `P-003` native gRPC unary runtime completion
- `P-004` SOAP / WSDL runtime execution
- `P-005` SQL execution/runtime completion
- `P-006` cross-protocol validator and capability-matrix hardening
- `L-001` through `L-006` as the final per-protocol `LLM-enabled E2E` proof track, with the existing OpenAPI + DeepSeek proof recorded as the completed baseline

### P-001: GraphQL runtime/data-plane completion ✅

Completed on `2026-03-25`.

Implementation highlights:
- Added typed GraphQL execution metadata to the IR in `libs/ir/models.py` via `GraphQLOperationConfig` and `GraphQLOperationType`
- Upgraded `libs/extractors/graphql.py` so extracted operations now carry executable GraphQL documents, default-safe selection sets, and origin-normalized `base_url` semantics instead of encoding the endpoint twice
- Upgraded `apps/mcp_runtime/proxy.py` so GraphQL operations now:
  - serialize upstream requests as `{query, operationName, variables}`
  - unwrap successful GraphQL `data`
  - treat HTTP `200` responses containing GraphQL `errors` as explicit runtime failures instead of false success
- Added local proof coverage in:
  - `tests/integration/test_mcp_runtime_proxy.py`
  - `libs/validator/tests/test_post_deploy.py`
  - `libs/extractors/tests/test_graphql.py`
  - `libs/ir/tests/test_models.py`

Current boundary after this slice:
- GraphQL is no longer extraction-only; it now has a typed local runtime/data-plane path
- GraphQL subscriptions remain outside scope and are still represented as explicit unsupported descriptors
- Final live/provider-backed GraphQL proof is intentionally deferred to the later `L-002` track after the remaining protocol-completion work advances

### P-002: REST discovered-API runtime hardening ✅

Completed on `2026-03-25`.

Implementation highlights:
- Hardened `libs/extractors/rest.py` so the discovery entrypoint path is preserved in `ServiceIR.base_url`; discovered APIs rooted under `/catalog`, `/api`, or similar subtrees no longer collapse back to the origin
- Normalized classifier output back to runtime-relative paths so `base_url=/catalog` plus `operation.path=/products/{id}` yields the correct runtime URL instead of duplicating the base path
- Preserved discovered query literals as `Param.default`, which makes worker-built and validator-driven sample invocations much closer to the discovery evidence
- Marked discovered write endpoints with explicit `body_param_name="payload"` rather than relying on the generic proxy heuristics alone
- Added local proof coverage in:
  - `libs/extractors/tests/test_rest.py`
  - `tests/integration/test_mcp_runtime_proxy.py`
  - `tests/integration/test_compiler_worker_activities.py`

Current boundary after this slice:
- REST discovered-API support now has a stronger local extractor/runtime/sample-invocation contract
- The broad crawler/classifier model is intentionally unchanged
- Final live/provider-backed REST proof remains downstream in the `L-003` track

### P-003: Native gRPC unary runtime completion ✅

Completed on `2026-03-25`.

Implementation highlights:
- Added `GrpcUnaryRuntimeConfig` to `libs/ir/models.py`, giving unary RPCs a typed native execution contract instead of relying on HTTP-shaped metadata alone
- Upgraded `libs/extractors/grpc.py` so extracted unary RPCs now carry native unary runtime metadata
- Added `apps/mcp_runtime/grpc_unary.py` with a reflection-backed `ReflectionGrpcUnaryExecutor`
- Upgraded `apps/mcp_runtime/main.py` and `apps/mcp_runtime/proxy.py` so native unary execution is explicitly wired behind `ENABLE_NATIVE_GRPC_UNARY`
- Extended `libs/validator/pre_deploy.py`, `libs/validator/post_deploy.py`, `libs/generator/generic_mode.py`, and `apps/compiler_worker/activities/production.py` so validation, manifest generation, and worker-side pre-validation all understand native unary gRPC support boundaries
- Added local proof coverage in:
  - `tests/integration/test_mcp_runtime_grpc_unary.py`
  - `tests/integration/test_compiler_worker_activities.py`
  - `libs/generator/tests/test_generic_mode.py`
  - `libs/validator/tests/test_pre_deploy.py`
  - `libs/validator/tests/test_post_deploy.py`
  - `libs/ir/tests/test_models.py`

Current boundary after this slice:
- Unary gRPC is no longer extractor-only; it now has a dedicated native runtime path
- Native unary execution remains explicit opt-in through `ENABLE_NATIVE_GRPC_UNARY`
- Server-stream proof remains covered by `R-003`; client-stream and bidirectional modes still remain out of scope until separately proven

### P-004: SOAP / WSDL runtime execution ✅

Completed on `2026-03-25`.

Implementation highlights:
- Added `SoapOperationConfig` to `libs/ir/models.py` so WSDL-derived operations carry a typed SOAP execution contract
- Upgraded `libs/extractors/soap.py` so extracted operations now emit target namespace, request/response element names, SOAP action metadata, and explicit rejection of non-document/non-literal bindings
- Upgraded `apps/mcp_runtime/proxy.py` so SOAP operations now:
  - build and send SOAP 1.1 envelopes
  - apply `SOAPAction` plus XML content-type headers
  - parse SOAP body payloads
  - surface SOAP Faults as explicit runtime failures
- Added local proof coverage in:
  - `tests/integration/test_mcp_runtime_proxy.py`
  - `libs/validator/tests/test_pre_deploy.py`
  - `libs/validator/tests/test_post_deploy.py`
  - `libs/extractors/tests/test_soap.py`
  - `libs/ir/tests/test_models.py`

Current boundary after this slice:
- SOAP / WSDL is no longer extraction-only; it now has a real local runtime/data-plane path
- WS-Security and broader XML/WSDL edge-case handling remain future work until separately proven

### P-005: SQL execution/runtime completion ✅

Completed on `2026-03-25`.

Implementation highlights:
- Added `SqlOperationConfig`, `SqlRelationKind`, and `SqlOperationType` to `libs/ir/models.py` so reflected SQL operations now carry an explicit runtime contract
- Upgraded `libs/extractors/sql.py` so extracted query/insert operations emit typed SQL metadata plus safe default `limit` semantics
- Added `apps/mcp_runtime/sql.py` with a native `SQLRuntimeExecutor` that supports:
  - parameterized equality / `IN` query filters
  - bounded `limit` handling
  - parameterized inserts
  - SQLite and PostgreSQL async URLs
- Upgraded `apps/mcp_runtime/main.py` and `apps/mcp_runtime/proxy.py` so SQL runtime execution is auto-wired from the typed IR contract
- Added local proof coverage in:
  - `tests/integration/test_mcp_runtime_sql.py`
  - `libs/validator/tests/test_post_deploy.py`
  - `libs/extractors/tests/test_sql.py`
  - `libs/ir/tests/test_models.py`

Current boundary after this slice:
- SQL is no longer extractor-only; it now has a safe native runtime path for the explicitly supported query/insert contract
- Arbitrary ad hoc SQL execution remains intentionally out of scope

### P-006: Cross-protocol validator and capability-matrix hardening ✅

Completed on `2026-03-25`.

Implementation highlights:
- Added `libs/validator/capability_matrix.py` as the machine-readable support matrix across `extract / compile / runtime / live proof / llm-e2e`
- Upgraded `apps/compiler_worker/activities/production.py` so auto-built sample invocations are protocol-aware for GraphQL and SQL rather than purely generic
- Upgraded `libs/validator/post_deploy.py` so invocation smoke tests now choose safer operations first:
  - GraphQL queries before mutations
  - SQL queries before inserts
  - read/query paths before more stateful alternatives where samples exist
- Added proof coverage in:
  - `libs/validator/tests/test_capability_matrix.py`
  - `tests/integration/test_compiler_worker_activities.py`
  - `libs/validator/tests/test_post_deploy.py`

Current capability matrix snapshot:

| Protocol | Extract | Compile | Runtime | Live proof | LLM-enabled E2E |
|----------|---------|---------|---------|------------|-----------------|
| OpenAPI | ✅ | ✅ | ✅ | ✅ | ✅ |
| REST discovery | ✅ | ✅ | ✅ | ❌ | ✅ |
| GraphQL | ✅ | ✅ | ✅ | ❌ | ✅ |
| gRPC unary | ✅ | ✅ | ✅ | ❌ | ✅ |
| gRPC server-stream | ✅ | ✅ | ✅ | ✅ | ✅ |
| SOAP / WSDL | ✅ | ✅ | ✅ | ❌ | ✅ |
| SQL | ✅ | ✅ | ✅ | ❌ | ❌ |

---

## Test Summary

| Module | Tests | Status |
|--------|-------|--------|
| `libs/ir/tests/test_models.py` | 43 | ✅ All passing |
| `libs/ir/tests/test_diff.py` | 12 | ✅ All passing |
| `libs/extractors/tests/test_conformance_corpus.py` | 6 | ✅ All passing |
| `libs/extractors/tests/test_detection.py` | 13 | ✅ All passing |
| `libs/extractors/tests/test_graphql.py` | 3 | ✅ All passing |
| `libs/extractors/tests/test_grpc.py` | 4 | ✅ All passing |
| `libs/extractors/tests/test_openapi.py` | 23 | ✅ All passing |
| `libs/extractors/tests/test_rest.py` | 3 | ✅ All passing |
| `libs/extractors/tests/test_soap.py` | 3 | ✅ All passing |
| `libs/extractors/tests/test_sql.py` | 2 | ✅ All passing |
| `libs/enhancer/tests/test_enhancer.py` | 19 | ✅ All passing |
| `libs/generator/tests/test_generic_mode.py` | 5 | ✅ All passing |
| `libs/observability/tests/test_observability.py` | 13 | ✅ All passing |
| `libs/validator/tests/test_capability_matrix.py` | 2 | ✅ All passing |
| `libs/validator/tests/test_pre_deploy.py` | 13 | ✅ All passing |
| `libs/validator/tests/test_post_deploy.py` | 12 | ✅ All passing |
| `libs/tests/test_db_models.py` | 26 | ✅ All passing |
| `tests/contract/test_api_contracts.py` | 2 | ✅ All passing |
| `tests/contract/test_local_dev_assets.py` | 4 | ✅ All passing |
| `tests/contract/test_observability_and_helm_assets.py` | 2 | ✅ All passing |
| `tests/e2e/test_full_compilation_flow.py` | 5 | ✅ All passing |
| `tests/integration/test_access_control_authn.py` | 3 | ✅ All passing |
| `tests/integration/test_access_control_authz.py` | 3 | ✅ All passing |
| `tests/integration/test_access_control_gateway_binding.py` | 5 | ✅ All passing |
| `tests/integration/test_access_control_audit.py` | 2 | ✅ All passing |
| `tests/integration/test_artifact_registry.py` | 2 | ✅ All passing |
| `tests/integration/test_compile_workflow.py` | 12 | ✅ All passing |
| `tests/integration/test_compiler_api.py` | 5 | ✅ All passing |
| `tests/integration/test_compiler_worker_activities.py` | 12 | ✅ All passing |
| `tests/integration/test_compiler_worker_app.py` | 1 | ✅ All passing |
| `tests/integration/test_compiler_worker_entrypoint.py` | 1 | ✅ All passing |
| `tests/integration/test_package_imports.py` | 1 | ✅ All passing |
| `tests/integration/test_proof_runner_http_mock.py` | 3 | ✅ All passing |
| `tests/integration/test_proof_runner_live_llm_e2e.py` | 4 | ✅ All passing |
| `tests/integration/test_rollback_workflow.py` | 1 | ✅ All passing |
| `tests/integration/test_streamable_http_tool_invoker.py` | 3 | ✅ All passing |
| `tests/integration/test_version_coexistence.py` | 1 | ✅ All passing |
| `tests/integration/test_mcp_runtime.py` | 4 | ✅ All passing |
| `tests/integration/test_mcp_runtime_grpc_stream.py` | 3 | ✅ All passing |
| `tests/integration/test_mcp_runtime_grpc_unary.py` | 2 | ✅ All passing |
| `tests/integration/test_mcp_runtime_proxy.py` | 19 | ✅ All passing |
| `tests/integration/test_mcp_runtime_sql.py` | 2 | ✅ All passing |
| `tests/integration/test_mcp_runtime_observability.py` | 2 | ✅ All passing |
| **Total** | **307** | **✅ All passing** |

**Additional verification:**
- `pytest -q` → `307 passed, 6 warnings` across the current repository test suite
- `ruff check .` → passes for the full repository
- `mypy libs apps tests/integration tests/contract tests/e2e` → passes for shared libraries, application code, contract tests, integration tests, and e2e coverage
- `.venv/bin/python scripts/validate_deepseek_enhancer.py` → proved one real DeepSeek-backed enhancement success path plus one expected authentication-failure path against the official endpoint
- `docker compose -f deploy/docker-compose.yaml config` → validates the local development topology successfully
- Live Helm/GKE DeepSeek compile proof in namespace `tool-compiler-gke-test-r13` succeeded for service `deepseek-live-r13-r16`: job `23de5a4b-48be-4d9b-b127-2d1c928a74f9` finished `succeeded`, the worker logged a real DeepSeek `chat/completions` `200`, compilation events persisted `operations_enhanced=2`, and the generated IR artifact shows LLM-sourced descriptions
- Local access-control-backed route publication, rollback restoration, and reconciliation are validated through the gateway-admin mock integration flow
- The original minimal live GKE gateway smoke harness in namespace `tool-compiler-gateway-smoke-r2` remains the control-plane-only proof point for route sync, drift reconciliation, and rollout semantics against the live Gateway Admin Mock admin path
- The hardened live GKE rollout/data-plane smoke succeeded in namespace `tool-compiler-gateway-smoke-r7`, proving actual forwarded gateway traffic for stable and pinned routes, plus stable-route rollback safety, through the gateway entrypoint
- Earlier live GKE validation remains the latest production-path proof point: namespace `tool-compiler-gke-test-r11` reached end-to-end compile success for `petstore-live-r11-r12`, with the deployed worker showing successful `/tools` and `POST /mcp/mcp` traffic against the generated runtime DNS name
- Live native gRPC proof succeeded in namespace `tool-compiler-grpc-stream-smoke-r1` using `scripts/smoke-gke-grpc-stream.sh` and runtime image `20260325-b0e27e6-r19`: the cluster-side runner returned `status="ok"`, `transport="grpc_stream"`, exactly one streamed protobuf event, and a passing `PostDeployValidator` report
- `pytest` still emits the known non-failing `sse_starlette` pending-task shutdown log at process exit; no functional test failure accompanies it

---

## What's Next

The original SDD backlog is complete. Current follow-on work should proceed in this order:
- The defined post-SDD modular backlog is complete (`H-001` through `H-008`)
- The `R-001` through `R-003` roadmap is complete
- `P-001` through `P-006` are complete
- The final per-protocol `LLM-enabled E2E` proof track is complete (`L-001` through `L-006`), and the local E2E suite now also supports an opt-in real DeepSeek enhancer mode behind `ENABLE_REAL_DEEPSEEK_E2E`
- GraphQL, REST, SOAP, SQL, and gRPC unary have now joined OpenAPI and native `grpc_stream` as live-proven slices; the next hardening step is a final `PROTOCOL=all` rerun

---

## Important Reference Files

Repository root on this machine:

- `/home/guoxy/esoc-agents/tool-compiler-v2`

Key absolute paths on this machine:

- `/home/guoxy/esoc-agents/tool-compiler-v2-sdd.md` — authoritative SDD, architecture, task backlog, and acceptance criteria
- `/home/guoxy/esoc-agents/tool-compiler-v2/agent.md` — current coding-agent briefing and handoff context
- `/home/guoxy/esoc-agents/tool-compiler-v2/devlog.md` — chronological implementation log and latest verification status
- `/home/guoxy/esoc-agents/tool-compiler-v2/docs/post-sdd-modular-expansion-plan.md` — modular post-SDD backlog, ordering, and exit criteria
- `/home/guoxy/esoc-agents/tool-compiler-v2/docs/context-engineering.md` — context-management rules for future module work
- `/home/guoxy/esoc-agents/tool-compiler-v2/pyproject.toml` — dependency set plus `pytest` / `ruff` / `mypy` configuration

Key paths below are relative to the repository root (`tool-compiler-v2/`):

- `../tool-compiler-v2-sdd.md` — authoritative SDD, architecture, task backlog, and acceptance criteria
- `./agent.md` — current coding-agent briefing and handoff context
- `./devlog.md` — chronological implementation log and latest verification status
- `./docs/post-sdd-modular-expansion-plan.md` — modular post-SDD backlog, ordering, and exit criteria
- `./docs/context-engineering.md` — context-management rules for future module work
- `./pyproject.toml` — dependency set plus `pytest` / `ruff` / `mypy` configuration
- `./migrations/env.py` — Alembic environment and sync-driver translation for containerized migrations
- `./libs/ir/models.py` — core IR contract and invariants
- `./libs/ir/schema.py` — IR serialization and JSON Schema helpers
- `./libs/enhancer/enhancer.py` — LLM enhancement pipeline
- `./libs/generator/generic_mode.py` — generic-mode Kubernetes manifest generation
- `./apps/compiler_api/main.py` — compiler API app entrypoint
- `./apps/compiler_api/models.py` — Compiler API request/response models
- `./apps/compiler_api/dispatcher.py` — compilation dispatch abstraction used by API submission endpoints
- `./apps/compiler_api/repository.py` — artifact registry persistence logic
- `./apps/compiler_api/routes/artifacts.py` — current registry HTTP routes
- `./apps/compiler_api/routes/compilations.py` — compilation submission, status, and SSE event endpoints
- `./apps/compiler_api/routes/services.py` — compiled service list endpoint
- `./apps/access_control/main.py` — access control FastAPI app entrypoint
- `./apps/access_control/authn/service.py` — JWT validation and PAT lifecycle service
- `./apps/access_control/authz/service.py` — policy CRUD and semantic authorization evaluation
- `./apps/access_control/gateway_binding/client.py` — gateway-admin protocol plus in-memory and HTTP-backed clients
- `./apps/access_control/gateway_binding/service.py` — gateway consumer / policy / service-route sync and drift reconciliation
- `./apps/access_control/audit/service.py` — append-only audit logging and query service
- `./apps/gateway_admin_mock/main.py` — lightweight HTTP gateway-admin plus forwarding data-plane mock used by compose, Helm, reconciliation, and live route smoke tests
- `./apps/compiler_worker/main.py` — compiler worker health and metrics shell used by local dev and deployment assets
- `./apps/compiler_worker/celery_app.py` — Celery app and task binding for the queue-backed compiler worker
- `./apps/compiler_worker/executor.py` — task executor adapters and per-task database-backed workflow runtime
- `./apps/compiler_worker/entrypoint.py` — worker process supervisor for the HTTP shell plus Celery consumer
- `./apps/compiler_worker/observability.py` — compilation workflow metrics for dashboards and worker health endpoints
- `./apps/compiler_worker/workflows/compile_workflow.py` — durable compilation workflow core with retries, rollback, and event persistence hooks
- `./apps/compiler_worker/workflows/rollback_workflow.py` — rollback orchestration for redeploying and reactivating prior service versions
- `./apps/compiler_worker/repository.py` — SQLAlchemy-backed compilation job/event store
- `./apps/compiler_worker/activities/pipeline.py` — stage and rollback activity registry abstraction
- `./libs/validator/pre_deploy.py` — pre-deploy validation harness for IR schema and auth smoke checks
- `./libs/validator/post_deploy.py` — post-deploy validation harness for runtime health, tool listing, and invocation smoke checks
- `./libs/extractors/grpc.py` — gRPC proto detection and unary extraction foundation
- `./libs/extractors/soap.py` — SOAP / WSDL extraction foundation
- `./libs/extractors/graphql.py` — GraphQL introspection extractor
- `./libs/extractors/sql.py` — SQL schema extractor backed by SQLAlchemy reflection
- `./libs/extractors/rest.py` — REST extractor with discovery and classifier-assisted normalization
- `./apps/mcp_runtime/main.py` — generic runtime FastAPI app entrypoint
- `./apps/mcp_runtime/loader.py` — runtime IR loading and dynamic tool registration
- `./apps/mcp_runtime/proxy.py` — upstream HTTP proxy execution path
- `./apps/mcp_runtime/observability.py` — runtime metrics and logging integration
- `./libs/observability/tracing.py` — shared tracing setup used by runtime
- `./deploy/docker-compose.yaml` — local development topology for PostgreSQL, Redis, Temporal, and service shells
- `./deploy/docker/Dockerfile.app` — shared container build entrypoint used by CI and deployment assets
- `./deploy/helm/tool-compiler/values.yaml` — full-platform Helm configuration defaults
- `./deploy/helm/tool-compiler/templates/` — Helm templates for infra, services, and migration hooks
- `./observability/grafana/compilation-dashboard.json` — compilation pipeline Grafana dashboard
- `./observability/grafana/runtime-dashboard.json` — runtime Grafana dashboard
- `./scripts/setup-dev.sh` — local bootstrap script for Python deps and compose validation
- `./scripts/smoke-dev.sh` — local smoke checks for ports and health endpoints
- `./scripts/smoke-gateway-routes.sh` — local route-publication smoke harness using Access Control and Gateway Admin Mock
- `./scripts/smoke-gke-gateway-routes.sh` — minimal live GKE route-publication, forwarded data-plane, and rollout smoke harness
- `./scripts/smoke-gke-grpc-stream.sh` — minimal live GKE native grpc server-stream smoke harness
- `./scripts/validate_deepseek_enhancer.py` — reproducible real-provider DeepSeek validation harness using the VM-local key file
- `./.github/workflows/ci.yaml` — CI workflow for lint, typecheck, contract tests, integration tests, and image builds
- `./docs/quickstart.md` — local onboarding and end-to-end quickstart guide
- `./docs/adr/` — ADR set for core platform decisions
- `./migrations/versions/001_initial.py` — current PostgreSQL schema baseline
- `./tests/fixtures/ir/` — canonical `ServiceIR` fixtures used by runtime and generator tests
- `./tests/fixtures/grpc_protos/` — gRPC proto fixtures
- `./tests/fixtures/wsdl/` — SOAP / WSDL fixtures
- `./tests/contract/` — contract tests for OpenAPI schemas, local dev assets, dashboards, and Helm chart structure
- `./tests/integration/test_artifact_registry.py` — artifact registry integration coverage
- `./tests/integration/test_compile_workflow.py` — compilation workflow retry, rollback, and persistence coverage
- `./tests/integration/test_compiler_api.py` — compiler API submit/status/SSE/service-list coverage
- `./tests/integration/test_compiler_worker_app.py` — compiler worker health and metrics coverage
- `./libs/validator/tests/test_pre_deploy.py` — pre-deploy validation coverage
- `./libs/validator/tests/test_post_deploy.py` — post-deploy validation coverage
- `./libs/extractors/tests/test_graphql.py` — GraphQL extractor coverage
- `./libs/extractors/tests/test_grpc.py` — gRPC extractor coverage
- `./libs/extractors/tests/test_soap.py` — SOAP / WSDL extractor coverage
- `./libs/extractors/tests/test_sql.py` — SQL extractor coverage
- `./libs/extractors/tests/test_rest.py` — REST extractor discovery and classification coverage
- `./tests/fixtures/graphql_schemas/` — GraphQL introspection fixtures
- `./tests/fixtures/sql_schemas/` — SQL schema fixtures used by reflection tests
- `./tests/integration/test_access_control_authn.py` — access control authn integration coverage
- `./tests/integration/test_access_control_authz.py` — access control authz integration coverage
- `./tests/integration/test_access_control_gateway_binding.py` — gateway binding integration coverage
- `./tests/integration/test_access_control_audit.py` — audit logging integration coverage
- `./tests/integration/test_rollback_workflow.py` — rollback workflow integration coverage
- `./tests/integration/test_version_coexistence.py` — version coexistence and active-switch coverage
- `./tests/integration/test_mcp_runtime.py` — runtime startup and tool registration coverage
- `./tests/integration/test_mcp_runtime_proxy.py` — runtime upstream proxy coverage
- `./tests/integration/test_mcp_runtime_observability.py` — runtime metrics/tracing coverage
- `./tests/e2e/test_full_compilation_flow.py` — end-to-end OpenAPI spec to runtime tool invocation coverage

---

## Project Scale And Maintenance Expectations

Snapshot as of `2026-03-25`:
- Production code: about `15,417` lines
- Test code: about `11,462` lines
- Total code (`.py`): about `26,879` lines
- Original SDD backlog completion by task count: `33 / 33` (`100%`)
- Post-SDD expansion backlog completion by task count: `8 / 8`
- Protocol-completion backlog completion by task count: `6 / 6`

Current estimate for follow-on growth:
- The original SDD backlog is complete; future code growth now depends on post-SDD hardening, real infrastructure integration, and modular capability expansion
- Expect near-term code growth to come from post-backlog hardening, optional live-provider proof paths, and operational polish rather than unfinished protocol proof coverage

Progress interpretation:
- The original SDD backlog is complete, and the repository now includes the core control-plane, build-plane, and runtime-plane implementation plus local-dev, CI, packaging, documentation, e2e coverage, repeated real GKE/Helm deployment passes, one real GKE queue-path validation pass, access-control-backed route-publication foundations, and the full ordered protocol-completion track (`P-001` GraphQL runtime, `P-002` REST discovered-runtime hardening, `P-003` native gRPC unary runtime completion, `P-004` SOAP runtime execution, `P-005` SQL runtime completion, and `P-006` capability-matrix / validator hardening)
- Remaining work should now be treated as post-backlog hardening, broader live-provider proof coverage, or productionization rather than unfinished protocol proof or runtime scope

### L-002: GraphQL LLM-enabled E2E (2026-03-26)

Completed the GraphQL local E2E proof by adding a full compilation flow test to `tests/e2e/test_full_compilation_flow.py`:

- Added `test_graphql_introspection_compiles_to_running_runtime_and_tool_invocation` that exercises the full 9-stage compilation workflow (detect → extract → enhance → validate_ir → generate → deploy → validate_runtime → route → register) using the `catalog_introspection.json` fixture
- GraphQL introspection payload submitted with `protocol=graphql` hint and `base_url`/`graphql_path` hints for endpoint resolution
- In-memory GraphQL upstream mock handles `POST /graphql` with `operationName` dispatch (`searchProducts` and `adjustInventory`)
- Runtime deploys in-process, `searchProducts` tool invoked with `{term: "puzzle", limit: 1}`, result correctly unwrapped from `data.searchProducts` response
- Verified: compilation job reaches `succeeded`, service registered in catalog (`graphql-catalog-api` v1), SSE events emitted, `service_ir.protocol == "graphql"`, `GraphQLOperationConfig` attached to operations
- Quality gates: `ruff check` clean, `mypy` no issues, `pytest` 1/1 passed (10.45s)

No production code changes required — the GraphQL extraction, runtime proxy, worker activities, and proof runner infrastructure were already complete from `P-001` and the proof track harness build.

---

### L-003: REST LLM-enabled E2E (2026-03-26)

Completed the REST local E2E proof by extending `tests/e2e/test_full_compilation_flow.py` with a full discovered-API compilation flow:

- Added `test_rest_discovery_compiles_to_running_runtime_and_tool_invocation` that exercises the full 9-stage compilation workflow (detect → extract → enhance → validate_ir → generate → deploy → validate_runtime → route → register) against a discovery-first REST source URL
- Discovery uses an in-memory catalog mock rooted at `https://catalog.example.test/rest/catalog`, preserving the subtree-aware base URL and runtime path shape hardened in `P-002`
- The extraction path uses an `LLM`-style classifier double so the compiled `ServiceIR` preserves `source="llm"` operation metadata while keeping the proof local and deterministic
- Runtime deploys in-process, `get_items_item_id` is invoked with `{item_id: "sku-123", view: "detail"}`, and the request resolves to `https://catalog.example.test/rest/catalog/items/sku-123?view=detail`
- Verified: compilation job reaches `succeeded`, service registers as `rest-catalog-api` v1, SSE events are emitted, the active runtime keeps `protocol == "rest"` with the subtree `base_url`, and the deployed discovered operation remains LLM-sourced
- Quality gates: `ruff check .`, `mypy libs apps tests/integration tests/contract tests/e2e`, and `pytest -q` all passed; the repository is now at `305 passed, 6 warnings`

No production code changes were required beyond the proof-oriented test and capability-status alignment; the REST discovery extractor, runtime proxy, and worker pipeline were already complete from `P-002`.

---

### L-004: gRPC LLM-enabled E2E (2026-03-26)

Completed the gRPC local E2E proof by extending `tests/e2e/test_full_compilation_flow.py` with a dual-slice native gRPC compilation flow:

- Added `test_grpc_proto_compiles_to_running_runtime_and_tool_invocation` that exercises the full 9-stage compilation workflow (detect → extract → enhance → validate_ir → generate → deploy → validate_runtime → route → register) using the `inventory.proto` fixture
- Submission uses `protocol=grpc` plus `enable_native_grpc_stream=true`, so the extracted `ServiceIR` includes both unary RPC execution metadata and a supported native `grpc_stream` descriptor for `WatchInventory`
- The in-memory runtime deploy path now accepts test-only runtime overrides so the E2E slice can inject fake native gRPC unary and stream executors without widening into unrelated production changes
- Runtime validation still uses the real `PostDeployValidator`, which chooses the safer unary `ListItems` tool for smoke invocation, while the final proof explicitly invokes both `ListItems` and `WatchInventory`
- Verified: compilation job reaches `succeeded`, service registers as `grpc-inventory-api` v1, the active runtime keeps `protocol == "grpc"`, `ListItems` remains wired through `grpc_unary`, `WatchInventory` remains wired through a supported `grpc_stream` descriptor, unary invocation returns catalog data, and stream invocation returns `transport="grpc_stream"` with a reflected protobuf-style event payload
- Quality gates: `ruff check .`, `mypy libs apps tests/integration tests/contract tests/e2e`, and `pytest -q` all passed; the repository is now at `305 passed, 6 warnings`

No production code changes were required beyond the proof-oriented E2E test, the single-file test harness extension needed to inject native gRPC executors locally, and capability-status alignment; the extractor, runtime wiring, validator behavior, and native stream groundwork were already complete from `P-003` and `R-003`.

---

### L-005: SOAP / WSDL LLM-enabled E2E (2026-03-26)

Completed the SOAP / WSDL local E2E proof by extending `tests/e2e/test_full_compilation_flow.py` with a full WSDL-driven compilation flow:

- Added `test_soap_wsdl_compiles_to_running_runtime_and_tool_invocation` that exercises the full 9-stage compilation workflow (detect → extract → enhance → validate_ir → generate → deploy → validate_runtime → route → register) using the `order_service.wsdl` fixture
- Detection and extraction now run directly from inline WSDL content, preserving the service endpoint from the fixture and proving the compiler does not require a separate live WSDL fetch for the local proof slice
- The in-memory upstream mock returns real SOAP envelopes for both `GetOrderStatus` and `SubmitOrder`, while the proof path invokes `GetOrderStatus` through the deployed runtime
- Runtime validation still uses the real `PostDeployValidator`, which selects the safer SOAP read path for smoke invocation before the final proof performs an explicit `GetOrderStatus` tool call
- Verified: compilation job reaches `succeeded`, service registers as `soap-order-api` v1, the active runtime keeps `protocol == "soap"`, the extracted operations retain typed `SoapOperationConfig` metadata, and the final tool invocation returns the parsed SOAP payload `{status: "SHIPPED", estimatedShipDate: "2026-03-26T10:00:00Z"}`
- Quality gates: `ruff check .`, `mypy libs apps tests/integration tests/contract tests/e2e`, and `pytest -q` all passed; the repository is now at `306 passed, 6 warnings`

No production code changes were required beyond the proof-oriented E2E test and capability-status alignment; the WSDL extractor, SOAP runtime adapter, and validator behavior were already complete from `P-004`.

---

### L-006: SQL LLM-enabled E2E (2026-03-26)

Completed the SQL local E2E proof by extending `tests/e2e/test_full_compilation_flow.py` with a reflected-schema compilation flow:

- Added `test_sql_schema_compiles_to_running_runtime_and_tool_invocation` that exercises the full 9-stage compilation workflow (detect → extract → enhance → validate_ir → generate → deploy → validate_runtime → route → register) against a temporary SQLite catalog source URL
- The proof path builds a local schema with `customers`, `orders`, and the `order_summaries` view, then extracts native SQL query/insert operations through `SQLExtractor` with the explicit `schema=main` hint
- Runtime validation still uses the real `PostDeployValidator`, which prefers the safer SQL query smoke path over inserts, while the final proof explicitly invokes `query_order_summaries`
- The shared local E2E enhancer hook now defaults to stub token accounting but can opt into a real DeepSeek-backed enhancement pass with `ENABLE_REAL_DEEPSEEK_E2E`; the real mode requires `DEEPSEEK_API_KEY` or `LLM_API_KEY` and fails fast if no real enhancement result is recorded
- Added `scripts/e2e-real-deepseek-smoke.sh` plus `make e2e-real-deepseek-smoke` as the local operator entrypoint for a minimal real-provider matrix; by default it runs the GraphQL + SQL local E2E proofs using the VM-local DeepSeek key file, while still allowing `LLM_API_KEY_FILE`, `DEEPSEEK_MODEL`, `DEEPSEEK_API_BASE_URL`, and `PYTEST_K_EXPR` overrides
- Verified: compilation job reaches `succeeded`, service registers as `sql-catalog-api` v1, the active runtime keeps `protocol == "sql"`, reflected metadata preserves `database_schema == "main"` plus the expected tables/views, and the final tool invocation returns the reflected `order_summaries` row for `Acme`
- Verified the dedicated local real-provider smoke target itself: `make e2e-real-deepseek-smoke` completed `test_graphql_introspection_compiles_to_running_runtime_and_tool_invocation` plus `test_sql_schema_compiles_to_running_runtime_and_tool_invocation` with `2 passed, 4 deselected`
- Quality gates: `ruff check .`, `mypy libs apps tests/integration tests/contract tests/e2e`, and `pytest -q` all passed; the repository is now at `307 passed, 6 warnings`

No production code changes were required beyond the proof-oriented E2E test, the optional local real-provider enhancer toggle for the local proof harness, and capability-status alignment; the SQL extractor, native runtime executor, and validator prioritization were already complete from `P-005` and `P-006`.

---

### GKE LLM-Enabled E2E Protocol Selector (2026-03-26)

Hardened the live GKE proof harness for the next post-backlog phase by making the protocol scope explicit instead of all-or-nothing:

- Updated `scripts/smoke-gke-llm-e2e.sh` to accept `PROTOCOL`, defaulting to `all`, validate allowed values (`all`, `graphql`, `rest`, `grpc`, `soap`, `sql`), and pass the selected protocol through to `apps.proof_runner.live_llm_e2e`
- This keeps the existing full-matrix behavior intact while enabling stepwise live proof passes such as `PROTOCOL=graphql make gke-llm-e2e-smoke`
- Updated `docs/quickstart.md` with the recommended single-protocol GKE workflow for the next hardening phase
- Updated `agent.md` so future handoffs treat per-protocol live proof closure as the main remaining path, rather than more local E2E or protocol-surface expansion
- Added contract coverage in `tests/contract/test_local_dev_assets.py` so the script, selector values, and quickstart usage stay aligned

Verification for this change set was limited to local contract/documentation coverage; no fresh GKE live run was executed as part of this edit.

---

### GraphQL Live GKE DeepSeek Proof (2026-03-26)

Ran the first single-protocol post-backlog live proof pass on GKE, using the new `PROTOCOL` selector to isolate GraphQL before attempting the full matrix:

- Executed `PROTOCOL=graphql make gke-llm-e2e-smoke` against namespace `tool-compiler-llm-graphql-015547`
- Used Artifact Registry images `access-control:20260325-b0e27e6-r20`, `compiler-api:20260325-b0e27e6-r21`, `compiler-worker:20260325-b0e27e6-r22`, `mcp-runtime:20260325-b0e27e6-r20`, and `compiler-api:20260325-b0e27e6-r21` as the proof helper image
- The proof runner job completed successfully and returned one GraphQL proof record with `job_id=c055d4e9-fcaa-42b1-8bec-9337d94f254a`, `operations_enhanced=2`, `llm_field_count=9`, and a successful `searchProducts` tool invocation returning `sku-puzzle`
- Worker logs in the same namespace recorded a real `POST https://api.deepseek.com/chat/completions` `HTTP/1.1 200 OK`, followed by deployment, runtime validation, route publication, and final task success for the GraphQL service
- Updated the machine-readable capability matrix so GraphQL now reports `live_proof=True`

This narrows the remaining live-proof gaps to `REST`, `SOAP`, `SQL`, and `gRPC` unary before the final `PROTOCOL=all` rerun.

---

### REST Live GKE DeepSeek Proof (2026-03-26)

Ran the second single-protocol post-backlog live proof pass on GKE, this time isolating the discovered REST path:

- Executed `PROTOCOL=rest make gke-llm-e2e-smoke` against namespace `tool-compiler-llm-rest-020103`
- Used Artifact Registry images `access-control:20260325-b0e27e6-r20`, `compiler-api:20260325-b0e27e6-r21`, `compiler-worker:20260325-b0e27e6-r22`, `mcp-runtime:20260325-b0e27e6-r20`, and `compiler-api:20260325-b0e27e6-r21` as the proof helper image
- The proof runner job completed successfully and returned one REST proof record with `job_id=577ba6a6-295b-4e22-947e-0821b05879b3`, `operations_enhanced=6`, `llm_field_count=9`, and a successful `get_items_item_id` tool invocation returning the discovered item payload for `sku-123`
- Worker logs in the same namespace recorded `Type detection: selected rest (confidence=1.00)` followed by a real `POST https://api.deepseek.com/chat/completions` `HTTP/1.1 200 OK`, and the task then completed deployment, runtime validation, route publication, and final success
- Updated the machine-readable capability matrix so REST discovery now reports `live_proof=True`

This narrows the remaining live-proof gaps to `SOAP`, `SQL`, and `gRPC` unary before the final `PROTOCOL=all` rerun.

---

### SOAP Live GKE DeepSeek Proof (2026-03-26)

Ran the third single-protocol post-backlog live proof pass on GKE, covering the WSDL-driven SOAP path:

- Executed `PROTOCOL=soap make gke-llm-e2e-smoke` against namespace `tool-compiler-llm-soap-020620`
- Used Artifact Registry images `access-control:20260325-b0e27e6-r20`, `compiler-api:20260325-b0e27e6-r21`, `compiler-worker:20260325-b0e27e6-r22`, `mcp-runtime:20260325-b0e27e6-r20`, and `compiler-api:20260325-b0e27e6-r21` as the proof helper image
- The proof runner job completed successfully and returned one SOAP proof record with `job_id=0499aebc-218f-4907-97aa-034c76739b41`, `operations_enhanced=2`, `llm_field_count=7`, and a successful `GetOrderStatus` tool invocation returning the parsed SOAP payload
- Worker logs in the same namespace recorded `Type detection: selected soap (confidence=0.95)` followed by a real `POST https://api.deepseek.com/chat/completions` `HTTP/1.1 200 OK`, after which deployment, runtime validation, route publication, and final task completion all succeeded
- Updated the machine-readable capability matrix so SOAP / WSDL now reports `live_proof=True`

This narrows the remaining live-proof gaps to `SQL` and `gRPC` unary before the final `PROTOCOL=all` rerun.

---

### SQL Live GKE DeepSeek Proof (2026-03-26)

Ran the fourth single-protocol post-backlog live proof pass on GKE, covering the reflected SQL path:

- Executed `PROTOCOL=sql make gke-llm-e2e-smoke` against namespace `tool-compiler-llm-sql-021037`
- Used Artifact Registry images `access-control:20260325-b0e27e6-r20`, `compiler-api:20260325-b0e27e6-r21`, `compiler-worker:20260325-b0e27e6-r22`, `mcp-runtime:20260325-b0e27e6-r20`, and `compiler-api:20260325-b0e27e6-r21` as the proof helper image
- The proof runner job completed successfully and returned one SQL proof record with `job_id=ae9f5237-8379-4d10-8ab5-7fc4b7349b2c`, `operations_enhanced=5`, `llm_field_count=25`, and a successful `query_order_summaries` tool invocation returning one reflected summary row
- Worker logs in the same namespace recorded `Type detection: selected sql (confidence=1.00)` followed by a real `POST https://api.deepseek.com/chat/completions` `HTTP/1.1 200 OK`, after which deployment, runtime validation, route publication, and final task completion all succeeded
- Updated the machine-readable capability matrix so SQL now reports `live_proof=True`

This narrowed the remaining live-proof gap to `gRPC` unary before the final `PROTOCOL=all` rerun.

---

### gRPC Unary Live GKE DeepSeek Proof (2026-03-26)

Closed the final single-protocol live-proof gap on GKE by rerunning the gRPC proof after fixing worker-generated gRPC smoke samples:

- Diagnosed the earlier live failure as a worker-side sample-invocation bug: `validate_runtime` was generating optional nested/object gRPC arguments such as `filter={"name":"sample"}`, which protobuf `ParseDict` rejected before unary execution even reached the upstream
- Narrowly fixed `apps/compiler_worker/activities/production.py` so gRPC smoke samples now omit unsafe optional object/array fields and keep only safer scalar query-style inputs
- Added regression coverage in `tests/integration/test_compiler_worker_activities.py` and `tests/integration/test_streamable_http_tool_invoker.py` so worker sample generation and the production streamable HTTP validator path both preserve parseable gRPC unary requests
- Built and pushed `compiler-worker:20260326-b0e27e6-r23` (`sha256:3740df213e21bbeba4d19eaba7783c30849bfc6149150ba0c4dc3270fa78c452`) using the existing `Dockerfile.app` build path; the other live-proof images remained `access-control:20260325-b0e27e6-r20`, `compiler-api:20260325-b0e27e6-r21`, and `mcp-runtime:20260325-b0e27e6-r20`
- The first rerun was blocked by cluster capacity noise (`NotReady` spot node plus `MemoryPressure` on the surviving node), so stale kept proof namespaces were deleted before the authoritative retry
- The authoritative retry executed `PROTOCOL=grpc make gke-llm-e2e-smoke` against namespace `tool-compiler-llm-grpc-024113`
- The proof runner job completed successfully and returned one gRPC proof record with `job_id=e629277b-31df-485a-b312-e65c88cd6d3b`, `operations_enhanced=3`, `llm_field_count=11`, a successful unary `ListItems` invocation returning catalog data, and a successful `WatchInventory` invocation returning `transport="grpc_stream"`
- Worker logs in the same namespace recorded `Type detection: selected grpc (confidence=0.95)` followed by a real `POST https://api.deepseek.com/chat/completions` `HTTP/1.1 200 OK`, and the compilation then completed `validate_runtime`, `route`, `register`, and final `job.succeeded`
- Updated the machine-readable capability matrix so `grpc_unary` now reports `live_proof=True`

This completes the single-protocol live-proof sequence; the remaining live hardening step is the final `PROTOCOL=all` rerun.

---

### Final `PROTOCOL=all` Live GKE Matrix And Worker Startup Hardening (2026-03-26)

Closed the final live-proof hardening loop by running the authoritative cross-protocol matrix on GKE, then narrowing and fixing the cold-start worker flake that the first minutes of that run exposed:

- Executed `PROTOCOL=all make gke-llm-e2e-smoke` against namespace `tool-compiler-llm-all-024755` using `access-control:20260325-b0e27e6-r20`, `compiler-api:20260325-b0e27e6-r21`, `compiler-worker:20260326-b0e27e6-r23`, `mcp-runtime:20260325-b0e27e6-r20`, and `compiler-api:20260325-b0e27e6-r21` as the proof-helper image
- The first worker instance in that namespace started before Redis was stably reachable, and although the API accepted the first GraphQL compilation, the queue stalled with `compiler.jobs` length `1`; restarting the worker immediately drained the queued message and allowed the matrix to continue, confirming a cold-start broker/consumer readiness flake rather than a protocol regression
- To harden that failure mode, `apps/compiler_worker/entrypoint.py` now waits for the Redis broker socket to become reachable and then waits for Celery to report `ready` before launching the worker HTTP shell; this makes deployment readiness much closer to real queue-consumption readiness during Helm/GKE rollout
- Added regression coverage in `tests/integration/test_compiler_worker_entrypoint.py` for Redis endpoint resolution, broker retry behavior, and early Celery-exit handling
- The completed proof runner then returned five successful records:
  - GraphQL `job_id=0b3b8bef-cec6-42ca-8704-f8916d8038c9`, `operations_enhanced=2`, `llm_field_count=9`, successful `searchProducts`
  - REST `job_id=512b9e93-7571-48ad-99ec-45a38ca3b4cc`, `operations_enhanced=6`, `llm_field_count=9`, successful `get_items_item_id`
  - gRPC `job_id=71040e7a-7c33-44eb-852c-0cff9bb4112b`, `operations_enhanced=3`, `llm_field_count=11`, successful `ListItems` plus `WatchInventory` with `transport="grpc_stream"`
  - SOAP `job_id=1653e378-d2c5-4cd5-84d3-b8438c13aca0`, `operations_enhanced=2`, `llm_field_count=7`, successful `GetOrderStatus`
  - SQL `job_id=364c37fe-a72e-4204-b970-cb49abb306d1`, `operations_enhanced=5`, `llm_field_count=25`, successful `query_order_summaries`
- Worker logs in the same namespace recorded real `POST https://api.deepseek.com/chat/completions` `HTTP/1.1 200 OK` calls for GraphQL, REST, gRPC, SOAP, and SQL during the same matrix run

Verification:

- `.venv/bin/ruff check apps/compiler_worker/entrypoint.py tests/integration/test_compiler_worker_entrypoint.py`
- `.venv/bin/mypy apps/compiler_worker/entrypoint.py tests/integration/test_compiler_worker_entrypoint.py`
- `.venv/bin/pytest -q tests/integration/test_compiler_worker_entrypoint.py tests/integration/test_compiler_worker_app.py`

This closes the final cross-protocol live-proof matrix: the supported compiler-managed protocols are now individually live-proven and jointly live-proven in a single authoritative `PROTOCOL=all` GKE run.

---

### Published Startup-Hardened Worker Image And Clean All-Matrix Rerun (2026-03-26)

Turned the broker-aware startup hardening into the new live baseline by publishing a fresh worker image and rerunning the full GKE matrix without any manual pod intervention:

- Built and pushed `compiler-worker:20260326-b0e27e6-r24` (`sha256:d4ce5d1acb07892e788a41fae31a047ccb6c5458b4af9620ee11313828b83104`) using the same `deploy/docker/Dockerfile.app` build path as the earlier live-proof worker image
- Started a fresh `PROTOCOL=all make gke-llm-e2e-smoke` run in namespace `tool-compiler-llm-all-031802`, keeping the other images pinned at `access-control:20260325-b0e27e6-r20`, `compiler-api:20260325-b0e27e6-r21`, and `mcp-runtime:20260325-b0e27e6-r20`
- The new worker now showed the intended startup ordering in-cluster: `Connected to redis` -> `celery ... ready.` -> only then `Uvicorn running on http://0.0.0.0:8002`, proving the HTTP readiness endpoint was no longer exposed ahead of queue-consumer readiness
- Unlike the earlier `tool-compiler-llm-all-024755` run, the first GraphQL job was consumed immediately with no manual worker restart and `compiler.jobs` queue depth stayed at `0` during startup validation
- The rerun completed successfully with terminal `exit_code: 0`, and the proof runner returned five successful records:
  - GraphQL `job_id=9f95397e-7445-4b4f-8700-404019654aaf`, `operations_enhanced=2`, `llm_field_count=9`
  - REST `job_id=4c70a554-90ae-408f-89ad-02f593e8e81b`, `operations_enhanced=6`, `llm_field_count=9`
  - gRPC `job_id=0500242b-653f-4eef-9a75-5e1a833e2c7b`, `operations_enhanced=3`, `llm_field_count=11`
  - SOAP `job_id=59f72d13-fdf4-4f81-a923-0545f11ef059`, `operations_enhanced=2`, `llm_field_count=7`
  - SQL `job_id=64f97e0f-ec79-496a-b8a5-f6a0a70fb8b1`, `operations_enhanced=5`, `llm_field_count=25`
- Worker logs in the same namespace again recorded real `POST https://api.deepseek.com/chat/completions` `HTTP/1.1 200 OK` calls for GraphQL, REST, gRPC, SOAP, and SQL during the rerun

This promotes `compiler-worker:20260326-b0e27e6-r24` to the new validated live baseline for the final cross-protocol proof harness.

---

### Current Conversion Coverage Assessment And Black-Box Direction (2026-03-26)

Captured the current confidence boundary more explicitly so the repository docs do not overstate what has been proven:

- Assessed the latest clean live namespace `tool-compiler-llm-all-031802` after the `compiler-worker:20260326-b0e27e6-r24` rerun
- The compiler-generated artifacts in that namespace expose `18` enabled operations across the five compiler-managed protocol slices (`graphql=2`, `rest=6`, `grpc=3`, `soap=2`, `sql=5`)
- The deployed runtimes' `/tools` endpoints matched those enabled operations exactly (`18 / 18`), so the structure-level `API/spec -> MCP tools` conversion coverage for the current proven slice is complete
- The live proof runner still executed only a representative subset of `6 / 18` tools (`graphql=1`, `rest=1`, `grpc=2`, `soap=1`, `sql=1`), so behavior-level runtime-call coverage is intentionally lower than structure coverage today
- This means the current repository can strongly claim spec-first / contract-first conversion success, but it does **not** yet justify a blanket claim that an arbitrary `100`-endpoint black-box service will be fully discovered, fully converted, and fully invocation-validated
- REST discovery remains the weakest semantics-recovery path; it is proven to compile and run in controlled fixtures/live proof, but it is still the most likely source of naming, deduplication, and endpoint-canonicalization drift on large undocumented services
- Updated `agent.md` to reflect the above coverage posture and updated `docs/post-sdd-modular-expansion-plan.md` to make black-box API exploration the next plan-first direction

Verification:

- `pytest -q tests/contract/test_local_dev_assets.py`

This leaves the repository in a good state for the next phase: black-box coverage instrumentation, discovery hardening, and large-surface pilot services.

---

### B-001 Generated-Tool Audit Slice 1 (2026-03-26)

Started the first concrete implementation step for black-box coverage instrumentation instead of leaving it as a documentation-only plan:

- Promoted the worker-side sample invocation builder into the public helper `build_sample_invocations(...)` so proof and validation paths can share the same protocol-aware sample generation logic
- Extended `apps/proof_runner/live_llm_e2e.py` with an opt-in `--audit-all-generated-tools` mode that emits a machine-readable `audit_summary`
- The new summary records `discovered_operations`, `generated_tools`, `audited_tools`, `passed`, `failed`, `skipped`, plus per-tool audit rows with arguments, results, and explicit failure/skip reasons
- The first audit policy is intentionally conservative: it reuses already executed representative proof results when available, audits safe tools, marks missing runtime tools as failed, and skips `writes_state`, `destructive`, and `external_side_effect` tools by policy instead of invoking them blindly
- Wired the same opt-in path through `scripts/smoke-gke-llm-e2e.sh` via `AUDIT_ALL_GENERATED_TOOLS=1` so the existing live GKE harness can exercise the audit without changing the current stable default behavior
- Added focused integration coverage in `tests/integration/test_proof_runner_live_llm_e2e.py` to prove the audit distinguishes `passed`, `failed`, and `skipped` outcomes and preserves representative-proof overrides
- Updated `docs/post-sdd-modular-expansion-plan.md` to move `B-001` from planned to in-progress with explicit remaining tasks, and updated `agent.md` to reflect the new state

Verification:

- `.venv/bin/ruff check apps/proof_runner/live_llm_e2e.py apps/compiler_worker/activities/__init__.py apps/compiler_worker/activities/production.py tests/integration/test_proof_runner_live_llm_e2e.py`
- `.venv/bin/mypy apps/proof_runner/live_llm_e2e.py apps/compiler_worker/activities/production.py tests/integration/test_proof_runner_live_llm_e2e.py`
- `.venv/bin/pytest -q tests/integration/test_proof_runner_live_llm_e2e.py tests/contract/test_local_dev_assets.py`

This lands the reporting/control-plane part of `B-001`, but not yet the first live audited baseline. The next concrete step is to run the new audit mode against GKE and capture real audited coverage numbers per protocol.

---

### First Live Generated-Tool Audit Baseline And REST Discovery Gap (2026-03-26)

Ran the first real GKE proof with `AUDIT_ALL_GENERATED_TOOLS=1` so `B-001` is now proven on live infrastructure rather than only local tests:

- Built and pushed `compiler-api:20260326-b0e27e6-r25` (`sha256:fa3166ffa6e3d6fcbdad1d94f069201938bc106b6651c3140a541f93cebb418b`) for the initial audit-capable proof-helper / compiler-api image
- The first live run in namespace `tool-compiler-llm-rest-audit-040632` reached the end-to-end proof successfully but then failed while printing results because the proof runner tried to `json.dumps(...)` raw MCP SDK `TextContent` objects inside error payloads
- Fixed `apps/proof_runner/live_llm_e2e.py` by normalizing invocation results through a recursive JSON-safe conversion path before storing and printing audit output, and added focused coverage for MCP SDK object serialization in `tests/integration/test_proof_runner_live_llm_e2e.py`
- Verified the fix locally with:
  - `.venv/bin/ruff check apps/proof_runner/live_llm_e2e.py tests/integration/test_proof_runner_live_llm_e2e.py`
  - `.venv/bin/mypy apps/proof_runner/live_llm_e2e.py tests/integration/test_proof_runner_live_llm_e2e.py`
  - `.venv/bin/pytest -q tests/integration/test_proof_runner_live_llm_e2e.py`
- Built and pushed the corrected image `compiler-api:20260326-b0e27e6-r26` (`sha256:a870a9475540433e2dd38d6efab7db3d4a90aca6b520158870718259482dccd3`)
- Re-ran `PROTOCOL=rest AUDIT_ALL_GENERATED_TOOLS=1 make gke-llm-e2e-smoke` in namespace `tool-compiler-llm-rest-audit-041525` using:
  - `access-control:20260325-b0e27e6-r20`
  - `compiler-api:20260326-b0e27e6-r26`
  - `compiler-worker:20260326-b0e27e6-r24`
  - `mcp-runtime:20260325-b0e27e6-r20`
  - `compiler-api:20260326-b0e27e6-r26` as the proof-helper image
- The rerun completed successfully with proof record `job_id=92370e5e-022e-4354-8bfc-8e2dcea3ca76`, `operations_enhanced=6`, `llm_field_count=9`, and an `audit_summary` block that recorded:
  - `discovered_operations=6`
  - `generated_tools=6`
  - `audited_tools=6`
  - `passed=1`
  - `failed=5`
  - `skipped=0`
- The only passing tool was the representative path `get_items_item_id`; the other five tools failed under live invocation with upstream `404` responses:
  - `get_rest_active` -> `/rest/active`
  - `get_rest_detail` -> `/rest/detail`
  - `get_rest_games` -> `/rest/games`
  - `get_rest_item_id` -> `/rest/{item_id}`
  - `get_rest_puzzle_box` -> `/rest/Puzzle Box`
- Pulled the compiled IR from configmap `rest-llm-e2e-041525-v1-ir` and confirmed those failing paths were exactly what REST discovery had emitted into the generated service contract, so this is not an audit harness false positive; it is real semantics/canonicalization drift in the discovery slice

Interpretation:

- `B-001` now has a live-proven reporting and coverage mechanism
- The first live audit result materially strengthens the earlier claim that REST discovery is the weakest black-box path today
- The next highest-value work item is `B-002` rather than broader audit rollout alone, because the audit is now doing its job by revealing concrete discovery errors

Handoff-ready next step:

- Another agent should pick up `B-002` starting in `libs/extractors/rest.py` and `libs/extractors/tests/test_rest.py`
- Keep `tool-compiler-llm-rest-audit-041525` as the primary reproduction namespace and `tool-compiler-llm-all-031802` as the clean cross-protocol baseline
- Use `compiler-api:20260326-b0e27e6-r26` plus `compiler-worker:20260326-b0e27e6-r24` for audit-enabled live reruns until a newer validated baseline is published
- The immediate goal is to eliminate or explicitly downgrade the five fake canonicalized REST tools without regressing the passing `get_items_item_id` path

---

### B-002: Black-box REST discovery hardening — first slice ✅

Completed on `2026-03-26`.

Root cause identified: `_extract_from_json` in `libs/extractors/rest.py` walked all string values in JSON response bodies and treated them as potential endpoint URLs via `urljoin`. When the REST discovery crawler followed a link to `/rest/catalog/items/{item_id}?view=detail` and received a JSON response containing fields like `"name": "Puzzle Box"`, `"status": "active"`, `"category": "games"`, and `"view": "detail"`, each of those bare words was resolved via `urljoin(base_url, ...)` into a spurious discovered endpoint such as `/rest/Puzzle Box`, `/rest/active`, `/rest/games`, `/rest/detail`, and `/rest/{item_id}`.

Implementation highlights:
- Added `_is_path_like()` filter in `libs/extractors/rest.py` so `_extract_from_json` only considers strings that start with `/`, `http://`, `https://`, or contain `://` — plain value words like `"active"` and `"Puzzle Box"` are no longer promoted to discovered endpoints
- Added `_coalesce_sibling_endpoints()` as defense in depth: when multiple leaf-level siblings share a parent path and any of them look like values (spaces, numeric IDs, UUIDs), the group is collapsed into a single template endpoint
- Added `_looks_like_value_segment()` heuristic to detect value-like leaf segments (spaces, numeric patterns, UUIDs)
- Added regression test `test_json_body_values_not_promoted_to_endpoints` reproducing the exact live audit failure pattern from namespace `tool-compiler-llm-rest-audit-041525`
- Added regression test `test_sibling_coalescing_merges_value_like_leaves` for the HTML sibling coalescing path

Quality gates:
- `ruff check .` → clean
- `mypy libs apps tests/integration tests/contract tests/e2e` → clean
- `pytest -q` → `316 passed` (1 pre-existing contract test failure in `test_smoke_scripts_and_quickstart_cover_gateway_route_smoke_flow` unrelated to this change)
- All 5 REST extractor tests pass, including the 2 new regression tests

Live GKE audit verification:
- Built and pushed `compiler-worker:20260326-b0e27e6-r27` (`sha256:012ac368d9790196d49a5d60e8d8ca7e0d53a9e802afea62245925751b85aea0`) and `compiler-api:20260326-b0e27e6-r27` (`sha256:d5149e456f558ea243f135667273e32b9be921c79ed5d35b4b6ba368df5c6076`)
- Ran `PROTOCOL=rest AUDIT_ALL_GENERATED_TOOLS=1` in namespace `tool-compiler-b002-rest-061245`
- Result: `discovered_operations=1`, `generated_tools=1`, `audited_tools=1`, `passed=1`, `failed=0`, `skipped=0`
- The five spurious REST endpoints are completely eliminated; only the legitimate `get_items_item_id` tool is generated and it passes with a real upstream `200` response
- Before the fix (namespace `tool-compiler-llm-rest-audit-041525`): `discovered=6`, `generated=6`, `audited=6`, `passed=1`, `failed=5`
- After the fix (namespace `tool-compiler-b002-rest-061245`): `discovered=1`, `generated=1`, `audited=1`, `passed=1`, `failed=0`
- Job `43124c32-35c0-409a-8865-049b9e789ff6` compiled service `rest-llm-e2e-061245` with `operations_enhanced=1`, `llm_field_count=3`, and the runtime tool invocation returned `{"item_id": "sku-123", "name": "Puzzle Box", "status": "active", "category": "games", "view": "detail"}`

---

### B-002 follow-up: restore relative JSON-link discovery and preserve coalesced query defaults ✅

Completed locally on `2026-03-26`.

Follow-up issue identified after the first `B-002` slice: the initial `_is_path_like()` defense was correct for bare JSON value words, but too strict for legitimate link-like relative JSON paths. As a result, a response such as `{"links": ["users/123/orders"]}` would no longer contribute a discovered endpoint at all. The same slice also dropped shared query defaults when value-like siblings such as `/shop/item/1?view=detail`, `/shop/item/2?view=detail`, and `/shop/item/3?view=detail` coalesced into `/shop/item/{id}`.

Implementation highlights:
- Updated `libs/extractors/rest.py` so `_extract_from_json()` now retains JSON parent-key context during traversal and `_is_path_like()` can allow safe relative paths again for link-like keys such as `links`, `href`, `url`, and `next`
- Kept the original anti-noise protection in place: plain value words like `"active"` and `"Puzzle Box"` still do not qualify as endpoints
- Updated `_coalesce_sibling_endpoints()` so a coalesced template path preserves a shared query suffix when all merged siblings carry the same query defaults
- Added regression tests `test_relative_json_links_are_still_discovered` and `test_sibling_coalescing_preserves_shared_query_defaults` in `libs/extractors/tests/test_rest.py`

Verification:
- `.venv/bin/ruff check libs/extractors/rest.py libs/extractors/tests/test_rest.py`
- `.venv/bin/mypy libs/extractors/rest.py libs/extractors/tests/test_rest.py`
- `.venv/bin/pytest -q libs/extractors/tests/test_rest.py` -> `7 passed`
- `.venv/bin/pytest -q tests/e2e/test_full_compilation_flow.py -k rest_discovery` -> `1 passed`

Current handoff implication:
- The follow-up fix is now live-proven on GKE: `tool-compiler-b002-rest-followup-065216` stayed at `discovered=1`, `generated=1`, `audited=1`, `passed=1`, `failed=0`, `skipped=0`
- The lower generated-tool total seen in the latest cross-protocol audit is expected: the earlier `18`-tool structural count included five fake REST endpoints that `B-002` intentionally removed, so the corrected audit baseline is `13` generated tools rather than `18`

---

### B-002 live revalidation and cross-protocol audit baseline ✅

Completed on `2026-03-26`.

Live GKE follow-up verification:
- Published `compiler-api:20260326-b0e27e6-r28` (`sha256:e5c5e84ed7e388143d297bcad8ddc54a0d5e9315752b29ab3b340dfe276a2df8`) and `compiler-worker:20260326-b0e27e6-r28` (`sha256:9589ecb89c9d5f94c9aa96154574679f201e76890edf83a0ae84f609bf733756`)
- Ran `PROTOCOL=rest AUDIT_ALL_GENERATED_TOOLS=1` in namespace `tool-compiler-b002-rest-followup-065216`
- Result: `discovered=1`, `generated=1`, `audited=1`, `passed=1`, `failed=0`, `skipped=0`
- Job `7c517e3c-a7b7-49ce-ab21-ee4e8fbfd810` compiled service `rest-llm-e2e-065216`; the follow-up fix preserved the clean catalog baseline while restoring legitimate relative JSON-link discovery and shared query-default preservation locally

Cross-protocol harness hardening:
- The first `PROTOCOL=all` rerun exposed an unrelated GKE harness issue rather than a REST regression: the `llm-proof-sql` Postgres pod could restart during initialization, leaving a partially initialized data directory without the `order_summaries` view
- Added a `startupProbe` to the `llm-proof-sql` Postgres deployment stanza in `scripts/smoke-gke-llm-e2e.sh` so liveness does not restart Postgres before `init.sql` completes
- Locked that harness change in with `tests/contract/test_local_dev_assets.py`, which now asserts the script contains `startupProbe:` and `failureThreshold: 60`

Authoritative cross-protocol audit baseline on GKE:
- Ran `PROTOCOL=all AUDIT_ALL_GENERATED_TOOLS=1` in namespace `tool-compiler-llm-all-audit-075849`
- GraphQL (`service_id=graphql-llm-e2e-075849`, `job_id=da4e426a-3fbc-4ec5-8374-c734be414dd6`): `discovered=2`, `generated=2`, `audited=1`, `passed=1`, `failed=0`, `skipped=1`
- REST (`service_id=rest-llm-e2e-075849`, `job_id=66594a00-1a12-4877-adad-76ed3ffcc030`): `discovered=1`, `generated=1`, `audited=1`, `passed=1`, `failed=0`, `skipped=0`
- gRPC (`service_id=grpc-llm-e2e-075849`, `job_id=c24431e7-8d8d-41ae-a5cc-479d05c44a68`): `discovered=3`, `generated=3`, `audited=1`, `passed=1`, `failed=0`, `skipped=2`
- SOAP (`service_id=soap-llm-e2e-075849`, `job_id=303bf84d-51ee-4bb5-89b0-e3f9729901cb`): `discovered=2`, `generated=2`, `audited=1`, `passed=1`, `failed=0`, `skipped=1`
- SQL (`service_id=sql-llm-e2e-075849`, `job_id=6ec64b4f-4eb3-412d-88ce-d3f7b62848c3`): `discovered=5`, `generated=5`, `audited=3`, `passed=3`, `failed=0`, `skipped=2`
- Aggregate: `discovered=13`, `generated=13`, `audited=7`, `passed=7`, `failed=0`, `skipped=6`
- The total generated-tool count is intentionally lower than the earlier structural `18`, because `B-002` removed the five fake REST endpoints (`/rest/active`, `/rest/detail`, `/rest/games`, `/rest/{item_id}`, `/rest/Puzzle Box`) that were previously inflating the count
- SQL `query_order_summaries` passed again in the clean rerun, confirming the `startupProbe` fix prevented the earlier init interruption

Current handoff implication:
- `B-002` is now live-proven on both the single-protocol REST rerun and the authoritative cross-protocol audit baseline
- The next highest-value work is `B-003` on a larger undocumented REST target plus broader persistence/reporting of `audit_summary`

---

- This repository should remain maintainable by strong coding agents if documentation stays current, tasks remain narrow, and repo-wide lint/type/test gates are preserved
- Human review remains required for migrations, auth, gateway behavior, Kubernetes rollout semantics, rollback logic, and other production-risking changes
- If the repository grows beyond roughly `30k+` production lines or operational behavior becomes more environment-specific, maintenance should shift toward tighter task decomposition and heavier human supervision

---

## Notes

- **No GitHub remote yet** — git is local only on the VM
- **VM SA has Vertex AI permissions** — Vertex AI path is wired in the enhancer factory
- **Provider config** — Anthropic/OpenAI use `LLM_API_KEY`; Vertex AI uses ADC plus optional `VERTEX_PROJECT_ID` / `VERTEX_LOCATION`
- **Celery + Redis** chosen as initial pipeline engine (not Temporal) per decision D1 in SDD
