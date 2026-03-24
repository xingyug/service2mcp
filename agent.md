# Tool Compiler v2 — Agent Briefing

> This file is the authoritative context document for any AI coding agent working on this project.
> Read this first. Then read the SDD (`../tool-compiler-v2-sdd.md`) for full architecture details.

## What This Project Is

**Tool Compiler v2** is an enterprise platform that automatically compiles any API (REST, GraphQL, gRPC, SQL, etc.) into a governed, observable [MCP](https://modelcontextprotocol.io/) tool server that AI agents can call through a standard protocol.

Given a URL or spec file → it detects the protocol → extracts the schema → normalizes it into an **Intermediate Representation (IR)** → generates Kubernetes manifests → deploys a tool server → registers gateway routes → provisions access control. All automated.

## Architecture (Three Planes)

```
CONTROL PLANE          BUILD PLANE              RUNTIME PLANE
├── Compiler API       ├── Type Detector         ├── Generic MCP Runtime
├── Artifact Registry  ├── Extractors (4 types)  ├── Codegen MCP Servers
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
│   │   └── tests/
│   ├── enhancer/                # LLM enhancement of IR (stub)
│   ├── validator/               # Pre-deploy + post-deploy validation (stub)
│   ├── generator/               # K8s manifest + codegen artifact generation (stub)
│   ├── registry_client/         # Client for artifact registry (stub)
│   └── observability/           # Shared metrics/tracing/logging utils (stub)
├── apps/                        # Deployable services
│   ├── compiler_api/            # FastAPI — accepts compilation requests
│   ├── compiler_worker/         # Pipeline orchestrator (Celery/Temporal)
│   ├── access_control/          # AuthN + AuthZ + gateway binding
│   └── mcp_runtime/             # Generic MCP runtime
├── tests/                       # Integration and E2E tests
│   ├── fixtures/                # Test spec files
│   │   └── openapi_specs/       # Petstore 3.0, Swagger 2.0 fixtures
│   └── conftest.py
├── migrations/                  # Alembic DB migrations
├── deploy/                      # Helm charts, docker-compose, k8s manifests
├── observability/               # Grafana dashboards, Prometheus alerts, OTel config
├── specs/                       # Detailed module specs (planned)
├── docs/                        # ADRs, quickstart guide
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

## Current Status

See `devlog.md` for detailed progress tracking.

**Completed:** T-001 through T-005 (repo structure, IR models, IR diff, extractor base, OpenAPI extractor)
**Next up:** T-006 (LLM enhancer), T-007 (observability utils), T-008 (PostgreSQL schema)

## Task Reference

Full task definitions are in the SDD (`../tool-compiler-v2-sdd.md`), section "Atomic Implementation Backlog" (T-001 through T-033). Tasks are ordered by dependency. Each task is designed for one focused coding session.

## Important Conventions

- **All source tracking:** Every field that could be LLM-generated carries `source` (extractor/llm/user_override) and `confidence` (0.0–1.0).
- **Risk classification:** Semantic, not HTTP-method-based. Each operation carries `RiskMetadata` with `writes_state`, `destructive`, `external_side_effect`, `idempotent`.
- **Extractor purity:** Extractors never call LLM. All their output is `source: "extractor"`.
- **IR versioning:** `ir_version` follows semver. Breaking changes = major bump.
- **Test-first:** Every module has a `tests/` directory. Property-based tests (Hypothesis) where applicable.

## Git Conventions

- Local git only (no GitHub remote yet)
- Commit messages prefixed with task ID: `T-00X: <description>`
- One commit per task completion
