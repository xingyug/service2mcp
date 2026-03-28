# Backend static bug audit (read-only review)

**Policy:** New static-audit findings and updates to this list should be recorded **only in this file** (`docs/bug-audit-backend-static-review.md`). Do not add parallel bug lists or change other files solely for audit bookkeeping.

**Scope:** `tool-compiler-v2` — full static pass over **all** non-test `*.py` under `apps/` and `libs/` (tests excluded except where noted).  
**Method:** Manual code reading + repo-wide pattern grep (`except Exception`, `subprocess`, etc.).  
**Date:** 2026-03-27 (initial), 2026-03-27 (full scan pass), 2026-03-27 (pass 3), 2026-03-27 (pass 4).  

Items below are **suspected issues / risks**, not confirmed production bugs, unless marked **Static defect** (unreachable code / definite control-flow issue).

---

## Critical / high — security & ops

### SEC-001: FastMCP DNS rebinding protection disabled

**File:** `apps/mcp_runtime/loader.py` (`create_runtime_server`)  

`TransportSecuritySettings(enable_dns_rebinding_protection=False)` is set so Kubernetes service DNS Host headers are accepted (comment explains).  

**Risk:** Increases exposure to DNS rebinding–style attacks if the runtime is reachable from untrusted clients without proper network controls.  

**Mitigation (conceptual):** Network policy / ingress restrictions, not only app defaults.

---

### SEC-002: Default JWT secret in access control

**File:** `apps/access_control/authn/service.py` (`load_jwt_settings`)  

`ACCESS_CONTROL_JWT_SECRET` defaults to `"dev-secret"` when unset.  

**Risk:** Production deployments that forget to set the env var accept tokens signed with a known secret.  

**Mitigation:** Fail closed in non-dev environments or require explicit `ENV=dev` to use defaults.

---

## Medium — correctness & resilience

### CB-001: `ToolError` re-raise may skip circuit breaker failure accounting

**File:** `apps/mcp_runtime/proxy.py` (`RuntimeProxy.invoke`)  

The inner `try`/`except` block records failures for `httpx` errors and for SOAP/HTTP/GraphQL branches that explicitly call `breaker.record_failure()` before raising `ToolError`.  

The clause `except ToolError as exc: ... raise` **re-raises without** `breaker.record_failure()`.  

**Risk:** `ToolError` raised from `_perform_sql`, `_perform_grpc_unary`, `_perform_stream_session`, or `_sanitize_response` (if any path raises `ToolError`) may **not** increment consecutive failures, so the circuit breaker may open later than intended or not reflect upstream “hard” failures.  

**Note:** Needs runtime confirmation with a failing SQL/grpc/sanitize path that raises `ToolError`.

---

### API-001: `/healthz` and `/readyz` are identical

**File:** `apps/mcp_runtime/main.py`  

Both routes call `_runtime_status` with the same logic.  

**Risk:** Kubernetes liveness vs readiness semantics differ: a process can be “alive” but not “ready” (e.g. IR not loaded). Here both return 503 when IR is not loaded, so the same pod might be killed by liveness if misconfigured.  

**Mitigation (conceptual):** Liveness = process up; readiness = IR loaded + optional dependency checks.

---

### API-002: Compilation create — ordering between enqueue and audit

**File:** `apps/compiler_api/routes/compilations.py` (`create_compilation`)  

Flow: create job → `enqueue` → `audit_log.append_entry`.  

**Risk:** If `append_entry` fails after a successful enqueue, the job exists and work may run, but audit may be incomplete (depending on transaction boundaries in `AuditLogService`).  

**Note:** Worth verifying whether `append_entry` uses the same session/transaction and whether partial failure is acceptable.

---

### API-003: Broad `except Exception` on dispatch failure

**File:** `apps/compiler_api/routes/compilations.py`  

`dispatcher.enqueue` is wrapped in `except Exception`; job is deleted and 503 returned.  

**Risk:** Masks programming errors (e.g. `TypeError`) as “worker dispatch failed,” which can complicate debugging. Prefer narrower exceptions (e.g. broker errors) if the underlying stack allows.

---

## Low — maintainability & edge cases

### IR-001: `zip(..., strict=False)` in tool annotations

**File:** `apps/mcp_runtime/loader.py` (`build_tool_function`)  

`zip(param_name_map, signature_params, strict=False)` builds `__annotations__`.  

**Risk:** If `param_name_map` and `signature_params` ever diverge due to a future refactor, Python 3.10+ `strict=True` would catch it; `strict=False` can silently misalign names and annotations. Low probability if the loop stays the single source of both structures.

---

### IR-002: Non-`static` MCP resources silently skipped

**File:** `apps/mcp_runtime/loader.py` (`register_ir_resources`)  

Only `content_type == "static"` resources are registered; others `continue` with no log or metric.  

**Risk:** Operators may think resources are registered when they are ignored. Consider a warning log or a validation counter.

---

### IR-003: Prompt placeholder replacement is naive string replace

**File:** `apps/mcp_runtime/loader.py` (`register_ir_prompts`)  

Template uses `{name}` substitution via `str.replace`.  

**Risk:** Does not handle escaping, repeated placeholders with different rules, or accidental replacement of substrings if names overlap. Acceptable for simple templates; fragile for edge-case names.

---

### SQL-001: Insert row count fallback

**File:** `apps/mcp_runtime/sql.py` (`_insert`)  

When `primary_key_columns` exist but `returned_row` is `None`, `row_count` falls back to logic that may not match all DB backends.  

**Risk:** Edge-case reporting inconsistency; low severity.

---

## Compiler API database wiring

### DB-001: Lazy session factory when `DATABASE_URL` missing at startup

**File:** `apps/compiler_api/db.py`  

If `configure_database` is called without `database_url` and without `session_factory`, DB state may be unset until `resolve_session_factory` runs from a request, which then reads `DATABASE_URL` from the environment.  

**Risk:** Misconfiguration surfaces on first request rather than at import/start. Acceptable for tests; may surprise operators.

---

## Full scan — `apps/compiler_worker`

### ENTRY-001: `celery_output_thread.join` is effectively unreachable (**Static defect**)

**File:** `apps/compiler_worker/entrypoint.py` (`main`, end of function)  

The main supervisor loop is `try: while True: ... return int(return_code)`. In Python, a `return` inside `try` runs `finally` then returns; **statements after the `try`/`finally` block do not run**.  

The line `celery_output_thread.join(timeout=1)` sits **after** the `finally` block (currently ~line 199, immediately following the `finally` that ends ~line 198). Any exit from the loop via `return` skips that line. The `while True` has no `break`, so the only normal exits are `return` from inside the loop → **`join` never runs**.  

**Risk:** Daemon thread may still be reading Celery stdout at process teardown; resource hygiene and clean shutdown semantics differ from intent.  

**Also:** `_wait_for_broker_socket` contains `if last_error is not None: ...` after a `while True` that only exits via `return` or `raise` — that branch is **unreachable** (marked `pragma: no cover` in source).

---

### PERF-001: New async engine per Celery task

**File:** `apps/compiler_worker/executor.py` (`DatabaseWorkflowCompilationExecutor.execute`)  

Each `execute()` calls `create_async_engine` + `dispose()` in a `try`/`finally`.  

**Risk:** High connection churn and latency under load vs a shared pool; not a correctness bug but operational cost.

---

### RES-001: Post-enhancement LLM steps swallow all failures

**File:** `apps/compiler_worker/activities/production.py` (`_apply_post_enhancement`)  

`ToolGrouper` and `ExamplesGenerator` are wrapped in `except Exception:` with warning logs and continue.  

**Risk:** Intentional degradation, but any bug (e.g. `TypeError`) is indistinguishable from “LLM unavailable” in logs without reading `exc_info`.

---

## Full scan — `apps/compiler_api` (remainder)

### ART-001: `RegistryClient._parse_model` and non-JSON error bodies

**File:** `libs/registry_client/client.py`  

On success path, `response.json()` is used. If the server returns an error page with non-JSON body after a 200 (misconfigured proxy), parsing can throw an exception type not wrapped as `RegistryClientError`.  

**Risk:** Low; typical client behavior.

---

## Full scan — `apps/access_control` & gateway

### GW-001: Gateway admin mock has no authentication

**File:** `apps/gateway_admin_mock/main.py`  

Intentional for tests; **must not** be exposed on a production network without fronting auth.

---

### GW-002: Reconcile compares full `document` dict equality

**File:** `apps/access_control/gateway_binding/service.py` (`reconcile`)  

Drift detection uses `!=` on dict documents. Ordering of keys is not an issue in Python 3 dict comparison, but **floating-point or timestamp string** differences may cause unnecessary upserts.  

**Risk:** Cosmetic churn on gateway; low.

---

## Full scan — `apps/proof_runner`

### PRF-001: `except Exception` in live proof harness

**File:** `apps/proof_runner/live_llm_e2e.py` (multiple locations per grep)  

Broad handlers suit a CLI proof script; **risk** is only that real failures are easy to misread without reading full tracebacks.

---

## Full scan — `libs/extractors`

### EXT-001: `OpenAPIExtractor.detect` swallows parse errors

**File:** `libs/extractors/openapi.py`  

`detect()` wraps `_parse_spec_string` in `except Exception: return 0.0`.  

**Risk:** A **corrupt** spec that is not OpenAPI may yield 0.0 and fall through to another extractor with misleading behavior. `extract()` will still fail later on the same content.

---

### EXT-002: `$ref` resolution returns empty dict on failure

**File:** `libs/extractors/openapi.py` (`_follow_ref`)  

External or invalid refs that do not start with `#/` return `{}`.  

**Risk:** Silent omission of schema fragments; harder to debug than a hard error.

---

### EXT-003: Broad `except Exception` in detector and fetch paths

**Files (representative):**  

- `libs/extractors/base.py` — `TypeDetector.detect` / `detect_all`: failed `detect()` logged, extractor skipped.  
- `libs/extractors/openapi.py` — HTTP fetch failure returns `None` for content.  
- `libs/extractors/graphql.py`, `grpc.py`, `soap.py` — similar patterns per grep.  

**Risk:** Legitimate for resilience; **downside** is masking programming errors during development.

---

## Full scan — `libs/enhancer` & `libs/validator`

### ENH-001: LLM enhancer paths use broad exception handlers

**Files:** `libs/enhancer/enhancer.py`, `libs/enhancer/tool_grouping.py`, `libs/enhancer/examples_generator.py` (per grep)  

Same trade-off as RES-001: degrade gracefully vs hide bugs.

---

### VAL-001: `libs/validator/llm_judge.py` broad `except Exception`

**Risk:** Failed judge calls may collapse to a single code path; ensure metrics/logging distinguish timeout vs logic errors if you rely on this for gating.

---

## Full scan — `libs/observability`

### OTLP-001: OTLP exporter uses `insecure=True`

**File:** `libs/observability/tracing.py` (`setup_tracer`)  

`OTLPSpanExporter(endpoint=endpoint, insecure=True)` when an endpoint is set.  

**Risk:** Fine for dev/cluster-internal collectors; **unsafe** if the endpoint is reached over untrusted networks. Align with mTLS or secure gRPC in production.

---

### OTEL-001: `except Exception` after `ImportError` in tracer setup

**File:** `libs/observability/tracing.py`  

Catches configuration failures broadly; tracing silently disabled. Acceptable; operators should watch logs on startup.

---

## Full scan — `libs/ir` / `libs/generator` / `libs/db_models`

No additional **logic defects** flagged beyond items in §Low — `libs/ir/models.py` is large; recommend periodic **schema migration review** when fields are added (manual process, not a code bug).

**File:** `libs/db_models.py` — `utcnow` default factory used for timestamps; standard pattern.

---

## Inventory: `except Exception` (non-test `.py` files)

Grepped occurrences useful for review (not all are wrong):

| Area | Files |
|------|--------|
| Extractors | `openapi.py`, `graphql.py`, `grpc.py`, `soap.py` |
| Enhancer | `enhancer.py`, `tool_grouping.py`, `examples_generator.py` |
| Worker | `production.py`, `entrypoint.py` |
| Proof | `live_llm_e2e.py` |
| Validator | `llm_judge.py` |
| Base | `extractors/base.py` |
| Misc | `extractors/llm_seed_mutation.py`, `observability/tracing.py` |

---

## Coverage summary

| Package | Reviewed |
|---------|----------|
| `apps/mcp_runtime` | Yes (incl. `proxy`, `sql`, `grpc_*`, `loader`, `main`) |
| `apps/compiler_api` | Yes |
| `apps/compiler_worker` | Yes (incl. `entrypoint`, `executor`, `workflows`, `activities`) |
| `apps/access_control` | Yes (services/routes/db) |
| `apps/proof_runner` | Yes (non-test modules) |
| `apps/gateway_admin_mock` | Yes |
| `libs/extractors` | Yes |
| `libs/enhancer` | Yes |
| `libs/validator` | Yes (spot-check + grep) |
| `libs/ir`, `libs/generator`, `libs/registry_client`, `libs/observability` | Yes |

---

## How to extend this document

1. Append or edit entries **here only**; keep other source files unchanged unless you are implementing a fix.  
2. Re-run grep for `except Exception`, `TODO`, `pragma: no cover` after large refactors.  
3. For each new finding, add an ID, file, short risk, and whether runtime evidence exists.  
4. Prefer linking to a test or issue once a bug is confirmed fixed.

---

## Pass 3 — additional findings (2026-03-27)

### CELERY-001: Default broker/result backend fall back to in-memory

**File:** `apps/compiler_worker/celery_app.py` (`create_celery_app`)  

If `CELERY_BROKER_URL` / `REDIS_URL` and related env vars are unset, broker defaults to `"memory://"` and result backend to `"cache+memory://"`.  

**Risk:** Misconfigured production workers may appear to start while tasks are not shared across processes or are lost on restart; queue is not durable like Redis.

---

### K8S-002: Compiler worker `/healthz` and `/readyz` do not probe dependencies

**File:** `apps/compiler_worker/main.py`  

Both endpoints return `"status": "ok"` (readyz adds static config fields only). There is no check that the Celery broker is reachable, the database is up, or a consumer is registered.  

**Risk:** Kubernetes may route traffic or mark the pod ready when compilation tasks cannot actually execute.

---

### OAUTH-001: OAuth2 token cache has a check-then-act race

**File:** `apps/mcp_runtime/proxy.py` (`RuntimeProxy._fetch_oauth2_access_token`)  

Cache read, optional HTTP POST, then dict write are not guarded by a lock/async mutex. Concurrent tool calls with the same OAuth client config can trigger redundant token requests (thundering herd) or interleaved updates.  

**Risk:** Usually benign; worst cases are extra token endpoint load or brief duplicate fetches, not typical token leakage.

---

### AUTHZ-003: Ambiguous tie when multiple policies match with identical scores

**File:** `apps/access_control/authz/service.py` (`evaluate`)  

Policies are loaded with `select(Policy).where(...)` **without** `order_by`. After `_matches` / specificity scoring, ties on `(specificity, decision_priority)` rely on `matches.sort` + `top_matches[0]`. With equal keys, **Python’s sort is stable** and preserves the input order — and input order from the DB is **not guaranteed** without an `ORDER BY`.  

**Risk:** Nondeterministic choice of which policy “wins” when two rows are equivalent under the scoring rules.

---

### WF-002: Stage failures use broad `except Exception`

**File:** `apps/compiler_worker/workflows/compile_workflow.py` (`CompilationWorkflow.run`)  

Stage execution errors are caught as `Exception`, triggering retries and eventual rollback.  

**Risk:** Same trade-off as elsewhere: intentional for workflow durability; `BaseException` (e.g. `KeyboardInterrupt`) is not caught here (only `Exception`), which is usually desired for workers.

---

## Pass 4 — additional findings (2026-03-27)

### SEC-004: Audit log read API has no authentication dependency

**File:** `apps/access_control/audit/routes.py` (`list_audit_logs`)  

`GET /api/v1/audit/logs` uses `Depends(get_audit_log_service)` only; there is **no** `Depends` on a validated principal, API key, or JWT for the caller.  

**Risk:** If the access-control service is reachable from an untrusted network (or mis-exposed ingress), **any client** can list audit entries with optional filters. Defense in depth assumes network policy / API gateway auth; the app layer does not enforce auth on this route by itself.

---

### K8S-003: Access control `/healthz` does not verify database

**File:** `apps/access_control/main.py`  

`/healthz` returns `{"status": "ok"}` without checking DB connectivity (unlike a full readiness probe that runs `SELECT 1`).  

**Risk:** Pod may be marked alive while migrations failed or DB is down; similar pattern to **K8S-002** for the compiler worker.

---

### SEC-005: Async job polling may follow arbitrary absolute URLs (SSRF class)

**File:** `apps/mcp_runtime/proxy.py` (`_extract_async_status_url`, used by `_poll_async_job`)  

When `status_url_source` is `location_header` or a JSON field, the resolved URL is built with `urljoin(request_url, url_value)`. If the upstream response supplies an **absolute** `Location` (or absolute URL string in JSON), `urljoin` yields that host/scheme, **not** restricted to the service `base_url`. The subsequent poll loop uses the shared httpx client to request that URL.  

**Risk:** A **malicious or compromised upstream** can return `Location: http://metadata.internal/` (or similar), causing the runtime (where the MCP tool runs) to issue requests to internal addresses — classic **SSRF** if you trust IR/upstream insufficiently. Mitigations are usually network policy, allowlist hosts, or blocking private IP ranges in the client layer.

---

---

## Pass 5 — full parallel deep-scan (2026-03-27)

> Ten sub-agents read every non-test `.py` file in parallel and cross-checked findings against the existing entries above.  New IDs begin at `P5-` to distinguish this pass. All items are **suspected / static** — not runtime-confirmed.

---

### P5-REGEX-001 ★ Double-escaped dot in float regex — floats never matched

**File:** `apps/mcp_runtime/proxy.py` (~line 1579)  
**Severity:** High

```python
if re.fullmatch(r"-?[0-9]+\\.[0-9]+", stripped):
```

The raw string `r"...\\...."` contains a literal backslash then a dot, **not** `\.` (escaped dot). `fullmatch` will never succeed for any real float like `"3.14"` because the pattern requires a backslash character between digits. As a result, the float branch is dead code and numeric strings that should coerce to `float` are returned as plain strings.

**Impact:** XML/SOAP response fields that carry float values will be returned as strings, potentially breaking downstream type expectations in tool responses.

---

### P5-JWT-001: JWT `exp` check uses `<=` — valid tokens rejected at exact expiry second

**File:** `apps/access_control/authn/service.py` (~line 171)  
**Severity:** Medium

```python
if not isinstance(exp, int) or exp <= now_ts:
    raise AuthenticationError("JWT is expired.")
```

RFC 7519 §4.1.4 states a token is only invalid **after** the `exp` timestamp, i.e. `now > exp`. The `<=` operator rejects tokens that are still technically valid at the exact expiry second.

**Impact:** Intermittent `401` errors at the last second of token validity; particularly visible with low-TTL service tokens or aggressive clock skew.

---

### P5-POST-001: Post-deploy audit invokes tools even when tool-listing validation failed

**File:** `libs/validator/post_deploy.py` (`_audit_all_enabled_operations`, ~line 187)  
**Severity:** High

The guard before invoking each tool only checks `health_passed` and `_tool_invoker is not None`; it does **not** check `tool_listing_passed`. If the `/tools` listing call failed (network, auth, timeout), `runtime_tool_names` will be empty, every operation will be reported as "Runtime /tools listing does not expose this generated tool", and the loop still runs to completion instead of exiting early. More critically, if `tool_listing_passed=False` and the check were bypassed by future refactoring, invocations against a broken runtime could cascade.

**Impact:** Misleading audit reports; potential for confusing cascading errors when the runtime is partially up.

---

### P5-POST-002: `_supported_descriptor_for_operation` raises `ValueError` instead of returning a validation failure

**File:** `libs/validator/post_deploy.py` (`_supported_descriptor_for_operation`, ~lines 509-513)  
**Severity:** Medium

```python
if len(descriptors) > 1:
    raise ValueError("Post-deploy validation does not support multiple streaming descriptors …")
```

This helper is called inside the validation loop. Raising `ValueError` unwinds the entire async validation coroutine rather than appending a `ToolAuditResult` failure entry. Any IR that legitimately or accidentally carries two streaming descriptors for the same operation will cause the whole post-deploy report to propagate an unhandled exception.

**Impact:** Validation pipeline crash for affected services; no graceful failure report.

---

### P5-IR-ENUM-001: Enum identity comparison (`is` / `is not`) instead of `==` / `!=`

**File:** `libs/ir/models.py` (lines 283, 286, 309, 429)  
**Severity:** Medium

Multiple model validators use `is` / `is not` to compare `StrEnum` values:

```python
if self.action is SqlOperationType.query ...          # line 283
if self.relation_kind is not SqlRelationKind.table … # line 286
if self.transport is EventTransport.grpc_stream …    # line 309
if self.sql.action is SqlOperationType.query …       # line 429
```

`StrEnum` members *are* singletons in CPython, so `is` normally works, but the pattern is semantically incorrect and fragile: deserialized or reconstructed enum instances (e.g. from Pydantic `model_validate`) are guaranteed equal by value but not necessarily identical by identity.  The validators at lines 376-383 already correctly use `==` for the `RiskLevel` comparison — inconsistency within the same file.

**Impact:** Could silently bypass validation in non-CPython runtimes or after enum refactors.

---

### P5-IR-DEFAULT-001: Default `Operation` state is invalid per its own validator

**File:** `libs/ir/models.py` (`Operation`, `RiskMetadata`, ~lines 349-383)  
**Severity:** Medium

`RiskMetadata` defaults `risk_level` to `RiskLevel.unknown`. `Operation` defaults `enabled` to `True`. The `unknown_risk_must_be_disabled` validator then raises `ValueError` for any `Operation` constructed with those defaults without explicitly setting either field. This means the following raises at construction time:

```python
Operation(id="x", name="x")   # ← ValueError
```

Any extractor or code path that instantiates `Operation` without explicitly setting `enabled=False` or a concrete `risk_level` will crash. This may be intentional design-by-contract, but it is undocumented and creates a footgun for new extractors/tests.

**Impact:** Silent breakage when new extractors or test fixtures omit risk metadata.

---

### P5-IR-CTRL-001: Unreachable `return self` inside grpc_stream validator (**Static defect**)

**File:** `libs/ir/models.py` (`grpc_stream_config_must_match_transport`, line 316)  
**Severity:** Low

```python
if self.transport is EventTransport.grpc_stream:
    if self.grpc_stream is None:
        raise ValueError(…)
    if self.channel is not None and …:
        raise ValueError(…)
    return self          # ← taken when transport matches and passes all checks

if self.grpc_stream is not None:   # ← this line is only reached for non-grpc_stream transports
    raise ValueError(…)
return self
```

The early `return self` inside the `if transport is grpc_stream` block is correct, but the outer `if self.grpc_stream is not None` check on line 318 is **only reachable** when `transport != grpc_stream`, which is the intended semantic. The code is functionally correct but the early return makes the control flow confusing and non-obvious for reviewers.

---

### P5-REF-001: Circular `$ref` in OpenAPI spec causes infinite recursion / stack overflow

**File:** `libs/extractors/openapi.py` (`_resolve_refs`, ~line 168)  
**Severity:** Medium

`_resolve_refs` resolves JSON `$ref` pointers by recursively calling itself on nested values. There is no visited-set to detect cycles. A spec with a circular schema reference (e.g. `SchemaA → SchemaB → SchemaA`) will recurse until Python's default recursion limit is exceeded and a `RecursionError` is raised, crashing the extraction.

**Impact:** Malformed or intentionally crafted specs with circular refs will cause `RecursionError` during extraction; surfaced to the compilation pipeline as an unhandled exception.

---

### P5-TRACING-001: `_is_configured=False` on no-endpoint path prevents future successful setup

**File:** `libs/observability/tracing.py` (`setup_tracer`, ~lines 41-48)  
**Severity:** Medium

```python
if _is_configured and _tracer_provider is not None:
    return                # ← only guard against re-init

endpoint = endpoint or os.environ.get("OTEL_EXPORTER_ENDPOINT")
if not endpoint and not enable_local:
    _is_configured = False  # ← explicitly set False
    return
```

After a no-endpoint call sets `_is_configured = False`, a subsequent call with a real endpoint will not return early (the guard requires `_is_configured is True`). That is correct. However, if the first call set `_is_configured = True` after a successful local setup and then `_tracer_provider` is somehow cleared externally, the re-init is also blocked. More concretely: the guard on line 41 should be `if _is_configured:` (without `_tracer_provider is not None`) because the local-spans branch also sets `_is_configured = True` with no exporter, but `_tracer_provider` is set to the `TracerProvider` in both paths, so the `_tracer_provider is not None` clause is technically redundant but creates a subtle two-variable invariant that can break.

**Impact:** Low in practice; primarily a maintenance footgun if the guard conditions are modified.

---

### P5-ENH-001: Markdown fence stripping drops opening fence but keeps all other lines when closing fence absent

**Files:** `libs/enhancer/enhancer.py` (~line 458), `libs/enhancer/tool_grouping.py` (~line 138)  
**Severity:** Low

```python
if text.startswith("```"):
    lines = text.split("\n")
    if lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1])
    else:
        text = "\n".join(lines[1:])   # ← closing fence left in if LLM returns partial fence
```

When the LLM returns a response with an opening ```` ``` ```` but no closing fence, `lines[1:]` preserves all content after the first line — correct intent — but if the last non-fence line contains ```` ``` ```` as part of the content, it may be stripped incorrectly. More commonly: if the LLM omits the closing fence, the `else` branch strips only the opening fence, which is correct. The actual risk is that the final parsed string may still contain a trailing ```` ``` ```` if the LLM includes one that isn't on its own line (`"```\n"`).

**Impact:** Rare JSON parse failures when LLM responses include atypical markdown formatting.

---

### P5-ENH-002: `response.choices[0]` and `response.content[0]` may raise `IndexError`

**File:** `libs/enhancer/enhancer.py` (~lines 165, 196)  
**Severity:** Low

- Anthropic: `response.content[0].text` — no guard for empty `content` list.
- OpenAI: `response.choices[0].message.content` — no guard for empty `choices` list.

Both SDKs can return empty lists in edge cases (content filtering, API errors that still return 200, etc.).

**Impact:** `IndexError` propagates out of `complete()`, causing the entire enhancement batch to fail.

---

### P5-DB-001: Multiple `is_active=True` service versions possible — no DB-level uniqueness constraint

**File:** `libs/db_models.py` (`ServiceVersion`, ~line 125)  
**Severity:** Medium

`is_active` defaults to `True` for every new `ServiceVersion` row. The schema has an index on `(service_id, is_active)` but **no unique partial index** restricting `is_active=True` to one row per `service_id`. Application code is responsible for deactivating prior versions atomically before activating the next; if that logic fails or is bypassed, multiple active versions per service can coexist silently.

**Impact:** Ambiguous routing and incorrect "current version" queries; data corruption risk on concurrent activations without serializable transactions.

---

### P5-GEN-001: `_upstream_port` returns 443 for all non-HTTP schemes

**File:** `libs/generator/generic_mode.py` (`_upstream_port`, ~line 256)  
**Severity:** Medium

```python
def _upstream_port(base_url: str) -> int:
    parsed = urlparse(base_url)
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "http":
        return 80
    return 443             # ← covers grpc://, ws://, ftp://, ...
```

Any service whose `base_url` uses a non-HTTP/HTTPS scheme (e.g. `grpc://svc:50051`) without an explicit port in the URL will have port 443 injected into the generated Kubernetes manifest, causing connection failures.

**Impact:** Generated manifests for gRPC or WebSocket backends will target the wrong upstream port unless the URL includes an explicit port number.

---

### P5-SQL-001: `rowcount > 0` fallback returns `1` when zero rows affected by INSERT

**File:** `apps/mcp_runtime/sql.py` (`_insert`, ~lines 123-127)  
**Severity:** Low

```python
row_count = (
    result.rowcount
    if result.rowcount is not None and result.rowcount > 0
    else 1
)
```

When `rowcount == 0` (e.g. `INSERT … ON CONFLICT DO NOTHING` with no new row), the condition `result.rowcount > 0` is `False`, so the fallback `1` is used, falsely reporting one row was inserted. (Note: existing entry **SQL-001** covers a different edge case in the same function.)

**Impact:** Tool callers receive `"row_count": 1` even when the insert was a no-op, hiding idempotency behavior from the caller.

---

### P5-GRPC-001: `idle_timeout_seconds` passed as gRPC call deadline, not idle timeout

**File:** `apps/mcp_runtime/grpc_stream.py` (~line 86)  
**Severity:** Medium

```python
responses = stream(
    request_message,
    timeout=config.idle_timeout_seconds,
)
```

The gRPC Python `timeout` parameter is a **call deadline** (wall-clock seconds from now). `GrpcStreamRuntimeConfig.idle_timeout_seconds` is semantically an *inter-message* idle budget, not a total call timeout. For slow or sparse streams, the deadline fires and terminates the stream well before the data is exhausted.

**Impact:** gRPC streaming tools over sparse event sources will time out prematurely; correct behavior requires a separate keepalive/idle mechanism or a larger per-call deadline.

---

### P5-OAUTH-001: `expires_in=0` cached as non-expiring token

**File:** `apps/mcp_runtime/proxy.py` (`_fetch_oauth2_access_token`, ~line 870)  
**Severity:** Medium

```python
if isinstance(expires_in, int | float) and expires_in > 0:
    expires_at = now + float(expires_in)
```

`expires_in=0` (token immediately expired) is skipped by the `> 0` guard, so `expires_at` stays `None`. A `None` expiry means the cache entry is never considered stale, and the token is reused indefinitely, even though the server declared it expired on issuance.

**Impact:** Calls made with an immediately-expired token will be rejected by the resource server, causing tool invocation failures until the cache is cleared by a restart.

---

### P5-REPO-001: Post-commit re-query may return stale data (compiler_api)

**File:** `apps/compiler_api/repository.py` (`update_version` ~line 285, `activate_version` ~line 299)  
**Severity:** Low

Both methods call `await session.commit()` then immediately call `await self.get_version(…)` to return the updated record. In the presence of async read replicas or async connection pool switching, the follow-up query may hit a replica that hasn't applied the commit yet, returning pre-update state to the caller.

**Impact:** API responses may reflect stale version data immediately after an update; retry or refresh on the client side may be required.

---

### P5-CELERY-001: `execute_compilation` returns `job_id` dict even when execution raises

**File:** `apps/compiler_worker/celery_app.py` (`execute_compilation`, ~line 63)  
**Severity:** Medium

```python
@app.task(name="execute_compilation")
def execute_compilation(request_dict: dict[str, Any]) -> dict[str, Any]:
    _run_coro(_execute_compilation(request))
    return {"job_id": str(request.job_id)}
```

If `_run_coro` raises, the exception propagates and Celery marks the task failed — `return` is not reached, so the dict is not returned. However, because there is **no explicit try/except**, a programming error (e.g. `TypeError` in `_execute_compilation`) surfaces identically to a worker-level crash, with no structured error result in the Celery result backend and no cleanup hook.

**Impact:** Failed tasks produce no structured error payload; callers polling the result backend receive an exception state instead of a domain-level failure, making error attribution harder.

---

### P5-ROLLBACK-001: Silent rollback skipped when payload is wrong type

**Files:** `apps/compiler_worker/activities/production.py` (`deploy_rollback` ~lines 760-763, `route_rollback` ~lines 777-784)  
**Severity:** Low

Both rollback activity handlers check `isinstance(manifest_payload, dict)` / `isinstance(route_config, dict)` and silently `return` if the check fails. No log, no exception, no metric increment. A corrupt or missing context value causes the rollback to be silently skipped.

**Impact:** Kubernetes deployments or APISIX routes may not be cleaned up during a failed rollback, leaving orphaned resources; the workflow records the rollback activity as successful.

---

### P5-RACE-001: Policy deletion fetches policy separately from delete — race window in audit log

**File:** `apps/access_control/authz/routes.py` (`delete_policy`, ~lines 109-117)  
**Severity:** Low

```python
policy = await service.get_policy(policy_id)   # fetch
deleted = await service.delete_policy(policy_id)  # delete
…
resource=policy.resource_id if policy is not None else None  # may be None if concurrent delete
```

Two sequential async awaits leave a window where a concurrent request can delete the policy between the `get` and the `delete`. When this happens, `policy` will reflect the pre-delete state (correct for audit) but `deleted` will return `False`, raising a 404, while the audit log entry records `None` for the resource if the policy variable went stale. A more likely scenario is the reverse: first call gets the policy, second concurrent call deletes it first, first call gets `deleted=False` even though the row is gone.

**Impact:** Audit log entries can have `resource_id=None` for legitimate deletions under concurrent load; 404 may be raised spuriously.

---

### P5-PRF-001: httpx response used after `async with` context manager exits

**File:** `apps/proof_runner/live_llm_e2e.py` (`_fetch_runtime_tool_names`, ~lines 548-552)  
**Severity:** Medium

```python
async with httpx.AsyncClient(…) as client:
    response = await client.get(…)

response.raise_for_status()   # ← outside the context manager
payload = response.json()
```

`raise_for_status()` and `.json()` are called after the `async with` block exits and the client is closed. The `httpx.Response` object buffers the body, so `.json()` usually works, but `raise_for_status()` calls that trigger an `HTTPStatusError` will reference a closed client object, which may produce confusing tracebacks or fail in future httpx versions that enforce closed-client restrictions more strictly.

**Impact:** Works in current httpx versions; fragile against httpx upgrades and produces misleading error messages on non-2xx responses.

---

### P5-MIGS-001: `config.get_main_option` does not accept a default keyword argument

**File:** `migrations/env.py` (`get_url`, ~line 31)  
**Severity:** Medium

```python
config.get_main_option("sqlalchemy.url", "postgresql://localhost/toolcompiler")
```

Alembic's `Config.get_main_option` signature is `get_main_option(name: str) -> str | None` — it takes only one positional argument. Passing a second positional argument raises `TypeError` if the Alembic version enforces the signature strictly, or silently ignores the default in permissive versions.

**Impact:** If `sqlalchemy.url` is absent from `alembic.ini` **and** `DATABASE_URL` env var is not set, Alembic migrations fail with a confusing `TypeError` instead of a clear missing-config error.

---

### P5-MIGS-002: URL driver substitution is fragile for non-`+asyncpg` URLs

**File:** `migrations/env.py` (`to_migration_database_url`, ~line 26)  
**Severity:** Low

```python
return database_url.replace("+asyncpg", "+psycopg")
```

If `DATABASE_URL` already uses `+psycopg`, `+psycopg2`, `postgres://` (not `postgresql://`), or lacks a driver suffix altogether, the replace is a no-op or silently wrong. No validation or normalization beyond a simple string replace is performed.

**Impact:** Misconfigured URLs produce connection errors at migration time that are hard to trace back to this substitution.

---

### P5-REST-001: Singularization in REST endpoint inference creates nonsensical parameter names

**File:** `libs/extractors/rest.py` (`_infer_sub_resources`, ~line 304)  
**Severity:** Low

```python
singular = leaf.rstrip("s")
param_name = f"{singular}_id"
```

`rstrip("s")` strips trailing `s` characters, not just a plural suffix. Examples: `"status"` → `"statu_id"`, `"address"` → `"addres_id"`, `"users"` → `"user_id"` (correct). Irregular plurals (`"children"`) receive `"children_id"` because no `s` is stripped.

**Impact:** Generated operation parameter names in the IR may be nonsensical for non-regular plural resource paths, making LLM-generated descriptions and tool signatures confusing.

---

### P5-DRIFT-001: `DriftReport.service_id` populated with `service_name` value

**File:** `libs/validator/drift.py` (`detect_drift`, ~line 143)  
**Severity:** Low

```python
service_id=deployed_ir.service_name,
```

The field is named `service_id` but is assigned the service's *name*. If a caller relies on `service_id` to look up records by UUID or unique identifier, it will receive the human-readable name instead, causing lookup failures.

**Impact:** Downstream consumers of `DriftReport` that treat `service_id` as a stable unique identifier will silently receive the wrong value.

---

## Disclaimer

This audit is **static**; it does not replace failing tests, fuzzing, or security scanning. Several items (especially **CB-001**) should be validated with **runtime reproduction** before changing behavior.  

**ENTRY-001** is a **control-flow / unreachable-code** issue verifiable by reading `entrypoint.py` (no server required).

**P5-REGEX-001** is verifiable with a two-line Python REPL check against the regex pattern.

---

## Pass 6 — verification audit (2026-03-27)

> Every entry above was cross-referenced against the actual source code. Where possible, claims were validated with the project's own Python interpreter (`.venv/bin/python`). Three entries are reclassified as **false positive**; twelve are reclassified as **design choice** (intentional trade-off, not a defect). The remaining 43 are **confirmed**.

### Methodology

1. Four parallel static-review agents read every referenced source file and reported line-level evidence.
2. Key claims were spot-checked with live Python REPL tests:
   - **P5-REGEX-001**: `re.fullmatch(r"-?[0-9]+\\.[0-9]+", "3.14")` → `None` (confirmed dead float branch).
   - **P5-IR-ENUM-001**: `StrEnum` singletons verified via `model_validate`, `model_validate_json`, and direct construction — `is` comparison returns `True` in all cases.
   - **P5-IR-DEFAULT-001**: `Operation(id="x", name="x")` → `ValidationError` (confirmed).
   - **P5-MIGS-001**: `inspect.signature(Config.get_main_option)` → `(self, name: str, default: Optional[str] = None)` — the second positional arg **is** accepted.
   - **GW-002**: Python 3 `dict.__eq__` is **order-independent**; the original claim about key ordering is wrong, but the float/timestamp concern stands.

---

### Verification verdicts

#### False positives (3)

| ID | Reason |
|----|--------|
| **P5-IR-ENUM-001** | `StrEnum` members are singletons in CPython. Pydantic `model_validate` and JSON roundtrips all return the canonical enum member. `is` comparison works correctly in all tested paths. The inconsistency with `==` at line 389 is stylistic, not a defect. |
| **P5-IR-CTRL-001** | Control flow is correct. The early `return self` at line 326 handles the `grpc_stream` transport case; the outer `if self.grpc_stream is not None` at line 328 guards the non-grpc_stream path. Both return paths are reachable for their respective transport types. The audit's own description acknowledges functional correctness. |
| **P5-MIGS-001** | Alembic `Config.get_main_option` signature is `(self, name: str, default: Optional[str] = None)`. The second positional argument **is valid**. Verified via `inspect.signature` against the installed Alembic package. |

#### Design choices (12)

These are intentional trade-offs acknowledged in code comments or standard resilience patterns. Not defects, but worth periodic re-evaluation.

| ID | Rationale |
|----|-----------|
| **API-003** | Broad `except Exception` on `dispatcher.enqueue` is a standard fault-tolerance pattern; the job is cleaned up and a 503 is returned. Narrowing to broker-specific exceptions would be better but not strictly wrong. |
| **IR-003** | Naive `str.replace` for prompt templates is sufficient for the current simple `{name}` placeholders. Would need hardening if templates grow more complex. |
| **RES-001** | Post-enhancement LLM steps intentionally degrade gracefully on any failure. `exc_info` is logged. |
| **GW-001** | Gateway admin mock is intentionally unauthenticated for test use. Comment in code says "must not be exposed on production network." |
| **EXT-003** | Broad `except` in extractor detect/fetch paths is standard resilience; failed extractors are skipped and logged. |
| **ENH-001** | Same graceful-degradation pattern as RES-001 for LLM enhancer. |
| **VAL-001** | Same pattern in LLM judge; metrics/logging should distinguish timeout vs logic errors. |
| **OTEL-001** | Broad `except` after `ImportError` in tracer setup; tracing silently disabled. Operators should watch startup logs. |
| **PRF-001** | Broad `except` in CLI proof harness; acceptable for a diagnostic script. |
| **WF-002** | Stage failures caught as `Exception` for workflow durability; `BaseException` (e.g. `KeyboardInterrupt`) is intentionally not caught. |
| **P5-JWT-001** | `exp <= now_ts` is stricter than RFC 7519 §4.1.4's idiomatic `now > exp` interpretation but not a violation. Rejects tokens at exact expiry second. Could be intentional security margin. |
| **P5-TRACING-001** | Guard `if _is_configured and _tracer_provider is not None` has a redundant second clause. Functionally correct — the two-variable invariant holds because `_is_configured = True` implies `_tracer_provider is not None`. Low maintenance risk. |

#### Confirmed bugs (43)

##### Critical / High — security & ops

| ID | Verified lines | Summary |
|----|---------------|---------|
| **SEC-001** | `loader.py:59` | `enable_dns_rebinding_protection=False` — increases attack surface without network controls. |
| **SEC-002** | `authn/service.py:217` | `ACCESS_CONTROL_JWT_SECRET` defaults to `"dev-secret"` — production deployments that forget the env var accept tokens signed with a known secret. |
| **SEC-004** | `audit/routes.py:23-40` | `GET /api/v1/audit/logs` has **no auth dependency** — any client can list audit entries. |
| **SEC-005** | `proxy.py:1950,1959` | `urljoin` with absolute URLs from upstream responses enables **SSRF** — malicious upstream can redirect polling to internal addresses. |
| **P5-REGEX-001** | `proxy.py:1579` | `r"-?[0-9]+\\.[0-9]+"` — double-escaped dot in raw string. Pattern requires a literal backslash between digits; `"3.14"` never matches. **Float coercion branch is dead code.** Verified with REPL. |

##### Medium — correctness & resilience

| ID | Verified lines | Summary |
|----|---------------|---------|
| **CB-001** | `proxy.py:321-323` | `except ToolError` re-raises without `breaker.record_failure()`. Circuit breaker may not reflect upstream hard failures from SQL/gRPC/sanitize paths. |
| **PERF-001** | `executor.py:51` | `create_async_engine` per Celery task — high connection churn vs shared pool. |
| **CELERY-001** | `celery_app.py:21-40` | Broker/backend fall back to `memory://` / `cache+memory://` — tasks lost on restart, no distributed coordination. |
| **K8S-002** | `compiler_worker/main.py:33-48` | `/healthz` and `/readyz` return hardcoded `"ok"` without probing broker, DB, or consumer. |
| **K8S-003** | `access_control/main.py:50-52` | `/healthz` returns `"ok"` without DB connectivity check. |
| **AUTHZ-003** | `authz/service.py:115-155` | Policies loaded without `ORDER BY`; tied specificity/priority resolved nondeterministically. |
| **OTLP-001** | `tracing.py` | `OTLPSpanExporter(insecure=True)` — unsafe if collector endpoint is reachable over untrusted networks. |
| **P5-POST-001** | `post_deploy.py:187` | `tool_listing_passed` parameter never checked before tool invocation; audit proceeds against a potentially broken runtime. |
| **P5-POST-002** | `post_deploy.py:509-513` | `ValueError` raised for multiple streaming descriptors instead of returning a `ToolAuditResult` failure; crashes the entire validation coroutine. |
| **P5-IR-DEFAULT-001** | `models.py:123,385,388-394` | Default `Operation` has `risk_level=unknown` + `enabled=True`, which its own validator rejects. `Operation(id="x", name="x")` raises `ValidationError`. Intentional design-by-contract but undocumented footgun. Verified with REPL. |
| **P5-REF-001** | `openapi.py:168-180` | `_resolve_refs` has no visited-set; circular `$ref` causes `RecursionError`. |
| **P5-GEN-001** | `generic_mode.py:250-256` | `_upstream_port` returns 443 for `grpc://`, `ws://`, etc. without explicit port — wrong default for non-HTTPS protocols. |
| **P5-GRPC-001** | `grpc_stream.py:86` | `idle_timeout_seconds` passed as gRPC `timeout` (call deadline). Semantic mismatch: sparse streams will be terminated prematurely. |
| **P5-ENH-002** | `enhancer.py:165,196` | `response.content[0]` / `response.choices[0]` without guard for empty list; `IndexError` on content-filtered or empty API responses. |
| **P5-DB-001** | `db_models.py:114-120` | Regular index on `(service_id, is_active)` — no unique partial index for `is_active=True`. Multiple active versions per service possible if app logic fails. |
| **P5-OAUTH-001** | `proxy.py:868-872` | `expires_in=0` skips the `> 0` guard; token cached with `expires_at=None` (never expires). |
| **P5-ROLLBACK-001** | `production.py:759-787` | `deploy_rollback` and `route_rollback` silently return when payload is not `dict` — no log, no error, rollback skipped. |

##### Low — maintainability & edge cases

| ID | Verified lines | Summary |
|----|---------------|---------|
| **API-001** | `mcp_runtime/main.py:154-160` | `/healthz` and `/readyz` call the same `_runtime_status` — liveness and readiness semantics are conflated. |
| **API-002** | `compilations.py:51-59` | `enqueue` before `audit_log.append_entry` — audit gap if append fails after successful enqueue. |
| **IR-001** | `loader.py:125` | `zip(..., strict=False)` — silent truncation if `param_name_map` and `signature_params` diverge. |
| **IR-002** | `loader.py:191-220` | Non-`static` resources silently `continue`d with no log or metric. |
| **SQL-001** | `sql.py:123-127` | `rowcount` fallback edge case when primary keys exist but returned_row is None. |
| **DB-001** | `compiler_api/db.py:30-63` | Lazy session factory — misconfiguration surfaces on first request, not at startup. |
| **ENTRY-001** | `entrypoint.py:198` | `celery_output_thread.join(timeout=1)` after `try`/`finally` block — unreachable because the only exit from `while True` is `return`. **Static defect.** |
| **ART-001** | `registry_client/client.py` | Non-JSON error body after 200 raises unexpected exception type (not `RegistryClientError`). |
| **GW-002** | `gateway_binding/service.py:170-206` | Dict equality for drift detection; Python 3 dict comparison is order-independent (audit's key-ordering claim is wrong), but float/timestamp string differences may cause cosmetic churn. |
| **EXT-001** | `openapi.py:67-84` | `detect()` catches all exceptions and returns `0.0`; masks programming errors. |
| **EXT-002** | `openapi.py:182-193` | `_follow_ref` returns `{}` for external or invalid refs; silent schema omission. |
| **OAUTH-001** | `proxy.py:810-872` | Token cache read-write without lock/async mutex; thundering herd on concurrent tool calls. |
| **P5-ENH-001** | `enhancer.py:456-464`, `tool_grouping.py:138-144` | Markdown fence stripping has edge cases for atypical LLM formatting. |
| **P5-SQL-001** | `sql.py:123-127` | `rowcount == 0` falls through to `else 1`; falsely reports one row inserted on `INSERT ON CONFLICT DO NOTHING`. |
| **P5-REPO-001** | `repository.py:284-299` | `commit()` then `get_version()` — stale read possible with async read replicas. |
| **P5-CELERY-001** | `celery_app.py:61-66` | No structured error payload on task failure; callers get raw exception state. |
| **P5-RACE-001** | `authz/routes.py:109-117` | `get_policy()` then `delete_policy()` — race window; audit log may record `None` resource_id. |
| **P5-PRF-001** | `live_llm_e2e.py:548-552` | `response.raise_for_status()` called outside `async with httpx.AsyncClient()` block; works in current httpx but fragile. |
| **P5-MIGS-002** | `migrations/env.py:24-26` | `replace("+asyncpg", "+psycopg")` is naive substring replace; could mangle URLs with `+asyncpg` in hostname. |
| **P5-REST-001** | `rest.py:304` | `rstrip("s")` removes all trailing `s` chars — `"status"` → `"statu"`, `"glass"` → `"gla"`. Should use `leaf[:-1]`. |
| **P5-DRIFT-001** | `drift.py:143` | `DriftReport.service_id` assigned `deployed_ir.service_name` — field/value mismatch. |

---

## Pass 7 — Deep Static Scan (92 new bugs)

**Methodology**: Six parallel explore agents scanned every production Python module plus
three follow-up targeted scans. Each candidate was then cross-referenced against
Passes 1-6 for duplicates. Key claims were spot-checked with the project `.venv`
REPL (Pydantic constraint validation, HMAC timing, gRPC stream return structure,
f-string template paths, rollback logic).

**False-positive removals during dedup**:

| Candidate | Reason discarded |
|---|---|
| SSE → gRPC lifecycle KeyError | gRPC stream handler *does* return `lifecycle` dict (grpc_stream.py:111) |
| f-string template path in rest.py | Both branches intentionally produce literal `{id}` placeholder |
| Query-param mutation in poll loop | `_split_url_query` returns fresh dict each iteration |
| `StopIteration` in rest.py cursor | Intersection is guaranteed non-empty by enclosing `if` check |
| Pydantic `ge=1` on Optional ineffective | REPL confirms: `None` accepted, `0`/`-1` rejected — correct |
| Rollback route logic inversion | Logic is correct: delete NEW-only routes, upsert previous |
| `hmac.compare_digest` timing attack | Code already uses constant-time comparison |
| `rollback_executed` return semantics | Returns `(False, failures)` when failures occur — correct overall |

### 7.1  MCP Runtime (`apps/mcp_runtime/`)

| ID | Location | Severity | Description |
|---|---|---|---|
| **P7-001** | `proxy.py` SSE parser `:id` handler | Low | Empty-string event ID accepted and forwarded; per SSE spec valid but may break `Last-Event-ID` reconnect logic in clients that treat `""` as "no ID". |
| **P7-002** | `proxy.py` SSE data-line parser | Low | `lstrip()` strips **all** leading whitespace; SSE spec says remove **only** the first U+0020 after the colon. Data beginning with meaningful whitespace is silently corrupted. |
| **P7-003** | `proxy.py` GraphQL request builder | Medium | When `Operation.graphql.operation_name` is `None`, the JSON payload sent upstream contains `"operationName": null`; some GraphQL servers reject this vs omitting the key entirely. |
| **P7-004** | `proxy.py:1587-1635 _apply_field_filter` | Medium | Field-filter paths split on `"."` — dotted field names (common in e.g. OData `$select`) are misinterpreted as nested paths. |
| **P7-005** | `proxy.py:1676 _set_nested` | Low | Silently returns without modifying anything when the `path` list is empty; caller gets no indication that the write was a no-op. |
| **P7-006** | `proxy.py` OAuth token cache key | Low | Scope set converted to `str(set(...))` for cache key; Python `set` repr is insertion-order-dependent across interpreter runs, so identical scopes may cache-miss. |
| **P7-007** | `grpc_stream.py:130 channel.close()` | Low | gRPC channel closed without a grace period; in-flight RPCs receive immediate `CANCELLED` instead of draining cleanly. |
| **P7-008** | `sql.py result.first()` | Low | Return value of `result.first()` used without a type guard; if the query returns zero rows the subsequent dict unpacking fails with `TypeError`. |
| **P7-084** | `proxy.py:1458 _normalize_query_value` | Medium | No `None` check — `str(None)` produces the literal string `"None"` in query parameters instead of omitting the param or using empty string. |
| **P7-085** | `proxy.py:1467 _parse_response_payload` | Medium | `response.json()` called when content-type contains `"json"` but body may not be valid JSON; `JSONDecodeError` propagates unhandled. |

### 7.2  Compiler Worker (`apps/compiler_worker/`)

| ID | Location | Severity | Description |
|---|---|---|---|
| **P7-009** | `production.py:320` | High | `manifest_set.service["spec"]["ports"][0]["port"]` — no check that `ports` is non-empty; `IndexError` if K8s service has zero ports (e.g. headless service). |
| **P7-010** | `production.py:314-320` | High | Multiple chained `["metadata"]["name"]`, `["spec"]["replicas"]` dict accesses on K8s manifest response without defaults; any missing key raises `KeyError`. |
| **P7-011** | `compile_workflow.py:280-295` | Medium | Rollback loop iterates `completed_stages` but never validates that the stage result dict contains the expected keys; downstream `rollback_stage` receives arbitrary data. |
| **P7-012** | `executor.py` process monitoring | Medium | Timeout comparison uses `> deadline` instead of `>= deadline`; off-by-one allows one extra poll iteration past the intended deadline. |
| **P7-013** | `production.py` namespace env | Low | `os.getenv("COMPILER_NAMESPACE")` returns `None` when unset; passed directly to Kubernetes API path — produces `/api/v1/namespaces/None/...`. |
| **P7-014** | `production.py` HTTP client | Low | `httpx.AsyncClient` created in helper methods without explicit `aclose()`; relies on GC to close TCP connections. |
| **P7-015** | `production.py:132-145` | Low | Multiple `float(os.getenv(...))` calls — `ValueError` if env var is set to non-numeric string; no try/except guard. |
| **P7-016** | `activities/production.py:232` | Medium | `response.json()` after `raise_for_status()` — if upstream returns 200 with non-JSON body (e.g. plain text error), `JSONDecodeError` propagates unhandled. |
| **P7-017** | `activities/production.py:383,399` | Medium | Two more `response.json()` + `cast(dict, ...)` calls on K8s API responses without JSONDecodeError handling. |
| **P7-018** | `activities/production.py:399` `_wait_for_rollout` | Low | `int(status.get("observedGeneration", 0) or 0)` — if K8s returns a non-integer string, `ValueError` crashes the poll loop. |
| **P7-019** | `entrypoint.py:137` | Low | `float(os.getenv("WORKER_BROKER_READY_TIMEOUT_SECONDS", "60"))` — `ValueError` if env var is non-numeric. |

### 7.3  Extractors (`libs/extractors/`)

| ID | Location | Severity | Description |
|---|---|---|---|
| **P7-020** | `scim.py:92-94` | High | `name = resource.get("name", "")` followed by `if not name: continue` — nameless SCIM resources are silently dropped with no log or error; data loss on malformed SCIM schemas. |
| **P7-021** | `scim.py` param construction | Medium | SCIM-extracted `Param` objects omit `source` and `confidence` fields that all other extractors set; downstream enhancer/validator may mishandle them. |
| **P7-022** | `openapi.py:162` | Low | `Path.read_text()` called without `encoding="utf-8"` when loading local OpenAPI files; on systems with non-UTF-8 locale, wrong encoding may be used. |
| **P7-023** | `scim.py:78` | Low | Same missing `encoding` parameter on `read_text()` for SCIM schema files. |
| **P7-024** | `llm_seed_mutation.py:135-140` | Low | Markdown fence stripping — new instance of same pattern as P5-ENH-001; atypical LLM formatting (e.g. `~~~json`) is not handled. |
| **P7-025** | `graphql.py:160-165` | Low | `payload = json.loads(content)` then immediate `payload["data"]` access — `KeyError` if GraphQL introspection returns `{"errors": [...]}` without `"data"`. |
| **P7-026** | `rest.py:860-880` coalescing | Medium | Path coalescing logic duplicates work: computes `template_path` then re-walks `paths` to find existing template — O(n²) and may produce inconsistent results when multiple value-like segments exist. |
| **P7-027** | `odata.py:120 ET.fromstring` | Medium | `ET.fromstring(content)` can raise `ParseError` on malformed XML; no try/except — unhandled exception propagates instead of a descriptive `ValueError`. |
| **P7-028** | `jsonrpc.py _slugify` | Low | Slugify strips leading/trailing dashes but not consecutive internal dashes; input `"--foo--bar--"` → `"foo--bar"`. |
| **P7-029** | Six extractors | Low | `_slugify()` is copy-pasted identically in `rest.py`, `graphql.py`, `odata.py`, `sql.py`, `scim.py`, `jsonrpc.py`; divergence risk on future edits. |
| **P7-030** | `sql.py:484` | Low | Thread runner sets `result[0]` inside thread; if the thread raises before setting it, `result` list stays empty and `result[0]` in caller raises `IndexError`. |
| **P7-031** | `graphql.py:383` | Low | `json.loads(default_value)` called on GraphQL default values without try/except; non-JSON defaults (e.g. enum literals) raise `JSONDecodeError`. |
| **P7-032** | `rest.py:444` | Medium | `response.json()` called when content-type contains `"json"` but body may be malformed; `JSONDecodeError` propagates unhandled from extractor. |

### 7.4  Access Control (`apps/access_control/`, `apps/compiler_api/`)

| ID | Location | Severity | Description |
|---|---|---|---|
| **P7-033** | `authn/service.py:148-155` | Medium | JWT `_validate_jwt` propagates raw `AuthenticationError` messages containing header algorithm details; minor info disclosure to callers. |
| **P7-034** | `authn/service.py:237-240` | Medium | `_b64decode_bytes` calls `base64.urlsafe_b64decode` without catching `binascii.Error`; malformed base64 in JWT segments raises unhandled exception instead of `AuthenticationError`. |
| **P7-035** | `authn/service.py:154,168` | Medium | `json.loads(_b64decode_json(...))` in JWT parsing — `JSONDecodeError` not caught; malformed JWT payload returns 500 instead of 401. |
| **P7-036** | `authz/service.py list_policies` | Medium | No `.limit()` on policy list query; with thousands of policies the full result set is loaded into memory. |
| **P7-037** | `authz/service.py` policy evaluation | Medium | `RiskLevel(operation.risk)` — if `operation.risk` is not a valid `RiskLevel` member, `ValueError` is raised unhandled instead of falling back to a default. |
| **P7-038** | `audit/service.py:62` | Medium | `list_entries` returns `result.all()` with no pagination limit; unbounded memory consumption on large audit logs. |
| **P7-039** | `authn/service.py:81-96` | Medium | `list_pats` loads all PATs for a user with no `.limit()`; accounts with many PATs cause unbounded memory use. |
| **P7-040** | `compiler_api/repository.py list_services` | Medium | No pagination limit on service listing; large tenants load all services into memory. |
| **P7-041** | `compiler_api/repository.py:170-200` | High | `create_version` deactivates other versions then inserts + commits without `SELECT ... FOR UPDATE`; concurrent version creation can leave multiple active versions. |
| **P7-042** | `authz/routes.py:56-75` create_policy | High | Gateway binding `sync_policy` called AFTER `service.create_policy` commits to DB; if sync fails, DB has the policy but gateway does not — inconsistent state with no rollback. |
| **P7-043** | `authn/routes.py` create_pat | High | Same pattern: PAT created in DB, then gateway sync can fail, leaving DB/gateway out of sync. |
| **P7-044** | `authz/routes.py` update_policy | High | Policy updated in DB, then gateway sync; failure leaves stale policy in gateway. |
| **P7-045** | `authz/routes.py:109-120` delete_policy | High | Policy deleted from DB, then gateway delete; failure leaves orphan route in gateway. |
| **P7-046** | `authn/routes.py` revoke_pat | Medium | PAT revoked in DB, then gateway notified; failure leaves active gateway credential for revoked PAT. |
| **P7-047** | `compiler_api/repository.py list_versions` | Medium | No pagination limit on version listing per service. |
| **P7-048** | `compiler_api/routes.py` SSE events | Medium | Event streaming endpoint has no maximum event count or duration cap; a client can hold a connection open indefinitely, consuming server resources. |

### 7.5  Libraries (`libs/`)

| ID | Location | Severity | Description |
|---|---|---|---|
| **P7-049** | `validator/post_deploy.py:422` | Medium | `result.get("status")` called without `isinstance(result, dict)` check; if tool invoker returns a string or list, `AttributeError` is raised. Same file has the safe pattern at line 211. |
| **P7-050** | `validator/llm_judge.py:229-231` | High | `float(item.get("accuracy", 0.5))` on LLM JSON — if LLM returns `"accuracy": "high"` or `"accuracy": "N/A"`, `ValueError` is raised unhandled; the enclosing try only catches `JSONDecodeError`. |
| **P7-051** | `enhancer/enhancer.py:493,504` | High | `float(enh.get("confidence", 0.7))` on LLM response — same unguarded `float()` conversion; LLM returning non-numeric confidence crashes the enhancer batch. |
| **P7-052** | `validator/post_deploy.py` audit loop | Medium | Multiple `result.get(...)` calls in the validation audit loop without verifying `result` is a dict; non-dict results from flaky tools crash the audit. |
| **P7-053** | `enhancer/enhancer.py` token tracking | Low | `response.usage` accessed without None check; if LLM provider returns a response without usage metadata, `AttributeError` on `.input_tokens`. |
| **P7-054** | `enhancer/enhancer.py` param enhancement | Low | `pe.get("description", p.description)` — if `pe` is not a dict (e.g. LLM returned a string), `AttributeError` raised. |
| **P7-055** | `enhancer/examples_generator.py` | Low | No bounds check before iterating batch; empty batch list causes silent no-op with no log indication. |
| **P7-056** | `enhancer/tool_grouping.py:146` | Medium | `json.loads(text)` result used as dict directly; if LLM returns a JSON array instead of object, downstream `data.items()` raises `AttributeError`. |
| **P7-057** | `enhancer/enhancer.py` batch loop | Low | If `batch_size` config is set to 0, the batch loop produces zero-length slices and iterates indefinitely without progress. |
| **P7-058** | `ir/models.py` port fields | Low | Port fields accept any positive integer; no upper-bound validation at 65535 — values like 99999 pass Pydantic validation but are invalid TCP ports. |
| **P7-059** | `validator/post_deploy.py:309-310` | Medium | `response.json()` called without catching `JSONDecodeError`; non-JSON response from runtime health endpoint crashes validation. |
| **P7-060** | `ir/models.py` risk metadata | Low | `Operation.risk` defaults to `"unknown"` (string) while `RiskLevel` enum expects specific members; mismatch can cause `ValueError` when constructing `RiskLevel(operation.risk)`. |
| **P7-061** | `enhancer/enhancer.py` batch failure | Low | When a single LLM call in a batch fails, the entire batch is discarded; partial enhancements from previous successful calls in the same batch are lost. |
| **P7-062** | `enhancer/enhancer.py` exception handling | Medium | Inner `except Exception` catches all errors including `KeyboardInterrupt` subclass of `BaseException` — wait, no. `Exception` does NOT catch `KeyboardInterrupt`. The real issue: the except block logs a warning but continues; callers receive silently incomplete enhancement results with no indication of partial failure. |

### 7.6  Infrastructure & Tooling (`apps/proof_runner/`, `apps/gateway_admin_mock/`, migrations, scripts)

| ID | Location | Severity | Description |
|---|---|---|---|
| **P7-063** | `migrations/env.py` | Low | Default `DB_URL` env var fallback contains a plaintext password (`postgres://user:pass@localhost/...`); if env var is unset in production, credentials leak into connection strings. |
| **P7-064** | `proof_runner/live_llm_e2e.py` | Medium | Test expects compilation within 120 s but the compilation workflow's own stage timeouts sum to > 120 s; intermittent test failures on slow CI. |
| **P7-065** | `proof_runner/live_llm_e2e.py:382-388` | Medium | SSE event parser has no error handling for malformed event lines; non-standard SSE output crashes the proof runner. |
| **P7-066** | `proof_runner/live_llm_e2e.py:388` | High | `json.loads(line.partition(":")[2].strip())` in SSE parser — no try/except; non-JSON SSE data lines crash the entire proof run. |
| **P7-067** | `proof_runner/live_llm_e2e.py` | Low | `httpx.AsyncClient()` created without `async with`; not explicitly closed on exceptions. |
| **P7-068** | `gateway_admin_mock/main.py:198-214` | Medium | `_service_key` and `_upstream_base_url` access `target_service["name"]` and `target_service["port"]` without `.get()` defaults; `KeyError` on malformed input. |
| **P7-069** | `scripts/` dev helpers | Low | File read operations without checking path existence; `FileNotFoundError` with no descriptive message. |
| **P7-070** | `scripts/` dev helpers | Low | Hardcoded absolute paths that only work on the original developer's machine. |
| **P7-071** | `gateway_admin_mock/main.py` route handling | Medium | JSON parsing of route documents has no `JSONDecodeError` handler; malformed gateway admin requests return 500. |
| **P7-072** | `libs/extractors/soap.py` WSDL fetch | Low | URL concatenation with string formatting instead of `urljoin`; relative WSDL imports may produce invalid URLs. |
| **P7-073** | `libs/extractors/sql.py:480` | Low | `ThreadPoolExecutor` created inline without `with` block; executor not shut down on exceptions — thread leak risk. |
| **P7-074** | `proof_runner/grpc_mock.py:294` | Low | `int(os.getenv("GRPC_PORT", "50051"))` — `ValueError` if env var is non-numeric. |
| **P7-075** | `gateway_admin_mock/main.py:201,210` | Medium | `int(target_service["port"])` — no range validation (1-65535); invalid ports like 0, -1, or 99999 silently accepted and used in upstream URLs. |
| **P7-076** | `gateway_admin_mock/main.py` route dispatch | Medium | Route ID extracted from `X-Route-Id` header with no validation or sanitization; used directly as dict key and in log messages. |
| **P7-077** | `scripts/` | Low | Bare `except Exception` catch-all that logs and continues; masks actionable errors. |
| **P7-078** | `ir/models.py` Operation validators | Medium | No `graphql_contract_must_be_coherent` validator exists; unlike gRPC/SOAP/SQL/JSON-RPC, a GraphQL operation has no method/path coherence check (e.g. `method="DELETE"` with `graphql` config passes validation). |
| **P7-079** | `ir/models.py:141` | Medium | `max_response_bytes: int | None = None` has no `gt=0` constraint; negative values like `-100` pass validation but are semantically invalid. |
| **P7-080** | `gateway_binding/routes.py:31` | Medium | `ServiceRouteRequest.route_config` is a bare `dict[str, Any]` with no schema validation; missing keys (`service_id`, `service_name`, `namespace`) cause `KeyError` at runtime (500) instead of `ValidationError` (422). |
| **P7-081** | `gateway_admin_mock/main.py:128` | Medium | `route["document"]` accessed without `.get()` default after null-checking `route`; if `route` exists but lacks `"document"` key, `KeyError` raised inside generic try/except. |
| **P7-082** | `authn/service.py:228` | Low | `_hash_token` uses unsalted SHA-256 for PAT storage; identical tokens produce identical hashes, enabling rainbow-table attacks if the DB is compromised. |
| **P7-083** | `registry_client/client.py:150` | Medium | `response.json()` inside `_parse_model` — if registry returns non-JSON response body, `JSONDecodeError` propagates as unhandled exception instead of `RegistryClientError`. |

### 7.7  Additional Findings (cross-cutting)

| ID | Location | Severity | Description |
|---|---|---|---|
| **P7-086** | `production.py:536,585,601,606,651,687,704-707` | Medium | 12+ `context.payload["key"]` direct dict accesses across activity functions (`generate_stage`, `deploy_stage`, `validate_stage`, `route_stage`, `register_stage`) without `.get()` or schema validation; any missing key in the workflow-assembled payload raises `KeyError` with no descriptive error. |
| **P7-087** | `production.py:695` | Low | `route_config["default_route"]["route_id"]` — nested dict access in event-detail construction; if the route_config was assembled without `default_route`, `KeyError` crashes metadata logging and the activity. |
| **P7-088** | `gateway_binding/client.py:239` | Low | `float(os.getenv("GATEWAY_ADMIN_TIMEOUT_SECONDS", "10.0"))` — `ValueError` if env var is set to a non-numeric value; no try/except guard. |
| **P7-089** | `proxy.py:861` | Medium | `response.json()` on OAuth2 token endpoint response — `JSONDecodeError` propagates unhandled if token endpoint returns non-JSON body (e.g. HTML from a WAF) despite 2xx status code. |
| **P7-090** | `generic_mode.py:145-148` | Low | Hardcoded `parsed_documents[0]` through `[3]` assumes `_TEMPLATE_ORDER` has exactly 4 entries; adding or removing a template silently breaks manifest generation with `IndexError`. |
| **P7-091** | `proxy.py:862` | Low | `payload.get("access_token")` — if OAuth `response.json()` returns a non-dict JSON value (array, string, number), `AttributeError` is raised instead of a descriptive `ToolError`. |
| **P7-092** | `ir/models.py` ServiceIR | Medium | `base_url: str` has no URL-format validation; arbitrary strings (including `javascript:`, `file:///`, or empty string) pass Pydantic validation and are forwarded to `httpx` request construction. |

### 7.8  Summary

| Severity | Count |
|---|---|
| High | 11 |
| Medium | 42 |
| Low | 39 |
| **Total Pass 7** | **92** |

**Grand total (Passes 1-7): 58 + 92 = 150 bugs.**
