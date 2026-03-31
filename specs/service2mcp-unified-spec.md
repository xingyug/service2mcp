# service2mcp Unified System Specification

_Consolidated from the original SDD (`tool-compiler-v2-sdd.md`), ADRs, post-SDD hardening plans, v3 expansion plans, current code, tests, and handoff docs._

## 1. Document Purpose

This document is the current, unified specification for the system historically called `tool-compiler-v2` and externally positioned as `service2mcp`.

It replaces the role previously split across:

- the original spec-driven design package in `../../tool-compiler-v2-sdd.md`
- ADRs under `../docs/adr/`
- `../docs/post-sdd-modular-expansion-plan.md`
- `../docs/v3-expansion/*.md`
- the rolling implementation status in `../agent.md` and `../devlog.md`

The goal of this document is not to restate the original design verbatim. It reconciles:

- what the original SDD intended
- what later hardening and expansion plans added
- what the repository actually implements today
- what remains planned but not yet complete

## 2. Source Precedence

When documents disagree, this specification follows this precedence order:

1. current code and tests in the main repository
2. explicit ADR decisions
3. current handoff documents (`agent.md`, `devlog.md`, `new-agent-reading-list.md`)
4. active roadmap documents (`post-sdd-modular-expansion-plan.md`, `docs/v3-expansion/*.md`)
5. older planning text in the original SDD

This matters because several formerly planned capabilities are now implemented, while some planned items are only partially wired into the production pipeline.

## 3. Naming and Terminology

### 3.1 Product name

- **External/project name:** `service2mcp`
- **Historical/internal name:** `tool-compiler-v2`

Both names refer to the same system. New public-facing documentation should prefer `service2mcp`, while code paths and older documents may still use `tool-compiler-v2`.

### 3.2 Core terms

- **Service source:** a service definition or live endpoint entry point submitted for compilation
- **Extractor:** a protocol-specific component that detects and converts a source into a `ServiceIR`
- **ServiceIR:** the normalized intermediate representation used by all downstream stages
- **Service identity (`service_id`):** the stable persisted identifier used by the registry, route publication, and service-summary APIs
- **Service display name (`service_name`):** the human-readable name carried in `ServiceIR`; in current request handling it also acts as the fallback identity seed when an explicit `service_id` is absent
- **Operation:** a single callable capability that becomes an MCP tool
- **Runtime:** the generic MCP server that loads IR and executes tool calls
- **Artifact version:** a persisted, versioned record of compiled output and metadata
- **Audit:** safe-subset runtime verification over generated tools
- **Black-box validation:** comparison of generated/discovered surface against a known ground-truth surface
- **Real-target coverage:** endpoint-surface comparison against live upstream systems used as acceptance targets
- **Access Control:** the named governance service under `apps/access_control`; lowercase `access-control` is used for publication modes, env vars, and URL/config forms
- **Proof fixtures:** checked-in target services and deployment assets used for validation and acceptance testing, not generated platform manifests

## 4. Executive Summary

`service2mcp` compiles services into governed MCP tool servers.

Given either a machine-readable service definition or a live endpoint, the system:

1. detects the source protocol
2. extracts the source into a normalized IR
3. enriches the IR with semantic metadata and optional LLM assistance
4. validates the IR
5. generates deployable runtime manifests
6. deploys a generic MCP runtime that reads the IR
7. validates the deployed runtime
8. publishes route metadata
9. stores a versioned artifact record for audit, rollback, diff, and review

The mainline repository currently supports these protocol families in code:

- OpenAPI
- REST discovery
- GraphQL
- gRPC extraction, with dedicated unary and server-stream runtime slices
- SOAP / WSDL
- SQL reflection
- OData v4
- SCIM 2.0
- JSON-RPC 2.0

The system also contains:

- semantic risk classification
- auth-aware runtime execution for multiple auth types
- pre-deploy and post-deploy validation
- generated-tool audit support
- black-box evaluation support
- drift detection support
- MCP resource and prompt registration support
- real-target proof infrastructure

The next major active planning target is **real-target full endpoint coverage parity** (`B-010`), not basic runtime viability.

## 5. Problem Statement

Organizations increasingly want AI agents to use existing services as tools, but turning a service into a safe, governed, deployable MCP server is labor-intensive and inconsistent. The hard problems are not limited to parsing schemas:

- protocol detection is heterogeneous
- auth and secret handling vary by service
- runtime behavior differs across HTTP, GraphQL, gRPC, SOAP, SQL, and enterprise APIs
- risk classification must be semantic, not syntactic
- deployment and routing need rollback-safe workflows
- validation must distinguish structural generation from safe live usability
- service surfaces drift over time

`service2mcp` exists to make service-to-tool onboarding repeatable, reviewable, and operationally safe.

## 6. Primary Users and Roles

### 6.1 Platform engineers

Own deployment, runtime policies, environment wiring, and quality gates.

### 6.2 API or service owners

Submit sources, review compiled surfaces, approve exposure, and inspect version diffs.

### 6.3 Agent developers

Consume the resulting MCP tools, prompts, and resources.

### 6.4 Security and governance teams

Define authentication/authorization policy, inspect audit logs, and enforce risk-aware exposure rules.

### 6.5 Operators and release engineers

Run live proof harnesses, black-box validation, and real-target coverage audits.

## 7. Goals

The system optimizes for:

- a single normalized contract across heterogeneous service protocols
- a generic runtime by default instead of per-service code generation
- durable artifact history and diffability
- semantic governance over tool surfaces
- safe deployment and rollback
- proof-driven confidence, including local, integration, and live proof paths
- extensibility through protocol-specific extractors and runtime adapters

## 8. Non-Goals

The system does **not** aim to:

- replace upstream services with a new API platform
- infer arbitrary browser-only or JavaScript-rendered service surfaces
- allow unsafe mutation tools by default during validation
- provide full support for every feature of every upstream protocol
- collapse all service semantics into a lossy lowest-common-denominator model
- publish a public open-source snapshot until content sanitization and release prep are complete

## 9. Design Principles

The current system follows these principles.

### 9.1 IR is the primary contract

Extractors produce `ServiceIR`. Runtimes, validators, manifest generation, registry storage, diffing, and review all consume `ServiceIR`.

### 9.2 Generic runtime is the default

The runtime reads IR from configuration and dynamically registers tools, resources, and prompts. Protocol-specific execution lives behind runtime adapters, not generated bespoke services.

### 9.3 Risk and governance are semantic

Authorization and validation decisions are based on `RiskMetadata`, `ToolIntent`, and explicit runtime capability, not just HTTP verbs.

### 9.4 Validation is multi-stage

Structural validation, post-deploy runtime validation, audit, black-box comparison, and real-target coverage are separate checks with different purposes.

### 9.5 Proof claims must be precise

The system distinguishes:

- generated tools
- audited tools
- live-invoked representative tools
- exact surface parity

Operationally green does not automatically mean structurally complete.

## 10. Capability Snapshot

### 10.1 Mainline implemented capabilities

| Capability | Extraction | Compile | Runtime | Live proof | LLM E2E | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| OpenAPI | Yes | Yes | Yes | Yes | Yes | Primary spec-first HTTP path |
| REST discovery | Yes | Yes | Yes | Yes | Yes | Discovery hardening and audit baselines exist |
| GraphQL | Yes | Yes | Yes | Yes | Yes | Typed execution path |
| gRPC generic | Yes | Yes | Partial | No | No | Generic row exists mainly for extraction classification |
| gRPC unary | Yes | Yes | Yes | Yes | Yes | Opt-in native runtime |
| gRPC server stream | Yes | Yes | Yes | Yes | Yes | Opt-in native runtime |
| SOAP / WSDL | Yes | Yes | Yes | Yes | Yes | Typed SOAP contract |
| SQL | Yes | Yes | Yes | Yes | Yes | Safe query/insert runtime implemented |
| OData v4 | Yes | Yes | Yes | Local proof | No | Runtime adapter implemented; no live proof row yet |
| SCIM 2.0 | Yes | Yes | Yes | Local proof | No | Runtime adapter implemented; live parity still nuanced |
| JSON-RPC 2.0 | Yes | Yes | Yes | Local proof | No | Runtime adapter implemented; live whole-method parity not yet complete |
| MCP resources | IR support | Yes | Yes | Local proof | N/A | Static resource registration implemented; default worker path does not auto-populate them |
| MCP prompts | IR support | Yes | Yes | Local proof | N/A | Prompt registration implemented; default worker path does not auto-populate them |
| Drift detection | N/A | N/A | N/A | N/A | N/A | Implemented, but current diff scope is shallow |

### 10.2 Planned but not mainline-delivered capabilities

| Capability | Status | Source |
| --- | --- | --- |
| CLI extraction and runtime | Planned future stream | `docs/v3-expansion/stream-a-cli-support.md` |
| AsyncAPI parsing and event bridge | Planned future stream | `docs/v3-expansion/stream-b-asyncapi-events.md` |
| REST auth/rate-limit/pagination hardening as a standalone track | Folded into `B-010` | `docs/post-sdd-modular-expansion-plan.md` |
| Open-source release prep | Planned | `docs/post-sdd-modular-expansion-plan.md` |

## 11. System Architecture

### 11.1 Three operational planes

#### Control plane

Responsible for submission, artifact history, workflow review, and policy services.

Primary apps:

- `apps/compiler_api`
- `apps/access_control`

Primary state:

- PostgreSQL-backed job, artifact, workflow, policy, PAT, and audit data

#### Build plane

Responsible for extraction, enhancement, validation, manifest generation, deploy orchestration, and rollback.

Primary app:

- `apps/compiler_worker`

Primary libraries:

- `libs/extractors`
- `libs/enhancer`
- `libs/validator`
- `libs/generator`
- `libs/ir`

#### Runtime plane

Responsible for serving the compiled MCP surface.

Primary app:

- `apps/mcp_runtime`

Primary responsibilities:

- load IR
- register tools
- register resources
- register prompts
- proxy or adapt tool calls to upstream systems
- expose health and metrics endpoints

### 11.2 Operator and proof surface

The repository also includes operator-oriented proof and smoke infrastructure:

- `apps/proof_runner/live_llm_e2e.py`
- `scripts/smoke-gke-llm-e2e.sh`
- `scripts/smoke-black-box-external.sh`
- protocol-specific smoke scripts
- `deploy/k8s/test-targets`
- `deploy/k8s/real-targets`

This is not the product control plane, but it is part of the system's acceptance model.

Current repository-packaged proof environments are intentionally split:

- `deploy/k8s/test-targets` provides one cluster-internal target service per supported protocol in namespace-oriented fixture form
- `deploy/k8s/real-targets` provides more production-like third-party services, richer auth/pagination/schema behavior, and optional seeding scripts

These environments are proof fixtures, not the platform's own control-plane deployment manifests.

## 12. Control Plane Specification

### 12.1 Compiler API

`apps/compiler_api` is the primary submission and registry-facing HTTP surface.

Route groups:

- `/api/v1/compilations`
- `/api/v1/artifacts`
- `/api/v1/services`
- `/api/v1/workflows`
- `/healthz`

#### Control-plane process wiring

The Compiler API process is intentionally lightweight and currently exposes only:

- `/healthz`

It does **not** currently expose a separate `/readyz`.

Database wiring is normally attached at startup, but current code also permits lazy session-factory creation from `DATABASE_URL` on first request.

Important current behavior:

- lazy DB initialization logs a warning rather than failing the process at startup
- this fallback exists for convenience/test wiring, but startup-time configuration remains the preferred production path

Compilation dispatch is pluggable and currently resolves to either a real worker backend or a fail-closed configuration sentinel:

- `WORKFLOW_ENGINE=celery` → `CeleryCompilationDispatcher`, which submits `compiler_worker.execute_compilation` via `task.apply_async(...)`
- any other value, including the unset default in the API process → `UnconfiguredCompilationDispatcher`, which raises on enqueue so the API deletes the pending job row and returns `503` instead of silently accepting dead-end work
- tests may still inject `InMemoryCompilationDispatcher` explicitly when they want an in-process capture sink

Artifact route publication is separately pluggable:

- if `ACCESS_CONTROL_URL` is unset, the Compiler API uses `UnconfiguredArtifactRoutePublisher`, which fails closed when route publication is attempted
- if `ACCESS_CONTROL_URL` is set, it delegates route sync/delete calls to Access Control gateway-binding endpoints
- the access-control-backed publisher authenticates with a short-lived internal service JWT

#### Submission model

Compilation submission accepts either an external source reference or inline source content.

Primary request fields are:

| Field | Type | Meaning |
| --- | --- | --- |
| `source_url` | string \| null | URL to probe, fetch, or treat as the upstream root |
| `source_content` | string \| null | Inline schema/proto/WSDL/OpenRPC/introspection payload |
| `source_hash` | string \| null | Optional caller-supplied source hash |
| `filename` | string \| null | Original filename hint for inline content |
| `created_by` | string \| null | Caller identity for audit and traceability |
| `service_id` | string \| null | Explicit stable service-identity override for registry/routing purposes |
| `service_name` | string \| null | Explicit IR/display-name override; also acts as the identity fallback when `service_id` is absent |
| `options` | object | Build/runtime/validation hints and overrides |

Validation rule:

- at least one of `source_url` or `source_content` must be present

Current service-identity rule is a small but important implemented nuance:

- if `service_name` is provided, the extract stage rewrites `ServiceIR.service_name` to that value
- the persisted service identity currently resolves in this order: explicit `service_id`, else explicit `service_name`, else extracted `ServiceIR.service_name`
- this means `service_name` currently influences both display naming and persisted identity when `service_id` is omitted

#### Request options contract

`CompilationRequest.options` is intentionally flexible, but the mainline production path already depends on a concrete subset of keys. These keys should be treated as the current operational contract.

| Option key | Type | Current meaning |
| --- | --- | --- |
| `hints` | object | Arbitrary extractor hints; stringified into `SourceConfig.hints` |
| `force_protocol` | string | Explicit extractor selection override |
| `protocol` | string | Secondary protocol hint if `force_protocol` is absent |
| `source_file_path` | string | File-backed extraction path when input is not inline |
| `auth_header` | string | Raw upstream auth header for extractors that need authenticated reads |
| `auth_token` | string | Simpler token-style discovery auth input |
| `auth` / `auth_config` | object | Auth override applied to extracted `ServiceIR.auth` |
| `tenant` | string | Scope override applied to job records and `ServiceIR` |
| `environment` | string | Scope override applied to job records and `ServiceIR` |
| `runtime_mode` | string | Deployment mode; `generic` by default, `codegen` as optional alternative |
| `skip_enhancement` | boolean | Disable LLM enhancement while still running deterministic post-enhancement |
| `preferred_smoke_tool_ids` | array[string] | Preferred post-deploy smoke tools |
| `sample_invocation_overrides` | object | Per-tool argument overrides for post-deploy validation |

Normalization rules currently implemented:

- `force_protocol` or `protocol` is copied into `SourceConfig.hints["protocol"]`
- `auth.compile_time_secret_ref` is normalized into `runtime_secret_ref` if needed
- flat OAuth2 fields can be normalized into nested `oauth2` config
- basic auth `username` / `password_secret_ref` can be normalized into `basic_username` / `basic_password_ref`
- API key auth can normalize `header_name` into `api_key_param`

#### Compilations contract

Key operations:

- create a compilation job
- list compilation jobs
- fetch a compilation job
- retry from a previous job, optionally from a stage hint
- request rollback for a previously successful job
- stream job events via SSE

Important characteristics:

- jobs are created before dispatch
- create/list/get/retry/rollback routes are currently unauthenticated
- enqueue failure deletes the just-created job
- create audit-logs `compilation.triggered`
- retry audit-logs `compilation.retried`
- rollback audit-logs `compilation.rollback_requested`
- SSE event streaming requires an authenticated caller
- terminal statuses are `succeeded`, `failed`, and `rolled_back`

#### Compilation job response model

The serialized job shape currently contains:

| Field | Meaning |
| --- | --- |
| `id` | job UUID |
| `source_url` | persisted external source reference, if any |
| `source_hash` | persisted source hash |
| `protocol` | resolved protocol once known |
| `status` | persisted job lifecycle state |
| `current_stage` | current workflow stage while running |
| `error_detail` | terminal or stage failure detail |
| `options` | persisted request options |
| `created_by` | caller identity |
| `service_id` / `service_name` | current job serializer mirrors the same persisted job field into both keys; callers should not treat them as fully independent values |
| `created_at` / `updated_at` | job timestamps |
| `tenant` / `environment` | normalized scope |

#### Retry and rollback submission semantics

The Compiler API exposes two job-cloning control actions:

- `POST /api/v1/compilations/{job_id}/retry`
- `POST /api/v1/compilations/{job_id}/rollback`

Current retry behavior:

- clones the persisted job record into a new job
- optionally accepts `from_stage` and stores it into the new job's `options`
- audit-logs `compilation.retried`
- returns `202 Accepted`
- deletes the new job again if queue dispatch fails

Current rollback behavior:

- is only allowed when the original job status is `succeeded`
- stores `rollback_from_job_id` in the new job's `options`
- audit-logs `compilation.rollback_requested`
- returns `409 Conflict` for non-succeeded source jobs
- deletes the new job again if queue dispatch fails

Important current limitation:

- retry/rollback clone the persisted job record, which stores `source_url` and `source_hash` but not the original inline `source_content`; current replay behavior is therefore strongest for URL-backed submissions

#### SSE event stream contract

`GET /api/v1/compilations/{job_id}/events` exposes workflow progress as server-sent events.

Current behavior:

- event name is the persisted workflow event type
- event data is a JSON serialization of the stored event record
- polling continues until terminal job status or client disconnect
- SSE is not anonymous; an authenticated caller is required
- browser `EventSource` compatibility is handled by accepting the bearer token as a `token` query parameter rather than an auth header

Current framing and delivery details:

- each event is emitted as `event: <event_type>\ndata: <compact-json>\n\n`
- polling interval is currently `100ms`
- the stream does not emit synthetic heartbeat events
- once a job reaches a terminal status, the stream drains remaining events and ends

Event payload fields are:

- `id`
- `job_id`
- `sequence_number`
- `stage`
- `event_type`
- `attempt`
- `detail`
- `error_detail`
- `created_at`

#### Artifact registry payload contract

Artifact-version creation and update currently revolve around these fields:

| Field | Meaning |
| --- | --- |
| `service_id` | stable registry identifier |
| `version_number` | monotonically increasing version |
| `ir_json` | validated `ServiceIR` payload used by the runtime |
| `raw_ir_json` | optional alternate IR snapshot |
| `compiler_version` | compiler build marker |
| `source_url` / `source_hash` | source traceability |
| `protocol` | normalized protocol |
| `validation_report` | post-deploy validation result payload |
| `deployment_revision` | runtime deployment revision |
| `route_config` | stable + version route metadata |
| `tenant` / `environment` | scoped registry dimensions |
| `is_active` | activation flag |
| `artifacts[]` | secondary records such as manifest and IR blobs |

Important current registry rules:

- `ir_json` and `raw_ir_json` are both validated as `ServiceIR` when present
- the first stored version for a service becomes active unless explicitly overridden
- activating a version deactivates sibling active versions in the same tenant/environment scope
- artifact creation, activation, update, and deletion are audit-logged
- activation and deletion may trigger route publication side effects
- artifact routes are currently unauthenticated

#### Artifact registry contract

Key operations:

- create artifact version
- list versions
- fetch version
- update version
- delete version
- activate version
- diff two versions

Important characteristics:

- artifact versions are tenant/environment aware
- activation and deletion are coupled with route synchronization
- route synchronization failures surface as gateway errors
- artifact actions are audit-logged
- activation/deletion hold the database transaction open until route sync and audit append both succeed
- when access-control-backed route publishing is disabled, these same routes succeed without external route mutation

#### Service catalog contract

Key operations:

- list compiled services
- fetch a single service summary

Current summary shape includes:

- `active_version`
- `version_count`
- `service_name`
- `service_description`
- `tool_count`
- `protocol`
- `tenant`
- `environment`
- `deployment_revision`
- `created_at`

Current service-catalog behavior:

- only active versions are surfaced
- service summary routes are currently unauthenticated

#### Review workflow contract

Workflow state model:

- `draft`
- `submitted`
- `in_review`
- `approved`
- `rejected`
- `published`
- `deployed`

Allowed transitions are explicit and enforced.

The workflow surface supports:

- fetch or initialize workflow state
- transition state
- save review notes
- fetch workflow history

Review notes are currently stored as a structured payload with:

- `operation_notes`
- `overall_note`
- `reviewed_operations`

Exact transition rules are currently:

| From | Allowed to |
| --- | --- |
| `draft` | `submitted` |
| `submitted` | `in_review` |
| `in_review` | `approved`, `rejected` |
| `approved` | `published` |
| `rejected` | `draft` |
| `published` | `deployed` |
| `deployed` | _none_ |

Current workflow-route semantics:

- all workflow routes require an authenticated caller
- the target service version must already exist
- `GET /api/v1/workflows/{service_id}/v/{version_number}` lazily creates a new workflow in `draft` state
- history entries are prepended newest-first
- each transition history entry contains `from`, `to`, `actor`, `comment`, and `timestamp`

Current control-plane security boundary is uneven:

- workflows and SSE are authenticated surfaces
- compilations, artifacts, and service-summary routes are currently public

This document records that as current implementation truth, not as an aspirational end state.

### 12.2 Access Control Service

`apps/access_control` provides the governance surface for compiled services.

Route groups:

- `/api/v1/authn`
- `/api/v1/authz`
- `/api/v1/gateway-binding`
- `/api/v1/audit`

#### Service lifecycle and readiness

The service exposes:

- `/healthz`
- `/readyz`

Current readiness semantics are database-backed:

- `/healthz` always returns `{"status":"ok"}`
- `/readyz` executes `SELECT 1`
- if the DB probe fails, `/readyz` returns `503 {"status":"not_ready"}`

Gateway-admin integration is also runtime-configurable:

- if `GATEWAY_ADMIN_URL` is unset, Access Control uses an in-memory gateway client
- if `GATEWAY_ADMIN_URL` is set, it uses an HTTP gateway-admin client
- `GATEWAY_ADMIN_TOKEN` is optionally forwarded for authenticated admin calls

#### AuthN

Supports:

- token validation
- personal access token creation
- PAT listing
- PAT revocation

Current route/auth model:

- `POST /api/v1/authn/validate` is anonymous and validates either JWT or PAT input
- PAT create/list/revoke require an authenticated caller
- PAT management is limited to the principal themself or an admin caller

Current admin-role normalization is case-insensitive and recognizes:

- `admin`
- `administrator`
- `superuser`

JWT validation is currently strict and intentionally narrow:

- only HS256 JWTs are accepted
- `exp` is enforced
- `nbf` is enforced when present
- `iss` and `aud` are enforced when configured
- username claims are resolved in this order: `preferred_username`, `username`, `cognito:username`, `login`

JWT secret loading is environment-sensitive:

- `ACCESS_CONTROL_JWT_SECRET` is required outside dev/test environments
- in dev/development/test, the service falls back to `dev-secret`

PAT behavior is currently:

- plaintext tokens are prefixed with `pat_`
- plaintext is returned only at creation time
- only the SHA-256 token hash is stored persistently
- PAT revocation is idempotent
- gateway sync uses the hashed credential form rather than the one-time plaintext token

The same authn module also mints short-lived internal service JWTs used by control-plane components such as the artifact route publisher.

#### AuthZ

Supports:

- policy creation
- policy listing
- policy retrieval
- policy update
- policy deletion
- explicit policy evaluation

Current route/auth model:

- policy create/update/delete are admin-only
- policy list/get/evaluate require any authenticated caller

Current policy-evaluation semantics are explicit:

- candidate policies must match `subject_type`
- `subject_id` may match exactly or via the wildcard `*`
- `resource_id` and `action_pattern` use `fnmatchcase` glob matching
- risk matching is threshold-based using ordered levels `safe < cautious < dangerous < unknown`
- if multiple policies match, specificity is scored first and decision priority breaks ties

Current specificity and priority rules are:

- exact subject match contributes the highest weight
- exact resource matches outrank glob resource matches
- exact action matches outrank broader action patterns
- decision priority is `deny > require_approval > allow`
- if nothing matches, the result is default deny

#### Gateway binding

Supports:

- reconciliation
- service-route sync
- service-route delete
- service-route rollback
- service-route listing

Current operational semantics:

- service-route sync computes stale route IDs that should be removed
- sync responses include prior route documents so later rollback can restore them
- rollback consumes previously captured route documents rather than reconstructing desired state from scratch
- reconcile compares expected PATs, policies, and routes from the database against current gateway-admin state and upserts/deletes diffs

Transactional coupling is intentional:

- PAT creation/revocation and policy mutations perform gateway sync in the same logical unit of work
- if gateway sync fails, the DB transaction is rolled back and the caller receives `502 Bad Gateway`

#### Audit

Supports:

- audit log listing
- single audit entry retrieval

Current audit characteristics:

- audit data is persisted in append-only `auth.audit_log`
- audit query routes require an authenticated caller
- PAT, policy, compilation, artifact, and route-related actions are recorded through this shared audit pattern

### 12.3 Persistence and shared state

The system uses one shared PostgreSQL-backed relational model with logically separated schemas.

Current schema inventory:

- `compiler`
  - `compilation_jobs`
  - `compilation_events`
  - `review_workflows`
- `registry`
  - `service_versions`
  - `artifact_records`
- `auth`
  - `users`
  - `pats`
  - `policies`
  - `audit_log`

Important current invariants:

- only one active `service_versions` row may exist per `(service_id, tenant, environment)` scope
- `(service_id, version_number, tenant, environment)` is unique for stored versions
- `(job_id, sequence_number)` is unique for compilation events
- `(service_id, version_number)` is unique for review workflows

Current persistence helpers add a few non-obvious operational guarantees:

- `CompilationRequest.to_payload()` requires at least one of `source_url` or `source_content`
- worker-side scope normalization derives `tenant` and `environment` from request options
- worker `create_job()` is idempotent when the caller provides a stable `job_id`
- worker `append_event()` retries up to 3 times on event-sequence integrity conflicts
- both compilation jobs and registry versions are scope-aware on `tenant` / `environment`

## 13. Compilation Workflow Specification

### 13.1 Workflow stages

The build plane uses a fixed ordered stage model:

1. `detect`
2. `extract`
3. `enhance`
4. `validate_ir`
5. `generate`
6. `deploy`
7. `validate_runtime`
8. `route`
9. `register`

Persisted statuses are:

- `pending`
- `running`
- `succeeded`
- `failed`
- `rolled_back`

Persisted event types include:

- job lifecycle events
- stage start/success/retry/failure events
- rollback start/success/failure events

Exact persisted event type values are:

- `job.created`
- `job.started`
- `job.succeeded`
- `job.failed`
- `job.rolled_back`
- `stage.started`
- `stage.succeeded`
- `stage.retrying`
- `stage.failed`
- `rollback.started`
- `rollback.succeeded`
- `rollback.failed`

Normal successful event ordering is:

- `job.created`
- `job.started`
- zero or more pairs of `stage.started` / `stage.succeeded`
- `job.succeeded`

On retryable stage failure, the stage emits `stage.retrying` before the next attempt.

On rollback-triggering failure, the failed stage emits `stage.failed`, then rollback events for rollback-enabled completed stages, then the job terminates as `job.rolled_back`.

#### Stage input/output contract

The workflow is easiest to reason about as a typed transformation pipeline over `CompilationContext.payload`.

| Stage | Primary inputs | Primary outputs |
| --- | --- | --- |
| `detect` | request source + hints | `detection_confidence`, resolved `protocol` |
| `extract` | selected extractor + source | `service_id`, `service_ir`, `source_hash`, `version_number` |
| `enhance` | `service_ir` | updated `service_ir`, `token_usage` |
| `validate_ir` | `service_ir` | `pre_validation_report` |
| `generate` | `service_ir`, `service_id`, `version_number`, `runtime_mode` | `manifest_yaml`, `route_config`, `generated_manifest_set` |
| `deploy` | manifest set | `deployment_revision`, `runtime_base_url`, `manifest_storage_path` |
| `validate_runtime` | `service_ir`, `runtime_base_url`, validation options | `post_validation_report`, `sample_invocations` |
| `route` | `route_config` | `route_publication` |
| `register` | IR + manifest + validation + route + deployment metadata | `registered_version` |

Additional notes:

- `generate` defaults to generic runtime manifests but can switch to `codegen` mode
- `register` persists both manifest and IR artifact records with content hashes
- `detect` and `extract` rebuild the extractor list fresh per stage
- `validate_runtime` first waits for both `/healthz` and `/readyz` to return `200` before running post-deploy validation, and the timeout budget is enforced against real wall-clock time rather than just poll sleeps
- the default production path auto-generates sample invocations, then merges caller overrides

Current stage event-detail payloads are also stage-specific:

- `detect` emits `confidence`
- `extract` emits `operation_count`
- `enhance` emits either `mode=passthrough` or `operations_enhanced`, `operations_skipped`, and `model`
- `validate_ir` emits `overall_passed`
- `generate` emits `deployment_name`
- `deploy` emits `deployment_revision` and `runtime_base_url`
- `validate_runtime` emits `overall_passed`
- `route` emits `route_id` and `publication_mode`
- `register` emits `service_id` and `version_number`

### 13.2 Workflow context

Each job carries a `CompilationRequest` and a mutable `CompilationContext`.

Relevant request inputs:

- `source_url`
- `source_content`
- `source_hash`
- `filename`
- `created_by`
- `service_id`
- `service_name`
- freeform `options`
- optional `job_id`

Context carries:

- payload data emitted by prior stages
- resolved protocol
- resolved service identity/name state
- per-stage results and rollback payloads

The initial workflow payload is seeded directly from the request with:

- `source_url`
- `source_content`
- `source_hash`
- `filename`
- `options`

#### Source resolution and override rules

The worker builds `SourceConfig` from:

- `request.source_url`
- `request.source_content`
- `options.source_file_path`
- `options.auth_header`
- `options.auth_token`
- `options.hints`

Protocol selection rules:

- if `context.protocol` or `SourceConfig.hints["protocol"]` is set, that extractor is selected directly
- otherwise `TypeDetector` runs all extractors and chooses the highest-confidence result

Scope override rules:

- `options.tenant` and `options.environment` update both the job scope and the extracted `ServiceIR`

Auth override rules:

- `options.auth` or `options.auth_config` can replace extractor-derived auth on `ServiceIR`

Service-identity rules:

- explicit `request.service_name` rewrites `ServiceIR.service_name`
- persisted service identity currently resolves as `request.service_id` → `request.service_name` → extracted `ServiceIR.service_name`
- current job persistence stores that resolved identity in a single `service_name` column, which is why compilation-job responses mirror it into both `service_id` and `service_name`

Validation-tooling option rules:

- `preferred_smoke_tool_ids` influences only post-deploy smoke selection
- `sample_invocation_overrides` augments generated sample arguments rather than replacing sample generation globally

### 13.3 Retry and rollback semantics

Each stage has retry policy support.

Current default retry policy is `max_attempts=3`.

Retry events currently include `detail.next_attempt` to indicate the next scheduled attempt number.

The workflow stage definitions currently mark these stages as rollback-enabled:

- `generate`
- `deploy`
- `route`

The production activity registry currently wires concrete rollback handlers only for:

- `deploy`
- `route`

Important nuance:

- `generate` is rollback-enabled at the workflow layer, but its rollback handler is currently absent in the production activity registry
- as implemented today, `generate` can therefore emit `rollback.started` / `rollback.succeeded` as a successful no-op during rollback

This means the system treats deployment and route publication as the real compensating side-effect boundaries, while generation rollback is currently bookkeeping-only.

Rollback payload expectations:

- `deploy` rollback expects serialized manifest set plus deployment metadata
- `route` rollback expects `route_config` plus publication metadata, including any previously active routes

Current deployment/rollback safety behavior:

- partial Kubernetes apply failures trigger best-effort deletion of manifests that were already created in that attempt
- rollback attempts all known manifest deletes even if one delete fails, then raises an aggregated cleanup error afterward
- rollout success requires the deployment generation to be observed, `availableReplicas >= expected_replicas`, and `updatedReplicas >= expected_replicas` when Kubernetes reports `updatedReplicas`

### 13.4 Route publication modes

The worker supports route publication modes including deferred and access-control-backed publishing.

This lets the build plane either:

- record route metadata for later application
- or actively synchronize routes through the Access Control service

Current publication semantics:

- **deferred** mode returns publication metadata without modifying gateway state
- **access-control** mode calls Access Control gateway-binding APIs for sync and rollback
- the default route-publisher mode in production settings is `deferred`

Current default production settings include:

- runtime image default: `tool-compiler/mcp-runtime:latest`
- target namespace default: service-account namespace if available, otherwise `default`
- image pull policy default: `IfNotPresent`
- proxy timeout default: `10.0s`
- runtime startup timeout default: `10.0s`
- runtime startup poll interval default: `1.0s`

### 13.5 Worker execution surfaces

`apps/compiler_worker` currently ships both:

- an HTTP shell process
- a Celery consumer process

The HTTP shell exposes:

- `/healthz`
- `/readyz`
- `/metrics`

Current readiness semantics are config-centric rather than dependency-probing:

- `/readyz` reports `workflow_engine`, `compilation_queue`, `task_name`, `runtime_image`, `target_namespace`, `route_publish_mode`, and `access_control_url`
- missing env-backed values are returned via a `missing[]` list
- readiness here does **not** prove live Redis, Kubernetes, or Access Control reachability

Current execution wiring:

- worker shell default `WORKFLOW_ENGINE` is `celery`
- the default executor is `DatabaseWorkflowCompilationExecutor`
- each task execution constructs a fresh workflow runtime with a DB-backed job store and the default activity registry

Current Celery semantics:

- broker fallback is `memory://`
- result-backend fallback is `cache+memory://`
- both fallbacks log warnings because queued tasks/results are ephemeral
- task return payload is `{"job_id": ...}` on success
- task return payload becomes `{"job_id": ..., "error": ..., "error_type": ...}` on failure

Current entrypoint supervision behavior:

- when `CELERY_BROKER_URL` / `REDIS_URL` points at `redis://` or `rediss://`, the entrypoint waits for broker socket reachability before continuing
- Celery readiness is inferred from stdout containing `" ready."`
- once Celery is considered ready, the HTTP shell is started
- if either child process exits, the sibling is terminated and the supervisor exits with that status

## 14. Extractor and Detection Specification

### 14.1 Extractor contract

Every extractor follows the same basic contract:

- `detect(source) -> confidence`
- `extract(source) -> ServiceIR`

The compiler chooses among extractors by detection confidence, with protocol-specific extractors taking precedence over fallback discovery behavior.

#### `SourceConfig`

Current source inputs are modeled as:

| Field | Meaning |
| --- | --- |
| `url` | remote service or schema URL |
| `file_path` | local file path |
| `file_content` | inline file content |
| `auth_header` | raw discovery auth header |
| `auth_token` | token-style discovery auth input |
| `hints` | string-based extractor hints |

At least one of `url`, `file_path`, or `file_content` must be present.

#### Detection semantics

Current detector behavior is:

- run `detect()` on every registered extractor
- clamp confidence into `[0.0, 1.0]`
- ignore extractors that return `0`
- ignore extractor detect failures while logging warnings
- sort remaining candidates by confidence descending
- fail if no extractor reports confidence `> 0`

Important selection nuance:

- there is no minimum-confidence acceptance threshold beyond `> 0`
- ties are resolved by extractor registration order because the detector sorts stably

When protocol forcing is used, extractor choice bypasses confidence ranking entirely.

### 14.2 Extractor order

The production worker currently builds extractors in this order:

1. OpenAPI
2. GraphQL
3. gRPC proto
4. SOAP / WSDL
5. OData
6. SCIM
7. JSON-RPC
8. SQL
9. REST discovery

REST serves as the most heuristic fallback.

### 14.3 OpenAPI

OpenAPI is the primary deterministic HTTP spec-driven path.

The extractor is expected to:

- preserve operation identity and HTTP contract
- infer auth metadata
- populate schemas and examples when available
- carry enough structure for runtime execution and validation

Current code-level behavior is more specific:

- it accepts both Swagger 2.0 and OpenAPI 3.x inputs from JSON or YAML
- it resolves in-document `#/...` refs eagerly but skips external refs
- Swagger loopback-style `host` values are replaced with the source URL host/scheme when needed
- when multiple security schemes exist, the extractor currently selects the first supported scheme rather than composing them
- operations are emitted only for `get`, `post`, `put`, `patch`, `delete`, `head`, and `options`
- missing `operationId` values fall back to `method + slug(path)`
- path template params are backfilled if the spec omitted them from the parameter list
- Swagger `in: body` shapes are flattened into params; OpenAPI 3 `requestBody` becomes either flattened JSON params or a synthetic `payload` param for multipart/raw bodies
- OpenAPI callbacks and top-level webhooks are represented as `unsupported` event descriptors, not executable runtime operations
- GET operations attempt pagination inference from both parameter names and response-envelope structure

### 14.4 REST discovery

REST discovery is the most heuristic extractor and the most heavily hardened discovery path.

Current behavior includes:

- heuristic detection from HTTP(S) URLs, with explicit de-prioritization for URLs that already look like OpenAPI/Swagger/GraphQL endpoints
- crawling from a root URL
- extracting candidates from HTML, JSON, and forms
- special-case bootstrap for JSON Server-style fixtures
- probing allowed methods
- using OPTIONS/HEAD-aware probing improvements
- inferring detail and sub-resource paths from collections
- iterating URI-structure inference in multiple passes
- deduplicating over-general or redundant paths
- deriving path parameter defaults
- optional LLM seed mutation for candidate expansion
- adding a synthetic `payload` object param to discovered write methods when no body param exists

The discovery pipeline explicitly separates:

- structural discovery
- generated tool count
- auditable tool count
- black-box coverage

This extractor is good enough for live use, but it is still the primary source of remaining endpoint-parity gaps in real-target work.

Operationally, REST discovery should be understood as a bounded best-effort crawler rather than a perfect API enumerator. Its output is therefore subject to:

- crawl-depth limits (`max_pages` defaults to `8`)
- root-page visibility limits
- auth-gated branch visibility
- collection-to-detail inference quality
- advertised-method completeness
- path-template sample quality
- downstream audit-policy filtering

Additional current nuance:

- `OPTIONS` is treated as authoritative when it advertises an `Allow` set
- if `OPTIONS` is absent or rejected, discovery falls back to `HEAD`, then to a validating `GET`
- LLM-backed classification changes operation provenance from `extractor` to `llm`

### 14.5 GraphQL

GraphQL extraction produces typed operations and runtime execution contracts rather than flattening GraphQL into generic HTTP descriptions.

Current code-level behavior includes:

- file-based inputs must already contain GraphQL introspection JSON; URL-based extraction performs a live introspection query
- queries are marked `safe`; mutations are marked `cautious`
- executable operations are emitted only for query and mutation root fields
- subscription root fields are currently represented as `graphql_subscription` event descriptors with `support=unsupported`
- `GraphQLOperationConfig.document` is synthesized automatically, including variable bindings and an auto-generated selection set
- selection-set generation is depth-bounded and falls back to `__typename` when the type graph would otherwise recurse or become ambiguous
- extracted auth defaults are inferred only from discovery inputs (`auth_header` / `auth_token`), not from schema metadata

### 14.6 gRPC

The gRPC family is modeled in three layers:

- generic gRPC extraction
- unary runtime contracts via `grpc_unary`
- server-stream runtime contracts via `grpc_stream` event descriptors

Not all gRPC capability is exposed through the generic `grpc` row. Runtime support is carried through the unary and streaming subcontracts.

Current extractor boundaries are:

- extraction is based on `.proto` source parsing, not server reflection
- unary RPCs become executable POST operations with `grpc_unary.rpc_path`
- streaming RPCs always create `grpc_stream` event descriptors
- only server-stream RPCs become executable operations, and only when `SourceConfig.hints["enable_native_grpc_stream"]` is truthy
- client-streaming and bidirectional RPCs are represented descriptively through event descriptors but are not executable in the current runtime
- a proto containing only unsupported streaming RPCs can fail extraction because no executable operations remain
- risk is inferred from RPC name prefixes rather than protobuf options

### 14.7 SOAP / WSDL

SOAP extraction produces typed SOAP execution metadata such as:

- target namespace
- request element
- response element
- SOAP action
- binding style and body use

Important current limits and behaviors:

- the extractor currently supports WSDL 1.1 `definitions` documents only
- only document-style, literal-body bindings are accepted
- the first discovered `service` / `port` pair is used as the runtime endpoint source of truth
- defaulted XSD fields are treated as optional call-time params even when `minOccurs` is omitted
- discovery auth may be used to fetch the WSDL, but extracted runtime auth still defaults to `none` unless later overridden by compilation options

### 14.8 SQL

SQL extraction reflects catalog structure into bounded query and insert operations.

The current runtime contract is intentionally conservative. Query and insert are implemented; broader DML parity remains part of future coverage work.

Current code-level behavior includes:

- the extractor accepts database URLs from either `source.url` or inline `source.file_content`
- detection recognizes `postgres`, `postgresql`, `mysql`, `mariadb`, and `sqlite` schemes
- actual async reflection URL rewriting is currently implemented only for PostgreSQL-family and SQLite URLs; MySQL/MariaDB detect positively but are not yet rewritten into a supported async driver URL in mainline
- the reflected schema defaults to `inspector.default_schema_name` or `public`
- every table/view yields a query operation, while tables also yield an insert operation
- query operations always include a synthetic `limit` param with default `50`
- column descriptions preserve both DB comments and inferred foreign-key references
- source hashes are schema fingerprints over reflected structure, not just raw connection strings

### 14.9 OData v4

OData extraction is based on `$metadata` parsing and emits CRUD-like operations over entity sets plus function/action imports.

The runtime restores `$` prefixes stripped by MCP-facing parameter naming, unwraps OData collection responses, and detects OData error payloads.

Current extractor specifics:

- detection is driven by protocol hint, `$metadata` URL suffixes, or EDMX markers
- the base URL is normalized by stripping the trailing `$metadata`
- each entity set yields five CRUD-style operations: list, get-by-key, create, update, delete
- list operations expose standard OData query params including `$filter`, `$select`, `$top`, `$skip`, and `$orderby`
- composite keys are encoded using OData key syntax such as `EntitySet(Key1={Key1},Key2={Key2})`
- unbound function imports become GET operations; action imports become POST operations

### 14.10 SCIM 2.0

SCIM extraction respects resource schema and attribute mutability rules.

The runtime understands SCIM list envelopes and SCIM error payloads.

Live parity remains tenant-sensitive because some SCIM discovery endpoints may be absent or return `404` on a concrete deployment.

Current extractor specifics:

- extraction supports both direct SCIM discovery responses and older wrapped fixture shapes
- when full schema discovery is unavailable, the extractor can fall back to built-in `User` / `Group` resource schemas for common `/Users`, `/Groups`, or generic `ListResponse` cases
- each resource yields list/get/create/update/delete operations
- PATCH operations are emitted only when `service_provider_config.patch.supported` is true
- optional global operations such as password change and bulk execution are emitted only when the service-provider config advertises support
- create/update params are filtered by SCIM attribute mutability rules
- base URL normalization strips common SCIM discovery suffixes such as `/Schemas` and `/ServiceProviderConfig`

### 14.11 JSON-RPC 2.0

JSON-RPC extraction supports OpenRPC-style extraction and manual definition inputs.

The runtime wraps requests in JSON-RPC envelopes, unwraps `result`, and raises on `error` objects.

Full live method discovery parity, for example through `system.listMethods`, is still part of future endpoint-coverage work.

Current extractor specifics:

- protocol hints can force detection to `1.0`
- OpenRPC documents score highest, but manual JSON-RPC service definitions and generic `methods[]` payloads are also accepted
- for OpenRPC docs served from paths like `/openrpc.json`, the default runtime endpoint is rewritten to a sibling `/rpc` path unless the spec provides an explicit server URL
- dotted method names are preserved for display but normalized into operation IDs by replacing `.` with `_`
- params default to named mode unless OpenRPC metadata explicitly requests positional semantics
- risk classification is based on the last segment of a dotted method name (for example `user.getById` is classified from `getById`)

## 15. IR Specification

### 15.1 IR role

`ServiceIR` is the single source of truth for the compiled service surface.

It is:

- versioned
- serializable
- persisted
- diffable
- validated
- consumed by runtime, registry, validator, and review flows

### 15.2 Top-level `ServiceIR`

Core fields include:

| Field | Meaning |
| --- | --- |
| `ir_version` | IR schema version |
| `compiler_version` | compiler build/version marker |
| `source_url` | optional original source URL |
| `source_hash` | hash of the submitted source input |
| `protocol` | normalized protocol key |
| `service_name` | human-readable service display name carried in the IR |
| `service_description` | human-readable description |
| `base_url` | runtime upstream base |
| `auth` | upstream auth config |
| `operations` | tool surface |
| `operation_chains` | ordered multi-step tool groups |
| `tool_grouping` | semantic clustering result |
| `event_descriptors` | event/stream metadata |
| `resource_definitions` | MCP resources |
| `prompt_definitions` | MCP prompts |
| `metadata` | extractor- or pipeline-specific metadata |
| `created_at` | IR creation timestamp |
| `tenant` / `environment` | deployment scope |

Current code-level defaults include:

- `ir_version = 1.0.0`
- `compiler_version = 0.1.0` when the producer does not override it
- `created_at` defaults to current UTC time

### 15.3 `Operation`

Each enabled `Operation` becomes one MCP tool.

Core fields include:

| Field | Meaning |
| --- | --- |
| `id` | stable tool identifier |
| `name` | display title |
| `description` | tool description |
| `method` | HTTP-like method or transport action |
| `path` | upstream path or RPC-like target |
| `params` | typed input parameters |
| `response_schema` | primary output schema |
| `error_schema` | normalized error model |
| `response_examples` | extracted or generated examples |
| `risk` | semantic risk metadata |
| `response_strategy` | pagination, truncation, filtering |
| `request_body_mode` | `json`, `multipart`, or `raw` |
| `body_param_name` | input field used as body |
| `async_job` | polling contract for async APIs |
| `graphql` | typed GraphQL execution config |
| `sql` | typed SQL execution config |
| `grpc_unary` | typed gRPC unary execution config |
| `soap` | typed SOAP execution config |
| `jsonrpc` | typed JSON-RPC execution config |
| `tags` | operation tags |
| `tool_intent` | `discovery` or `action` |
| `source` | extractor vs LLM vs override provenance |
| `confidence` | confidence score |
| `enabled` | publishability flag |

### 15.3.1 Execution subcontracts

`Operation` is intentionally transport-polymorphic. In current mainline, operations execute in one of these modes:

- generic HTTP proxy mode via `method` + `path`
- GraphQL mode via `graphql`
- SQL native mode via `sql`
- gRPC unary native mode via `grpc_unary`
- SOAP mode via `soap`
- JSON-RPC mode via `jsonrpc`
- async HTTP overlay via `async_job`
- streaming overlay via matching `event_descriptors`

Important runtime rules:

- generic HTTP proxy execution requires both `method` and `path`
- GraphQL, SOAP, and JSON-RPC replace generic body shaping with protocol-native envelopes
- async job polling is an overlay on top of an initial HTTP response, not a separate operation type
- stream execution is selected by matching `EventDescriptor.operation_id`
- multiple stream descriptors for the same operation are currently treated as ambiguous and rejected at runtime
- `grpc_unary` cannot be combined with GraphQL, SQL, SOAP, or JSON-RPC execution metadata, and requires `POST`
- when `grpc_unary` is present, `operation.path` must match `grpc_unary.rpc_path`
- SOAP operations cannot be combined with GraphQL, SQL, gRPC unary, or JSON-RPC execution metadata, and require `POST`
- SQL query operations require `GET`; SQL insert operations require `POST`
- JSON-RPC operations cannot be combined with GraphQL, SQL, gRPC unary, or SOAP execution metadata, and require `POST`

### 15.4 `Param`

Parameters carry:

- name
- JSON-schema-like type
- required/default state
- description
- provenance
- confidence

Extractor-produced params require `confidence >= 0.8`.

### 15.5 Risk metadata

`RiskMetadata` carries semantic fields:

- `writes_state`
- `destructive`
- `external_side_effect`
- `idempotent`
- `risk_level`
- `confidence`
- `source`

Important rule: operations with `risk_level == unknown` are automatically disabled by model validation unless deliberately changed before validation.

### 15.6 Response strategy

`ResponseStrategy` supports:

- pagination config
- byte limits
- array limits
- field filtering
- truncation policy

This is part of the runtime contract, not merely documentation.

Current enum values and defaults include:

- `truncation_policy`: `none`, `truncate`, `summarize`
- pagination style: `offset`, `cursor`, `page`
- pagination defaults: `page`, `page_size`, default size `20`, max size `100`

### 15.7 Auth config

`AuthConfig` supports:

- bearer
- basic
- api key
- custom header
- OAuth2
- none
- compile-time and runtime secret refs
- legacy flat OAuth2 token URL/scopes fields
- optional nested OAuth2 client credentials config
- optional mTLS config
- optional request-signing config

Auth config is validated for coherence. For example:

- OAuth2 nested config requires `auth.type=oauth2`
- basic auth requires both username and password ref
- custom-header auth requires `header_name`

### 15.8 Event descriptors

The IR can describe event or stream-related capability using `event_descriptors`.

Current transport enum values include:

- `websocket`
- `sse`
- `webhook`
- `callback`
- `graphql_subscription`
- `grpc_stream`
- `async_event`

This allows the IR to represent stream-capable or stream-adjacent surfaces even when not every transport is executable in the runtime.

### 15.9 Tool grouping

`ToolGroup` provides semantic grouping of operations by business intent.

It is an enrichment artifact, not a replacement for operations.

### 15.10 Resources and prompts

The IR supports first-class MCP resources and prompts through:

- `resource_definitions`
- `prompt_definitions`

Current runtime behavior:

- static resources are registered
- dynamic resources are currently skipped by runtime registration
- prompts are registered and rendered through template substitution

Important implementation nuance:

- the IR and runtime support these surfaces today
- helper generators for automatic resource and prompt generation exist
- those helper generators are **not** currently wired into the default production `_apply_post_enhancement()` path

Therefore the spec treats resources and prompts as **supported IR/runtime surfaces**, while **automatic production population remains partial**.

Current IR field shapes are:

- `ResourceDefinition`: `id`, `name`, `description`, `uri`, `mime_type`, `content_type`, `content`, `operation_id`, `tags`
- `PromptDefinition`: `id`, `name`, `description`, `template`, `arguments`, `tool_ids`, `tags`
- `PromptArgument`: `name`, `description`, `required`, `default`

### 15.11 Model invariants

Current code enforces these important invariants:

- operation IDs must be unique
- operation-chain steps must reference valid operations
- event descriptors must reference valid operations when `operation_id` is present
- tool-group operation IDs must reference valid operations
- prompt tool IDs must reference valid operations
- resource `operation_id` references must be valid
- resource IDs must be unique
- prompt IDs must be unique
- protocol-specific execution contracts are mutually exclusive where required
- SQL limits must be coherent
- grpc stream descriptors must carry matching runtime config

## 16. Enhancement Specification

### 16.1 LLM enhancement

The system supports LLM-assisted IR enhancement, but the implemented contract is intentionally narrow.

Current enhancement guarantees:

- LLM enhancement rewrites descriptive fields, not structural execution metadata
- only `Operation.description` and `Param.description` are modified by the main enhancer
- operation IDs, names, HTTP method/path, risk metadata, and protocol-specific execution subcontracts are preserved
- if no enhancement lands, the original IR is returned unchanged rather than forcing partial synthetic output

Current provider surface includes:

- `openai` (default provider)
- `anthropic`
- `deepseek`
- `vertexai`

Current default model/provider pairing is:

- provider: `openai`
- model: `gpt-4o-mini`

Enhancement can be enabled by explicit worker flags or by available LLM credentials.

Current enablement rules are:

- `options.skip_enhancement=true` disables LLM enhancement even if credentials are present
- otherwise enhancement is enabled when `WORKER_ENABLE_LLM_ENHANCEMENT` is truthy
- otherwise enhancement also auto-enables when `LLM_API_KEY` or `VERTEX_PROJECT_ID` is present
- all non-Vertex providers currently require `LLM_API_KEY`

Current selection and batching behavior:

- by default, operations are selected only when their description is very short (`< 20` chars) or any param description is very short (`< 10` chars)
- default batch size is `10` operations per LLM call
- per-job token budget defaults to `50_000`
- token accounting is cumulative across batches
- batch failures are logged and skipped rather than failing the workflow

### 16.2 Deterministic post-enhancement

The production worker always applies these deterministic transforms:

- derive `tool_intent`
- bifurcate descriptions into discovery/action framing
- normalize error schemas

Optional transforms:

- tool grouping, when explicitly enabled and an LLM client is available
- example generation, when an LLM client is available

Additional current nuance:

- tool-grouping failures degrade to empty groups instead of failing compilation
- example-generation failures are non-blocking and simply leave examples absent
- deterministic prompt/resource generators exist, but they are not auto-wired into the default production `_apply_post_enhancement()` path

### 16.3 Tool intent

The system explicitly distinguishes:

- `discovery` tools
- `action` tools

This distinction affects:

- audit selection
- safe exploration patterns
- prompt generation
- future product UX

Current intent derivation is conservative:

- safe/list/read/search-style operations trend toward `discovery`
- mutating/administrative operations trend toward `action`
- description bifurcation can prefix descriptions with `[DISCOVERY]` or `[ACTION]`

### 16.4 Tool grouping

Tool grouping is the operational pipeline behavior for the IR enrichment described in Section 15.9. It is opt-in rather than default, and failures currently collapse to an empty grouping set rather than failing compilation.

### 16.5 Example generation

Example generation is additive and non-blocking.

If generation fails, the pipeline continues without synthetic examples.

### 16.5.1 Deterministic helper generators not yet auto-wired

The enhancer package also ships deterministic helper generators that are important to the overall platform contract even though they are not yet default pipeline outputs.

Current helper outputs include:

- three standard static resources: schema, operations, and auth-requirements
- prompt templates oriented around explore, safe discovery, and management tasks

### 16.6 Error normalization

Error normalization is deterministic and always runs. It ensures every operation leaves enhancement with a non-empty error model, even when extractor data is sparse.

## 17. Runtime Specification

### 17.1 Generic runtime startup

At startup, the runtime:

1. loads `ServiceIR` from the configured path
2. builds the FastMCP server
3. selects optional native executors when enabled
4. creates a `RuntimeProxy`
5. registers enabled tools
6. registers resources
7. registers prompts
8. exposes health, readiness, and metrics endpoints

Important runtime bootstrap nuances:

- if `SERVICE_IR_PATH` is missing or invalid, the runtime still boots an HTTP shell with a recorded `load_error`
- in that state, liveness remains healthy but readiness- and tool-listing endpoints return not-ready responses
- FastMCP DNS rebinding protection is disabled by default because the runtime is normally reached through Kubernetes service DNS names; operators can re-enable protection with `MCP_DISABLE_DNS_REBINDING_PROTECTION`

### 17.2 Runtime endpoints

The runtime exposes:

- `/mcp`
- `/healthz`
- `/readyz`
- `/tools`
- `/metrics`

#### Health and readiness semantics

Current runtime endpoint expectations are:

- `/healthz`: process liveness and always returns `{status:"ok"}` while the process is up
- `/readyz`: IR-loaded runtime readiness
- `/tools`: JSON description of registered tools; useful for post-deploy validation
- `/metrics`: process/runtime metrics for scraping and debugging

Current `/readyz` shapes are:

- **ready**: `status`, `service_name`, `tool_count`, `service_ir_path`
- **not ready** (`503`): `status=not_ready`, `error`, `service_ir_path`

Current `/tools` shapes are:

- **ready**: `status=ready`, `service_name`, `tool_count`, `tools[]`
- **not ready** (`503`): `status=not_ready`, `error`, `tool_count=0`, `tools=[]`

Each ready tool entry currently contains:

- `name`
- `description`
- `input_schema`

### 17.3 Runtime proxy behavior

`RuntimeProxy` is the central execution engine.

It handles:

- HTTP upstream calls
- request-body shaping
- query parameter shaping
- auth injection
- response truncation and shaping
- SOAP fault detection
- GraphQL error detection
- OData error detection
- JSON-RPC error detection
- SQL native execution
- gRPC unary native execution
- gRPC server-stream native execution
- SSE and WebSocket stream sessions

#### Request shaping rules

Current request-building behavior is protocol-specific:

- path templates such as `{id}` are resolved from tool arguments and URL-encoded
- non-null path arguments are removed from the remaining argument set before body/query shaping
- write methods default to JSON-body behavior unless `request_body_mode` says otherwise
- `request_body_mode=multipart` builds `data` + `files`
- `request_body_mode=raw` emits raw bytes/string content with explicit content type
- GraphQL emits `{"query","variables","operationName?"}`
- SOAP emits an XML envelope and adds `SOAPAction` when configured
- JSON-RPC emits a JSON-RPC 2.0 envelope with positional or named params
- OData restores `$` prefixes for system query parameters such as `$filter`
- WebSocket sessions treat non-path arguments as query params plus optional outbound message payloads

Current async-job defaults are:

- initial HTTP status codes: `[202]`
- status URL source: `Location` / `Content-Location` header by default
- status field: `status`
- pending states: `pending`, `queued`, `running`, `in_progress`
- success states: `completed`, `succeeded`, `done`, `success`
- failure states: `failed`, `error`, `cancelled`, `canceled`
- poll interval: `0.5s`
- timeout: `30.0s`

Security nuance:

- async poll URLs resolved from headers or response bodies are currently restricted to the same origin as the initial request to reduce SSRF risk

#### Auth and secret-resolution rules

Current auth behavior is driven entirely by `ServiceIR.auth`:

- bearer and OAuth2 credentials default to the `Authorization` header unless overridden
- API keys can be emitted in headers or query parameters
- custom header auth requires an explicit header name
- basic auth supports either a full secret value or split username/password refs
- OAuth2 client-credentials tokens are fetched lazily and cached until expiry
- request-signing, when configured, adds signature/timestamp headers over method + URL + query + body
- mTLS client cert/key/CA material is resolved from runtime secret refs when the HTTP client is created
- missing runtime secret refs fail tool invocation explicitly; the runtime does not silently skip auth

#### Streaming session contract

Current runtime-supported stream transports are:

- SSE
- WebSocket
- gRPC server stream

Current stream result shape is lifecycle-oriented rather than infinite:

- SSE returns collected events plus `termination_reason`, `events_collected`, `max_events`, and `idle_timeout_seconds`
- WebSocket returns collected messages plus `messages_sent` and the same lifecycle metadata
- gRPC stream delegates to the configured native executor and returns its structured result payload

#### Response shaping and protocol-error mapping

After upstream execution, the runtime:

- unwraps SOAP envelopes
- unwraps GraphQL `data`
- unwraps OData and SCIM collection envelopes
- unwraps JSON-RPC `result`
- applies field filtering
- applies array limits
- applies truncation/byte-budget policy

Current response-shaping specifics include:

- OData collections are normalized to `items`, with optional `total_count` and `next_link`
- SCIM list responses are normalized to `items`, with optional `total_count`, `start_index`, and `items_per_page`
- binary upstream payloads are surfaced as `{binary, content_type, content_base64, size_bytes}`
- field filters support top-level keys, nested dot paths, and array paths such as `items[].id`
- field-filter paths can escape literal dots with `\.`
- array limits truncate either a top-level list or list-valued fields inside a top-level object
- byte truncation wraps oversized payloads into `{content, original_type, truncated}` and can also report `utf8_boundary_trimmed`
- `truncation_policy=summarize` is currently treated the same as byte truncation; there is not yet a distinct summarization implementation

Protocol-specific error detection currently includes:

- SOAP faults
- GraphQL `errors`
- OData JSON `error`
- JSON-RPC `error`
- SCIM error envelopes
- async job polling timeout or invalid poll payloads

The normalized successful tool result shape for proxied HTTP invocations is:

- `status`
- `operation_id`
- `upstream_status`
- `result`
- `truncated`

### 17.4 Circuit breaking and observability

Runtime execution is guarded by per-operation circuit breakers and records observability data per tool call.

Observed runtime metrics/events include:

- success/error tool-call counts
- per-operation latency
- upstream error classification
- circuit-breaker state transitions

Current breaker behavior is intentionally simple:

- failure threshold defaults to `5` consecutive failures per operation
- breakers open immediately once the threshold is reached
- the current implementation does **not** include a half-open or cooldown state
- once opened, a breaker remains open until process restart or explicit in-memory reset

### 17.5 Native executors

Native runtime behavior is conditionally enabled for:

- SQL
- gRPC unary
- gRPC server stream

The system keeps these behind explicit checks instead of assuming every extracted protocol family is always runtime-safe.

Current enablement rules differ by executor family:

- SQL native execution auto-enables whenever the IR contains enabled SQL operations
- gRPC unary requires both enabled unary metadata in the IR and a truthy `ENABLE_NATIVE_GRPC_UNARY`
- gRPC server stream requires both supported `grpc_stream` descriptors in the IR and a truthy `ENABLE_NATIVE_GRPC_STREAM`

Truthy env forms currently accepted for the gRPC flags are:

- `1`
- `true`
- `yes`
- `on`

### 17.6 OData note

FastMCP-facing parameter names do not keep `$` prefixes, so the runtime must restore them when building OData upstream requests.

### 17.7 Resource and prompt registration

The runtime loader currently:

- registers tools for enabled operations
- registers static resources
- registers prompts

Dynamic resource execution is not yet part of default runtime registration.

Prompt registration currently performs argument-aware template substitution at invocation time rather than introducing a separate prompt DSL.

Additional loader details from current code:

- tool call signatures are synthesized from `Operation.params` as keyword-only Python arguments
- non-Python-safe, duplicate, or keyword parameter names are sanitized before registration
- the original IR parameter names are preserved in tool metadata via `param_name_map`
- registered tool metadata also carries `operation_id`, `operation_name`, `method`, and `path`
- only resources whose `content_type == "static"` are registered by default; non-static resources are skipped with a log entry

## 18. Validation Specification

### 18.1 Pre-deploy validation

Pre-deploy validation covers:

- IR schema validation
- event support validation
- auth smoke validation

Important event-support rules:

- SSE and WebSocket are approved transports
- `grpc_stream` requires explicit native enablement and compatible mode
- `grpc_unary` requires explicit native enablement
- planned-but-not-supported event descriptors fail validation

Current event-support validation is more specific than that shorthand:

- descriptors explicitly marked `unsupported` are allowed and reported as explicitly unsupported
- approved HTTP-native transports must still reference an `operation_id`
- `grpc_stream` must have an `operation_id`, concrete `grpc_stream` runtime config, explicit native enablement, and `server` stream mode
- native `grpc_unary` operations fail validation when native unary runtime is not allowed for the current build/deploy path

#### Auth smoke semantics

Current `auth_smoke` behavior is intentionally lightweight:

- if `auth.type=none` and neither mTLS nor request-signing is configured, auth smoke passes immediately with no network check
- non-OAuth auth types do not validate live credentials; they currently validate that the expected secret reference fields exist
- OAuth2 currently performs a reachability probe to the token endpoint and fails on request errors, `404`, or `5xx`
- mTLS and request-signing are currently validated for configuration presence, not for live handshake/signature success

### 18.2 Post-deploy validation

Post-deploy validation covers:

- runtime health and readiness
- tool listing
- representative tool invocation
- optional full audit over auditable tools

#### Smoke-tool selection

If the caller does not force a preferred smoke tool, the validator chooses one using a current heuristic priority:

- SQL query operations first
- GraphQL query and safe HTTP `GET`/`HEAD` operations next
- gRPC unary next
- supported gRPC stream next
- SOAP next
- generic `POST` after safe reads
- state-mutating `PUT`/`PATCH` and destructive `DELETE` last

Tie-breakers favor:

- lower semantic risk
- fewer required parameters
- lexicographically stable operation IDs

#### Default sample invocation generation

The default production activity feeds post-deploy validation with auto-generated sample invocations before applying any caller overrides.

Current generic sample-value heuristics are:

- integer → `1`
- number → `1.0`
- boolean → `true`
- array → `["sample"]`
- object → `{"name":"sample"}`
- names ending with `id` → `"1"`
- parameter named `status` → `"available"`
- otherwise → `"sample"`

Current protocol-specific sampling behavior is:

- HTTP/SOAP-style operations include only required params, params with defaults, and path params
- GraphQL queries may use an empty argument map when no required/defaulted params exist
- SQL query operations default `limit` to `1` when needed
- gRPC sampling can include a small safe subset of optional pagination/search/id-style params

### 18.3 Audit model

Audit exists to verify a safe subset of generated tools without claiming that every generated tool was live-invoked.

The audit model includes:

- per-tool audit results
- summary counts
- skip policy
- thresholds for large-surface pilot regression control

Current audit policy supports filters such as:

- skip destructive
- skip state-mutating
- skip external side effects
- optionally force auditing of safe HTTP methods
- optionally force auditing of discovery-intent tools

Audit outcomes are explicit:

- `passed`: invocation succeeded
- `failed`: invocation or exposure contract failed
- `skipped`: policy or sample-quality rules prevented safe audit

Important current skip semantics:

- tools without sample invocations are skipped
- tools whose path parameters still use synthetic placeholder values can be skipped
- safe-method and discovery-intent overrides can force audit even when broader skip policy would otherwise exclude the tool

Thresholds are expressed separately from policy and currently support:

- minimum audited/generated ratio
- maximum failed count
- minimum passed count

Current aggregate audit summary fields are:

- `discovered_operations`
- `generated_tools`
- `audited_tools`
- `passed`
- `failed`
- `skipped`
- detailed per-tool `results`

Current invariant:

- `audited_tools = passed + failed`

### 18.4 Black-box validation

Black-box validation compares IR output against known ground truth.

The comparison engine currently tracks:

- discovery coverage
- risk accuracy
- matched endpoints
- unmatched ground truth
- extra discovered operations
- failure patterns

This is used for both fixture-driven confidence and operator-run external validation.

`BlackBoxReport` currently includes:

- target name/base URL/protocol
- ground-truth count and resource groups
- discovered operation count and discovered paths
- matched endpoints
- unmatched ground truth
- extra discovered operations
- `discovery_coverage`
- `risk_accuracy`
- failure-pattern objects with `pattern_name`, affected endpoints, and description

Observed failure-pattern taxonomies include:

- nested resources not discovered
- mutation endpoints not discovered
- parameterized paths not generalized
- risk classification mismatches

Important current boundary:

- black-box `risk_accuracy` currently validates `writes_state` and `destructive`
- it does not yet verify the entire richer risk envelope such as external side effects or idempotency

### 18.5 Capability matrix

The repository maintains a machine-readable protocol capability matrix.

This matrix must distinguish:

- extract support
- compile support
- runtime support
- live proof support
- LLM E2E support

Current matrix rows are:

- `openapi`
- `rest`
- `graphql`
- `grpc`
- `grpc_unary`
- `grpc_stream`
- `soap`
- `sql`
- `odata`
- `scim`
- `jsonrpc`

For concrete `ServiceIR` instances, the matrix key is not always just `service_ir.protocol`:

- `grpc_stream` is selected when a gRPC IR includes supported `grpc_stream` descriptors
- otherwise `grpc_unary` is selected when enabled unary metadata is present
- otherwise the generic `grpc` row is used

### 18.6 Drift detection

The repository includes drift detection support that re-extracts from source and compares against deployed IR.

Current implemented scope includes:

- added operations
- removed operations
- parameter add/remove/type changes
- risk-level changes
- path changes
- method changes
- selected top-level schema changes such as service name, base URL, and auth type

The current `DriftReport` shape contains:

- `service_name`
- `checked_at`
- `has_drift`
- `added_operations`
- `removed_operations`
- `modified_operations[]`
- `schema_changes[]`

`modified_operations` currently carries human-readable change strings rather than structured field-level diff objects.

Important current limitation:

the current drift implementation is **not yet a complete capability-surface diff**. Known gaps include missing comparisons for:

- operation `name`
- operation `description`
- `operation.enabled`
- parameter `required`
- parameter `description`
- parameter `default`
- risk flags other than `risk_level`
- response schema changes
- error schema changes
- response examples
- `resource_definitions`
- `prompt_definitions`
- `event_descriptors`

This means drift detection exists and is useful, but should currently be treated as a **partial contract-drift signal**, not an exhaustive one.

## 19. Deployment and Artifact Specification

### 19.1 Artifact versioning

Compiled outputs are stored as versioned artifacts with:

- version number
- IR payload
- manifest metadata
- activation state
- tenant and environment scope
- route config

Artifact registry routes are scope-aware through optional `tenant` and `environment` query dimensions on:

- list version
- get version
- update version
- delete version
- activate version
- diff version

Current repository semantics are:

- persisted `ir_json` and optional `raw_ir_json` are normalized through `ServiceIR.model_validate(...)` before storage
- when `is_active` is omitted on create, the first version for a `(service_id, tenant, environment)` scope becomes active automatically; later versions default inactive
- creating or activating an active version first deactivates other versions in the same scope
- deactivation uses a row-locking step on currently active rows to reduce concurrent activation races
- deleting an active version promotes the highest remaining version number in the same scope, if one exists
- artifact replacement is wholesale on update rather than field-by-field patching
- `diff_versions` returns `None` rather than a partial payload if either requested version is missing

Current service-catalog read-model semantics are:

- only active versions appear in `list_services` / `get_service`
- version counts and `last_compiled_at` are aggregated per `(service_id, tenant, environment)` scope
- summary payloads derive `service_name`, `service_description`, and `tool_count` from the persisted IR rather than denormalized columns alone

### 19.1.1 Artifact diff semantics

`GET /api/v1/artifacts/{service_id}/diff?from=<n>&to=<n>` returns an `ArtifactDiffResponse`.

The current response contains:

- `service_id`
- `from_version`
- `to_version`
- `summary`
- `is_empty`
- `added_operations`
- `removed_operations`
- `changed_operations[]`

Each changed operation currently contains:

- `operation_id`
- `operation_name`
- `changes[]`
- `added_params`
- `removed_params`

`changed_operations` may include synthetic section rows for non-tool surfaces:

- `__service__` for top-level `ServiceIR` metadata
- `__resource_definitions__`
- `__prompt_definitions__`
- `__event_descriptors__`

The current artifact diff implementation compares:

- top-level `ServiceIR` metadata including `service_name`, `service_description`, `base_url`, `protocol`, `auth`, `operation_chains`, `tool_grouping`, `metadata`, `tenant`, and `environment`
- operation `name`
- operation `description`
- operation `method`
- operation `path`
- operation `enabled`
- operation response-contract fields such as `response_schema`, `error_schema`, `response_examples`, and `response_strategy`
- operation request/execution-contract fields such as `request_body_mode`, `body_param_name`, `async_job`, and protocol-specific execution configs (`graphql`, `sql`, `grpc_unary`, `soap`, `jsonrpc`)
- operation tags/tool-intent
- risk fields: `writes_state`, `destructive`, `external_side_effect`, `idempotent`, `risk_level`
- parameter add/remove
- parameter field changes: `type`, `required`, `description`, `default`
- resource/prompt/event capability surfaces as section-level changes
- malformed stored `ir_json` produces a structured `409` conflict instead of an unhandled validation crash

Important current boundary:

- artifact diff is richer than the current drift detector in some operation-level areas (for example `operation.enabled`)
- artifact diff now covers the main non-operation capability surface, but still reports those non-tool changes through synthetic section entries inside `changed_operations`

### 19.2 Generic manifest generation

The generator produces generic-mode deployment artifacts that mount or provide the IR to the runtime rather than generating per-service code.

Current generic manifest set contains:

- ConfigMap containing gzipped/base64 IR data
- Deployment for the generic FastMCP runtime
- Service for stable in-cluster access
- NetworkPolicy
- `route_config`
- concatenated multi-document YAML bundle

Current defaults in `GenericManifestConfig` are:

- namespace: `default`
- IR path in runtime: `/config/service-ir.json.gz`
- container/service port: `8003`
- replicas: `1`
- image pull policy: `IfNotPresent`
- runtime secret name: `tool-compiler-runtime-secrets`

Manifest annotations and labels currently preserve:

- base URL
- compiler version
- IR version
- protocol
- service name
- source hash
- optional tenant/environment

Auth-related runtime secret refs are materialized into container env vars only for the refs actually referenced by `ServiceIR.auth`.

#### Manifest template hardening details

The current generic deployment template also hardens the runtime pod by default:

- `automountServiceAccountToken: false`
- non-root UID/GID `10001`
- `seccompProfile: RuntimeDefault`
- `allowPrivilegeEscalation: false`
- all Linux capabilities dropped
- read-only root filesystem
- dedicated writable `/tmp` via `emptyDir`
- liveness probe on `/healthz`
- readiness probe on `/readyz`

The current manifest resource roles are:

- ConfigMap stores the gzipped/base64 IR under `binaryData`
- Deployment mounts that ConfigMap at `/config` and sets `SERVICE_IR_PATH`
- Service is `ClusterIP`
- NetworkPolicy currently permits only:
  - TCP egress to the resolved upstream API port
  - TCP egress to the OAuth2 token endpoint port when the IR uses OAuth2 client credentials on a different port
  - TCP/UDP DNS egress on port `53`

Current runtime env wiring rules:

- `SERVICE_IR_PATH=/config/service-ir.json.gz`
- `TMPDIR=/tmp`
- `ENABLE_NATIVE_GRPC_UNARY=true` only when the IR contains enabled unary-native operations
- `ENABLE_NATIVE_GRPC_STREAM=true` only when the IR contains supported native grpc stream descriptors in `server` mode
- runtime secret refs are converted to env var names by uppercasing and replacing non-word characters with `_`
- Kubernetes `secretKeyRef.key` preserves already valid secret-ref keys and normalizes refs with unsupported key characters
- if `runtime_secret_name` is `None`, secret-backed env vars are rendered only when the IR does not require runtime secrets; otherwise manifest generation fails explicitly
- distinct runtime secret refs that normalize to the same env var name are rejected explicitly during manifest/runtime startup validation

### 19.2.1 Codegen mode boundary

`runtime_mode=codegen` exists in the production generate stage, but current codegen mode is still a thin variant of generic deployment rather than a distinct runtime architecture.

Current mainline behavior is:

- the worker selects codegen mode only when `CompilationRequest.options["runtime_mode"] == "codegen"`; default behavior remains generic mode
- `generate_codegen_manifests(...)` currently delegates to `generate_generic_manifests(...)`
- the only concrete manifest differences today are image selection (`codegen_image` if provided, otherwise `runtime_image`) plus `tool-compiler-v2/runtime-mode=codegen` labels/annotations
- ConfigMap, Deployment, Service, NetworkPolicy, route config, and YAML bundle structure otherwise stay aligned with generic mode
- the code comment/docstring points toward a future dedicated codegen container flow, but that topology is **not** implemented in current mainline

### 19.3 Activation and coexistence

The system supports:

- active version promotion
- version coexistence
- rollback to prior stable routes

### 19.4 Route publication

Route publication is part of deployment correctness, not an afterthought.

Activation and deletion flows are route-aware and must remain transactionally safe with failure handling.

Current `route_config` structure is:

| Field | Meaning |
| --- | --- |
| `service_id` | stable route identity |
| `service_name` | versioned workload/service name |
| `namespace` | route namespace |
| `default_route` | active stable route |
| `version_number` | optional artifact version |
| `version_route` | optional header-pinned route |

Current route details:

- `default_route.route_id` is `<service_id>-active`
- `default_route.switch_strategy` is `atomic-upstream-swap`
- `version_route.route_id` is `<service_id>-v<version>`
- `version_route.match.headers["x-tool-compiler-version"]` pins traffic to a specific compiled version

Current publishing nuance differs by plane:

- worker route stage defaults to deferred publication unless configured otherwise
- Compiler API artifact mutations use a noop route publisher unless `ACCESS_CONTROL_URL` is configured
- when Access Control is configured, artifact sync/delete and worker route sync/rollback delegate to gateway-binding APIs

Deletion semantics are slightly subtler than activation semantics:

- deleting an inactive version removes only its version-pinned route when one exists
- deleting an active version attempts to promote the remaining active replacement and sync its stable route
- deleting the last active version removes the deleted version's routes entirely
- route-publisher failures during activation or deletion surface as `502 Bad Gateway` and roll back the database transaction

### 19.5 Capability manifest

The generator can also emit a lightweight capability manifest derived from `ServiceIR`.

Current shape:

- `tools[]` with `id`, `name`, `description`, `method`, `path`
- `resources[]` with `uri`, `name`, `description`, `mime_type`
- `prompts[]` with `name`, `description`, and prompt argument descriptors

This manifest is intentionally summary-oriented: it is suitable for catalog/UI display and quick inspection, but it is not a replacement for the full IR or registry artifact payload.

Important nuance:

- capability-manifest generation walks the IR directly
- it therefore reports all IR resources and prompts, even when the default runtime loader would skip some of them at registration time (for example non-static resources)

### 19.6 Repository deployment assets

In addition to worker-generated per-service manifests, the repository ships bootstrap and fixture deployment assets under `deploy/`.

#### 19.6.1 Local compose topology

`deploy/docker-compose.yaml` currently stands up this local multi-service topology:

- PostgreSQL
- Redis
- Temporal
- Compiler API
- Access Control
- gateway-admin-mock
- Compiler Worker
- one fixture MCP Runtime

Important current compose behavior:

- Python services run from the checked-out workspace and install `-e ".[all]"` at container start
- Compiler API runs Alembic migrations before starting uvicorn
- Access Control points at `gateway-admin-mock`
- Compiler Worker runs the supervised Celery + HTTP-shell entrypoint with `ROUTE_PUBLISH_MODE=access-control`
- worker concurrency is intentionally constrained (`CELERY_WORKER_CONCURRENCY=1`, `CELERY_WORKER_POOL=solo`)
- the standalone runtime boots from `tests/fixtures/ir/service_ir_valid.json`
- compose still provisions Temporal and passes `TEMPORAL_ADDRESS`, even though current mainline API dispatch behavior is Celery-backed rather than Temporal-backed

`deploy/docker/Dockerfile.app` is the repository's generic Python app image template:

- base image: `python:3.12-slim`
- configurable `APP_MODULE`, `APP_PORT`, and `INSTALL_EXTRAS`
- default extras: `extractors,enhancer,observability`
- copies `apps`, `libs`, `migrations`, and `tests/fixtures` into the image before installing the package

#### 19.6.2 Helm chart packaging

The checked-in Helm chart is `deploy/helm/tool-compiler`.

Current chart characteristics:

- chart type: application
- chart version: `0.1.0`
- deploys platform services plus lightweight infra

Current Helm-managed components include:

- PostgreSQL
- Redis
- Temporal
- Compiler API
- Access Control
- Compiler Worker
- optional gateway-admin-mock
- optional fixture MCP Runtime

Current chart operational details:

- migration job runs as a Helm `post-install,post-upgrade` hook
- generated secret currently carries `postgres-password`, `jwt-secret`, and `billing-secret`
- Compiler Worker gets a dedicated service account plus Role/RoleBinding to manage `configmaps`, `services`, `deployments`, and `networkpolicies`
- Compiler API and Access Control use `/healthz` for readiness/liveness in the chart, even though Access Control also implements `/readyz`
- Compiler Worker and MCP Runtime use `/readyz` for readiness and `/healthz` for liveness
- gateway-admin-mock is disabled by default

The chart's optional runtime is a fixture-style embedded runtime rather than a worker-produced artifact deployment:

- `Values.mcpRuntime.serviceIrJson` is injected directly into a ConfigMap key named `service-ir.json.gz`
- the runtime then mounts that ConfigMap at `/config`
- this is a bootstrap/demo path, not the same thing as the Compiler Worker's generated manifest flow

Important current packaging boundary:

- Helm infra is intentionally lightweight and bootstrap-oriented
- PostgreSQL storage is currently `emptyDir`
- Redis and Temporal are single-replica, non-HA deployments
- chart defaults therefore read more like dev/integration packaging than a hardened production SRE blueprint

## 20. Security and Governance Specification

### 20.1 Authentication

The system uses standard auth forms instead of bespoke schemes.

Key support areas:

- JWT-backed service auth
- personal access tokens
- bearer/basic/api-key/custom-header auth
- OAuth2 client credentials
- mTLS
- request signing

Current control-plane auth specifics include:

- Access Control accepts both JWTs and PATs; the JWT validation path is currently HS256-only
- PATs are prefixed `pat_` and stored as SHA-256 hashes
- compiler/control-plane internal route-publication calls use short-lived admin JWTs minted by Access Control authentication helpers

### 20.2 Authorization

Authorization uses policy evaluation over actor, resource, action, and semantic risk.

Default posture is deny-by-default.

Current policy evaluation is not a simple first-match scan:

- resource/action matching uses case-sensitive glob semantics
- risk is thresholded by ordered risk levels
- specificity ranking is applied before decision priority
- `deny` outranks `require_approval`, which outranks `allow`

### 20.3 Semantic risk

Risk is a first-class part of the exposed tool surface and must flow through:

- extraction
- enhancement
- validation
- Access Control
- review
- audit selection

### 20.4 Audit logging

Key control-plane actions are audit-logged, including compilation, artifact, and route-related operations.

Important currently observed audit actions include:

- `compilation.triggered`
- `compilation.retried`
- `compilation.rollback_requested`
- `artifact.created`
- `artifact.updated`
- `artifact.activated`
- `artifact.deleted`
- `pat.created`
- `pat.revoked`

Access Control persists audit entries in `auth.audit_log`.

### 20.5 Current control-plane auth posture

The implemented auth boundary is currently stronger on governance endpoints than on registry/compilation CRUD.

Authenticated today:

- workflow routes
- SSE compilation event streams
- most Access Control governance routes (the notable exception is anonymous token validation)

Currently public:

- `POST /api/v1/authn/validate`
- compilation create/list/get/retry/rollback
- artifact CRUD/diff routes
- service summary routes

This is an implementation gap that operators should treat as real until explicitly closed in code.

## 21. Real Proof and Coverage Specification

### 21.1 Proof taxonomy

The system uses several different proof layers:

- unit and integration tests
- end-to-end local compilation flow tests
- protocol-specific local runtime proofs
- live LLM-enabled protocol proofs
- real-target non-LLM live coverage audits

The proof runner currently supports these protocol families:

- `graphql`
- `rest`
- `openapi`
- `grpc`
- `jsonrpc`
- `odata`
- `scim`
- `soap`
- `sql`

### 21.1.1 Proof profiles and result contract

Current proof profiles are:

- `mock`
- `real-targets`

Each proof case carries:

- protocol
- service ID
- optional tenant/environment scope overrides
- compiler request payload
- optional concrete tool invocations
- optional preferred smoke tools
- optional audit skip tool IDs
- optional `case_id`

Each completed proof emits a `ProofResult` containing:

- `protocol`
- `service_id`
- `job_id`
- `active_version`
- `operations_enhanced`
- `llm_field_count`
- `invocation_results`
- optional `audit_summary`
- optional `tool_intent_counts`
- optional `judge_evaluation`
- optional `case_id`
- optional `error`

Important runtime-proof behavior:

- failed proof cases still emit a structured `ProofResult` with `error`
- proof runs can optionally audit all generated tools
- proof runs can optionally enable an LLM judge over produced artifacts/results
- artifact/service-catalog lookups forward tenant/environment query filters when the proof case or request payload specifies scope
- runtime Service DNS names are derived from the compiled `service_name` with the same DNS-sanitizing/versioning rules used by generic manifest generation, not from raw `service_id`

### 21.1.2 Proof fixtures and mock surfaces

The proof runner relies on explicit mock services in addition to real-target runs.

Current HTTP mock surface includes:

- REST catalog endpoints under `/rest/catalog`
- GraphQL endpoint under `/graphql`
- SOAP order service under `/soap/order-service`

Current gRPC mock surface exposes a runtime-built descriptor for:

- `catalog.v1.InventoryService/ListItems`
- `catalog.v1.InventoryService/AdjustInventory`
- `catalog.v1.InventoryService/WatchInventory`

Important current gRPC proof nuance:

- the mock gRPC server enables reflection
- the streaming proof path currently emits a small fixed event stream rather than an unbounded live feed

### 21.1.3 LLM judge contract

Optional proof/judge output is modeled explicitly rather than as free-form reviewer text.

Per-tool judge scoring currently includes:

- `accuracy`
- `completeness`
- `clarity`
- weighted `overall`
- short `feedback`

Current weighting is:

- `accuracy * 0.35`
- `completeness * 0.35`
- `clarity * 0.30`

Current judge thresholds are:

- `low_quality_threshold = 0.5`
- `quality_passed = average_overall >= 0.6`

Current operational behavior:

- enabled operations are evaluated in batches of `10`
- judge failures log warnings and return a zero-score aggregate when nothing could be parsed
- judge output is therefore advisory but structured enough for gating/reporting

### 21.2 Current proof posture

The repository has already progressed beyond a simple "can deploy a runtime" claim.

Main current confidence signals include:

- cross-protocol LLM-enabled GKE baseline for the core proven set
- enterprise-protocol local runtime proofs
- a green non-LLM real-target matrix covering 11 live services

Representative currently wired real-target cases include:

- Directus GraphQL / REST / OpenAPI
- Gitea OpenAPI
- PocketBase REST
- Jackson SCIM
- NorthBreeze OData
- aria2 JSON-RPC
- OpenFGA gRPC
- PostgreSQL SQL

### 21.3 Current real-target acceptance truth

The live `realr24all` matrix proves that all 11 current real-target runtimes are usable, but not all are coverage-complete.

Exact or near-exact parity currently exists for:

- Directus GraphQL
- Directus OpenAPI
- Gitea OpenAPI
- NorthBreeze OData
- SOAP CXF

Known structural coverage gaps remain most visible in:

- REST sources that are still collection-scoped
- aria2 JSON-RPC whole-method discovery
- OpenFGA gRPC full reflected surface
- SQL update/delete and broader semantic parity
- SCIM tenant-specific discovery nuance

### 21.4 Canonical next acceptance target

The active next planning target is **B-010: Real-Target Full Endpoint Coverage**.

That means the next major claim the system should earn is:

> not merely that all runtimes are live-usable, but that each runtime's generated tool set matches the intended visible upstream surface as closely as the protocol permits.

## 22. Testing and Quality Gates

The repository uses a multi-layer quality model:

- unit tests
- integration tests
- end-to-end tests
- proof runner scenarios
- lints and type checks
- security and dependency gates

### 22.1 Shared observability contract

The repository now contains explicit shared observability helpers rather than per-service ad hoc logging.

Current structured logging contract is single-line JSON with guaranteed fields:

- `timestamp`
- `level`
- `component`
- `logger`
- `message`

Optional structured fields currently include:

- `trace_id`
- `span_id`
- `extra`
- `exception`

Current metrics helpers expose deduplicated Prometheus primitives:

- counter
- histogram
- gauge

Histogram defaults currently use these latency buckets:

- `0.005`
- `0.01`
- `0.025`
- `0.05`
- `0.1`
- `0.25`
- `0.5`
- `1.0`
- `2.5`
- `5.0`
- `10.0`
- `30.0`

Current tracing behavior is intentionally safe-by-default:

- tracing is no-op when `OTEL_EXPORTER_ENDPOINT` is absent and local tracing is not explicitly enabled
- local/in-process spans can still be enabled via `enable_local=True`
- OTLP gRPC export honors `OTEL_EXPORTER_OTLP_INSECURE`
- helper span contexts degrade to no-op span objects when tracing is disabled

Repository-shipped dashboard assets currently include:

- `observability/grafana/runtime-dashboard.json`
- `observability/grafana/compilation-dashboard.json`

These dashboards currently expect Prometheus metrics such as:

- `mcp_runtime_tool_calls_total`
- `mcp_runtime_tool_latency_seconds_bucket`
- `mcp_runtime_upstream_errors_total`
- `mcp_runtime_circuit_breaker_state`
- `compiler_workflow_jobs_total`
- `compiler_workflow_stage_duration_seconds_bucket`
- `compiler_extractor_runs_total`
- `compiler_llm_tokens_total`

Important current repository boundary:

- checked-in Grafana dashboard JSONs exist
- checked-in `observability/otel` and `observability/prometheus` directories are currently empty/placeholder rather than carrying a complete local collector/scrape stack

### 22.2 Toolchain direction

Current toolchain direction in mainline includes:

- `uv`
- `nox`
- `basedpyright`
- `ruff`
- `pre-commit`
- `gitleaks`

The repository policy requires secret scanning before push.

## 23. Web UI Specification

The repository includes a web UI under `apps/web-ui`.

This specification treats the UI as an operator-facing control-plane companion, not a future polish item.

### 23.1 UI architecture

Current UI implementation is a substantial Next.js App Router application rather than a placeholder shell.

Current stack includes:

- Next.js 16
- React 19
- Zustand for client/auth/workflow state
- TanStack Query for server-state fetching/mutation
- a custom typed fetch client in `src/lib/api-client.ts`
- App Router route groups for authenticated dashboard pages and auth pages

Current client-facing environment contract:

- `NEXT_PUBLIC_COMPILER_API_URL` (defaults to `http://localhost:8000`)
- `NEXT_PUBLIC_ACCESS_CONTROL_URL` (defaults to `http://localhost:8001`)

Authentication state is persisted client-side:

- bearer/PAT token is stored in local storage
- dashboard routes are wrapped by an auth guard
- unauthenticated users are redirected to `/login`

### 23.2 Current route surface

Implemented or scaffolded operator routes currently include:

- `/`
- `/compilations`
- `/compilations/new`
- `/compilations/[jobId]`
- `/services`
- `/services/[serviceId]`
- `/services/[serviceId]/review`
- `/services/[serviceId]/versions`
- `/gateway`
- `/pats`
- `/policies`
- `/audit`
- `/observe`
- `/login`

### 23.3 API integration contract

The web UI consumes both Compiler API and Access Control API surfaces.

Current typed client modules include:

- compilation API
- service API
- artifact API
- workflow API
- auth API
- policy API
- audit API
- gateway API

Current auth propagation behavior:

- standard fetch requests inject `Authorization: Bearer <token>`
- SSE requests append `token=<bearer>` to the URL because browser `EventSource` cannot set custom auth headers

### 23.4 Current operator workflows

The UI already implements several non-trivial end-to-end workflows.

Current compilation workflow UX includes:

- a 4-step wizard for source input, protocol/options, auth configuration, and final review
- support for URL, pasted content, and uploaded file sources
- runtime-mode, tenant/environment, and skip-enhancement controls
- auth-config capture for bearer/basic/api-key/custom-header/OAuth2 shapes
- redirect into live job detail after submission

Current compilation detail UX includes:

- stage timeline rendering
- persisted event log rendering
- live SSE-driven progress updates
- retry/rollback actions when allowed by job state

Current service/review UX includes:

- service summary and version history views
- workflow state display and transitions
- per-operation review notes
- Monaco-backed IR editing
- version diff visualization

Current workflow UI mirrors backend transitions and also coordinates side effects such as artifact activation and gateway sync/rollback when publish/deploy actions occur.

Current governance UX also includes:

- PAT create/list/revoke with one-time plaintext token display
- gateway route sync/delete/rollback interactions

### 23.5 Current completion level

The UI is not uniformly complete across every operator surface.

Current maturity split is:

- core flows such as auth, compilations, services, review, PATs, and gateway route management are materially implemented
- secondary surfaces such as policies, audit, and observe exist in navigation but remain less mature/scaffolded than the core flows

The UI is not the source of truth; it consumes control-plane APIs.

## 24. Planned Expansion Beyond Mainline

The v3 expansion documents still matter, but they should now be read as **forward extensions on top of a far more mature platform** than the original planning assumed.

### 24.1 Stream A: CLI support

Still a valid future expansion.

Intended outcome:

- treat command-line interfaces as compilable service surfaces
- model execution context explicitly
- expose CLI commands as governed tools

### 24.2 Stream B: AsyncAPI and event systems

Still a valid future expansion.

Intended outcome:

- parse AsyncAPI
- expose observable/publishable event interactions
- bridge broker-backed systems into bounded MCP surfaces

### 24.3 Stream C: enterprise protocols

This stream is already substantially delivered in mainline for extraction and runtime support.

### 24.4 Stream D: IR evolution

This stream is partially delivered:

- IR models for resources and prompts exist
- runtime registration exists
- generator helpers exist

But fully automatic pipeline population remains incomplete.

### 24.5 Stream E: protocol deepening

This stream is partially delivered:

- error normalization exists
- example generation exists
- drift detection exists

But the depth of these capabilities still needs extension, especially around exhaustive drift comparison and endpoint coverage hardening.

## 25. Known Current Gaps

The unified spec explicitly records these important current gaps.

### 25.1 Endpoint parity is incomplete on some live targets

The system is live-usable across the real-target matrix, but not all protocol targets are yet surface-complete.

### 25.2 Resource and prompt auto-population is not yet a default production behavior

IR and runtime support the surfaces, but the default production enhancement path does not yet auto-generate them.

### 25.3 Drift detection is partial

It should not yet be treated as a comprehensive capability-diff engine.

### 25.4 Dynamic resources are modeled but not registered by default

Runtime registration currently skips non-static resources.

### 25.5 Live proof coverage is uneven by protocol

Core protocol families have live proof and LLM E2E coverage; enterprise protocols currently rely on local runtime proof rather than equivalent live LLM proof.

### 25.6 Control-plane authentication is only partially enforced

Workflow and SSE routes are authenticated, but compilation/artifact/service CRUD routes are still public.

### 25.7 Readiness signals are uneven across services

- Compiler API currently has `/healthz` only
- worker `/readyz` reports config completeness, not external dependency reachability

### 25.8 Some deployment/storage metadata remains weakly validated

Fields such as `route_config`, `deployment_revision`, and artifact `storage_path` are persisted and operationally meaningful, but they are not yet strongly schema-validated end to end.

### 25.9 Checked-in deployment packaging is bootstrap-oriented

The repository ships useful compose/Helm assets, but they currently use dev-style defaults and lightweight persistence/HA assumptions rather than a fully hardened production topology.

## 26. Acceptance Criteria for This Unified Spec

The system described here is considered aligned with the repository if the following remain true.

### 26.1 Structural criteria

- compilation remains centered on `ServiceIR`
- the 9-stage workflow remains the canonical compilation path
- generic runtime remains the default deployment model
- artifact versioning, diffing, and review remain first-class

### 26.2 Governance criteria

- semantic risk remains part of the operational contract
- Access Control remains standard-auth-based and default-deny
- safe-subset audit remains distinct from full-surface parity claims

### 26.3 Runtime criteria

- tools are registered from enabled operations
- resource and prompt registration remain supported
- protocol-specific adapters remain explicit rather than hidden behind lossy generic behavior

### 26.4 Roadmap criteria

- future claims about coverage must be validated against machine-readable truth sources
- `B-010` endpoint-parity work must not regress already exact-parity protocols

## 27. Implementation Anchors

The most important code anchors for this specification are:

- `apps/compiler_worker/activities/production.py`
- `apps/compiler_worker/models.py`
- `apps/compiler_worker/main.py`
- `apps/compiler_worker/celery_app.py`
- `apps/compiler_worker/entrypoint.py`
- `apps/compiler_api/main.py`
- `apps/compiler_api/dispatcher.py`
- `apps/compiler_api/route_publisher.py`
- `apps/compiler_api/routes/*.py`
- `apps/access_control/main.py`
- `apps/access_control/authn/service.py`
- `apps/access_control/authz/service.py`
- `apps/access_control/gateway_binding/service.py`
- `apps/access_control/**/routes.py`
- `apps/mcp_runtime/main.py`
- `apps/mcp_runtime/loader.py`
- `apps/mcp_runtime/proxy.py`
- `apps/proof_runner/live_llm_e2e.py`
- `apps/gateway_admin_mock/main.py`
- `apps/web-ui/src/**`
- `libs/db_models.py`
- `libs/ir/models.py`
- `libs/extractors/*.py`
- `libs/enhancer/*.py`
- `libs/observability/*.py`
- `libs/validator/*.py`
- `tests/e2e/test_full_compilation_flow.py`
- `tests/integration/test_large_surface_pilot.py`
- `tests/integration/test_black_box_validation.py`
- `tests/integration/test_mcp_runtime_odata.py`
- `tests/integration/test_mcp_runtime_scim.py`
- `tests/integration/test_mcp_runtime_jsonrpc.py`
- `tests/integration/test_mcp_runtime_resources_prompts.py`

## 28. Relationship to the Original SDD

The original SDD remains valuable as the foundational redesign document, especially for:

- why the rebuild was justified
- why IR persistence matters
- why the generic runtime default matters
- why semantic governance replaced syntactic heuristics

However, it is no longer sufficient as the single system spec because the repository has since added:

- post-SDD runtime and protocol completion
- live proof infrastructure
- black-box validation and audit models
- enterprise protocol support
- MCP resource and prompt support
- drift detection
- real-target coverage planning

This unified spec should therefore be treated as the authoritative current system baseline.

## 29. Quality Improvement Track (Q-series)

### 29.1 Purpose

Code quality audit on `2026-03-31` identified six systemic weaknesses below enterprise baseline. This track defines targeted, low-risk improvements that raise engineering discipline without altering architecture or feature scope.

Baseline metrics (2026-03-31):

| Metric | Value |
| --- | --- |
| Ruff lint errors | 85 (62 E501, 18 I001, 2 F841, misc) |
| Unformatted files | 65 / 279 total |
| Test coverage | 54.1% (15,428 / 28,493 lines) |
| Broad `except Exception` | 33 production files, 74 occurrences |
| Functions missing return type annotation | 394 / 4,154 (~9.5%) |
| `Any` type usage | 145 instances across libs/ and apps/ |
| Test runnability issues | 4 collection errors (JWT config), 4 unit test failures |
| REST extractor size | 1,555 lines |

Target metrics:

| Metric | Target |
| --- | --- |
| Ruff lint errors | 0 |
| Unformatted files | 0 |
| Test coverage | ≥ 70% |
| Broad `except Exception` in production | < 10 justified, all logged and documented |
| Functions missing return type | < 50 (test helpers excluded) |
| Test runnability | 0 collection errors, 0 unit test failures |
| REST extractor size | < 800 lines |

### 29.2 Working rules

- Every Q-task must leave `ruff check .` and `pytest` green after completion.
- Q-tasks must not change feature behavior; only improve code hygiene, type safety, and test robustness.
- Exception handling changes must preserve existing semantics.
- Refactoring tasks must have full test coverage before extracting modules.

### 29.3 Task definitions

#### Q-001: Lint and format cleanup

Status: complete (2026-03-31)

Scope: Fix all ruff lint errors (85 → 0), format all files (65 unformatted → 0).

#### Q-002: Fix test runnability

Status: pending

Scope: Add `ACCESS_CONTROL_JWT_SECRET` default in `tests/conftest.py` for test collection. Fix 4 failing tests in `apps/compiler_api/tests/test_init_main_uncovered.py`. Exit: `pytest -q` = all pass, 0 errors, 0 collection errors.

#### Q-003: Exception handling hardening

Status: pending

Scope: Replace 74 broad `except Exception` with specific exceptions across 33 production files. Add logging and `# broad-except: <reason>` comment for justified catches. Target: < 10 justified broad catches remaining.

#### Q-004: Type annotation completion

Status: pending

Scope: Add return type annotations to public functions in libs/ and apps/. Replace `dict[str, Any]` with specific `TypedDict` or narrower types where schema is known. Fix gRPC files excluded from pyright. Target: < 50 missing returns, < 80 `Any` usages.

#### Q-005: Unit test coverage boost to 70%

Status: pending

Scope: Add unit tests for extractor helpers, validator logic, enhancer pipeline. Add negative tests. Target: overall coverage ≥ 70%.

#### Q-006: Extractor complexity reduction

Status: pending

Scope: Split REST extractor into `rest.py` + `rest_probing.py` + `rest_classification.py` + `rest_schema.py`. Consolidate duplicated `_get_content()` and content hash utilities into `libs/extractors/base.py`. Target: `rest.py` < 800 lines.

### 29.4 Dependency graph

```
Q-001 (lint/format) ✅
  ├── Q-002 (test runnability)
  │     ├── Q-003 (exception handling)
  │     ├── Q-005 (test coverage boost)
  │     │     └── Q-006 (extractor refactor)
  │     └── Q-004 (type annotations)
  └── Q-004 (type annotations)
```

### 29.5 Exit criteria

The Q-series is complete when:

- `ruff check .` = 0 errors
- `ruff format --check .` = 0 reformats
- `pytest -q` = all pass, 0 errors
- coverage ≥ 70%
- broad `except Exception` < 10 (all documented)
- missing return types < 50
- REST extractor < 800 lines
- `agent.md` and `devlog.md` updated with Q-series completion status
