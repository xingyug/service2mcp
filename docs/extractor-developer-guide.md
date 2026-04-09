# Protocol Extractor Developer Guide

> **Audience:** Developers adding new protocol extractors to service2mcp.
> See also: [Architecture Deep-Dive](architecture.md) · [IR Models](../libs/ir/models.py) · [ADR Index](adr/)

---

## 1. Extractor Architecture

Extractors run as **stage ②** of the compilation pipeline. The `TypeDetector`
(stage ①) calls every extractor's `detect()` and picks the highest confidence.
The winner's `extract()` produces a `ServiceIR` consumed by all downstream stages.

```
 Source (URL / file / inline content)
   │
   ▼
 ① detect ── TypeDetector picks extractor by confidence (base.py)
   │
   ▼
 ② extract ── Your extractor → ServiceIR           ← YOU ARE HERE
   │
   ▼
 ③ enhance ── LLM enrichment (libs/enhancer/)      ← source="llm"
   │
   ▼
 ④–⑨ validate → generate → deploy → validate_runtime → route → register
```

### Extractor purity rule (non-negotiable)

> **Extractors must NOT call LLMs.** All output uses `source: SourceType.extractor`.

LLM work lives exclusively in `libs/enhancer/`. If extraction needs probabilistic
reasoning, set a lower `confidence` and let the enhancer refine downstream.

| Concern | Extractor (`source: "extractor"`) | Enhancer (`source: "llm"`) |
|---------|-----------------------------------|----------------------------|
| IDs, names, types | ✅ Extracts from spec | ❌ Never changes |
| Descriptions | ✅ Copies from spec (may be empty) | ✅ Improves/generates |
| Risk metadata | ✅ Deterministic rules | ✅ May refine |
| Tool intent / grouping | ❌ Leave as `None` / empty | ✅ Derives |

---

## 2. Creating a New Extractor

Walk-through: a hypothetical `libs/extractors/asyncapi.py`.

### The ExtractorProtocol interface (`libs/extractors/base.py`)

```python
@runtime_checkable
class ExtractorProtocol(Protocol):
    protocol_name: str
    def detect(self, source: SourceConfig) -> float: ...   # 0.0–1.0
    def extract(self, source: SourceConfig) -> ServiceIR: ...
```

### Minimal extractor skeleton

```python
"""AsyncAPI extractor — parses AsyncAPI 2.x/3.x specs into ServiceIR."""
from __future__ import annotations
import json, logging
from typing import Any
import yaml
from libs.extractors.base import SourceConfig
from libs.extractors.utils import compute_content_hash, get_content, slugify
from libs.ir.models import (
    AuthConfig, AuthType, Operation, Param,
    RiskLevel, RiskMetadata, ServiceIR, SourceType,
)

logger = logging.getLogger(__name__)

class AsyncAPIExtractor:
    protocol_name: str = "asyncapi"

    def detect(self, source: SourceConfig) -> float:
        content = get_content(source)
        if content is None:
            return 0.0
        try:
            spec = self._parse(content)
        except (json.JSONDecodeError, yaml.YAMLError, ValueError):
            return 0.0
        return 0.95 if "asyncapi" in spec else 0.0

    def extract(self, source: SourceConfig) -> ServiceIR:
        content = get_content(source)
        if content is None:
            raise ValueError("Could not read AsyncAPI source content")
        spec = self._parse(content)
        return ServiceIR(
            source_url=source.url,
            source_hash=compute_content_hash(content),
            protocol="asyncapi",
            service_name=slugify(spec.get("info", {}).get("title", "asyncapi-service")),
            service_description=spec.get("info", {}).get("description", ""),
            base_url=self._base_url(spec, source),
            auth=self._auth(spec),
            operations=self._operations(spec),
            metadata={"asyncapi_version": spec.get("asyncapi", "unknown")},
        )

    def _parse(self, content: str) -> dict[str, Any]:
        c = content.strip()
        return json.loads(c) if c.startswith("{") else yaml.safe_load(c)

    def _base_url(self, spec: dict, source: SourceConfig) -> str:
        for srv in (spec.get("servers") or {}).values():
            if isinstance(srv, dict) and "url" in srv:
                return srv["url"]
        return source.url or "http://localhost"

    def _auth(self, spec: dict) -> AuthConfig:
        schemes = spec.get("components", {}).get("securitySchemes", {})
        for scheme in schemes.values():
            if scheme.get("type") == "http" and scheme.get("scheme") == "bearer":
                return AuthConfig(type=AuthType.bearer,
                                  header_name="Authorization", header_prefix="Bearer")
        return AuthConfig(type=AuthType.none)

    def _operations(self, spec: dict) -> list[Operation]:
        ops: list[Operation] = []
        for ch_name, ch in (spec.get("channels") or {}).items():
            if not isinstance(ch, dict):
                continue
            for action in ("publish", "subscribe"):
                op = ch.get(action)
                if not isinstance(op, dict):
                    continue
                op_id = op.get("operationId", f"{action}_{slugify(ch_name)}")
                is_read = action == "subscribe"
                ops.append(Operation(
                    id=op_id, name=op.get("summary", op_id),
                    description=op.get("description", ""),
                    method="POST", path=ch_name,
                    params=self._params(op),
                    risk=RiskMetadata(
                        writes_state=not is_read, destructive=False,
                        idempotent=is_read,
                        risk_level=RiskLevel.safe if is_read else RiskLevel.cautious,
                        confidence=0.85, source=SourceType.extractor,
                    ),
                    tags=["asyncapi", action],
                    source=SourceType.extractor, confidence=0.85, enabled=True,
                ))
        return ops

    def _params(self, op: dict) -> list[Param]:
        payload = op.get("message", {}).get("payload", {})
        required = set(payload.get("required", []))
        return [
            Param(name=k, type=v.get("type", "string"), required=k in required,
                  description=v.get("description", ""),
                  source=SourceType.extractor, confidence=0.85)
            for k, v in payload.get("properties", {}).items()
        ]
```

---

## 3. ServiceIR Contract

The IR (`libs/ir/models.py`) is the product — extractors produce it, everything
else consumes it.

### Required `ServiceIR` fields

| Field | Type | Notes |
|-------|------|-------|
| `source_hash` | `str` | SHA-256 via `compute_content_hash()` |
| `protocol` | `str` | `"openapi"`, `"graphql"`, `"asyncapi"`, etc. |
| `service_name` | `str` | Slugified, non-empty (use `slugify()`) |
| `base_url` | `str` | Upstream API base URL |
| `operations` | `list[Operation]` | At least one for a useful IR |

### Operation essentials

```python
Operation(
    id="getUser",                   # unique within IR (REQUIRED)
    name="Get User",                # human-readable (REQUIRED)
    description="Fetch user by ID", # from spec; may be empty
    method="GET", path="/users/{id}",
    params=[Param(name="id", type="string", required=True,
                  source=SourceType.extractor, confidence=0.9)],
    risk=RiskMetadata(writes_state=False, destructive=False,
                      idempotent=True, risk_level=RiskLevel.safe,
                      confidence=0.9, source=SourceType.extractor),
    source=SourceType.extractor, confidence=0.9, enabled=True,
)
```

**Key validators:**
- `unknown_risk_must_be_disabled` — operations with `risk_level=unknown` are auto-disabled.
- Operation IDs must be unique within a `ServiceIR`.
- Protocol configs (`graphql`, `grpc_unary`, `soap`, `sql`, `jsonrpc`) are mutually exclusive.

### Source tracking & confidence

All extractor-sourced fields use `source=SourceType.extractor`. The `Param` model
enforces `confidence >= 0.8` for extractor sources — lower values raise `ValidationError`.

| Confidence | Meaning |
|------------|---------|
| ≥ 0.9 | Directly from spec, no ambiguity |
| 0.8–0.9 | Inferred from spec structure (e.g. path template params) |
| < 0.8 | **Invalid** for `source="extractor"` — use only with `source="llm"` |

### RiskMetadata rules

Use deterministic rules, not guessing:

| Signal | `risk_level` |
|--------|-------------|
| HTTP GET / HEAD / OPTIONS, GraphQL query | `safe` |
| HTTP POST / PUT / PATCH, GraphQL mutation | `cautious` |
| HTTP DELETE, method name `delete*` / `remove*` | `dangerous` |
| Unknown / ambiguous | `unknown` (operation will be auto-disabled) |

---

## 4. Protocol Detection

### Confidence scale

| Score | Meaning | Example |
|-------|---------|---------|
| 0.0 | Not this protocol | JSON with no AsyncAPI key |
| 0.3–0.5 | URL/filename hint only | URL contains `"asyncapi"` |
| 0.6–0.8 | Structural match | Has `channels` + `info` keys |
| 0.9–0.98 | Key discriminator present | `"asyncapi": "2.6.0"` in doc |

### Patterns from existing extractors

**OpenAPI** — checks for `"openapi"` / `"swagger"` keys → 0.95.
**SOAP** — `.wsdl` extension + XML content → 0.98; XML content alone → 0.95.
**GraphQL** — parses introspection JSON → 0.95; URL hint `"graphql"` → 0.4.

### Rules for `detect()`

1. **Never raise** — catch all parse errors and return 0.0.
2. Prefer content inspection over URL heuristics for higher confidence.
3. Avoid network calls in `detect()` unless the protocol requires live probing.
4. Use `SourceConfig.hints` for disambiguation when content is ambiguous.

---

## 5. Shared Utilities

`libs/extractors/utils.py` — pure helpers, no LLM calls.

| Function | Signature | Purpose |
|----------|-----------|---------|
| `slugify` | `(text, *, camel_case=False, default="unnamed") → str` | Kebab-case slug for names/IDs |
| `get_auth_headers` | `(source: SourceConfig) → dict[str, str]` | Build `Authorization` header from source |
| `get_content` | `(source, *, timeout=30.0) → str \| None` | Three-tier fallback: `file_content → file_path → url` |
| `compute_content_hash` | `(content: str \| bytes) → str` | SHA-256 hex digest for `source_hash` |

```python
slugify("My Cool Service")              # → "my-cool-service"
slugify("MyService", camel_case=True)   # → "my-service"
get_content(source)                      # returns str or None, never raises
compute_content_hash(raw_content)        # → "a1b2c3..."
```

> **Override `get_content` only when needed.** GraphQL overrides it to POST an
> introspection query — most protocols can use the default GET-based fetch.

---

## 6. Testing Requirements

### File layout

```
libs/extractors/tests/test_<protocol>.py    # unit tests
tests/fixtures/<protocol>_specs/            # fixture files
```

### What tests must cover

1. **Detection** — high confidence for valid specs, 0.0 for other formats
2. **Extraction** — valid `ServiceIR` with correct field mapping
3. **Parameters** — types, required flags, descriptions
4. **Risk** — deterministic risk levels per operation kind
5. **Auth** — security schemes → `AuthConfig`
6. **Edge cases** — empty specs, missing fields, malformed input
7. **Source tracking** — all fields use `source=SourceType.extractor`, confidence ≥ 0.8

### Example test

```python
from libs.extractors.base import SourceConfig
from libs.extractors.asyncapi import AsyncAPIExtractor
from libs.ir.models import RiskLevel, SourceType

def test_detects_asyncapi_spec() -> None:
    ext = AsyncAPIExtractor()
    assert ext.detect(SourceConfig(
        file_content='{"asyncapi": "2.6.0", "info": {"title": "T"}}')) >= 0.9

def test_rejects_openapi() -> None:
    ext = AsyncAPIExtractor()
    assert ext.detect(SourceConfig(file_content='{"openapi": "3.0.0"}')) == 0.0

def test_extract_produces_valid_ir() -> None:
    ext = AsyncAPIExtractor()
    ir = ext.extract(SourceConfig(file_content=SPEC, url="https://example.com"))
    assert ir.protocol == "asyncapi"
    assert ir.service_name and ir.source_hash
    assert len(ir.operations) > 0
    for op in ir.operations:
        assert op.source == SourceType.extractor
        assert op.confidence >= 0.8
        assert op.risk.risk_level != RiskLevel.unknown

def test_extract_raises_on_empty() -> None:
    with pytest.raises(ValueError, match="Could not read"):
        AsyncAPIExtractor().extract(SourceConfig(url="https://bad.example.com"))
```

---

## 7. Auth Extraction

### `AuthConfig` essentials

```python
AuthConfig(
    type=AuthType.bearer,           # bearer|basic|api_key|custom_header|oauth2|none
    header_name="Authorization",
    header_prefix="Bearer",
)
```

### Patterns

**From spec security schemes** (OpenAPI, AsyncAPI — see §2 example above).

**From `SourceConfig` hints** (GraphQL, gRPC):
```python
def _derive_auth(self, source: SourceConfig) -> AuthConfig:
    if source.auth_header or source.auth_token:
        return AuthConfig(type=AuthType.bearer,
                          header_name="Authorization", header_prefix="Bearer")
    return AuthConfig(type=AuthType.none)
```

**Rules:** If multiple schemes exist, log info and use the first supported one.
Never invent schemes the spec doesn't declare. Default to `AuthType.none`.

---

## 8. Error Handling

### Raise on total failure

```python
def extract(self, source: SourceConfig) -> ServiceIR:
    content = get_content(source)
    if content is None:
        raise ValueError("Could not read source content")
```

### Return partial results on partial failure

```python
for channel_name, channel_spec in channels.items():
    try:
        ops.append(self._parse_channel(channel_name, channel_spec))
    except (KeyError, TypeError, ValueError):
        logger.warning("Skipping malformed channel %s", channel_name, exc_info=True)
```

### Detection must never raise

Catch all exceptions in `detect()` and return 0.0. The `TypeDetector` has a
safety net, but clean handling is preferred.

### Error schema

For protocols with structured errors, populate `ErrorSchema`:
```python
ErrorSchema(
    default_error_schema={"type": "object", "properties": {"error": {"type": "string"}}},
    responses=[ErrorResponse(status_code=404, description="Not found")],
)
```

---

## 9. Registration

### Step 1 — Export from `libs/extractors/__init__.py`

```python
from libs.extractors.asyncapi import AsyncAPIExtractor
__all__ = [... , "AsyncAPIExtractor"]
```

### Step 2 — Add to `TypeDetector` instantiation

Find where extractors are registered (typically in `apps/compiler_worker/`):
```python
detector = TypeDetector([
    OpenAPIExtractor(),
    GraphQLExtractor(),
    AsyncAPIExtractor(),   # ← new
    ...
])
```

Order doesn't matter — selection is by highest confidence score.

### Step 3 — Update docs

Add a row to the protocol table in `docs/architecture.md`.

---

## 10. Code Examples from Existing Extractors

### OpenAPI — most complete reference (`libs/extractors/openapi.py`)

- Parses YAML/JSON with `$ref` resolution and cycle detection
- Supports Swagger 2.0 + OpenAPI 3.x
- Infers path template params (`/users/{id}` → required string param)
- Extracts pagination config, error schemas, response examples
- Maps HTTP methods to risk levels via `_METHOD_RISK` dict

### GraphQL — custom content fetch (`libs/extractors/graphql.py`)

- Overrides `_get_content()` to POST an introspection query
- Generates `GraphQLOperationConfig` with document and variable names
- Detects subscriptions → `EventDescriptor` (support level: `unsupported`)
- Builds selection sets with depth limits to avoid infinite recursion

### JSON-RPC — method-name risk heuristics (`libs/extractors/jsonrpc.py`)

- Classifies risk by method name prefixes (`get*` → safe, `delete*` → dangerous)
- Supports OpenRPC spec parsing + live `system.listMethods` fallback
- Populates `JsonRpcOperationConfig` with method name and params type
- Includes standard JSON-RPC error schema with well-known error codes

### SOAP/WSDL — XML namespace parsing (`libs/extractors/soap.py`)

- Namespace-aware XML parsing via `xml.etree.ElementTree`
- XSD → JSON Schema type mapping
- Populates `SoapOperationConfig` with target namespace, SOAP action, etc.
- Operation name prefix heuristics for risk (e.g. `Get*` → safe)

---

## New Extractor Checklist

- [ ] Implements `ExtractorProtocol` (`protocol_name`, `detect()`, `extract()`)
- [ ] `detect()` returns 0.0–1.0, never raises
- [ ] `extract()` returns valid `ServiceIR`, raises `ValueError` on total failure
- [ ] All fields use `source=SourceType.extractor`, `confidence >= 0.8`
- [ ] `RiskMetadata` uses deterministic rules
- [ ] Uses `compute_content_hash()` and `slugify()`
- [ ] No LLM calls anywhere in the extractor
- [ ] Registered in `__init__.py` and `TypeDetector`
- [ ] Tests in `libs/extractors/tests/test_<protocol>.py`
- [ ] Fixtures in `tests/fixtures/<protocol>/`
- [ ] Protocol row added to `docs/architecture.md`
- [ ] Quality gates pass: `ruff check`, `ruff format`, `mypy`, `pytest`
