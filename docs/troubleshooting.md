# Troubleshooting Runbook

Operator runbook for service2mcp. All commands assume namespace `service2mcp` — replace as needed.

---

## 1. Quick Diagnostic Commands

```bash
# Pod overview
kubectl -n service2mcp get pods -o wide

# Recent events
kubectl -n service2mcp get events --sort-by='.lastTimestamp' | tail -20

# Describe a failing pod
kubectl -n service2mcp describe pod <pod-name>

# Tail structured logs across all components
kubectl -n service2mcp logs -l app.kubernetes.io/instance=tool-compiler \
  --all-containers -f --tail=100 | jq .

# Health checks — each component exposes /healthz (liveness) and /readyz (readiness)
kubectl -n service2mcp exec deploy/tool-compiler-compiler-api     -- curl -sf localhost:8000/readyz | jq .
kubectl -n service2mcp exec deploy/tool-compiler-access-control   -- curl -sf localhost:8001/readyz | jq .
kubectl -n service2mcp exec deploy/tool-compiler-compiler-worker  -- curl -sf localhost:8002/readyz | jq .
kubectl -n service2mcp exec deploy/tool-compiler-mcp-runtime      -- curl -sf localhost:8003/readyz | jq .

# Celery queue status
kubectl -n service2mcp exec deploy/tool-compiler-compiler-worker -- \
  celery -A apps.compiler_worker.celery_app inspect active
kubectl -n service2mcp exec deploy/tool-compiler-compiler-worker -- \
  celery -A apps.compiler_worker.celery_app inspect reserved

# Helm release
helm status tool-compiler -n service2mcp
helm history tool-compiler -n service2mcp
```

---

## 2. Alert Response Procedures

Each alert maps to a rule in `observability/prometheus/alerts.yml`.
Format: **Symptom → Cause → Fix → Prevention.**

### 2.1 Service2MCPApiDown
`up{job="compiler-api"} == 0` for 5 min · **critical**

- **Symptom:** Compiler API unreachable; all API calls fail.
- **Cause:** Pod crash-loop, OOM-kill, failed readiness probe (DB unreachable), node failure.
- **Fix:** Check pod status & logs. Verify DB reachable from pod (`curl localhost:8000/readyz`). If OOM, raise memory limit (current default: 1Gi). See §3.5 for crash-loop details.
- **Prevention:** Set memory limits with headroom. Enable HPA (2–8 replicas, 70% CPU).

### 2.2 Service2MCPRuntimeDown
`up{job="mcp-runtime"} == 0` for 5 min · **critical**

- **Symptom:** MCP Runtime unreachable; all tool invocations fail.
- **Cause:** Missing/invalid `SERVICE_IR_PATH`, IR parse failure, secret collision, OOM.
- **Fix:** Check logs for `"Failed to load IR"` or `"SERVICE_IR_PATH is not configured"`. Verify ConfigMap/volume mount. Validate IR: `python -c "import json,gzip; json.loads(gzip.open('/path/ir.json.gz').read())"`.
- **Prevention:** Pre-validate IR before deploy. Readiness probe catches IR issues before traffic.

### 2.3 Service2MCPWorkerDown
`up{job="compiler-worker"} == 0` for 10 min · **critical**

- **Symptom:** No compilation jobs processed; tasks queue indefinitely in Redis.
- **Cause:** Redis broker unreachable, unsupported `WORKFLOW_ENGINE`, missing `DATABASE_URL`, OOM during LLM enhancement.
- **Fix:** Check `/readyz` for specific failure. Test Redis: `redis-cli -u "$REDIS_URL" ping`. Verify env vars: `printenv | grep -E 'DATABASE|REDIS|CELERY|WORKFLOW'`.
- **Prevention:** Monitor broker connectivity. Set `CELERY_WORKER_CONCURRENCY` to avoid memory pressure.

### 2.4 HighCompilationLatency
P95 `compiler_workflow_stage_duration_seconds` > 30s for 10 min · **warning**

- **Symptom:** Compilations slow; SSE streams may time out.
- **Cause:** Slow extractors (large specs), LLM enhancement timeouts, DB contention.
- **Fix:** Query metrics by `stage` label to identify the slow stage. If `ENHANCE` — check LLM provider or set `WORKER_ENABLE_LLM_ENHANCEMENT=false`. If `EXTRACT` — check spec size. If `DEPLOY` — check K8s API server.
- **Prevention:** Set per-stage timeouts. Monitor stage histograms. Scale workers.

### 2.5 HighToolInvocationLatency
P95 `mcp_runtime_tool_latency_seconds` > 5s for 5 min · **warning**

- **Symptom:** Tool calls slow; downstream agents time out.
- **Cause:** Upstream degradation, DNS delays, TLS issues, circuit breaker in half-open.
- **Fix:** Identify slow operations via `operation` label. Test upstream directly: `curl -w '%{time_total}' -o /dev/null -sf <upstream>`. Check circuit breaker via `/metrics`. Increase `PROXY_TIMEOUT` if needed (default: 10s).
- **Prevention:** Monitor upstream SLOs independently.

### 2.6 HighToolErrorRate
Error rate > 10% for 5 min · **warning**

- **Symptom:** >10% of tool calls return errors.
- **Cause:** Upstream 5xx, stale IR, circuit breakers tripping.
- **Fix:** Check `mcp_runtime_upstream_errors_total` by `error_type`. If 5xx → upstream issue. If timeouts → §2.5. If circuit breakers → §2.9. If parameter errors → recompile (upstream API changed).
- **Prevention:** Recompile when upstream APIs change.

### 2.7 HighUpstreamErrorRate
Upstream 5xx > 20% for 5 min · **warning**

- **Symptom:** Upstream APIs returning server errors; breakers may trip.
- **Cause:** Upstream degradation, deployment on upstream, network partition.
- **Fix:** Identify affected operations via metrics. Test upstream directly from pod. If maintenance — wait; breakers auto-recover on success.
- **Prevention:** Document upstream maintenance windows.

### 2.8 HighCompilationFailureRate
Failed compilations > 25% for 10 min · **warning**

- **Symptom:** Many compilations fail; new services not deploying.
- **Cause:** Bad input specs, extractor bugs, K8s quota exhaustion, LLM provider outage.
- **Fix:** Inspect `error_detail` on failed jobs. Common patterns: `"YAML parsing error"` → bad spec; `"Pod image pull failed"` → wrong `MCP_RUNTIME_IMAGE`; `"LLM enhancement timeout"` → disable/retry. Use `POST /api/v1/compilations/{job_id}/retry` for valid retries.
- **Prevention:** Pre-validate specs. Monitor per-stage failure rates.

### 2.9 CircuitBreakerOpen
`mcp_runtime_circuit_breaker_state == 1` for 2 min · **warning**

- **Symptom:** Operation blocked; callers receive `"Circuit breaker is open for operation {id}"`.
- **Cause:** 5 consecutive failures (default `FAILURE_THRESHOLD=5`) for the operation.
- **Fix:** Test upstream for the affected `operation_id`. If recovered, one successful request resets the breaker. For manual reset: `kubectl rollout restart deploy/tool-compiler-mcp-runtime` (see §6.2).
- **Prevention:** Tune `FAILURE_THRESHOLD`. Monitor upstream health independently.

### 2.10 MultipleCircuitBreakersOpen
>3 breakers open for 2 min · **critical**

- **Symptom:** Widespread tool failures; most operations blocked.
- **Cause:** Network partition, DNS failure, shared upstream outage.
- **Fix:** Check if operations share a common upstream. Test DNS/network from pod. Check NetworkPolicy. Restart runtime to clear all breakers.
- **Prevention:** Separate upstreams onto different failure domains.

### 2.11 HighMemoryUsage
Container memory > 80% of limit for 5 min · **warning**

- **Symptom:** Pod approaching OOM-kill.
- **Cause:** Large IR files, connection pool growth, concurrent LLM tasks.
- **Fix:** `kubectl top pods`. If Worker — reduce `CELERY_WORKER_CONCURRENCY`. If Runtime — check IR size. Increase limits if workload is legitimate.
- **Prevention:** Set limits with 20% headroom. Monitor trends.

### 2.12 HighCPUUsage
CPU > 80% for 15 min sustained · **warning**

- **Symptom:** Latency increase; pods throttled.
- **Cause:** Large spec extraction, high concurrency, burst traffic.
- **Fix:** `kubectl top pods`. Check HPA status (`kubectl get hpa`). Reduce concurrency or add replicas.
- **Prevention:** Enable HPA with appropriate thresholds.

### 2.13 DatabaseConnectionPoolExhausted
`pg_stat_activity_count >= max_connections - 2` for 2 min · **critical**

- **Symptom:** New DB connections rejected; API returns 503.
- **Cause:** Connection leak, too many replicas × large pool sizes, long-running queries.
- **Fix:** `SELECT state, count(*) FROM pg_stat_activity WHERE datname='tool_compiler' GROUP BY state;` Kill idle: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND query_start < now() - interval '5 min';`
- **Prevention:** Use PgBouncer. Configure pool sizes per component.

### 2.14 NoCompilationsIn1Hour
Zero successful compilations during business hours (08–20 UTC) · **info**

- **Symptom:** No compilations completing.
- **Cause:** Worker down, Redis unreachable, all jobs failing, or legitimately quiet.
- **Fix:** Check worker health (§2.3). Check queue (`celery inspect reserved`). Check failure rate (§2.8). Verify it isn't a legitimate quiet period.
- **Prevention:** Synthetic compilation canary during business hours.

### 2.15 AuditFailureSpike
15-min failure rate > 2× the 1-hour baseline for 15 min · **warning**

- **Symptom:** Sudden increase in compilation failures.
- **Cause:** Bad deployment, upstream API change, extractor regression.
- **Fix:** Correlate with recent deploys (`helm history`). Check if failures concentrate in one stage. Rollback if correlated: `helm rollback tool-compiler <REV> -n service2mcp`.
- **Prevention:** Canary deployments. Automated rollback on error-rate thresholds.

---

## 3. Common Failure Scenarios

### 3.1 Compilation Jobs Stuck in "pending"

- **Symptom:** Jobs accepted (202) but SSE shows no events beyond `JOB_CREATED`.
- **Cause:** Worker not running, Redis unreachable, queue name mismatch (`COMPILATION_TASK_QUEUE`), all worker slots occupied.
- **Fix:**
  ```bash
  # Verify worker readiness
  kubectl -n service2mcp exec deploy/tool-compiler-compiler-worker -- curl -sf localhost:8002/readyz | jq .
  # Check active/reserved tasks
  celery -A apps.compiler_worker.celery_app inspect active
  # Verify queue name matches between API and Worker (default: compiler.jobs)
  kubectl -n service2mcp exec deploy/tool-compiler-compiler-worker -- printenv COMPILATION_TASK_QUEUE
  # Last resort — purge queue and resubmit
  celery -A apps.compiler_worker.celery_app purge
  ```
- **Prevention:** Monitor `NoCompilationsIn1Hour`. Ensure queue name consistent in Helm values.

### 3.2 Tool Invocations Returning 503 / Timeout

- **Symptom:** `ToolError("Upstream timeout ...")` or `ToolError("Circuit breaker is open ...")`.
- **Distinguishing upstream vs internal:**
  ```bash
  # Check circuit breaker state (1=open)
  curl -sf localhost:8003/metrics | grep circuit_breaker_state
  # Test upstream directly from runtime pod
  curl -w '\nHTTP %{http_code} in %{time_total}s\n' -o /dev/null -sf <upstream-url>
  ```
- **If upstream:** Wait for recovery; breakers reset on first success.
- **If internal:** Check logs, verify `SERVICE_IR_PATH`, restart runtime.
- **Prevention:** Tune `PROXY_TIMEOUT` (default: 10s) and `FAILURE_THRESHOLD` (default: 5).

### 3.3 Database Migration Failures

- **Symptom:** Migration job fails; API readiness fails with schema errors.
- **Fix:**
  ```bash
  alembic -c migrations/alembic.ini current           # Check current revision
  alembic -c migrations/alembic.ini downgrade -1       # Rollback one step
  alembic -c migrations/alembic.ini stamp <revision>   # Stamp to known-good if inconsistent
  ```
- **Prevention:** Back up database before migrations. Test in staging first.

### 3.4 Auth Token Issues

| Issue | Log Pattern | Fix |
|-------|-------------|-----|
| JWT expired | `"token expired"` | Refresh from identity provider |
| Wrong JWT secret | `"JWT signature verification failed"` | Verify `ACCESS_CONTROL_JWT_SECRET` matches across pods |
| PAT revoked | `"PAT not found or revoked"` | Reissue via Access Control API |
| OIDC misconfigured | Access Control `/readyz` returns 503 | Check `JWT_ISSUER` / `JWT_AUDIENCE` match IdP |

```bash
# Verify JWT secret consistency
kubectl -n service2mcp get secret tool-compiler-secrets -o jsonpath='{.data.JWT_SECRET}' | base64 -d
```

### 3.5 Pod Crash Loops

| Cause | Log / Event Pattern | Fix |
|-------|---------------------|-----|
| OOM killed | `OOMKilled` in pod events | Increase memory limit |
| Missing env var | `KeyError: 'DATABASE_URL'` | Check secret mounts |
| DB unreachable | `ConnectionRefusedError` | Verify DB + NetworkPolicy |
| Invalid config | `"Unsupported workflow engine"` | Fix Helm values |

```bash
kubectl -n service2mcp describe pod <pod>                   # Events + exit code
kubectl -n service2mcp logs <pod> --previous                 # Previous crash logs
kubectl -n service2mcp exec deploy/<comp> -- printenv | sort # Verify env
```

### 3.6 Redis Connection Issues

- **Symptom:** Worker readiness fails; Celery logs show `ConnectionError`.
- **Common causes:** Memorystore IP changed, AUTH mismatch, NetworkPolicy blocking egress, Redis at memory limit.
- **Fix:**
  ```bash
  # Test from worker pod
  python -c "import redis; r = redis.from_url('$REDIS_URL'); print(r.ping())"
  # Verify endpoint
  printenv | grep -E 'REDIS|CELERY_BROKER'
  ```
- **Prevention:** Use Secret Manager for connection strings. Set `maxmemory-policy noeviction` for broker.

---

## 4. Performance Troubleshooting

### 4.1 Identifying Slow Extractors

Query `compiler_workflow_stage_duration_seconds_bucket` by `stage` label. Common slow spots:
- **Large OpenAPI specs** (>5 MB) — extraction scales with spec size.
- **GraphQL introspection** — blocks on upstream schema fetch.
- **REST discovery** — sequential OPTIONS probes across many endpoints.

**Mitigation:** Increase worker memory. Use spec-first compilation instead of discovery. Set stage timeouts.

### 4.2 Memory Leak Detection

```bash
kubectl -n service2mcp top pods --containers   # Observe trend over time
```

Common leak sources: IR objects retained after job completion (Worker), unclosed HTTP sessions (Runtime), Celery result accumulation (set `CELERY_RESULT_EXPIRES=3600`).

### 4.3 Connection Pool Exhaustion

```bash
# Check active connections by component
psql "$DATABASE_URL" -c \
  "SELECT application_name, state, count(*) FROM pg_stat_activity
   WHERE datname='tool_compiler' GROUP BY 1,2 ORDER BY 3 DESC;"
```

**Fix:** Reduce pool size per component, add PgBouncer, or increase `max_connections`.

---

## 5. Log Analysis

### 5.1 Key Log Patterns

| Pattern | Meaning |
|---------|---------|
| `"Stage failed:"` | Compilation stage error — check `stage`, `error`, `attempt` |
| `"Circuit breaker is open"` | Upstream blocked — see §2.9 |
| `"Upstream timeout"` | Request deadline exceeded — see §2.5 |
| `"Failed to load IR"` | Runtime IR load failure — check path/format |
| `"SERVICE_IR_PATH is not configured"` | Missing env var |
| `"Dispatch failed"` | Worker unreachable — check Redis |
| `"Protocol detection failed"` | Unknown API format in input |
| `"Pod image pull failed"` | Wrong `MCP_RUNTIME_IMAGE` |

### 5.2 Structured Log Fields

All components emit JSON logs: `timestamp`, `level`, `component` (`compiler-api` | `compiler-worker` | `mcp-runtime`), `logger`, `message`, `job_id`, `stage`, `attempt`, `error`, `trace_id`, `span_id`, `request_id` (from `X-Request-ID` header).

### 5.3 Log Level Adjustment

```bash
# Increase worker verbosity (requires restart)
kubectl -n service2mcp set env deploy/tool-compiler-compiler-worker CELERY_WORKER_LOGLEVEL=DEBUG

# Filter logs by level or job
kubectl -n service2mcp logs deploy/tool-compiler-compiler-worker --tail=500 | jq 'select(.level=="ERROR")'
kubectl -n service2mcp logs deploy/tool-compiler-compiler-worker --tail=1000 | jq 'select(.job_id=="<uuid>")'
```

---

## 6. Recovery Procedures

### 6.1 Full Restart Sequence

Order matters — dependencies must be healthy before dependents.

```bash
NS=service2mcp

# 1. Verify externals (DB + Redis) are reachable first

# 2. Restart in dependency order
kubectl -n $NS rollout restart deploy/tool-compiler-compiler-worker
kubectl -n $NS rollout status  deploy/tool-compiler-compiler-worker --timeout=120s

kubectl -n $NS rollout restart deploy/tool-compiler-access-control
kubectl -n $NS rollout status  deploy/tool-compiler-access-control --timeout=120s

kubectl -n $NS rollout restart deploy/tool-compiler-compiler-api
kubectl -n $NS rollout status  deploy/tool-compiler-compiler-api --timeout=120s

kubectl -n $NS rollout restart deploy/tool-compiler-mcp-runtime
kubectl -n $NS rollout status  deploy/tool-compiler-mcp-runtime --timeout=120s

# 3. Verify all pods ready
kubectl -n $NS wait --for=condition=ready pod \
  -l app.kubernetes.io/instance=tool-compiler --timeout=300s
```

### 6.2 Circuit Breaker Manual Reset

Breakers are in-memory; they reset on first successful upstream request or on pod restart.

```bash
# Option A — restart runtime (resets ALL breakers)
kubectl -n service2mcp rollout restart deploy/tool-compiler-mcp-runtime

# Option B — send a test request through the affected operation (upstream must be healthy)
curl -X POST http://runtime:8003/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"<tool>","arguments":{}},"id":1}'

# Verify — no output means all breakers closed
curl -sf localhost:8003/metrics | grep 'circuit_breaker_state.*1'
```

### 6.3 Clearing Stuck Compilation Jobs

```bash
# Retry from last checkpoint
curl -X POST http://api:8000/api/v1/compilations/<job_id>/retry

# Retry from a specific stage
curl -X POST "http://api:8000/api/v1/compilations/<job_id>/retry?from_stage=extract"

# Purge queue + resubmit (last resort)
celery -A apps.compiler_worker.celery_app purge
```

### 6.4 Database Recovery from Backup

```bash
NS=service2mcp

# 1. Stop all writes
kubectl -n $NS scale deploy/tool-compiler-compiler-api    --replicas=0
kubectl -n $NS scale deploy/tool-compiler-compiler-worker --replicas=0
kubectl -n $NS scale deploy/tool-compiler-access-control  --replicas=0
kubectl -n $NS scale deploy/tool-compiler-mcp-runtime     --replicas=0

# 2. Restore (Cloud SQL example)
gcloud sql backups restore <BACKUP_ID> --restore-instance=<INSTANCE> --backup-instance=<INSTANCE>

# 3. Run pending migrations
alembic -c migrations/alembic.ini upgrade head

# 4. Scale back up
kubectl -n $NS scale deploy/tool-compiler-compiler-worker --replicas=1
kubectl -n $NS scale deploy/tool-compiler-access-control  --replicas=1
kubectl -n $NS scale deploy/tool-compiler-compiler-api    --replicas=2
kubectl -n $NS scale deploy/tool-compiler-mcp-runtime     --replicas=2

# 5. Verify
kubectl -n $NS wait --for=condition=ready pod \
  -l app.kubernetes.io/instance=tool-compiler --timeout=300s
```

### 6.5 Helm Rollback

```bash
helm history tool-compiler -n service2mcp
helm rollback tool-compiler <REVISION> -n service2mcp
kubectl -n service2mcp rollout status deploy/tool-compiler-compiler-api
```

---

## 7. FAQ

### 7.1 Why is the MCP transport path `/mcp/mcp`?

This is **by design**, not a bug. The MCP Runtime's FastAPI app mounts the
SDK's `streamable_http_app()` at the `/mcp` prefix (`main.py:212`). The SDK
itself serves its Streamable HTTP transport at `/mcp` within that sub-app.
The two prefixes compose to produce the full path `/mcp/mcp`.

All internal callers — the compiler worker's `ToolInvoker`
(`activities/production.py:1983`), smoke/post-deploy validators, and the
circuit breaker reset example in §6.2 — use `/mcp/mcp`. External MCP
clients (e.g. Claude Desktop, Cursor) should also be configured with
`http://<host>:<port>/mcp/mcp` as the transport URL.
