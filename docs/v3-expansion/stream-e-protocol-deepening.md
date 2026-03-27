# Stream E: Protocol Deepening + Governance

## Goal

Make the existing 6 protocols production-grade with:
1. Unified error model across all protocols
2. Response examples generation for LLM enhancement quality
3. Drift detection framework for deployed services
4. Enhanced pagination inference
5. OpenAPI deep extraction improvements

## Scope

- **In scope:** Error normalization, examples generation, drift detection, pagination improvements for existing 6 protocols.
- **Out of scope:** New protocols (that's Streams A/B/C), IR schema changes (that's Stream D).

---

## Architecture

```
Existing 6 Protocols (OpenAPI, REST, GraphQL, gRPC, SOAP, SQL)
        │
        ▼
┌─────────────────────────┐
│ Enhanced Extractors     │  (existing files, additive changes)
│ + examples generation   │
│ + better pagination     │
│ + error normalization   │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ Unified Error Model     │  libs/ir/models.py (additive)
│ ErrorSchema on Operation│
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ Drift Detector          │  libs/validator/drift.py (NEW)
│ Compare live vs compiled│
└─────────────────────────┘
```

## IR Extension

### ErrorSchema (unified error model)

```python
class ErrorResponse(BaseModel):
    """A single documented error response for an operation."""

    status_code: int | None = None  # None for non-HTTP protocols
    error_code: str | None = None  # protocol-specific error code
    description: str = ""
    schema: dict[str, Any] | None = None  # JSON Schema of error body

class ErrorSchema(BaseModel):
    """Unified error model for an operation, normalized across protocols."""

    responses: list[ErrorResponse] = Field(default_factory=list)
    default_error_schema: dict[str, Any] | None = None  # fallback error shape
```

On `Operation`:
```python
error_schema: ErrorSchema = Field(default_factory=ErrorSchema)
```

### ResponseExample

```python
class ResponseExample(BaseModel):
    """A synthetic or extracted example response for LLM context."""

    name: str = Field(min_length=1)
    description: str = ""
    status_code: int | None = None
    body: dict[str, Any] | str | None = None
    source: SourceType = SourceType.extractor
```

On `Operation`:
```python
response_examples: list[ResponseExample] = Field(default_factory=list)
```

## Modified Files

| File | Change |
|---|---|
| `libs/ir/models.py` | Add `ErrorResponse`, `ErrorSchema`, `ResponseExample`, fields on `Operation` |
| `libs/extractors/openapi.py` | Extract error responses, response examples from OpenAPI specs |
| `libs/extractors/graphql.py` | Generate error schema for GraphQL errors |
| `libs/extractors/grpc.py` | Map gRPC status codes to error schema |
| `libs/extractors/soap.py` | Map SOAP faults to error schema |
| `libs/extractors/sql.py` | Generate error schema for SQL errors |
| `libs/extractors/rest.py` | Infer error patterns from discovery |
| `libs/enhancer/enhancer.py` | Examples generation pass, error normalization pass |
| `apps/compiler_worker/activities/production.py` | Add error normalization to pipeline |

## New Files

| File | Purpose |
|---|---|
| `libs/validator/drift.py` | Drift detection: compare deployed IR vs live source |
| `libs/enhancer/examples_generator.py` | LLM-assisted response example generation |
| `libs/enhancer/error_normalizer.py` | Cross-protocol error model normalization |
| `libs/validator/tests/test_drift.py` | Drift detection tests |
| `libs/enhancer/tests/test_examples_generator.py` | Examples generation tests |
| `libs/enhancer/tests/test_error_normalizer.py` | Error normalizer tests |

---

## Task Backlog

### DEP-001: ErrorSchema and ResponseExample models
**File:** `libs/ir/models.py`
**What:** Add `ErrorResponse`, `ErrorSchema`, `ResponseExample` Pydantic models. Add `error_schema: ErrorSchema = Field(default_factory=ErrorSchema)` and `response_examples: list[ResponseExample] = Field(default_factory=list)` to `Operation`.
**Backward compat:** Both default to empty — existing IRs unaffected.
**Tests:** Unit tests for new models, verify defaults.
**Exit:** `pytest libs/ir/tests/ -q` green, `mypy` clean, zero existing test breakage.

### DEP-002: OpenAPI error response extraction
**File:** `libs/extractors/openapi.py`
**What:** Extract `responses` section from OpenAPI operations:
- Map `4xx`/`5xx` responses to `ErrorResponse` entries
- Extract `default` response as `default_error_schema`
- Extract `examples` from response content (OpenAPI 3.x `examples` field)
- Map to `ResponseExample` entries
**Tests:** OpenAPI fixture with error responses and examples → verify extraction.
**Exit:** Error responses and examples populated for operations that have them.

### DEP-003: OpenAPI response examples extraction
**File:** `libs/extractors/openapi.py`
**What:** Extract inline `example` and `examples` from OpenAPI response schemas:
- Single `example` field → one `ResponseExample`
- `examples` map → multiple `ResponseExample` entries
- Schema-level `example` → `ResponseExample` with generated name
**Tests:** Fixture with various example formats.
**Exit:** All example formats extracted correctly.

### DEP-004: GraphQL/gRPC/SOAP/SQL error normalization
**Files:** `libs/extractors/graphql.py`, `grpc.py`, `soap.py`, `sql.py`
**What:** Each protocol generates its own error schema:
- **GraphQL:** Standard `{"errors": [{"message": str, "locations": [...], "path": [...]}]}` shape
- **gRPC:** Map gRPC status codes (INVALID_ARGUMENT, NOT_FOUND, etc.) to ErrorResponse entries
- **SOAP:** Map SOAP Fault structure (`faultcode`, `faultstring`, `detail`) to ErrorResponse
- **SQL:** Map common SQL errors (constraint violation, syntax error, timeout) to ErrorResponse
**Tests:** Verify each protocol produces correct error schema shape.
**Exit:** All 4 protocols produce non-empty error_schema on extracted operations.

### DEP-005: Error normalizer (enhancer pass)
**File:** `libs/enhancer/error_normalizer.py`
**What:** Post-extraction normalization pass:
- Ensure all operations have at least a `default_error_schema`
- For HTTP operations without explicit errors, infer standard 4xx/5xx patterns
- Normalize error code formats across protocols
- Enrich error descriptions with LLM if they're empty
**Tests:** IR with mixed protocols → all operations get normalized error schemas.
**Exit:** No operation leaves the enhancer without an error schema.

### DEP-006: LLM examples generator
**File:** `libs/enhancer/examples_generator.py`
**What:** Generate synthetic response examples for operations that don't have them:
- Use the response_schema to generate realistic example data
- For list operations: generate example with 2-3 items
- For single-item operations: generate one realistic example
- Use LLM (DeepSeek) to make examples semantically meaningful (not just random data)
- Mark generated examples with `source=SourceType.llm`
**Tests:** Generate examples for operations with schema but no examples.
**Exit:** Generated examples validate against response_schema.

### DEP-007: Enhanced pagination inference
**Files:** `libs/extractors/openapi.py`, `rest.py`
**What:** Improve pagination detection:
- Detect cursor-based pagination from response schema (fields like `next_cursor`, `next_page_token`)
- Detect Link header pagination (RFC 8288)
- Detect envelope-style pagination (`{"data": [...], "meta": {"total": N, "page": M}}`)
- Set `PaginationConfig.style` and params automatically
**Tests:** Fixtures with various pagination patterns.
**Exit:** Pagination detected for at least 3 different patterns.

### DEP-008: Drift detection framework
**File:** `libs/validator/drift.py`
**What:** Compare a deployed `ServiceIR` against a fresh extraction from the same source:
```python
class DriftReport(BaseModel):
    """Report of differences between deployed IR and live source."""

    service_id: str
    checked_at: datetime
    has_drift: bool
    added_operations: list[str]  # new ops in source not in deployed
    removed_operations: list[str]  # ops in deployed not in source
    modified_operations: list[DriftDetail]  # ops with param/type changes
    schema_changes: list[str]  # structural changes

class DriftDetail(BaseModel):
    operation_id: str
    changes: list[str]  # human-readable change descriptions
```

Implement `detect_drift(deployed_ir: ServiceIR, live_ir: ServiceIR) -> DriftReport`.
**Comparison logic:**
- Operation set difference (added/removed)
- Per-operation: param changes (added/removed/type changed), risk level changes, path changes
- Schema-level: auth config changes, base URL changes
**Tests:** Create two IRs with known differences, verify drift report.
**Exit:** Drift correctly detected for add/remove/modify scenarios.

### DEP-009: Drift detection integration
**File:** `libs/validator/drift.py`
**What:** Add `check_drift_from_source(deployed_ir: ServiceIR, source: SourceConfig) -> DriftReport`:
- Re-extract from source
- Compare against deployed IR
- Return drift report
This is the entry point for scheduled drift checks.
**Tests:** Mock extraction + comparison.
**Exit:** Full drift check flow works.

### DEP-010: Pipeline integration
**File:** `apps/compiler_worker/activities/production.py`
**What:** Add error normalization and examples generation to the enhance stage:
- After LLM enhancement, run error normalizer
- After error normalizer, run examples generator (if LLM client available)
- Both are additive — they only populate empty fields, never overwrite extractor data
**Tests:** Verify enhanced IR has error schemas and examples.
**Exit:** Pipeline produces richer IR without breaking existing behavior.

### DEP-011: REST discovery pagination improvement
**File:** `libs/extractors/rest.py`
**What:** Improve REST discovery's pagination inference:
- Probe for `Link` header in responses
- Detect `offset`/`limit` vs `page`/`per_page` vs `cursor` patterns from response body shape
- Set pagination config based on probing results
**Tests:** Mock responses with different pagination styles.
**Exit:** REST discovery correctly identifies pagination style.

### DEP-012: Capability matrix notes update
**File:** `libs/validator/capability_matrix.py`
**What:** Update notes for all 6 existing protocols to reflect new capabilities:
- Error model: ✅
- Response examples: ✅
- Drift detection: ✅
**Tests:** Notes updated, matrix still renders correctly.
**Exit:** Matrix reflects production-grade status.

---

## Key Design Decisions

1. **Error normalization is a separate enhancer pass, not per-extractor.** Extractors extract what they know (protocol-native errors). The normalizer ensures consistency. This keeps extractors focused.
2. **Examples generation uses LLM only when schemas exist.** No schema = no generated example. We don't guess at response shapes.
3. **Drift detection is offline, not real-time.** It compares two IRs — it doesn't watch for live changes. Scheduled drift checks are a deployment concern.
4. **All new fields are additive with empty defaults.** Zero breakage for existing IRs, existing tests, existing deployments.
5. **Enhancer passes are ordered:** LLM enhancement → error normalization → examples generation. Each pass can use the output of previous passes.
