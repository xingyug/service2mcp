# Tool Compiler v2 — Agent Briefing

> This file is the authoritative context document for any AI coding agent working on this project.
> Read this first. Then read the SDD (`../tool-compiler-v2-sdd.md`) for full architecture details.

## What This Project Is

**Tool Compiler v2** is an enterprise platform that automatically compiles any API (REST, GraphQL, gRPC, SQL, etc.) into a governed, observable [MCP](https://modelcontextprotocol.io/) tool server that AI agents can call through a standard protocol.

Given a URL or spec file → it detects the protocol → extracts the schema → normalizes it into an **Intermediate Representation (IR)** → generates Kubernetes manifests → deploys a tool server → registers gateway routes → provisions access control. All automated.

## Important Reference Files

Repository root on this machine:

- `/home/guoxy/esoc-agents/tool-compiler-v2`

Key absolute paths on this machine:

- `/home/guoxy/esoc-agents/tool-compiler-v2-sdd.md` — authoritative SDD, architecture, backlog, and acceptance criteria
- `/home/guoxy/esoc-agents/tool-compiler-v2/agent.md` — current coding-agent briefing and handoff context
- `/home/guoxy/esoc-agents/tool-compiler-v2/devlog.md` — chronological implementation log and latest verification status
- `/home/guoxy/esoc-agents/tool-compiler-v2/docs/post-sdd-modular-expansion-plan.md` — modular post-SDD backlog, sequencing, and exit criteria
- `/home/guoxy/esoc-agents/tool-compiler-v2/docs/context-engineering.md` — context-bounding rules for future agents and module slices
- `/home/guoxy/esoc-agents/tool-compiler-v2/pyproject.toml` — dependency set plus `pytest` / `ruff` / `mypy` configuration

Key paths below are relative to the repository root (`tool-compiler-v2/`):

- `../tool-compiler-v2-sdd.md` — authoritative SDD, architecture, backlog, and acceptance criteria
- `./agent.md` — current coding-agent briefing and handoff context
- `./devlog.md` — chronological implementation log and latest verification status
- `./docs/post-sdd-modular-expansion-plan.md` — post-SDD modular backlog, sequencing, and exit criteria
- `./docs/context-engineering.md` — context-management rules for future agents
- `./pyproject.toml` — dependency set plus `pytest` / `ruff` / `mypy` configuration
- `./apps/web-ui/` — Next.js 16 frontend (TypeScript, Tailwind, shadcn/ui, TanStack React Query, Zustand)
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
- `./apps/compiler_api/routes/services.py` — compiled service listing endpoints
- `./apps/access_control/main.py` — access control FastAPI app entrypoint
- `./apps/access_control/authn/service.py` — JWT validation and PAT lifecycle service
- `./apps/access_control/authz/service.py` — policy CRUD and semantic authorization evaluation
- `./apps/access_control/gateway_binding/service.py` — APISIX consumer/policy sync and drift reconciliation
- `./apps/access_control/audit/service.py` — append-only audit logging and query service
- `./apps/compiler_worker/main.py` — compiler worker health and metrics shell used by local dev and deployment assets
- `./apps/compiler_worker/celery_app.py` — Celery app and compilation task binding used by the queue-backed worker path
- `./apps/compiler_worker/executor.py` — task executor adapters and per-task database-backed workflow runtime
- `./apps/compiler_worker/entrypoint.py` — process supervisor that runs the worker HTTP shell and Celery consumer together
- `./apps/compiler_worker/activities/production.py` — production activity handlers, manifest deployment, runtime readiness waiting, and live validation logic
- `./apps/compiler_worker/observability.py` — compilation workflow metrics for dashboards and worker health endpoints
- `./apps/compiler_worker/workflows/compile_workflow.py` — durable compilation workflow core with retries and rollback
- `./apps/compiler_worker/workflows/rollback_workflow.py` — rollback orchestration for redeploying and reactivating prior service versions
- `./apps/compiler_worker/repository.py` — compilation job and event persistence layer
- `./apps/compiler_worker/activities/pipeline.py` — activity registry helpers for stage and rollback handlers
- `./libs/validator/pre_deploy.py` — pre-deploy validation harness for IR schema and auth smoke tests
- `./libs/validator/post_deploy.py` — post-deploy validation harness for runtime health, tool listing, and invocation smoke checks
- `./libs/extractors/grpc.py` — gRPC `.proto` detection and unary RPC extraction foundation
- `./libs/extractors/soap.py` — SOAP / WSDL 1.1 extraction foundation
- `./libs/extractors/graphql.py` — GraphQL introspection extractor
- `./libs/extractors/sql.py` — SQL schema extractor backed by SQLAlchemy reflection
- `./libs/extractors/rest.py` — REST extractor with discovery and classifier-assisted normalization
- `./apps/gateway_admin_mock/main.py` — lightweight HTTP gateway-admin mock used by local and live reconciliation tests
- `./apps/mcp_runtime/main.py` — generic runtime FastAPI app entrypoint
- `./apps/mcp_runtime/grpc_stream.py` — native grpc reflection-backed server-stream executor
- `./apps/mcp_runtime/loader.py` — runtime IR loading and dynamic tool registration
- `./apps/mcp_runtime/proxy.py` — upstream HTTP proxy execution path
- `./apps/mcp_runtime/observability.py` — runtime metrics and logging integration
- `./libs/observability/tracing.py` — shared tracing setup used by runtime
- `./deploy/docker-compose.yaml` — local development topology for PostgreSQL, Redis, Temporal, and all service shells
- `./deploy/docker/Dockerfile.app` — shared container build entrypoint used by CI and Helm values
- `./deploy/helm/tool-compiler/values.yaml` — full-platform Helm configuration defaults
- `./deploy/helm/tool-compiler/templates/` — Helm templates for infra, services, and migration hooks
- `./deploy/helm/tool-compiler/templates/infra.yaml` — shared PostgreSQL / Redis / Temporal deployment assets and cold-start probe defaults
- `./observability/grafana/compilation-dashboard.json` — compilation pipeline Grafana dashboard
- `./observability/grafana/runtime-dashboard.json` — runtime Grafana dashboard
- `./scripts/setup-dev.sh` — local bootstrap script for Python deps and compose validation
- `./scripts/git-hooks/pre-push.sample` — optional hook; copy to `.git/hooks/pre-push` to run `gitleaks` before every push (see Git Conventions)
- `./scripts/smoke-dev.sh` — local smoke checks for ports and health endpoints
- `./scripts/smoke-gateway-routes.sh` — local route-publication smoke harness using Access Control and Gateway Admin Mock
- `./scripts/smoke-gke-gateway-routes.sh` — minimal live GKE route-publication and drift-reconciliation smoke harness
- `./.github/workflows/ci.yaml` — CI workflow for lint, typecheck, contract tests, integration tests, and image builds
- `./docs/quickstart.md` — local onboarding and end-to-end quickstart guide
- `./docs/adr/` — ADR set for core platform decisions
- `./migrations/versions/001_initial.py` — current PostgreSQL schema baseline
- `./tests/fixtures/ir/` — canonical `ServiceIR` fixtures used by runtime and generator tests
- `./tests/contract/` — contract tests for OpenAPI schemas, dev assets, dashboards, and Helm chart structure
- `./tests/integration/test_artifact_registry.py` — artifact registry integration coverage
- `./tests/integration/test_compile_workflow.py` — compilation workflow retry, rollback, and persistence coverage
- `./tests/integration/test_compiler_api.py` — compiler API submit/status/SSE/service-list coverage
- `./tests/integration/test_compiler_worker_activities.py` — production activity integration coverage, including runtime startup-lag handling
- `./tests/integration/test_compiler_worker_app.py` — compiler worker health and metrics coverage
- `./tests/integration/test_streamable_http_tool_invoker.py` — live HTTP transport regression coverage for runtime tool invocation
- `./libs/validator/tests/test_pre_deploy.py` — pre-deploy validation coverage
- `./libs/validator/tests/test_post_deploy.py` — post-deploy validation coverage
- `./libs/validator/audit.py` — shared audit types, skip-policy, and regression thresholds
- `./libs/validator/tests/test_audit.py` — audit policy and threshold coverage
- `./libs/extractors/tests/test_graphql.py` — GraphQL extractor coverage
- `./libs/extractors/tests/test_rest.py` — REST extractor coverage including B-002 regression tests
- `./tests/fixtures/large_surface_rest_mock.py` — B-003 large-surface pilot fixture (62 endpoints, 9 resource groups)
- `./tests/fixtures/openapi_specs/large_surface_api.yaml` — B-003 spec-first pilot fixture (62 operations, same domain)
- `./tests/integration/test_large_surface_pilot.py` — B-003 pilot integration tests (black-box, P1, spec-first)
- `./libs/extractors/tests/test_grpc.py` — gRPC proto extraction coverage
- `./libs/extractors/tests/test_conformance_corpus.py` — corpus-driven regression coverage for messy and unsupported fixtures
- `./libs/extractors/tests/test_soap.py` — SOAP / WSDL extraction coverage
- `./libs/extractors/tests/test_sql.py` — SQL extractor coverage
- `./libs/extractors/tests/test_rest.py` — REST extractor discovery and classification coverage
- `./tests/fixtures/graphql_schemas/` — GraphQL introspection fixtures
- `./tests/fixtures/grpc_protos/` — gRPC proto fixtures
- `./tests/fixtures/conformance/` — conformance corpus manifest and messy real-world fixtures
- `./tests/fixtures/sql_schemas/` — SQL schema fixtures used by reflection tests
- `./tests/fixtures/wsdl/` — WSDL fixtures used by SOAP extractor tests
- `./tests/integration/test_access_control_authn.py` — access control authn integration coverage
- `./tests/integration/test_access_control_authz.py` — access control authz integration coverage
- `./tests/integration/test_access_control_gateway_binding.py` — gateway binding integration coverage
- `./tests/integration/test_access_control_audit.py` — audit logging integration coverage
- `./tests/integration/test_rollback_workflow.py` — rollback workflow integration coverage
- `./tests/integration/test_version_coexistence.py` — version coexistence and active-switch coverage
- `./tests/integration/test_mcp_runtime.py` — runtime startup and tool registration coverage
- `./tests/integration/test_mcp_runtime_grpc_stream.py` — native grpc stream runtime coverage
- `./tests/integration/test_mcp_runtime_proxy.py` — runtime upstream proxy coverage
- `./tests/integration/test_mcp_runtime_observability.py` — runtime metrics/tracing coverage
- `./tests/e2e/test_full_compilation_flow.py` — end-to-end OpenAPI spec to runtime tool invocation coverage

## Architecture (Three Planes)

```
CONTROL PLANE          BUILD PLANE              RUNTIME PLANE
├── Compiler API       ├── Type Detector         ├── Generic MCP Runtime
├── Artifact Registry  ├── Extractors (6 types)  ├── Codegen MCP Servers
├── Access Control     ├── LLM Enhancer          ├── APISIX Gateway
│   (AuthN/AuthZ)      ├── Validation Harness    └── Observability Stack
└── PostgreSQL         └── Pipeline Orchestrator
```

## Core Design Principles

1. **The IR is the product.** Everything upstream is an extractor, everything downstream is a consumer. The IR is versioned, persisted, diffable.
2. **Generic runtime by default.** One container image reads IR at startup, dynamically registers MCP tools. No per-service codegen unless necessary.
3. **Every pipeline step is retryable and reversible.** State machine, not a script.
4. **Secure by default.** Unknown-risk operations are restricted. Semantic risk classification, not HTTP-method guessing.
5. **Observable from birth.** Prometheus metrics, OpenTelemetry traces, structured logging on every component.
6. **Contracts over conventions.** Typed Pydantic schemas between all components.

## Repository Layout

```
tool-compiler-v2/
├── libs/                        # Shared libraries (the core)
│   ├── ir/                      # IR models, schema, diff (THE central contract)
│   │   ├── models.py            # Pydantic v2 models: ServiceIR, Operation, Param, RiskMetadata, etc.
│   │   ├── schema.py            # JSON Schema generation + serialization utils
│   │   ├── diff.py              # Structured diff between two ServiceIR instances
│   │   └── tests/
│   ├── extractors/              # Protocol-specific extractors → raw IR
│   │   ├── base.py              # ExtractorProtocol + TypeDetector
│   │   ├── openapi.py           # Swagger 2.0 / OpenAPI 3.0 / 3.1 extractor
│   │   ├── graphql.py           # GraphQL introspection extractor
│   │   ├── grpc.py              # gRPC proto unary extraction foundation
│   │   ├── soap.py              # SOAP / WSDL extraction foundation
│   │   ├── sql.py               # SQLAlchemy-backed SQL schema extractor
│   │   ├── rest.py              # REST discovery extractor with classifier hook
│   │   └── tests/
│   ├── enhancer/                # LLM enhancement of IR
│   ├── validator/               # Pre-deploy + post-deploy validation harnesses
│   ├── generator/               # K8s manifest + codegen artifact generation (stub)
│   ├── registry_client/         # Client + shared models for artifact registry
│   └── observability/           # Shared metrics/tracing/logging utilities
├── apps/                        # Deployable services
│   ├── compiler_api/            # FastAPI — accepts compilation requests
│   ├── compiler_worker/         # Pipeline orchestrator (Celery/Temporal)
│   ├── access_control/          # AuthN + AuthZ + gateway binding
│   ├── gateway_admin_mock/      # Lightweight gateway-admin mock for local/live smoke
│   └── mcp_runtime/             # Generic MCP runtime
├── tests/                       # Integration and E2E tests
│   ├── fixtures/                # Test spec files
│   │   ├── openapi_specs/       # Petstore 3.0, Swagger 2.0 fixtures
│   │   ├── graphql_schemas/     # Introspection JSON fixtures
│   │   ├── grpc_protos/         # gRPC proto fixtures
│   │   ├── sql_schemas/         # SQL schema fixtures for database reflection tests
│   │   └── wsdl/                # WSDL fixtures
│   └── conftest.py
├── migrations/                  # Alembic DB migrations
├── deploy/                      # Helm charts, docker-compose, k8s manifests
├── observability/               # Grafana dashboards, Prometheus alerts, OTel config
├── specs/                       # Detailed module specs (planned)
├── docs/                        # ADRs, quickstart guide, modular expansion docs
├── scripts/                     # Dev scripts
├── pyproject.toml               # Python project config (monorepo, hatchling)
└── Makefile                     # Common commands
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Models / Validation | Pydantic v2 |
| API Framework | FastAPI + Uvicorn |
| HTTP Client | httpx |
| Database | PostgreSQL (asyncpg + SQLAlchemy async) |
| Migrations | Alembic |
| Pipeline Engine | Celery + Redis (Temporal later) |
| Code Templates | Jinja2 |
| API Specs | PyYAML, openapi-spec-validator |
| LLM Clients | anthropic, openai, google-cloud-aiplatform |
| Observability | prometheus-client, opentelemetry-sdk |
| Testing | pytest, pytest-asyncio, hypothesis, testcontainers, respx |
| Linting | ruff, mypy |

## Key Types (from `libs/ir/models.py`)

```python
ServiceIR          # Top-level: the complete compiled representation of a service
├── AuthConfig     # How to authenticate with the upstream API
├── Operation[]    # Each callable operation → becomes an MCP tool
│   ├── Param[]    # Parameters with type, source, confidence
│   ├── RiskMetadata  # Semantic risk: writes_state, destructive, risk_level, confidence
│   └── ResponseStrategy  # Pagination, truncation, field filtering
└── OperationChain[]  # Sequences of operations to invoke together
```

**Key invariants enforced by validators:**
- Operation IDs unique within a ServiceIR
- Extractor-sourced params must have confidence ≥ 0.8
- Operations with `risk_level: "unknown"` must have `enabled: False`
- OperationChain steps must reference valid operation IDs

## Development Workflow

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,extractors]"

# Run tests
pytest                          # all tests
pytest libs/ir/tests/ -v        # specific module
pytest -k "test_openapi" -v     # by name pattern

# Lint + type check
ruff check .
mypy libs/
```

## Standard Toolchain Policy

Preferred long-term quality stack for this repository:
- `uv` for environment sync and lock-driven execution
- `ruff` for formatting and linting
- `basedpyright` for strict type checking
- `pytest` with coverage and `hypothesis` for functional and property testing
- `pre-commit` for local hooks
- `nox` for a single quality-gate entrypoint
- `semgrep`, `deptry`, `pip-audit`, and `import-linter` for security, dependency hygiene, and architecture constraints

Required execution order for future work:
1. `uv sync`
2. `uv run nox -s lint`
3. `uv run nox -s typecheck`
4. `uv run nox -s tests`
5. `uv run nox -s security deps arch`

Transition rule for the current repository state:
- This repository is not fully migrated to `uv` / `nox` / `basedpyright` yet.
- Until that migration lands, agents must follow the same gate order but run the repo-supported equivalent commands from `.venv`, with `ruff`, `mypy`, and `pytest` remaining the authoritative gates.
- Do not claim `uv`, `nox`, or `basedpyright` results unless those commands actually exist in the repo and were executed successfully.

## Current Status

See `devlog.md` for detailed progress tracking.

**Completed:** T-001 through T-033 (repo structure, IR models/diff, OpenAPI extraction, enhancer, observability, PostgreSQL schema, artifact registry, generic runtime startup path, runtime upstream proxy, runtime metrics/tracing, generic-mode manifest generator, durable compilation workflow/state machine, Compiler API endpoints, pre-deploy validation harness, post-deploy validation harness, GraphQL extractor, SQL extractor, REST extractor, Access Control AuthN/AuthZ/Gateway Binding modules, audit logging, rollback workflow, version coexistence support, local development environment, CI pipeline, Grafana dashboards, Helm chart, ADRs, quickstart guide, end-to-end compilation flow) plus post-SDD expansion tasks H-001 (gRPC proto detection and unary extraction foundation), H-002 (SOAP / WSDL extraction foundation), H-003 (streaming / event descriptors), H-004 (multipart, binary-safe, and async job runtime foundations), H-005 (approved streaming runtime support), H-006 (advanced auth schema, validation, and runtime adapters), H-007 (messy-spec conformance corpus), and H-008 (live gateway / rollout hardening).
**Current hardening state:** Post-backlog hardening now includes repeated real infrastructure/provider proof points, four runtime/capability slices, a cross-protocol conformance corpus, and completed follow-on roadmap tracks `R-001` through `R-003`: the clean live GKE compilation success in namespace `tool-compiler-gke-test-r11` for service `petstore-live-r11-r12`, the local access-control-backed route-publication foundation (HTTP gateway-admin clients, service-route sync / rollback / reconciliation endpoints, worker-side route publication through Access Control, rollback restoration of previous stable routes, and `apps.gateway_admin_mock` for compose/Helm plus integration coverage), the earlier minimal live GKE gateway smoke harness in namespace `tool-compiler-gateway-smoke-r2` that proved drift reconciliation plus control-plane rollout/rollback semantics, the completed `R-001` DeepSeek track in both local-provider and full platform form (explicit `deepseek` provider wiring, configurable OpenAI-compatible base URLs, a reproducible harness at `scripts/validate_deepseek_enhancer.py`, and a live Helm/GKE compile-deploy-register proof in namespace `tool-compiler-gke-test-r13` for service `deepseek-live-r13-r16` where the worker logged a real `POST https://api.deepseek.com/chat/completions` `200`, persisted `operations_enhanced=2` in compilation events, and produced an IR artifact with `source: "llm"` descriptions), the completed `R-002` live gateway/data-plane slice (the gateway admin mock now exposes a real forwarding path via published route documents, integration coverage proves active plus pinned route selection and failure visibility, and the live GKE harness in namespace `tool-compiler-gateway-smoke-r7` proved stable-route rollout to `v2`, rollback to `v1`, and pinned `v2` continuity through the gateway entrypoint), the advanced-auth path spanning nested IR auth models plus runtime OAuth2 / request-signing / mTLS support, the H-004 runtime slice covering explicit request-body modes plus multipart/raw/binary/async-job handling, the H-003 typed `event_descriptors` slice that records OpenAPI callbacks/webhooks, GraphQL subscriptions, and gRPC streaming RPCs as explicit unsupported descriptors while the pre-deploy validator rejects false runtime-support claims, the H-005 streaming runtime slice that adds bounded `sse` and `websocket` session handling with lifecycle/backpressure limits while explicitly rejecting non-approved transports such as native `grpc_stream`, the now-complete `R-003` native grpc slice that includes typed `GrpcStreamRuntimeConfig` / `GrpcStreamMode`, proto-extracted native stream config, a dedicated runtime executor seam, a reflection-backed concrete server-stream executor in `apps/mcp_runtime/grpc_stream.py`, opt-in runtime wiring via `ENABLE_NATIVE_GRPC_STREAM`, post-deploy validation coverage through `libs/validator/post_deploy.py` plus the streamable HTTP tool-invoker path, a reproducible live harness in `scripts/smoke-gke-grpc-stream.sh`, the descriptor-priming fix needed for reflection-backed method lookup, rollout-convergence waiting to avoid stale service endpoints, and an authoritative live GKE proof in namespace `tool-compiler-grpc-stream-smoke-r1` using runtime image `20260325-b0e27e6-r19` where the runner job returned `status="ok"`, `transport="grpc_stream"`, and a reflected protobuf event with `{"sku":"sku-live","status":"ready"}`. The protocol-completion track is now fully complete: `P-001` added typed GraphQL execution plus local runtime proof; `P-002` hardened discovered REST runtime semantics; `P-003` added native gRPC unary execution behind `ENABLE_NATIVE_GRPC_UNARY`; `P-004` added typed `SoapOperationConfig` plus SOAP envelope/action/fault execution and validator proof; `P-005` added typed `SqlOperationConfig` plus the safe native SQL query/insert executor in `apps/mcp_runtime/sql.py`; and `P-006` added the machine-readable protocol capability matrix in `libs/validator/capability_matrix.py`, protocol-aware GraphQL/SQL sample invocation generation, and post-deploy smoke selection that prefers safer query/read paths over mutation/insert paths. The original post-SDD modular expansion backlog remains complete at `8 / 8`, and the protocol-completion backlog now stands at `6 / 6`. The final cross-protocol `LLM-enabled E2E` proof track is now also complete: `L-001` remains the live OpenAPI + DeepSeek baseline, `L-002` through `L-006` cover local GraphQL, REST, gRPC, SOAP, and SQL proofs, and the shared local E2E enhancer path can optionally switch from stub mode to a real DeepSeek call via `ENABLE_REAL_DEEPSEEK_E2E`. GraphQL cleared the single-protocol live GKE DeepSeek proof path in namespace `tool-compiler-llm-graphql-015547`; REST discovery did the same in `tool-compiler-llm-rest-020103`; SOAP / WSDL did the same in `tool-compiler-llm-soap-020620`; SQL did the same in `tool-compiler-llm-sql-021037`; and gRPC unary did the same in `tool-compiler-llm-grpc-024113` after rebuilding `compiler-worker:20260326-b0e27e6-r23` to fix worker-generated gRPC smoke samples. The authoritative joint rerun then succeeded in namespace `tool-compiler-llm-all-024755`, returning successful proof records for GraphQL (`job_id=0b3b8bef-cec6-42ca-8704-f8916d8038c9`, `operations_enhanced=2`, `llm_field_count=9`), REST (`job_id=512b9e93-7571-48ad-99ec-45a38ca3b4cc`, `operations_enhanced=6`, `llm_field_count=9`), gRPC (`job_id=71040e7a-7c33-44eb-852c-0cff9bb4112b`, `operations_enhanced=3`, `llm_field_count=11`), SOAP (`job_id=1653e378-d2c5-4cd5-84d3-b8438c13aca0`, `operations_enhanced=2`, `llm_field_count=7`), and SQL (`job_id=364c37fe-a72e-4204-b970-cb49abb306d1`, `operations_enhanced=5`, `llm_field_count=25`), with successful runtime tool invocations for every protocol and real `POST https://api.deepseek.com/chat/completions` `200 OK` evidence in worker logs across the matrix. In response to the cold-start queue flake observed at the beginning of that run, `apps/compiler_worker/entrypoint.py` now waits for the Redis broker socket and for Celery to report `ready` before exposing the worker HTTP shell, with regression coverage in `tests/integration/test_compiler_worker_entrypoint.py`.
**Latest verification:** Published `compiler-api:20260327-75be3a5-r29` (`sha256:b8567690b32b89f8a478ea426506f030a707fa60c8cac4c69b0ec7686d30f53b`), `compiler-worker:20260327-75be3a5-r29` (`sha256:3d5d213c62ab6bbd1478f51d78923ddd398d35fb1837642ac16b7e629541c201`), `access-control:20260327-75be3a5-r29` (`sha256:ed444a118fa7b7f213e105f2fc11cbad3411a0f7b5ef0951e60b063e56a257ad`), and `mcp-runtime:20260327-75be3a5-r29` (`sha256:bbeb9dd27f8dd0747460b3bb0f2ad791053a8dd0abf78fac29dea4e572e4cc22`). The `PROTOCOL=all AUDIT_ALL_GENERATED_TOOLS=1` cross-protocol GKE LLM E2E rerun succeeded in namespace `tool-compiler-llm-b003-032621` with the `r29` images. The audit returned GraphQL `2/2/1/1/0/1` (`job_id=dd870f22`), REST `1/1/1/1/0/0` (`job_id=001f25fb`), gRPC `3/3/1/1/0/2` (`job_id=66941b44`), SOAP `2/2/1/1/0/1` (`job_id=16eb7017`), and SQL `5/5/3/3/0/2` (`job_id=849efc9d`) for `discovered/generated/audited/passed/failed/skipped`, for an aggregate **13/13/7/7/0/6** — matching the previous `r28` audit baseline with zero regressions, confirming the B-003 OPTIONS deep probing + iterative inference + dedup changes are production-safe. REST discovery correctly returned `get_items_item_id` with `upstream_status: 200`; gRPC streaming returned 2 protobuf events via `grpc_stream` transport; SQL `query_order_summaries` returned the cross-table JOIN view.
**Current conversion posture:** The B-003 REST discovery improvements are now GKE-live-proven at `r29`. The cross-protocol audit aggregate remains stable at `13/13/7/7/0/6`, confirming the OPTIONS-authoritative probing, iterative sub-resource inference, and generality-ranked deduplication changes introduced no regressions in the live environment. The earlier `r28` audit baseline in namespace `tool-compiler-llm-all-audit-075849` is now superseded by this `r29` run.
**Repository state:** The working tree tracks the private GitHub repository `xingyug/service2mcp` on branch `main`. Latest significant commit at this documentation snapshot: `75be3a5` (`fix: add README.md to Docker image build`), preceded by `3e9ff04` (`B-003: OPTIONS deep probing, iterative inference, generality-ranked dedup`). Run `make gitleaks` before each push.
**Next up:** `B-004` (P1 Features Live LLM Proof) is in progress — wiring `WORKER_ENABLE_TOOL_GROUPING` into GKE harness, adding `--enable-llm-judge` to proof runner, adding `judge_evaluation` to `ProofResult`, and verifying `tool_intent` presence in compiled IR. After that: `B-005` (Real External API Black-Box Validation). See `docs/post-sdd-modular-expansion-plan.md` for full roadmap. Previous slice: repository DTO transformers (compiler_api, compiler_worker) and audit service _to_response (23 tests across 3 files). 1086 tests, ruff/mypy clean (190 source files).
**Key files for the B-001/B-002 fourth slice:**
- `libs/validator/audit.py` — `AuditPolicy` with `audit_safe_methods`, `audit_discovery_intent` overrides
- `libs/validator/pre_deploy.py` — `ValidationReport` with embedded `audit_summary: ToolAuditSummary | None`
- `libs/validator/post_deploy.py` — `validate_with_audit()` now embeds audit_summary in the report
- `libs/extractors/rest.py` — `_head_probe()`, hardened `_probe_and_register()` and `_probe_allowed_methods()`
- `tests/integration/test_large_surface_pilot.py` — `PILOT_BASELINE_THRESHOLDS`, coverage regression baselines
**Key files for the P1 pipeline integration slice:**
- `apps/compiler_worker/activities/production.py` — `_apply_post_enhancement()`, `_tool_grouping_enabled()`, wiring in `enhance_stage`
- `tests/e2e/test_full_compilation_flow.py` — `tool_intent` assertions in OpenAPI, REST, GraphQL E2E tests
- `tests/integration/test_compiler_worker_activities.py` — `test_apply_post_enhancement_sets_tool_intent_and_bifurcates_descriptions`
**Key files for the P1 features (previous slice):**
- `libs/extractors/llm_seed_mutation.py` — `generate_seed_candidates()`, `SeedCandidate`, RESTSpecIT-style LLM prompt
- `libs/extractors/rest.py` — `_llm_seed_mutation()` phase in `_discover()`, opt-in `llm_client` param
- `libs/enhancer/tool_grouping.py` — `ToolGrouper`, `apply_grouping()`, LLM-ITL clustering prompt
- `libs/enhancer/tool_intent.py` — `derive_tool_intents()`, `bifurcate_descriptions()`, intent derivation rules
- `libs/validator/llm_judge.py` — `LLMJudge`, `ToolQualityScore`, `JudgeEvaluation`, judge prompt
- `libs/ir/models.py` — `ToolIntent` enum, `ToolGroup` model, `tool_grouping` on `ServiceIR`, `tool_intent` on `Operation`
- `tests/integration/test_large_surface_pilot.py` — P1 pilot test exercising all four features with mock LLM
**Key files for B-003 GKE image build / live proof:**
- `deploy/docker/Dockerfile.app` — must `COPY README.md` alongside `pyproject.toml` so hatchling can build wheels in container builds
**Open-source posture:** If/when this project is published publicly, prefer creating a fresh public repository without carrying over the current private/internal history. That keeps internal handoff notes, environment-specific defaults, and intermediate hardening history separate from the eventual public release.

## Project Size Expectations

As of `2026-03-27`, the repository contains approximately:
- `25,149` lines of production Python code (`apps/`, `libs/`, `migrations/`)
- `25,500` lines of frontend TypeScript/TSX code (`apps/web-ui/src/`)
- `11,044` lines of Python test code; plus `1,023` lines of YAML test fixtures
- `5,600` lines of frontend test code (Vitest unit + Playwright E2E)
- `36,277` total Python code lines including repo `scripts/` and excluding virtualenv / generated caches

Original SDD-completion estimate:
- Production code: roughly `14,000` to `16,000` lines
- Total code including tests: roughly `24,000` to `28,000` lines

Current interpretation:
- The repository is now materially beyond the original SDD-only size estimate because the post-SDD protocol-expansion, live-proof, and hardening tracks are complete.

Progress tracking guidance:
- By backlog count, `33 / 33` tasks are complete (`100%`)
- By engineering effort against the current SDD, the planned backlog is complete
- The post-SDD expansion backlog (`H-001` through `H-008`) is complete, with `8 / 8` expansion tasks complete
- `R-001`, `R-002`, and `R-003` are complete, including live provider, live gateway/data-plane, and live native grpc server-stream proof points
- The protocol-completion backlog (`P-001` through `P-006`) is complete, with `6 / 6` tasks complete
- The final cross-protocol `LLM-enabled E2E` proof roadmap (`L-001` through `L-006`) is complete: `L-001` remains the live OpenAPI + DeepSeek baseline, `L-002` through `L-006` cover local GraphQL, REST, gRPC, SOAP, and SQL proofs, and local E2E can now opt into a real DeepSeek enhancer path with `ENABLE_REAL_DEEPSEEK_E2E`
- OpenAPI, GraphQL, REST, gRPC unary/server-stream, SOAP, and SQL are now live-proven slices, and the compiler-managed protocols have passed the authoritative joint `PROTOCOL=all` GKE matrix in namespace `tool-compiler-llm-all-024755`; the B-003 REST OPTIONS + dedup slice was revalidated with the same aggregate audit (**13/13/7/7/0/6**) in namespace `tool-compiler-llm-b003-032621` at image tag `20260327-75be3a5-r29`
- Real GKE Helm validation, real GKE queue-path validation, repeated deployed production-activity validation, the final cross-protocol live matrix, and a published broker-aware worker-image rerun baseline have all been exercised against the test cluster, so further work should be treated as post-backlog hardening, capability expansion, or productionization rather than unfinished SDD scope
- The next unresolved confidence gap is not protocol support but black-box coverage: proving discovered endpoint coverage, generated-tool coverage, and real invocation pass rate for large services without authoritative specs
- **Product UI (shipped):** A first-party web UI for operators and tenants is now implemented in `apps/web-ui/`. Built with Next.js 16, TypeScript, Tailwind CSS, shadcn/ui, TanStack React Query, and Zustand. **102 source files, ~25,500 lines of code, 16 routes.** Covers: login (password + PAT), dashboard with stat cards and recent activity, compilation wizard (4-step: source → protocol → auth → review), compilation list with filters and job detail with SSE event streaming, service registry (grid/list with protocol/risk/intent badges), Monaco IR editor with dual code/tree views, review/approval workflow state machine (draft → submitted → in_review → approved/rejected → published → deployed), version diff viewer, policy CRUD with evaluation tester, PAT management, audit log with filters and CSV export, gateway route management with reconciliation, and observability dashboards (Grafana iframes). **Human review and approval are mandatory scope**, as required by the SDD: the console supports explicit review/edit of IR, recorded decisions, and gated promotion. **Test suite:** 318 unit tests (Vitest + React Testing Library) covering stores, API client, hooks, and 22 component test files; plus 32 E2E tests (Playwright) covering login, navigation, compilation wizard, theme toggling, and responsive layout. All tests passing. CI workflow at `.github/workflows/web-ui.yml` (lint → typecheck → build). Docker multi-stage build in `apps/web-ui/Dockerfile`.

## AI Maintenance Requirements

This project is still within the range that strong coding agents can maintain effectively, but only under explicit operating constraints:

1. Keep `../tool-compiler-v2-sdd.md`, `./agent.md`, and `./devlog.md` current whenever scope, sequencing, or implementation status changes.
2. Preserve hard quality gates in the documented order: prefer `uv run nox -s lint`, `typecheck`, `tests`, then `security deps arch` once the repo is migrated; until then, the `.venv`-backed `ruff`, `mypy`, and `pytest` equivalents must stay green before closing substantial work.
3. Prefer narrow, testable tasks with clear acceptance criteria; avoid large cross-module refactors without first updating the written plan and task context.
4. Require human review for high-risk changes, especially database schema/migrations, auth, gateway routing, Kubernetes deployment behavior, rollback logic, and secret handling.
5. Keep external system assumptions explicit in code or docs; do not rely on unstated operational knowledge.
6. When the repository grows past roughly `30k+` production lines or accumulates significant environment-specific behavior, tighten task scoping further and expect more human supervision.

## Task Reference

Full task definitions are in the SDD (`../tool-compiler-v2-sdd.md`), sections "Atomic Implementation Backlog" (T-001 through T-033) and "Post-SDD Expansion Backlog" (`H-001` through `H-008`, `R-001` through `R-003`, `P-001` through `P-006`, and `L-001` through `L-006`). The follow-on black-box exploration track lives in `./docs/post-sdd-modular-expansion-plan.md`; it remains outside the original SDD-owned committed delivery backlog, but `B-001` has now started with the first generated-tool audit slice.

## Important Conventions

- **All source tracking:** Every field that could be LLM-generated carries `source` (extractor/llm/user_override) and `confidence` (0.0–1.0).
- **Risk classification:** Semantic, not HTTP-method-based. Each operation carries `RiskMetadata` with `writes_state`, `destructive`, `external_side_effect`, `idempotent`.
- **Extractor purity:** Extractors never call LLM. All their output is `source: "extractor"`.
- **IR versioning:** `ir_version` follows semver. Breaking changes = major bump.
- **Test-first:** Every module has a `tests/` directory. Property-based tests (Hypothesis) where applicable.

## Git Conventions

- **Remote:** The canonical collaboration remote is the private GitHub repository `xingyug/service2mcp` (`main`). If a public copy is published later, prefer a fresh export without importing private history (see README).
- **Secrets scanning (mandatory before push):** Run `gitleaks` on the repository before every `git push` (and before pushing any commit that adds or changes files that might contain secrets). Use `make gitleaks` from the repo root; fix or allowlist any findings before pushing. Optionally install `scripts/git-hooks/pre-push.sample` as `.git/hooks/pre-push` so the scan runs automatically on push.
- Commit messages prefixed with task ID: `T-00X: <description>` or backlog id (`B-00X:`, `B-001:`, etc.) when applicable
- One commit per task completion when practical

## Environment Notes

- Manual LLM-path testing on this VM may use the DeepSeek official API endpoint together with the user-provided API key stored at `/home/guoxy/esoc-agents/.deepseek_api_key`.
- The local operator entrypoint for the minimal real-provider matrix is `make e2e-real-deepseek-smoke`; it defaults to the GraphQL + SQL local E2E proofs and reads the same VM-local key file unless `LLM_API_KEY_FILE` is overridden.
- Treat that key as local operator state only: never copy the secret into repository files, tests, fixtures, logs, or generated artifacts.
