# Stream C: Enterprise Protocols (OData, SCIM, JSON-RPC)

## Goal

Add extractors for three common enterprise protocols, following the proven extractor pattern.  Each produces standard `ServiceIR` and reuses the existing HTTP runtime proxy.

## Scope

- **In scope:** OData v4 metadata extraction, SCIM 2.0 schema extraction, JSON-RPC 2.0 method discovery.
- **Out of scope:** OData v2/v3, SCIM provisioning proxy, JSON-RPC over WebSocket.

---

## Architecture

All three extractors follow the identical pattern established by `openapi.py`, `graphql.py`, etc.:

```
Metadata/Schema/Spec document
        │
        ▼
┌───────────────────┐
│ XyzExtractor      │  libs/extractors/{odata,scim,jsonrpc}.py
│ protocol_name=    │
│   "odata"|"scim"  │
│   |"jsonrpc"      │
│ detect() → float  │
│ extract() → ServiceIR│
└───────┬───────────┘
        │
        ▼ ServiceIR (protocol="odata"|"scim"|"jsonrpc")
        │   Standard Operation model with method/path/params
        │
┌───────────────────┐
│ RuntimeProxy      │  Existing HTTP proxy — no changes needed
│ (existing)        │
└───────────────────┘
```

**Key insight:** OData, SCIM, and JSON-RPC are all HTTP-based.  They produce standard `Operation` models with `method` and `path`.  The existing `RuntimeProxy` handles them without modification — the protocol complexity lives entirely in the extractor.

## New Files

| File | Purpose |
|---|---|
| `libs/extractors/odata.py` | OData v4 $metadata parser → ServiceIR |
| `libs/extractors/scim.py` | SCIM 2.0 /Schemas + /ServiceProviderConfig parser → ServiceIR |
| `libs/extractors/jsonrpc.py` | JSON-RPC 2.0 method discovery → ServiceIR |
| `tests/fixtures/odata_metadata/` | OData $metadata XML fixtures |
| `tests/fixtures/scim_schemas/` | SCIM schema JSON fixtures |
| `tests/fixtures/jsonrpc_specs/` | JSON-RPC spec/discovery fixtures |
| `libs/extractors/tests/test_odata.py` | Unit tests |
| `libs/extractors/tests/test_scim.py` | Unit tests |
| `libs/extractors/tests/test_jsonrpc.py` | Unit tests |
| `tests/integration/test_mcp_runtime_odata.py` | Integration test |
| `tests/integration/test_mcp_runtime_scim.py` | Integration test |
| `tests/integration/test_mcp_runtime_jsonrpc.py` | Integration test |

## Modified Files

| File | Change |
|---|---|
| `libs/extractors/__init__.py` | Register all three extractors |
| `libs/validator/capability_matrix.py` | Add `odata`, `scim`, `jsonrpc` rows |

**Note:** No IR model changes needed — these protocols map cleanly to existing `Operation` with standard `method`/`path`/`params`.

---

## OData v4 Extractor

### ENT-001: OData $metadata XML parser
**File:** `libs/extractors/odata.py`
**What:** Parse OData v4 $metadata EDMX XML document:
- Extract `EntityType` → entity definitions with properties and keys
- Extract `EntitySet` → collections with CRUD operations
- Extract `FunctionImport` / `ActionImport` → custom operations
- Extract `NavigationProperty` → relationship traversals
- Handle `Edm` primitive type mapping to JSON Schema types
**Parsing:** Use `xml.etree.ElementTree` (stdlib). OData metadata is well-structured XML with `edmx:` and `edm:` namespaces.
**Tests:** At least 2 fixtures (simple entity model, complex with navigation properties).
**Exit:** Parser extracts all entity types and operations from fixtures.

### ENT-002: OData extractor detect() + extract()
**File:** `libs/extractors/odata.py`
**What:**
`detect()`:
- `hints["protocol"] == "odata"` → 1.0
- URL ends in `$metadata` → 0.95
- Content contains `<edmx:Edmx` → 0.9
- Otherwise → 0.0

`extract()`:
- For each EntitySet, generate operations:
  - `list_{entity_set}` — GET with $filter, $select, $top, $skip, $orderby params (risk=safe)
  - `get_{entity_set}_by_key` — GET by primary key (risk=safe)
  - `create_{entity_set}` — POST with entity properties as params (risk=cautious)
  - `update_{entity_set}` — PATCH with entity properties (risk=cautious)
  - `delete_{entity_set}` — DELETE by key (risk=dangerous)
- For each FunctionImport, generate a GET operation (risk=safe)
- For each ActionImport, generate a POST operation (risk=cautious)
- Paths follow OData URL conventions: `/{EntitySet}`, `/{EntitySet}({key})`
- OData query params ($filter, $select, etc.) become `Param` entries
**Tests:** Extract from fixtures, verify operation count, method, path, risk.
**Exit:** `ServiceIR` validates, operations match expected OData patterns.

### ENT-003: OData integration test
**File:** `tests/integration/test_mcp_runtime_odata.py`
**What:** Full path: OData $metadata fixture → extract → compile → register in runtime → call list operation via RuntimeProxy with mock HTTP response → verify $filter and $select are passed correctly in upstream request.
**Exit:** Integration test green.

---

## SCIM 2.0 Extractor

### ENT-004: SCIM schema parser
**File:** `libs/extractors/scim.py`
**What:** Parse SCIM 2.0 schemas from `/Schemas` endpoint response:
- Extract `Resource Type` schemas (User, Group, Enterprise User, custom)
- Extract attribute definitions (name, type, mutability, required, multiValued)
- Parse `/ServiceProviderConfig` for supported operations (filter, sort, patch, bulk, changePassword)
- Map SCIM attribute types to JSON Schema types
**Tests:** Fixtures for standard User/Group schemas + one custom schema.
**Exit:** All standard attribute types mapped correctly.

### ENT-005: SCIM extractor detect() + extract()
**File:** `libs/extractors/scim.py`
**What:**
`detect()`:
- `hints["protocol"] == "scim"` → 1.0
- URL contains `/scim/v2` or response has SCIM content-type → 0.9
- Content has `"schemas"` and `"urn:ietf:params:scim:schemas:"` → 0.85
- Otherwise → 0.0

`extract()`:
- For each ResourceType, generate operations:
  - `list_{resource}s` — GET /{Resources} with filter, startIndex, count params (risk=safe)
  - `get_{resource}` — GET /{Resources}/{id} (risk=safe)
  - `create_{resource}` — POST /{Resources} with schema attributes as params (risk=cautious)
  - `update_{resource}` — PUT /{Resources}/{id} (risk=cautious)
  - `patch_{resource}` — PATCH /{Resources}/{id} (risk=cautious)
  - `delete_{resource}` — DELETE /{Resources}/{id} (risk=dangerous)
- If ServiceProviderConfig supports `changePassword`, add `change_password` operation
- If ServiceProviderConfig supports `bulk`, add `bulk_operation` (risk=dangerous)
- Respect mutability: readOnly attributes excluded from create/update params
**Tests:** Extract from User+Group fixture, verify operation shapes.
**Exit:** `ServiceIR` validates, readOnly attributes not in write operations.

### ENT-006: SCIM integration test
**File:** `tests/integration/test_mcp_runtime_scim.py`
**What:** Full path: SCIM schema fixture → extract → register → call list_users via RuntimeProxy → verify SCIM filter parameter passed correctly.
**Exit:** Integration test green.

---

## JSON-RPC 2.0 Extractor

### ENT-007: JSON-RPC method discovery
**File:** `libs/extractors/jsonrpc.py`
**What:** Discover JSON-RPC methods from:
1. OpenRPC spec file (machine-readable method descriptions)
2. `rpc.discover` / `rpc.describe` introspection method call
3. Manual spec file (our `.jsonrpc.yaml` format as fallback)

Parse method names, parameter schemas, result schemas.
**Tests:** At least 2 fixtures (OpenRPC spec, manual spec).
**Exit:** Methods discovered with typed parameters.

### ENT-008: JSON-RPC extractor detect() + extract()
**File:** `libs/extractors/jsonrpc.py`
**What:**
`detect()`:
- `hints["protocol"] == "jsonrpc"` → 1.0
- Content contains `"jsonrpc": "2.0"` → 0.9
- URL response to POST with `{"jsonrpc":"2.0","method":"rpc.discover","id":1}` succeeds → 0.85
- File is OpenRPC spec (`openrpc:`) → 0.9
- Otherwise → 0.0

`extract()`:
- For each discovered method, generate one `Operation`:
  - `name` = method name (dots replaced with underscores)
  - `method` = "POST" (JSON-RPC is always POST)
  - `path` = base URL path (all methods share one endpoint)
  - Params from method parameter schemas
  - Risk classification: methods starting with `get`/`list`/`query` → safe, others → cautious
- ServiceIR metadata includes `jsonrpc_version: "2.0"`
**Tests:** Extract from fixtures, verify method mapping.
**Exit:** Operations have correct POST method, shared path, correct params.

### ENT-009: JSON-RPC runtime enhancement
**File:** `libs/extractors/jsonrpc.py` (extract-time transform)
**What:** JSON-RPC tools need special request body construction at runtime. Two approaches:
1. **Preferred:** Add `jsonrpc: JsonRpcOperationConfig | None` to `Operation` (like graphql/soap pattern) with method_name and params mapping. Runtime wraps call in `{"jsonrpc":"2.0","method":"...","params":{...},"id":...}`.
2. **Simpler:** Embed JSON-RPC envelope construction in the operation params (a `_jsonrpc_method` hidden param, body template). Runtime proxy handles it transparently.

Go with option 1 for consistency with other protocol-specific configs.
**IR change:** Add `JsonRpcOperationConfig` model, `jsonrpc` field on `Operation`, mutual exclusion validator.
**Tests:** Verify envelope construction.
**Exit:** JSON-RPC calls produce correct JSON-RPC 2.0 envelope.

### ENT-010: JSON-RPC integration test
**File:** `tests/integration/test_mcp_runtime_jsonrpc.py`
**What:** Full path: OpenRPC spec → extract → register → call method via runtime → verify JSON-RPC 2.0 envelope sent to upstream mock, response unwrapped correctly.
**Exit:** Integration test green.

### ENT-011: Capability matrix updates
**File:** `libs/validator/capability_matrix.py`
**What:** Add `odata`, `scim`, `jsonrpc` rows.
**Tests:** Matrix includes all three with correct flags.
**Exit:** All three rows render correctly.

### ENT-012: Extractor registration
**File:** `libs/extractors/__init__.py`
**What:** Register `ODataExtractor`, `SCIMExtractor`, `JsonRpcExtractor` in the default TypeDetector registry.
**Tests:** `TypeDetector.detect_all()` includes all three for matching inputs.
**Exit:** All three respond to their respective protocol hints.

---

## Key Design Decisions

1. **No new runtime adapters for OData and SCIM.** They're HTTP-based and the existing RuntimeProxy handles them. Protocol complexity is extracted away at compile time.
2. **JSON-RPC needs a runtime config** because the request envelope is protocol-specific (like SOAP/GraphQL). This follows the established `Operation.graphql` / `Operation.soap` pattern.
3. **OData $filter is passed as a string param.** We don't try to decompose OData filter syntax into structured params — that's the upstream API's query language.
4. **SCIM attribute mutability drives param generation.** readOnly attributes aren't exposed as create/update params. This is compile-time intelligence.
5. **OpenRPC is the preferred JSON-RPC discovery mechanism.** Manual spec files are the fallback for APIs without introspection.
