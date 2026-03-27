# Tool Compiler v3 — Expansion Master Plan

## Purpose

Evolve the tool compiler from a **protocol-to-MCP-tool** system into an **enterprise capability-to-MCP** platform.  The existing 6 protocols (OpenAPI, REST discovery, GraphQL, gRPC, SOAP/WSDL, SQL) form a proven foundation.  This plan adds CLI, event systems, enterprise protocols, and deeper governance — while keeping each work stream independent enough for parallel agent execution.

## Current State Assessment

### What We Have (Proven)

| Capability | Status | Evidence |
|---|---|---|
| 6-protocol extract → compile → runtime → GKE proof | ✅ | `r29` aggregate audit 13/13/7/7/0/6 |
| Semantic risk classification | ✅ | `RiskMetadata` with writes_state/destructive/external_side_effect/idempotent |
| LLM enhancement pipeline | ✅ | DeepSeek live-proven, tool intent, tool grouping, LLM judge |
| Auth: bearer, basic, api_key, oauth2, mTLS, request-signing | ✅ | `AuthConfig` + runtime adapters |
| AuthN/AuthZ/Gateway binding/Audit | ✅ | Access Control service suite |
| Pre/post-deploy validation | ✅ | `PreDeployValidator`, `PostDeployValidator`, `validate_with_audit()` |
| Pagination, truncation, field filtering | ✅ | `ResponseStrategy`, `PaginationConfig` |
| Event descriptors (typed, unsupported-by-default) | ✅ | `EventDescriptor` for webhook/SSE/WS/gRPC stream/GraphQL subscription |
| Native gRPC (unary + server-stream) | ✅ | Reflection-backed executors |
| Native SQL execution | ✅ | Bounded query/insert via `SQLRuntimeExecutor` |
| SOAP envelope/fault execution | ✅ | Runtime SOAP adapter |
| Web UI (16 routes, review workflow) | ✅ | 25.5K TS lines, 350 tests |
| 1086 Python tests, ruff/mypy clean | ✅ | CI green |

### What We Don't Have (Gaps)

| Gap | Proposal Section | Priority |
|---|---|---|
| MCP resources and prompts (IR only produces tools) | §4 CapabilityIR | P0 |
| Unified error model across protocols | §10 | P0 |
| CLI extraction and runtime | §5 | P1A |
| AsyncAPI parsing | §6 | P1B |
| Event observation/control pattern (Kafka/RabbitMQ/Pulsar bridge) | §6 | P1B |
| OData extractor | §7.2 | P2 |
| SCIM extractor | §7.3 | P2 |
| JSON-RPC extractor | §7.1 | P2 |
| File/object-store data source extractor | §8 | P2 |
| LDAP extractor | §7.4 | P3 |
| Execution context abstraction (local/container/bastion/cluster) | §10.3 | P1A (for CLI) |
| Drift detection framework | §11.5 | P1B |
| Response examples generation | §4.1 OpenAPI deep | P0 |

### What the Proposal Overestimates

The proposal suggests 5-layer IR (SourceIR, ProtocolIR, SemanticIR, CapabilityIR, GovernanceIR).  This is over-engineered for our stage.  Our `ServiceIR` already combines protocol structure + semantic enhancement + governance metadata in one model.  The practical move is:

1. **Keep ServiceIR as the core** — it works, it's tested, it's deployed.
2. **Add `resource_definitions` and `prompt_definitions` fields** — extend, don't replace.
3. **Add protocol-specific execution configs as we've been doing** — `graphql`, `sql`, `soap`, `grpc_unary` on `Operation` are the pattern.

The proposal's "Governance Compiler" is largely built: we have AuthN/AuthZ, audit, risk classification, gateway binding.  The real gap is execution context (local vs container vs bastion) for CLI.

---

## Work Streams

Five independent streams, designed for parallel agent execution in separate worktrees.

```
                    ┌─────────────────────┐
                    │  Stream D: IR Evol  │  (P0 — 1 week)
                    │  resources + prompts│
                    └────────┬────────────┘
                             │ IR schema ready
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                   ▼
  ┌───────────────┐  ┌──────────────┐  ┌────────────────┐
  │ Stream A: CLI │  │ Stream B:    │  │ Stream C:      │
  │               │  │ AsyncAPI +   │  │ OData + SCIM + │
  │ P1A           │  │ Events  P1B  │  │ JSON-RPC   P2  │
  └───────────────┘  └──────────────┘  └────────────────┘
          │                  │                   │
          └──────────────────┼───────────────────┘
                             ▼
                    ┌─────────────────────┐
                    │  Stream E: Deepen   │  (P0 — parallel)
                    │  existing 6 protos  │
                    └─────────────────────┘
```

**True independence:** Streams A, B, C create only NEW files (new extractors, new runtime adapters, new tests).  They follow the existing `ExtractorProtocol` and emit standard `ServiceIR`.  They have zero file-level overlap with each other.

**Stream D** modifies `libs/ir/models.py` (additive, backward-compatible).  It should land first or in parallel with A/B/C since the new fields are optional.

**Stream E** modifies existing extractors and enhancer.  It does NOT conflict with A/B/C.

---

## Stream Summaries

### Stream A: CLI Support (`stream-a-cli-support.md`)

**Goal:** Compile CLI tools into governed MCP capabilities.

New files:
- `libs/extractors/cli.py` — CLI discovery + command tree extraction
- `libs/extractors/cli_output_parser.py` — JSON/table/text output parsing
- `apps/mcp_runtime/cli.py` — CLI execution adapter (local/container/sandbox)
- `tests/fixtures/cli_mocks/` — mock CLI binaries for testing
- `libs/extractors/tests/test_cli.py`
- `tests/integration/test_mcp_runtime_cli.py`

Tasks: CLI-001 through CLI-010

### Stream B: AsyncAPI + Event Systems (`stream-b-asyncapi-events.md`)

**Goal:** Turn event-driven systems into observable, controllable MCP capabilities.

New files:
- `libs/extractors/asyncapi.py` — AsyncAPI 2.x/3.x spec parser
- `apps/mcp_runtime/event_bridge.py` — event observation/control runtime
- `apps/mcp_runtime/webhook_adapter.py` — webhook management adapter
- `tests/fixtures/asyncapi_specs/` — AsyncAPI spec fixtures
- `libs/extractors/tests/test_asyncapi.py`
- `tests/integration/test_event_bridge.py`

Tasks: EVT-001 through EVT-010

### Stream C: Enterprise Protocols (`stream-c-enterprise-protocols.md`)

**Goal:** OData, SCIM, JSON-RPC extractors following the proven extractor pattern.

New files:
- `libs/extractors/odata.py` — OData v4 metadata parser + entity extraction
- `libs/extractors/scim.py` — SCIM 2.0 schema parser + resource extraction
- `libs/extractors/jsonrpc.py` — JSON-RPC 2.0 method discovery + extraction
- `tests/fixtures/odata_metadata/`, `tests/fixtures/scim_schemas/`, `tests/fixtures/jsonrpc_specs/`
- `libs/extractors/tests/test_odata.py`, `test_scim.py`, `test_jsonrpc.py`
- `tests/integration/test_mcp_runtime_odata.py`, etc.

Tasks: ENT-001 through ENT-012

### Stream D: IR Evolution (`stream-d-ir-evolution.md`)

**Goal:** Extend ServiceIR with MCP resources and prompts alongside tools.

Modified files:
- `libs/ir/models.py` — add `ResourceDefinition`, `PromptDefinition`, `resource_definitions`, `prompt_definitions`
- `libs/ir/schema.py` — schema generation for new fields
- `libs/generator/generic_mode.py` — generate resource/prompt registration manifests
- `apps/mcp_runtime/main.py` — register MCP resources and prompts from IR
- `apps/mcp_runtime/loader.py` — load resource/prompt definitions

Tasks: IRX-001 through IRX-008

### Stream E: Protocol Deepening + Governance (`stream-e-protocol-deepening.md`)

**Goal:** Make existing 6 protocols production-grade with unified error model, enhanced pagination, examples, and drift detection.

Modified files:
- Existing extractors (additive enhancements)
- `libs/enhancer/` — examples generation, error model normalization
- `libs/validator/` — drift detection, unified error model
- `apps/compiler_worker/activities/production.py` — error normalization in pipeline

Tasks: DEP-001 through DEP-012

---

## Sequencing and Dependencies

### Phase 0 (Week 1 — can start immediately)
- **Stream D** (IR Evolution): Land resource + prompt fields in ServiceIR.  This is additive and backward-compatible.  All new fields are `list[...] = Field(default_factory=list)`.
- **Stream E** (Protocol Deepening): Start with unified error model and examples generation.

### Phase 1 (Week 2–4 — parallel after Stream D IR changes land)
- **Stream A** (CLI): Fully independent new files.
- **Stream B** (AsyncAPI + Events): Fully independent new files.
- **Stream C** (Enterprise Protocols): Fully independent new files.

### Phase 2 (Week 4–5)
- Integration testing across streams
- Cross-protocol capability matrix update
- GKE live proof for new protocols

### Exit Criteria

Each stream is complete when:
1. All tasks in its backlog are done
2. `ruff check .`, `mypy`, `pytest -q` are green repo-wide
3. `agent.md` and `devlog.md` are updated
4. The protocol/capability appears in `libs/validator/capability_matrix.py`
5. At least one integration test proves the full extract → compile → runtime path

---

## Agent Assignment Guide

Each stream is designed for one AI agent working in its own git worktree.

| Stream | Worktree Branch | Agent Context Files | Key Constraint |
|---|---|---|---|
| A: CLI | `feat/cli-support` | `stream-a-cli-support.md`, `agent.md`, `libs/extractors/base.py`, `libs/ir/models.py` | New files only except for base.py registration |
| B: Events | `feat/asyncapi-events` | `stream-b-asyncapi-events.md`, `agent.md`, `libs/extractors/base.py`, `libs/ir/models.py` | New files only |
| C: Enterprise | `feat/enterprise-protocols` | `stream-c-enterprise-protocols.md`, `agent.md`, `libs/extractors/base.py`, `libs/ir/models.py` | New files only |
| D: IR Evolution | `feat/ir-evolution` | `stream-d-ir-evolution.md`, `agent.md`, `libs/ir/models.py`, `apps/mcp_runtime/main.py` | Additive IR changes only |
| E: Deepening | `feat/protocol-deepening` | `stream-e-protocol-deepening.md`, `agent.md`, existing extractor files | No new protocol families |

**Merge order:** D first (or in parallel since changes are additive), then A/B/C/E can merge in any order.

---

## What We Explicitly Defer

- Full 5-layer IR refactor (SourceIR/ProtocolIR/SemanticIR/CapabilityIR/GovernanceIR) — over-engineered for current scale
- LDAP extractor — sensitive, low ROI for initial expansion
- File/object-store data sources — valuable but shapeless; needs product definition first
- Kafka/RabbitMQ/Pulsar native consumers — Stream B builds the bridge pattern first
- Bidirectional gRPC streaming — already deferred in R-003
- Full WS-Security — already deferred in P-004
- Browser/JS crawling for REST discovery — already deferred in B-002
