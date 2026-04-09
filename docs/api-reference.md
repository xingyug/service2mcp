# API Reference — service2mcp

> All services are FastAPI applications exposing JSON over HTTP.
> Base path for versioned endpoints: `/api/v1/`.

---

## Authentication

All authenticated endpoints require a **Bearer token** in the `Authorization` header:

```
Authorization: Bearer <jwt_or_pat>
```

| Token type | Format | Notes |
|------------|--------|-------|
| **JWT** | Standard JWT (RS256 / HS256) | Issued by an external IdP; validated against configured public key. |
| **PAT** | `pat_…` prefix | Personal Access Token; stored as a hash; created via the AuthN API. |

**SSE endpoints** also accept a `?token=…` query parameter (logged as a security warning).

### Role-based access

| Decorator / helper | Effect |
|--------------------|--------|
| `require_authenticated_caller` | Any valid JWT or PAT. |
| `require_admin_caller` | Caller must have role `admin`, `administrator`, or `superuser`. |
| `require_self_or_admin` | Caller must own the resource **or** be admin. |
| `require_scope_access` | Non-admin callers must have matching `tenant` / `environment` claims. |

---

## Common conventions

### Error response

Every error uses the FastAPI default envelope:

```json
{ "detail": "Human-readable error message." }
```

| Status | Meaning |
|--------|---------|
| 400 | Bad request / malformed input |
| 401 | Missing or invalid token |
| 403 | Insufficient role or scope |
| 404 | Resource not found |
| 409 | Conflict (duplicate, invalid state transition) |
| 422 | Validation error (Pydantic) |
| 502 | Gateway sync failure |
| 503 | Dependency unavailable (DB, worker, runtime) |

### Pagination (where supported)

| Query param | Default | Constraints |
|-------------|---------|-------------|
| `page` | 1 | ≥ 1 |
| `page_size` | 100 | 1–200 |

Response includes `total`, `page`, `page_size` alongside `items`.

### Request headers

| Header | Purpose |
|--------|---------|
| `Authorization` | Bearer token (required for authed endpoints) |
| `X-Request-ID` | Optional; echoed back and used in logs. Auto-generated if absent. |

### Security response headers

All Compiler API responses include:

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 0
Referrer-Policy: strict-origin-when-cross-origin
Cache-Control: no-store
```

### Rate limiting

Not currently enforced at the application layer.

---

## 1 — Compiler API

Default port: **8000**.

### 1.1 Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/healthz` | None | Liveness probe. Returns `{"status":"ok"}`. |
| GET | `/readyz` | None | Readiness probe. Checks DB; returns 503 if unreachable. |

### 1.2 Compilations `/api/v1/compilations`

#### Create compilation

```
POST /api/v1/compilations
```

Submits an API spec for asynchronous compilation. Returns **202 Accepted**.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `source_url` | string | ✦ | URL to fetch the spec from. |
| `source_content` | string | ✦ | Inline spec content. |
| `source_hash` | string | — | Content hash for dedup. |
| `filename` | string | — | Hint for protocol detection. |
| `service_id` | string | — | Target service identifier. |
| `service_name` | string | — | Human-friendly name. |
| `options` | object | — | Compiler options (e.g. `{"enhance": true}`). |

✦ Exactly one of `source_url` or `source_content` is required.

**Response** — `CompilationJobResponse`:

```json
{
  "id": "uuid",
  "status": "RUNNING",
  "current_stage": "detect",
  "source_url": null,
  "source_hash": null,
  "protocol": null,
  "error_detail": null,
  "options": {},
  "created_by": "user@example.com",
  "service_id": null,
  "service_name": null,
  "tenant": null,
  "environment": null,
  "artifacts": null,
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

```bash
curl -X POST https://compiler.example.com/api/v1/compilations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_url":"https://petstore3.swagger.io/api/v3/openapi.json"}'
```

#### List compilations

```
GET /api/v1/compilations[?service_id=<id>]
```

Returns `CompilationJobResponse[]`. No pagination (returns full list).

#### Get compilation

```
GET /api/v1/compilations/{job_id}
```

Returns a single `CompilationJobResponse` or **404**.

#### Retry compilation

```
POST /api/v1/compilations/{job_id}/retry[?from_stage=<stage>]
```

Creates a new job cloning the original request. Optional `from_stage` resumes from a
checkpoint (valid stages: `detect`, `extract`, `normalize`, `validate`, `deploy`).
Returns **202**. Errors: 404, 409 (invalid stage or missing checkpoint), 422, 503.

```bash
curl -X POST https://compiler.example.com/api/v1/compilations/$JOB_ID/retry?from_stage=validate \
  -H "Authorization: Bearer $TOKEN"
```

#### Rollback compilation

```
POST /api/v1/compilations/{job_id}/rollback
```

Rolls a succeeded job back to the previous artifact version. The original job must have
`status=SUCCEEDED` with a resolvable `service_id` and a prior version. Returns **202**.
Errors: 404, 409 (no rollback target), 503.

#### Stream events (SSE)

```
GET /api/v1/compilations/{job_id}/events[?token=<pat>]
```

Server-Sent Events stream. Emits `stage_started`, `stage_succeeded`, `stage_failed`,
and `stream.error` events. Polls every 100 ms; closes on terminal status. Auth via
header **or** `?token=` query param.

```bash
curl -N https://compiler.example.com/api/v1/compilations/$JOB_ID/events \
  -H "Authorization: Bearer $TOKEN"
```

### 1.3 Artifact Registry `/api/v1/artifacts`

#### Create artifact version

```
POST /api/v1/artifacts
```

Registers a compiled ServiceIR as a versioned artifact. Returns **201**.

Key body fields: `service_id` (required), `version_number` (int ≥ 1, required),
`ir_json` (required), `raw_ir_json`, `compiler_version`, `source_url`, `source_hash`,
`protocol`, `validation_report`, `deployment_revision`, `route_config`, `tenant`,
`environment`, `is_active`, `artifacts[]`.

```bash
curl -X POST https://compiler.example.com/api/v1/artifacts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_id":"petstore","version_number":1,"ir_json":{...}}'
```

#### List versions

```
GET /api/v1/artifacts/{service_id}/versions[?tenant=&environment=]
```

Returns `{"service_id":"…","versions":[…]}`.

#### Get version

```
GET /api/v1/artifacts/{service_id}/versions/{version_number}[?tenant=&environment=]
```

Returns `ArtifactVersionResponse` or **404**.

#### Update version

```
PUT /api/v1/artifacts/{service_id}/versions/{version_number}[?tenant=&environment=]
```

Partial update. All body fields optional. Returns updated `ArtifactVersionResponse`.

#### Delete version

```
DELETE /api/v1/artifacts/{service_id}/versions/{version_number}[?tenant=&environment=]
```

Returns **204**. Syncs route deletion with gateway. If the deleted version was active,
rolls back to the previous version. Errors: 404, 502 (gateway sync failure).

#### Activate version

```
POST /api/v1/artifacts/{service_id}/versions/{version_number}/activate[?tenant=&environment=]
```

Sets `is_active=true` and syncs routes to the gateway. Returns `ArtifactVersionResponse`.
Errors: 404, 502.

#### Diff versions

```
GET /api/v1/artifacts/{service_id}/diff?from=<int>&to=<int>[&tenant=&environment=]
```

Returns an IR-level diff between two artifact versions:

```json
{
  "service_id": "petstore",
  "from_version": 1,
  "to_version": 2,
  "summary": "2 added, 1 removed, 1 changed",
  "is_empty": false,
  "added_operations": ["createPet"],
  "removed_operations": ["deletePet"],
  "changed_operations": [{ "operation_id": "listPets", "changes": [...] }]
}
```

### 1.4 Services `/api/v1/services`

#### Dashboard summary

```
GET /api/v1/services/dashboard/summary[?tenant=&environment=&limit=10]
```

Scope-checked. Returns:

```json
{
  "total_services": 5,
  "total_tools": 42,
  "protocol_distribution": {"http": 3, "graphql": 2},
  "recent_compilations": [],
  "services_by_status": {"active": 4, "draft": 1}
}
```

#### List services

```
GET /api/v1/services[?tenant=&environment=]
```

Returns `{"services":[…]}` with `ServiceSummaryResponse` items containing
`service_id`, `active_version`, `version_count`, `tool_count`, `protocol`, etc.

#### Get service

```
GET /api/v1/services/{service_id}[?tenant=&environment=]
```

Returns `ServiceSummaryResponse` or 404 / 409.

### 1.5 Review Workflows `/api/v1/workflows`

Workflow states: `draft` → `submitted` → `in_review` → `approved` → `published` → `deployed`.

#### Get workflow

```
GET /api/v1/workflows/{service_id}/v/{version_number}[?tenant=&environment=]
```

Returns `WorkflowResponse`. Auto-creates a `draft` record if none exists.

```json
{
  "id": "uuid",
  "service_id": "petstore",
  "version_number": 1,
  "state": "draft",
  "review_notes": null,
  "history": [],
  "tenant": null,
  "environment": null,
  "created_at": "…",
  "updated_at": "…"
}
```

#### Transition workflow

```
POST /api/v1/workflows/{service_id}/v/{version_number}/transition[?tenant=&environment=]
```

Body:

```json
{ "to": "in_review", "actor": "alice", "comment": "Ready for review" }
```

Role requirements for specific transitions:

| From → To | Required role |
|-----------|---------------|
| `in_review` → `approved` | `reviewer` or `admin` |
| `approved` → `published` | `publisher` or `admin` |
| `published` → `deployed` | `deployer` or `admin` |

Errors: 403, 404, 409 (invalid transition), 422.

#### Save review notes

```
PUT /api/v1/workflows/{service_id}/v/{version_number}/notes[?tenant=&environment=]
```

Body:

```json
{
  "notes": {"op-1": "Looks good", "op-2": "Needs risk label"},
  "overall_note": "Approved with minor comments",
  "reviewed_operations": ["op-1", "op-2"]
}
```

#### Get workflow history

```
GET /api/v1/workflows/{service_id}/v/{version_number}/history[?tenant=&environment=]
```

Returns `WorkflowHistoryEntry[]` with `from`, `to`, `actor`, `comment`, `timestamp`.

---

## 2 — MCP Runtime

Default port: **8001**.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/healthz` | None | Liveness probe. |
| GET | `/readyz` | None | Readiness with tool count and upstream status. Returns 503 if not loaded. |
| GET | `/tools` | None | Lists all MCP tools (name, description, input_schema). 503 if not ready. |
| GET | `/metrics` | None | Prometheus metrics (`text/plain`). |
| — | `/mcp/*` | MCP protocol | FastMCP streamable HTTP transport. Not a REST endpoint. |

**`/readyz` response:**

```json
{
  "status": "ok",
  "service_name": "petstore",
  "tool_count": 12,
  "service_ir_path": "/data/ir.json",
  "upstream_problems": []
}
```

**`/tools` response:**

```json
{
  "status": "ok",
  "service_name": "petstore",
  "tool_count": 12,
  "tools": [
    { "name": "listPets", "description": "List all pets", "input_schema": {…} }
  ]
}
```

---

## 3 — Access Control Service

Default port: **8002**.

### 3.1 Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/healthz` | None | Liveness probe. |
| GET | `/readyz` | None | Checks JWT config, gateway binding, and DB. 503 on failure. |

### 3.2 Authentication `/api/v1/authn`

#### Validate token

```
POST /api/v1/authn/validate
```

**No auth required.** Body: `{"token":"…"}`. Returns `TokenPrincipalResponse`:

```json
{
  "subject": "user-uuid-or-sub",
  "username": "alice",
  "token_type": "jwt",
  "claims": { "roles": ["admin"], "tenant": "acme" }
}
```

Errors: 401.

#### Create PAT

```
POST /api/v1/authn/pats
```

Auth: self-or-admin. Body: `{"username":"alice","name":"ci-token"}`.
Returns **201** with `PATCreateResponse` (includes plaintext `token` — shown **once**).

```bash
curl -X POST https://acl.example.com/api/v1/authn/pats \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","name":"ci-token"}'
```

#### List PATs

```
GET /api/v1/authn/pats?username=alice[&page=1&page_size=100]
```

Auth: self-or-admin. Paginated response:

```json
{ "items": [{ "id": "uuid", "username": "alice", "name": "ci-token", "created_at": "…", "revoked_at": null }], "total": 1, "page": 1, "page_size": 100 }
```

#### Revoke PAT

```
POST /api/v1/authn/pats/{pat_id}/revoke
```

Auth: self-or-admin. Sets `revoked_at`. Syncs revocation to gateway. Errors: 400, 403, 404, 502.

### 3.3 Authorization `/api/v1/authz`

#### Create policy *(admin)*

```
POST /api/v1/authz/policies
```

```json
{
  "subject_type": "user",
  "subject_id": "alice",
  "resource_id": "petstore",
  "action_pattern": "tool.*",
  "risk_threshold": "safe",
  "decision": "allow"
}
```

Returns **201** `PolicyResponse`. `decision` must be `allow`, `deny`, or `require_approval`.
`risk_threshold`: `safe` | `cautious` | `dangerous` | `unknown`.

#### List policies

```
GET /api/v1/authz/policies[?subject_type=&subject_id=&resource_id=]
```

Returns `{"items":[…]}`.

#### Get / Update / Delete policy

```
GET    /api/v1/authz/policies/{policy_id}        # any authed caller
PUT    /api/v1/authz/policies/{policy_id}        # admin only
DELETE /api/v1/authz/policies/{policy_id}        # admin only, returns 204
```

#### Evaluate policy

```
POST /api/v1/authz/evaluate
```

```json
{
  "subject_type": "user",
  "subject_id": "alice",
  "resource_id": "petstore",
  "action": "tool.deletePet",
  "risk_level": "dangerous"
}
```

Response:

```json
{ "decision": "deny", "matched_policy_id": "uuid-or-null", "reason": "…" }
```

### 3.4 Audit `/api/v1/audit`

#### List audit logs

```
GET /api/v1/audit/logs[?actor=&action=&resource=&start_at=&end_at=&limit=1000&include_all=false]
```

Returns `{"items":[…]}`. Set `include_all=true` to ignore the limit.

#### Get audit log entry

```
GET /api/v1/audit/logs/{entry_id}
```

```json
{ "id": "uuid", "actor": "alice", "action": "compilation.triggered", "resource": "petstore", "detail": {…}, "timestamp": "…" }
```

### 3.5 Gateway Binding `/api/v1/gateway-binding` *(admin)*

All gateway-binding endpoints require **admin** role.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/gateway-binding/reconcile` | Full gateway state reconciliation. |
| POST | `/api/v1/gateway-binding/service-routes/sync` | Sync routes from `route_config`. |
| POST | `/api/v1/gateway-binding/service-routes/delete` | Delete routes for a service. |
| POST | `/api/v1/gateway-binding/service-routes/rollback` | Rollback to `previous_routes`. |
| GET  | `/api/v1/gateway-binding/service-routes` | List all synced routes. |

**Reconcile response:**

```json
{
  "consumers_synced": 3,
  "consumers_deleted": 1,
  "policy_bindings_synced": 5,
  "policy_bindings_deleted": 0,
  "service_routes_synced": 8,
  "service_routes_deleted": 2
}
```

---

## 4 — Gateway Admin (Mock)

Test-only service simulating an external API gateway.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Liveness probe. |
| GET | `/admin/consumers` | List consumers. |
| PUT | `/admin/consumers/{id}` | Upsert consumer. |
| DELETE | `/admin/consumers/{id}` | Delete consumer. |
| GET | `/admin/policy-bindings` | List policy bindings. |
| PUT | `/admin/policy-bindings/{id}` | Upsert binding. |
| DELETE | `/admin/policy-bindings/{id}` | Delete binding. |
| GET | `/admin/routes` | List routes. |
| PUT | `/admin/routes/{id}` | Upsert route. |
| DELETE | `/admin/routes/{id}` | Delete route. |
| ALL | `/gateway/{service_id}/{path}` | Proxy to upstream (uses route matching). |

---

## Audit log actions

| Action | Trigger |
|--------|---------|
| `artifact.created` | Artifact version registered |
| `artifact.updated` | Artifact version patched |
| `artifact.deleted` | Artifact version removed |
| `artifact.activated` | Artifact version promoted to active |
| `compilation.triggered` | New compilation job created |
| `compilation.retried` | Compilation retried (detail includes `from_stage`) |
| `compilation.rollback_requested` | Rollback initiated |
| `pat.created` | PAT issued |
| `pat.revoked` | PAT revoked |
| `policy.created` | AuthZ policy created |
| `policy.updated` | AuthZ policy updated |
| `policy.deleted` | AuthZ policy removed |
| `authz.evaluate` | Policy evaluation performed |
