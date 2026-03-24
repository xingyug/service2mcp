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

## Test Summary

| Module | Tests | Status |
|--------|-------|--------|
| `libs/ir/tests/test_models.py` | 26 | ✅ All passing |
| `libs/ir/tests/test_diff.py` | 12 | ✅ All passing |
| `libs/extractors/tests/test_detection.py` | 13 | ✅ All passing |
| `libs/extractors/tests/test_openapi.py` | 22 | ✅ All passing |
| **Total** | **73** | **✅ All passing** |

---

## What's Next

Per the SDD backlog, the next tasks are:

| Task | Description | Dependencies |
|------|-------------|--------------|
| T-006 | LLM enhancer (basic) | T-002 |
| T-007 | Shared observability utilities | T-001 |
| T-008 | PostgreSQL schema + Alembic migrations | T-001 |
| T-009 | Artifact registry data layer | T-002, T-008 |
| T-010 | Generic MCP runtime — IR loader + tool registration | T-002 |

T-006, T-007, and T-008 have no mutual dependencies and can be worked on in parallel.

---

## Notes

- **No GitHub remote yet** — git is local only on the VM
- **VM SA has Vertex AI permissions** — ready for T-006 LLM integration when needed
- **Multi-provider LLM support** (Anthropic/OpenAI/Vertex AI via API keys) is planned for T-006, using `LLM_API_KEY` + `LLM_PROVIDER` env vars
- **Celery + Redis** chosen as initial pipeline engine (not Temporal) per decision D1 in SDD
