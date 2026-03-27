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

## Disclaimer

This audit is **static**; it does not replace failing tests, fuzzing, or security scanning. Several items (especially **CB-001**) should be validated with **runtime reproduction** before changing behavior.  

**ENTRY-001** is a **control-flow / unreachable-code** issue verifiable by reading `entrypoint.py` (no server required).
