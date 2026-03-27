# Post-SDD Modular Expansion Plan

## Purpose

The original SDD backlog (`T-001` through `T-033`) is complete. This document defines the modular expansion track that follows the SDD without collapsing unrelated capabilities into one branch of work.

The goal is not "support everything at once." The goal is to grow protocol coverage and runtime capability while preserving the existing compiler/control-plane architecture, keeping tasks independently testable, and maintaining green repo-wide quality gates.

## Working Rules

- Every expansion module must fit through the existing pipeline shape: `detect -> extract -> normalize to ServiceIR -> validate -> deploy/runtime -> publish`.
- New modules should reuse existing IR, workflow, registry, validation, and observability contracts unless a documented schema change is required.
- Each module must define explicit non-goals so unsupported capability is visible instead of implied.
- Unsupported features must fail explicitly and observably; they must not silently degrade into incorrect compiled output.
- New protocol support should land in "foundation" slices first, then runtime/data-plane slices second.

## Module Map

### Track A: Protocol Extraction

#### H-001: gRPC Proto Extraction Foundation

Status: complete

Scope:
- Detect `.proto` files
- Extract unary RPC methods
- Normalize request fields into `ServiceIR` params
- Record deferred streaming RPCs as unsupported metadata

Out of scope:
- Reflection
- Streaming runtime execution
- grpc-gateway transcoding

#### H-002: SOAP / WSDL Extraction Foundation

Status: complete

Scope:
- Detect WSDL 1.1 documents
- Extract services, bindings, request/response operations
- Map simple XSD field types into `ServiceIR`
- Preserve SOAP metadata such as target namespace, port type, binding, endpoint, and action

Out of scope:
- Full SOAP runtime execution
- WS-Security
- Complex XML serialization corner cases

#### H-003: Streaming/Event Protocol Descriptors

Status: complete

Scope:
- Add typed `ServiceIR.event_descriptors` support plus transport enums for WebSocket, SSE upstream, webhook callbacks, GraphQL subscriptions, gRPC streaming RPCs, and async event contracts
- Normalize transport shape and unsupported runtime constraints into structured IR fields instead of only loose metadata
- Reject false runtime-support claims during pre-deploy validation until runtime support is explicitly approved per transport

Out of scope:
- Full event-driven runtime execution in the first slice

### Track B: Runtime/Data-Plane Capability

#### H-004: Multipart, Binary, and Async Job Foundations

Status: complete

Scope:
- Multipart upload proxying
- Binary-safe request/response handling
- Async job polling patterns

Out of scope:
- Arbitrary bidirectional stream execution in the first slice

#### H-005: Full Streaming Runtime Support

Status: complete

Scope:
- Approved HTTP-native streaming transport handling for `sse` and `websocket`
- Runtime-side backpressure and lifecycle management via bounded event/message collection
- Explicit rejection of unsupported transport combinations and non-approved transports such as native `grpc_stream`

Out of scope:
- Protocol-specific UI affordances
- Native gRPC bidirectional data-plane support inside the generic HTTP runtime

### Track C: Authentication Capability

#### H-006: Advanced Auth Schema and Validation

Status: complete

Scope:
- OAuth2 client credentials
- mTLS references
- Request-signing metadata
- Runtime secret/credential validation

Out of scope:
- Every vendor-specific auth scheme in one pass

### Track D: Conformance and Hardening

#### H-007: Messy-Spec Conformance Corpus

Status: complete

Scope:
- Dirty OpenAPI fixtures
- Proto/WSDL edge fixtures
- Auth-heavy fixtures
- Explicit pass/fail/unsupported expectations

#### H-008: Live Gateway / Rollout Hardening

Status: complete

Scope:
- Real gateway route publication
- Reconciliation drift correction against live control plane
- APISIX / rollout smoke tests on GKE

Current progress:
- Local route publication, rollback restoration, and reconciliation are covered in integration and contract tests
- A minimal live GKE gateway smoke harness proves real route sync plus drift reconciliation without depending on the full compiler/runtime stack
- The same live harness now also proves forward rollout of the stable route target to `v2` and rollback back to `v1` on GKE through the APISIX-style control-plane path

## Recommended Order

The current post-SDD modular expansion track is complete (`H-001` through `H-008`).

## Follow-On Roadmap

The next track is intentionally ordered and should not be parallelized until each item has a written status update in `agent.md` and `devlog.md`.

### R-001: Real DeepSeek Endpoint Validation

Status: complete

Scope:
- Validate the enhancer/runtime path against the official DeepSeek API endpoint with a real provider response
- Use the VM-local key at `/home/guoxy/esoc-agents/.deepseek_api_key` only as runtime input, never as repository state
- Prove one end-to-end success path and one failure-path surface for provider errors or invalid credentials
- Prove the same provider path inside the real compile/deploy/register workflow rather than only through a local harness

Completed implementation notes:
- `libs/enhancer/enhancer.py` now exposes an explicit `deepseek` provider with provider defaults and configurable OpenAI-compatible base URL handling
- `scripts/validate_deepseek_enhancer.py` provides a reproducible local validation harness for the real provider path
- `deploy/helm/tool-compiler/templates/apps.yaml` plus `values.yaml` now allow secret-backed `compiler-worker` env injection, and `migration-job.yaml` supports an independent migration image override for live GKE runs
- `deploy/docker/Dockerfile.app` now installs `.[extractors,enhancer,observability]` by default so the deployed worker carries the real provider client dependency
- `EnhancerConfig.from_env()` now trims secret-file whitespace, fixing the live-cluster trailing-newline secret case
- The success path and invalid-key authentication failure path were both exercised successfully against the official DeepSeek endpoint on `2026-03-25`
- A full live Helm/GKE compile proof also succeeded on `2026-03-25` in namespace `tool-compiler-gke-test-r13`: job `23de5a4b-48be-4d9b-b127-2d1c928a74f9` compiled service `deepseek-live-r13-r16`, the worker logged a real DeepSeek `chat/completions` `200`, compilation events persisted `operations_enhanced=2`, and the generated IR artifact contained `source: "llm"` descriptions before deployment, runtime validation, route sync, and service registration completed

Out of scope:
- General multi-provider benchmarking
- Long-lived credential storage changes inside the repository

### R-002: Live Gateway/Data-Plane Hardening Beyond The Mock Control Plane

Status: complete

Scope:
- Move beyond the gateway-admin mock proof and validate live route publication/reconciliation semantics deeper in the real data plane
- Extend smoke coverage around route drift, rollout safety, and failure visibility in the deployed path
- Keep GKE-based validation authoritative for this track

Completed implementation notes:
- `apps/gateway_admin_mock/main.py` now exposes a forwarded gateway data-plane path backed by published route documents, with active-route and pinned-route selection plus explicit `404` / `502` failure surfaces
- `tests/integration/test_access_control_gateway_binding.py` now proves stable-route forwarding, version pinning, drift failure visibility, reconcile recovery, rollout, and rollback semantics through that data-plane path
- `scripts/smoke-gke-gateway-routes.sh` now deploys lightweight versioned runtime services, validates forwarded gateway responses on GKE, and supports a dedicated `MIGRATION_IMAGE` override for reliable live execution
- The authoritative live proof succeeded on `2026-03-25` in namespace `tool-compiler-gateway-smoke-r7`, where active traffic rolled forward to `v2`, rolled back to `v1`, and pinned `v2` traffic stayed on the versioned runtime throughout

Out of scope:
- Broad redesign of the control-plane model
- Unrelated protocol expansion

### R-003: Native `grpc_stream` Expansion Track

Status: complete

Scope:
- Define a non-HTTP-native runtime/data-plane path for real gRPC streaming support
- Preserve the current explicit rejection semantics until native support exists end to end
- Add dedicated extraction/runtime/validation boundaries instead of overloading the current HTTP streaming slice

Completed implementation notes so far:
- `libs/ir/models.py` now has typed `GrpcStreamRuntimeConfig` and `GrpcStreamMode`, making native gRPC stream contracts explicit in the IR
- `libs/extractors/grpc.py` now emits structured native stream config for streaming RPC descriptors while keeping them unsupported by default
- `apps/mcp_runtime/proxy.py` now has a dedicated `GrpcStreamExecutor` seam, so native gRPC streaming no longer shares the HTTP-native `sse` / `websocket` code path
- `apps/mcp_runtime/grpc_stream.py` now provides a concrete reflection-backed server-stream executor for native gRPC streaming
- `apps/mcp_runtime/main.py` can now auto-wire that executor behind `ENABLE_NATIVE_GRPC_STREAM`, while still preserving the default explicit-rejection behavior when the flag is absent
- `libs/validator/post_deploy.py` now validates streaming transport shape for supported event descriptors, including native `grpc_stream`
- `libs/validator/pre_deploy.py` now distinguishes native `grpc_stream` enablement from approved HTTP-native streaming and keeps default compilation behavior in explicit-rejection mode unless native support is turned on on purpose
- `tests/integration/test_mcp_runtime_grpc_stream.py` now covers both the opt-in runtime auto-wiring path and the concrete reflection-backed executor behavior against real protobuf descriptors
- `libs/validator/tests/test_post_deploy.py` and `tests/integration/test_streamable_http_tool_invoker.py` now cover post-deploy validation of native `grpc_stream` through both direct runtime calls and the production streamable HTTP invoker path
- `apps/mcp_runtime/grpc_stream.py` now primes the reflected service descriptor before method lookup, matching the live reflection-backed execution path
- `scripts/smoke-gke-grpc-stream.sh` now provides a reproducible live GKE proof harness that deploys a runtime plus reflection-enabled upstream mock and validates the native grpc result shape through `PostDeployValidator`
- The authoritative live proof completed on `2026-03-25` in namespace `tool-compiler-grpc-stream-smoke-r1` with runtime image `20260325-b0e27e6-r19`, returning `status="ok"`, `transport="grpc_stream"`, and a single reflected protobuf event for `watchInventory`

Future follow-on if this track expands again:
- Extend beyond the current server-stream slice if client-stream or bidirectional modes are declared supported later

Out of scope:
- Pretending the existing `sse` / `websocket` runtime path is sufficient for native gRPC streaming
- UI-specific streaming affordances

The `R-001` through `R-003` roadmap is now complete. The next ordered work is a protocol-completion track that fills the remaining runtime/data-plane gaps before protocol-wide `LLM-enabled E2E` proofs are attempted.

## Protocol Completion Roadmap

### P-001: GraphQL Runtime/Data-Plane Completion

Status: complete

Scope:
- Add a typed GraphQL execution contract to the IR instead of relying on loose metadata
- Make the extractor emit executable GraphQL documents plus safe default selection sets
- Make the runtime serialize GraphQL requests as `{query, variables, operationName}` and treat `200` responses with GraphQL `errors` as failures
- Prove the GraphQL runtime path through integration and post-deploy validation coverage

Completed implementation notes:
- `libs/ir/models.py` now exposes `GraphQLOperationConfig` and `GraphQLOperationType` on `Operation`
- `libs/extractors/graphql.py` now emits typed GraphQL operation config, executable documents, default scalar-safe selection sets, and a normalized origin-level `base_url`
- `apps/mcp_runtime/proxy.py` now serializes GraphQL requests correctly, unwraps successful GraphQL `data`, and fails explicitly when a GraphQL response carries `errors`
- `tests/integration/test_mcp_runtime_proxy.py` and `libs/validator/tests/test_post_deploy.py` now prove the GraphQL runtime and validator path locally

Out of scope:
- GraphQL subscriptions
- Persisted queries
- GraphQL-over-WebSocket transports

### P-002: REST Discovered-API Runtime Hardening

Status: complete

Scope:
- Harden discovered REST base URL / path normalization so runtime execution matches extracted shape
- Tighten sample invocation heuristics and request-shaping for discovered operations
- Add protocol-specific runtime/post-deploy coverage for classifier-driven REST extraction output

Completed implementation notes:
- `libs/extractors/rest.py` now preserves the discovery entrypoint base path in `ServiceIR.base_url` instead of collapsing everything to the origin
- Classifier-emitted REST paths are now normalized back to runtime-relative paths, preventing duplicated base-path prefixes when the discovery entrypoint lives under a subtree such as `/catalog`
- Discovered REST query literals now flow into `Param.default`, improving sample invocation realism for validator and worker-generated smoke calls
- Discovered write operations now carry `body_param_name="payload"` explicitly
- `tests/integration/test_mcp_runtime_proxy.py` now proves runtime execution against a discovered REST service rooted under a non-root base path, and `tests/integration/test_compiler_worker_activities.py` now proves discovery defaults survive into generated sample invocations

Out of scope:
- Broad crawler redesign
- Vendor-specific discovery plugins in the first slice

### P-003: Native gRPC Unary Runtime Completion

Status: complete

Scope:
- Add a dedicated native unary gRPC execution path rather than routing unary RPCs through the HTTP proxy
- Extend validation and runtime wiring to support native unary gRPC semantics explicitly
- Keep non-proven client-stream and bidirectional modes outside scope

Completed implementation notes:
- Added `GrpcUnaryRuntimeConfig` to the IR and emitted native unary metadata from `libs/extractors/grpc.py`
- Added `apps/mcp_runtime/grpc_unary.py` with a reflection-backed `ReflectionGrpcUnaryExecutor`
- Wired native unary execution into `apps/mcp_runtime/main.py` and `apps/mcp_runtime/proxy.py` behind `ENABLE_NATIVE_GRPC_UNARY`
- Extended `libs/validator/pre_deploy.py`, `libs/validator/post_deploy.py`, `libs/generator/generic_mode.py`, and `apps/compiler_worker/activities/production.py` so validation, manifest generation, and worker-side validation all understand native unary gRPC
- Added proof coverage in `tests/integration/test_mcp_runtime_grpc_unary.py`, `tests/integration/test_compiler_worker_activities.py`, `libs/generator/tests/test_generic_mode.py`, `libs/validator/tests/test_pre_deploy.py`, and `libs/validator/tests/test_post_deploy.py`

Out of scope:
- Pretending unary support implies complete gRPC support
- Non-reflection bootstrap paths in the first slice

### P-004: SOAP / WSDL Runtime Execution

Status: complete

Scope:
- Add a SOAP runtime adapter that builds envelopes, sends the correct SOAP action metadata, and parses XML responses/errors
- Add validator/runtime tests that prove the extracted SOAP metadata is sufficient for execution

Completed implementation notes:
- Added `SoapOperationConfig` to the IR so extracted SOAP operations carry typed runtime metadata
- Upgraded `libs/extractors/soap.py` to emit target namespace, element names, and SOAP action metadata while rejecting non-document/non-literal bindings
- Upgraded `apps/mcp_runtime/proxy.py` to build SOAP envelopes, send `SOAPAction`, parse SOAP XML bodies, and surface SOAP Faults explicitly
- Added proof coverage in `tests/integration/test_mcp_runtime_proxy.py`, `libs/validator/tests/test_pre_deploy.py`, `libs/validator/tests/test_post_deploy.py`, `libs/extractors/tests/test_soap.py`, and `libs/ir/tests/test_models.py`

Out of scope:
- Full WS-Security
- Exhaustive XML schema corner cases in the first slice

### P-005: SQL Execution/Runtime Completion

Status: complete

Scope:
- Define the supported SQL runtime contract explicitly instead of leaving SQL in extractor-only limbo
- Implement the chosen safe execution model plus validation/runtime coverage, or explicitly lock SQL to compile-only until that model exists

Completed implementation notes:
- Added `SqlOperationConfig`, `SqlRelationKind`, and `SqlOperationType` to the IR
- Upgraded `libs/extractors/sql.py` so extracted query/insert operations emit typed SQL runtime metadata and safe default `limit` semantics
- Added `apps/mcp_runtime/sql.py` with the native `SQLRuntimeExecutor` for bounded query and insert execution against SQLite/PostgreSQL async URLs
- Wired SQL execution into `apps/mcp_runtime/main.py` and `apps/mcp_runtime/proxy.py`
- Added proof coverage in `tests/integration/test_mcp_runtime_sql.py`, `libs/validator/tests/test_post_deploy.py`, `libs/extractors/tests/test_sql.py`, and `libs/ir/tests/test_models.py`

Out of scope:
- Arbitrary ad hoc SQL execution with no policy boundary
- Pretending schema extraction alone is equal to runtime support

### P-006: Cross-Protocol Validator and Capability-Matrix Hardening

Status: complete

Scope:
- Add an explicit protocol support matrix covering `extract / compile / runtime / live proof / llm-e2e`
- Make sample-invocation building and post-deploy validation protocol-aware where the generic defaults are insufficient
- Ensure every protocol advertises unsupported boundaries explicitly before the final proof track starts

Completed implementation notes:
- Added `libs/validator/capability_matrix.py` as the machine-readable support matrix
- Upgraded `apps/compiler_worker/activities/production.py` so GraphQL and SQL sample invocation generation is protocol-aware
- Upgraded `libs/validator/post_deploy.py` so invocation smoke prefers safer read/query operations over mutation/insert alternatives when multiple tools are available
- Added proof coverage in `libs/validator/tests/test_capability_matrix.py`, `tests/integration/test_compiler_worker_activities.py`, and `libs/validator/tests/test_post_deploy.py`

Current capability matrix:

| Protocol | Extract | Compile | Runtime | Live proof | LLM-enabled E2E |
|----------|---------|---------|---------|------------|-----------------|
| OpenAPI | ✅ | ✅ | ✅ | ✅ | ✅ |
| REST discovery | ✅ | ✅ | ✅ | ✅ | ✅ |
| GraphQL | ✅ | ✅ | ✅ | ✅ | ✅ |
| gRPC unary | ✅ | ✅ | ✅ | ✅ | ✅ |
| gRPC server-stream | ✅ | ✅ | ✅ | ✅ | ✅ |
| SOAP / WSDL | ✅ | ✅ | ✅ | ✅ | ✅ |
| SQL | ✅ | ✅ | ✅ | ✅ | ✅ |

All individually supported protocol slices now have both local `LLM-enabled E2E` proof and a live GKE proof, and the authoritative joint `PROTOCOL=all` rerun also succeeded in namespace `tool-compiler-llm-all-024755`, returning successful GraphQL, REST, gRPC, SOAP, and SQL proof records with real DeepSeek-backed enhancement evidence.

Out of scope:
- New protocol families
- UI/reporting polish

## Final Proof Roadmap

The final proof track exists to demonstrate that every supported protocol family can complete a real `LLM-enabled E2E` path once its runtime/data-plane slice is already complete.

### L-001: OpenAPI `LLM-enabled E2E` Baseline

Status: complete

Scope:
- Keep the existing OpenAPI + DeepSeek full compile/deploy/register proof as the baseline reference implementation for this roadmap

### L-002: GraphQL `LLM-enabled E2E`

Status: complete

Dependencies:
- P-001

### L-003: REST `LLM-enabled E2E`

Status: complete

Dependencies:
- P-002

### L-004: gRPC `LLM-enabled E2E`

Status: complete

Dependencies:
- The proven native unary/server-stream grpc slices from `P-003` and `R-003`

### L-005: SOAP / WSDL `LLM-enabled E2E`

Status: complete

Dependencies:
- P-004

### L-006: SQL `LLM-enabled E2E`

Status: complete

Dependencies:
- P-005

Roadmap close-out:
- The final proof roadmap is now fully complete, including the joint GKE `PROTOCOL=all` rerun in namespace `tool-compiler-llm-all-024755`
- The only follow-on from that run was a startup hardening improvement: `apps/compiler_worker/entrypoint.py` now waits for broker reachability plus Celery readiness before exposing worker HTTP readiness
- That hardening has now also been published and revalidated through a clean GKE rerun in namespace `tool-compiler-llm-all-031802` using `compiler-worker:20260326-b0e27e6-r24`, with no manual worker restart needed for the first GraphQL compile
- Further work from this point should be treated as production hardening, repeatability, or new capability expansion rather than unfinished proof coverage

## Black-Box API Exploration Roadmap

Purpose:

The current platform is now strongly proven for spec-first / contract-first inputs, but the next confidence gap is black-box API coverage: discovering endpoints from incomplete or absent specs, converting them into stable MCP tools, and proving those generated tools work under real invocation rather than only representative smoke calls.

Current starting point:

- The earlier clean structure baseline in namespace `tool-compiler-llm-all-031802` exposed `18` enabled operations across GraphQL, REST discovery, gRPC, SOAP, and SQL, and the deployed runtimes' `/tools` endpoints matched those enabled operations exactly (`18 / 18`)
- The current authoritative audit-enabled baseline is namespace `tool-compiler-llm-all-audit-075849`, which returned aggregate `discovered=13`, `generated=13`, `audited=7`, `passed=7`, `failed=0`, `skipped=6`
- The difference between the earlier structure total `18` and the current audit total `13` is expected: `B-002` removed five fake REST endpoints that had previously inflated the structural count
- Behavior-level coverage still trails structure-level coverage because the current skip policy intentionally leaves some generated tools unaudited
- The strongest current confidence remains spec-first inputs; REST discovery is the least mature semantics-recovery path and should be treated as the primary black-box hardening target

### B-001: Generated-Tool Audit Coverage

Status: in progress

Scope:
- Add a machine-readable coverage report for one compiled service that records `discovered/endpoints`, `generated/tools`, `audited/tools`, `passed`, `failed`, and `skipped`
- Extend the proof/validation path so it can iterate over all safe generated tools instead of only one representative smoke tool
- Make unsupported or unaudited tools explicit rather than silently counting them as success

Implemented in the first slice:
- `apps.proof_runner.live_llm_e2e` now exposes an opt-in `--audit-all-generated-tools` mode
- The proof runner emits an `audit_summary` block with `discovered_operations`, `generated_tools`, `audited_tools`, `passed`, `failed`, `skipped`, and per-tool results
- The audit reuses shared `build_sample_invocations(...)` inputs, reuses representative proof results when they already exist, and fails tools that are missing from runtime `/tools`
- The first policy intentionally skips `writes_state`, `destructive`, and `external_side_effect` tools instead of executing them by default
- `scripts/smoke-gke-llm-e2e.sh` now accepts `AUDIT_ALL_GENERATED_TOOLS=1` to pass the audit flag through the live GKE harness
- The first live audit baseline has now been captured on REST discovery in namespace `tool-compiler-llm-rest-audit-041525`: `discovered=6`, `generated=6`, `audited=6`, `passed=1`, `failed=5`, `skipped=0`
- That baseline proved the audit path itself works and also produced concrete black-box evidence that REST discovery still emits semantically wrong canonicalized paths in some cases (`/rest/active`, `/rest/detail`, `/rest/games`, `/rest/{item_id}`, `/rest/Puzzle Box`)

Remaining implementation tasks:
- Extend the new audit mode through live GKE across the remaining proven protocol slices beyond the first REST baseline
- Decide whether the representative `6 / 18` proof path should remain alongside the audit or collapse into a stricter audit-driven gate later
- Feed the concrete REST audit failures directly into `B-002` canonicalization and naming hardening, because the first live audit already exposed real discovery defects rather than hypothetical ones

Implemented in the second slice (completed `2026-03-26`):
- Extracted shared audit types (`ToolAuditResult`, `ToolAuditSummary`) into `libs/validator/audit.py`
- Added `AuditPolicy` with configurable skip rules: `skip_destructive`, `skip_external_side_effect`, `skip_writes_state`, and `allow_idempotent_writes` for safe mutations
- Added `AuditThresholds` with `min_audited_ratio`, `max_failed`, and `min_passed` regression expectations, plus `check_thresholds()` verification helper
- `PostDeployValidator` now exposes `validate_with_audit()` for full generated-tool audit alongside standard validation
- `apps/proof_runner/live_llm_e2e.py` now uses shared types and `AuditPolicy` instead of inline definitions
- Quality gates green (ruff, mypy, 339 passed)

Out of scope:
- Default execution of dangerous or destructive tools just to improve a headline coverage number
- Claiming blanket endpoint completeness before a ground-truth comparison exists

### B-002: Black-Box REST Discovery Hardening

Status: complete for the current catalog/audit slice (first slice live-proven on GKE, follow-up regression fix live-revalidated, and cross-protocol audit baseline captured)

Scope:
- Improve discovered REST endpoint canonicalization, deduplication, and tool naming so literal path fragments and crawl artifacts leak less often into generated MCP tool IDs
- Harden parameter/default inference for discovered operations so generated sample invocations are more realistic
- Add messy-discovery fixtures and regression coverage specifically for undocumented or partially documented REST services

First slice completed on `2026-03-26`:
- Root cause: `_extract_from_json` promoted plain JSON response body values (e.g., `"active"`, `"Puzzle Box"`) as discovered endpoints via `urljoin`
- Added `_is_path_like()` filter so plain JSON value words are rejected during discovery
- Added `_coalesce_sibling_endpoints()` defense-in-depth for HTML-discovered sibling deduplication
- Added 2 regression tests reproducing the live audit failure and the sibling coalescing case
- Quality gates green (ruff, mypy, 316 passed)

Live GKE verification on `2026-03-26`:
- Published `compiler-worker:20260326-b0e27e6-r27` and `compiler-api:20260326-b0e27e6-r27`
- Ran `PROTOCOL=rest AUDIT_ALL_GENERATED_TOOLS=1` in namespace `tool-compiler-b002-rest-061245`
- Before fix: `discovered=6`, `generated=6`, `audited=6`, `passed=1`, `failed=5`
- After fix: `discovered=1`, `generated=1`, `audited=1`, `passed=1`, `failed=0`

Follow-up regression fix completed locally on `2026-03-26`:
- Root cause: the first `_is_path_like()` hardening became too strict and stopped discovering legitimate link-like relative JSON paths such as `users/123/orders`; sibling coalescing also dropped shared query defaults such as `?view=detail` when several value-like leaves collapsed into one template endpoint
- `_extract_from_json()` now keeps parent-key context for JSON discovery and accepts safe relative paths again for link-like keys such as `links`, `href`, `url`, and `next`, while continuing to reject plain value words like `"active"` and `"Puzzle Box"`
- `_coalesce_sibling_endpoints()` now preserves a shared query suffix when all coalesced siblings carry the same query defaults
- Added regression tests `test_relative_json_links_are_still_discovered` and `test_sibling_coalescing_preserves_shared_query_defaults`
- Local verification completed with `ruff check libs/extractors/rest.py libs/extractors/tests/test_rest.py`, `mypy libs/extractors/rest.py libs/extractors/tests/test_rest.py`, `pytest -q libs/extractors/tests/test_rest.py`, and `pytest -q tests/e2e/test_full_compilation_flow.py -k rest_discovery`

Live GKE revalidation completed on `2026-03-26`:
- Published `compiler-api:20260326-b0e27e6-r28` (`sha256:e5c5e84ed7e388143d297bcad8ddc54a0d5e9315752b29ab3b340dfe276a2df8`) and `compiler-worker:20260326-b0e27e6-r28` (`sha256:9589ecb89c9d5f94c9aa96154574679f201e76890edf83a0ae84f609bf733756`)
- Re-ran `PROTOCOL=rest AUDIT_ALL_GENERATED_TOOLS=1` in namespace `tool-compiler-b002-rest-followup-065216` and preserved the clean catalog result: `discovered=1`, `generated=1`, `audited=1`, `passed=1`, `failed=0`, `skipped=0`
- The first `PROTOCOL=all` rerun exposed a GKE harness issue in `llm-proof-sql`, not a REST regression: Postgres could restart during initialization and leave `order_summaries` missing permanently
- `scripts/smoke-gke-llm-e2e.sh` now adds a `startupProbe` for the SQL proof Postgres pod, and `tests/contract/test_local_dev_assets.py` asserts that the probe remains present
- The clean authoritative rerun in namespace `tool-compiler-llm-all-audit-075849` then completed with GraphQL `2/2/1/1/0/1`, REST `1/1/1/1/0/0`, gRPC `3/3/1/1/0/2`, SOAP `2/2/1/1/0/1`, and SQL `5/5/3/3/0/2` for `discovered/generated/audited/passed/failed/skipped`
- Aggregate cross-protocol audit baseline: `discovered=13`, `generated=13`, `audited=7`, `passed=7`, `failed=0`, `skipped=6`
- The lower generated-tool total relative to the earlier structural `18` is expected and correct: `B-002` removed five fake REST endpoints that had previously inflated the count

Remaining implementation tasks:
- Carry the new `audit_summary` data into validator/reporting surfaces beyond the proof runner
- Refine the skip-policy classification so more explicitly safe tools can be audited without widening execution risk accidentally
- Continue hardening REST discovery for larger and messier undocumented targets beyond the catalog mock (`B-003`)

Actual write set after follow-up and live harness hardening:
- `libs/extractors/rest.py`
- `libs/extractors/tests/test_rest.py`
- `scripts/smoke-gke-llm-e2e.sh`
- `tests/contract/test_local_dev_assets.py`
- handoff/status docs (`agent.md`, `devlog.md`, this plan)

Out of scope:
- Full browser/JavaScript crawling in the first slice
- Vendor-specific discovery plugins for every API product

### B-003: Large-Surface Black-Box Pilot

Status: complete

Scope:
- Select one or two services with large surfaces (targeting roughly `50` to `100+` endpoints) and limited authoritative specs
- Measure three separate numbers: endpoint discovery coverage, generated MCP-tool coverage, and real audited invocation pass rate
- Capture the unsupported patterns encountered (auth, async, streaming, pagination, naming drift, write-safety limits) as explicit backlog inputs

Implemented in the first slice (completed `2026-03-26`):
- Created `tests/fixtures/large_surface_rest_mock.py` with 62 ground-truth endpoint definitions across 9 resource groups (users, products, orders, categories, inventory, notifications, reports, webhooks, admin)
- Added `LargeSurfacePilotReport` dataclass to `libs/validator/audit.py` capturing the three B-003 coverage numbers plus unsupported patterns
- Created `tests/integration/test_large_surface_pilot.py` running the full discovery → extraction → runtime → audit pipeline
- First pilot baseline results against the 62-endpoint mock:
  - Ground truth unique paths: `39`
  - Discovered endpoints: `10` (discovery coverage: `25.6%`)
  - Generated tools: `16` (generation coverage: `160%` — GET+POST per discovered path)
  - Audited tools: `10`, passed: `10`, failed: `0` (audit pass rate: `100%`)
  - Unsupported patterns captured: `3` (deeply nested resources, un-crawlable write/mutation endpoints, external side-effect skip)
- Key finding: the REST extractor's GET-based crawl inherently cannot discover POST/PUT/DELETE endpoints or deeply nested resources without explicit links, confirming the need for `OPTIONS`-based and spec-first discovery paths for large surfaces

Remaining implementation tasks:
- Improve REST discovery to better leverage `OPTIONS` probing on discovered paths to surface additional HTTP methods
- Consider an OpenAPI-spec-first pilot with a large spec (50+ endpoints) to measure spec-first coverage vs black-box coverage
- Define regression thresholds for the pilot baseline so future extractor changes can be measured against it

Implemented in the second slice (completed `2026-03-26`):
- `RESTExtractor._discover()` now follows JSON links during crawl (not just HTML)
- Added `_infer_sub_resources()` implementing URI-based resource dependency tree: collection → detail `{id}` probing, detail → common sub-resource probing via `_common_sub_resources()` heuristic
- Added `_probe_and_register()` for OPTIONS+GET validation of inferred paths
- Enhanced mock with HATEOAS detail responses and collection-level OPTIONS support
- Pilot improved: discovered 10→17 (+70%), generated 16→32 (+100%), audit 10→17 all passed

Paper-informed next steps (P0 quick wins completed, P1 major projects completed):
- ~~P0: Response field pruning to protect LLM context window from noise~~ ✅ completed `2026-03-26`
- ~~P1: LLM-driven seed mutation (RESTSpecIT-style) for 88%+ black-box route discovery~~ ✅ completed `2026-03-27`
- ~~P1: Semantic tool aggregation via LLM-ITL intent clustering — add `tool_grouping` to IR~~ ✅ completed `2026-03-27`
- ~~P1: Discovery vs Action tool bifurcation in generated tool descriptions~~ ✅ completed `2026-03-27`
- ~~P1: Full LLM-as-a-Judge evaluation pipeline for tool description quality~~ ✅ completed `2026-03-27`

Implemented in the third slice (completed `2026-03-27`):
- **LLM-driven seed mutation**: `libs/extractors/llm_seed_mutation.py` — `generate_seed_candidates()` sends discovered endpoints to an LLM to generate candidate paths, validates via HTTP probing. Opt-in via `llm_client` param on `RESTExtractor`. Phase 3 in `_discover()`.
- **Semantic tool aggregation**: `libs/enhancer/tool_grouping.py` — `ToolGrouper` uses LLM to cluster operations into business-intent groups. New `ToolGroup` model and `tool_grouping` field on `ServiceIR`. `apply_grouping()` helper.
- **Discovery/Action bifurcation**: `libs/enhancer/tool_intent.py` — `derive_tool_intents()` derives `ToolIntent` (discovery/action) from `RiskMetadata` and HTTP method. `bifurcate_descriptions()` prepends `[DISCOVERY]`/`[ACTION]` prefixes. New `ToolIntent` enum and `tool_intent` field on `Operation`.
- **LLM-as-a-Judge**: `libs/validator/llm_judge.py` — `LLMJudge` evaluates tool descriptions on accuracy, completeness, clarity (0.0–1.0 each) with weighted overall score. `JudgeEvaluation` with per-tool `ToolQualityScore` and aggregate quality metrics.
- Pilot test updated: `tests/integration/test_large_surface_pilot.py` exercises all four P1 features with mock LLM clients.
- Verification: 410 tests passed, ruff clean, mypy clean (150 files).

Out of scope:
- Generalizing one pilot into a universal `100-endpoint service => 100 working MCP tools` guarantee
- Blurring together spec-first confidence and black-box confidence into a single unqualified success claim

## Delivery Shape For Each Module

Every module should ship in three passes:

1. Foundation
- Detection
- Basic extraction or execution contract
- Explicit unsupported markers
- Fixture tests

2. Integration
- Workflow wiring
- Validation wiring
- Runtime/control-plane integration
- Integration tests

3. Hardening
- Dirty fixtures
- Failure-path coverage
- Live infrastructure smoke checks if the module touches deployment/gateway/runtime behavior

## Exit Criteria For A Module

A module is only "complete" when all of the following are true:

- The module's scope and non-goals are written down
- Tests prove supported behavior
- Unsupported behavior is explicit
- `ruff check .`, `mypy libs apps tests/integration tests/contract tests/e2e`, and `pytest -q` are green
- `agent.md` and `devlog.md` reflect the new state

## Repo-Level Guardrails

- Do not fork the architecture per protocol.
- Do not add a second pipeline shape for one protocol family.
- Do not let runtime capability assumptions leak back into extractor modules.
- Do not claim support for a protocol mode until the runtime/data-plane path is actually validated.
