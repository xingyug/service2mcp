# Tool Compiler v2 Bug Scan Log — 2026-03-29

> Owner: Copilot CLI session `84ed1d4a-0d8b-4375-ae86-e98d1c071819`
>
> Scope: backend + frontend static/runtime scan
>
> Rule: record only, no fixes in this pass

## Status

- Reading set completed from `new-agent-reading-list.md`
- Bug scan started
- 74 bugs recorded so far
- This is a brand new log for this session and will be updated continuously

## Project model snapshot

- The system centers on `ServiceIR` and spans compiler API, compiler worker, access control, generic MCP runtime, protocol extractors, validators, deployment assets, and a Next.js web UI.
- Current high-risk areas called out by project docs include black-box discovery, proof-runner sampling drift, SOAP real-target handling, real-target protocol coverage gaps, and frontend review/auth workflows.

## Logging format

Each finding is recorded as:

- `BUG-###`
- Area
- Severity
- Files
- Summary
- Evidence / reasoning

## Findings

### Backend

- `BUG-001` — **high** — `backend/toolchain`
  - Files: `noxfile.py`, `.github/workflows/ci.yaml`, `pyproject.toml`
  - Summary: `nox` `typecheck/tests` are not runnable in the default environment because the sessions resolve against Python 3.11 while the project requires `>=3.12`.
  - Evidence: `uv run nox -s lint typecheck tests` passed `lint`, then failed dependency resolution with `current Python version (3.11.2) does not satisfy Python>=3.12`.

- `BUG-002` — **high** — `backend/sql-extractor`
  - Files: `libs/extractors/sql.py`
  - Summary: SQL extraction emits only query and insert operations, so `UPDATE` and `DELETE` support is silently missing.
  - Evidence: `SQLExtractor._build_operations()` calls only `_build_query_operation()` and `_build_insert_operation()`.

- `BUG-003` — **medium** — `backend/jsonrpc-extractor`
  - Files: `libs/extractors/jsonrpc.py`
  - Summary: JSON-RPC extraction depends on OpenRPC/manual method lists and has no fallback discovery path such as `system.listMethods`.
  - Evidence: The extractor reads declared `methods` and config markers only; there is no live endpoint probing for method enumeration.

- `BUG-004` — **medium** — `backend/grpc-extractor`
  - Files: `libs/extractors/grpc.py`
  - Summary: gRPC extraction only parses fetched `.proto` text and cannot discover reflection-enabled services.
  - Evidence: `_get_content()` fetches text and `extract()` parses messages/services directly; no reflection client or descriptor fetch path exists.

- `BUG-005` — **high** — `backend/soap-runtime`
  - Files: `apps/mcp_runtime/proxy.py`
  - Summary: SOAP request serialization always namespaces child arguments under the target namespace, which breaks unqualified-child WSDLs.
  - Evidence: `_build_soap_envelope()` passes `target_namespace` into `_append_soap_argument()`, and `_append_soap_argument()` recursively emits `{namespace}{name}` tags.

- `BUG-031` — **high** — `backend/control-plane`
  - Files: `apps/compiler_worker/repository.py`
  - Summary: Compilation event sequence numbers are generated with `select(max)+1` outside any lock, so concurrent writers can assign duplicate sequence numbers and lose events.
  - Evidence: `_next_sequence_number()` computes the next value before insert/commit, leaving a race against other writers before the unique `(job_id, sequence_number)` constraint.

- `BUG-032` — **high** — `backend/control-plane`
  - Files: `apps/compiler_api/routes/compilations.py`
  - Summary: Compilation submission deletes the fresh job record when dispatcher enqueue fails, erasing the job instead of preserving a failed/pending artifact for audit and recovery.
  - Evidence: `create_compilation()` creates the job, then on enqueue failure calls `repository.delete_job(job.id)` and returns `503`.

- `BUG-033` — **medium** — `backend/control-plane`
  - Files: `apps/compiler_api/repository.py`
  - Summary: Service version activation can race because the repository locks only currently active rows and then deactivates all versions for the service.
  - Evidence: `_deactivate_service_versions()` selects active rows `FOR UPDATE`, then updates every version for the service with `is_active=False`.

- `BUG-034` — **medium** — `backend/access-control`
  - Files: `apps/access_control/authn/routes.py`, `apps/access_control/authz/routes.py`
  - Summary: PAT and policy mutations report success even when gateway synchronization fails, leaving DB state and gateway state inconsistent.
  - Evidence: PAT/policy route handlers catch broad gateway sync exceptions, log warnings, and still return success responses.

- `BUG-035` — **medium** — `backend/workflow`
  - Files: `apps/compiler_worker/activities/production.py`
  - Summary: `DeferredRoutePublisher` assumes `route_config.default_route.route_id` always exists, so malformed route metadata explodes with `KeyError`/`TypeError`.
  - Evidence: `publish()` directly indexes `route_config["default_route"]["route_id"]` without validation.

- `BUG-036` — **medium** — `backend/workflow`
  - Files: `apps/compiler_worker/activities/production.py`
  - Summary: Route and register stages dereference payload keys and cast payload objects without runtime validation, so partial workflow context fails late with `KeyError` or attribute errors.
  - Evidence: `route_stage()` and `register_stage()` read required payload fields with `[]` and unchecked `cast(...)`.

- `BUG-037` — **medium** — `backend/streaming`
  - Files: `apps/compiler_api/routes/compilations.py`
  - Summary: The compilation SSE stream terminates silently when the job disappears, so clients get a dropped connection without any terminal event.
  - Evidence: `stream_compilation_events()` breaks immediately when `poll_repository.get_job(job_id)` returns `None`.

- `BUG-038` — **high** — `backend/validator`
  - Files: `libs/validator/black_box.py`
  - Summary: Black-box failure pattern analysis indexes `p.split("/")[1]` without guarding empty or relative paths.
  - Evidence: `base = "/" + p.split("/")[1]` assumes at least two path segments and will throw `IndexError` on inputs like `items/{id}` or `""`.

- `BUG-039` — **medium** — `backend/sql-runtime`
  - Files: `apps/mcp_runtime/sql.py`
  - Summary: SQL insert execution fabricates `row_count=1` whenever the driver reports unknown rowcount, which can misreport inserts and hide no-op behavior.
  - Evidence: `_insert_rows()` falls back to `1` when `result.rowcount` is `None` or negative.

- `BUG-048` — **high** — `backend/grpc-streaming`
  - Files: `libs/extractors/grpc.py`
  - Summary: The gRPC extractor silently ignores client-streaming and bidirectional streaming RPCs, so those APIs never become invokable operations even when streaming metadata is present.
  - Evidence: Streaming RPCs become operations only when native streaming is enabled and mode is `server`; otherwise they are pushed into `ignored_streaming_rpcs` and the event descriptor has `operation_id=None`.

- `BUG-049` — **high** — `backend/grpc-streaming`
  - Files: `apps/mcp_runtime/grpc_stream.py`
  - Summary: The native gRPC streaming runtime only implements server-stream mode and raises `ToolError` for client-streaming or bidirectional operations.
  - Evidence: `ReflectionGrpcStreamExecutor._invoke_sync()` raises `not implemented yet` whenever `config.mode is not GrpcStreamMode.server`.

- `BUG-050` — **high** — `backend/openapi-extractor`
  - Files: `libs/extractors/openapi.py`
  - Summary: OpenAPI `$ref` resolution silently converts invalid or external references into empty dicts, which drops schema information without surfacing an error.
  - Evidence: `_follow_ref()` returns `{}` for external refs and for any missing internal pointer segment via `current.get(part, {})`.

- `BUG-051` — **medium** — `backend/health`
  - Files: `apps/access_control/main.py`
  - Summary: The access-control readiness endpoint returns HTTP `200` with body `status=not_ready` when the database is unreachable, so orchestrators still see the service as ready.
  - Evidence: `readyz()` catches DB exceptions and returns `{"status": "not_ready"}` instead of a non-2xx response.

- `BUG-052` — **high** — `backend/registry`
  - Files: `libs/db_models.py`, `apps/compiler_api/repository.py`
  - Summary: Registry versioning and active-version constraints ignore `tenant` and `environment`, so different tenants/environments cannot safely hold independent versions or active revisions for the same `service_id`.
  - Evidence: `ServiceVersion` stores `tenant` and `environment`, but uniqueness and single-active indexes are scoped only by `service_id` / `service_id + version_number`; repository create/activation logic also operates by `service_id` alone.

- `BUG-053` — **medium** — `backend/health`
  - Files: `apps/compiler_worker/main.py`
  - Summary: The compiler-worker readiness endpoint returns HTTP `200` even when required runtime configuration is missing, so orchestration can route work to an unready worker.
  - Evidence: `readyz()` computes `status="not_ready"` and a `missing` list, but returns the body directly without setting a non-2xx status code.

- `BUG-054` — **critical** — `backend/authn`
  - Files: `apps/access_control/authn/routes.py`
  - Summary: PAT creation is exposed without any authentication or authorization guard, so any caller can mint a PAT for an arbitrary username.
  - Evidence: `create_pat()` depends only on service dependencies; there is no caller identity dependency or role check before creating a PAT for `payload.username`.

- `BUG-055` — **high** — `backend/authn`
  - Files: `apps/access_control/authn/routes.py`
  - Summary: PAT listing is exposed without authentication, so any caller can enumerate PAT metadata for any username by passing a query parameter.
  - Evidence: `list_pats(username: str)` has no authentication dependency and returns PAT metadata for the requested username.

- `BUG-056` — **high** — `backend/authn`
  - Files: `apps/access_control/authn/routes.py`
  - Summary: PAT revocation is exposed without authentication, so any caller who learns a PAT UUID can revoke it.
  - Evidence: `revoke_pat()` validates the UUID and revokes it through `AuthnService` with no caller authentication or ownership check.

- `BUG-057` — **critical** — `backend/authz`
  - Files: `apps/access_control/authz/routes.py`
  - Summary: Policy CRUD and evaluation endpoints are exposed without authentication, so any caller can create, modify, delete, or test authorization policy state.
  - Evidence: `authz/routes.py` wires create/list/get/update/delete/evaluate handlers without any dependency that authenticates or authorizes the caller.

- `BUG-058` — **critical** — `backend/gateway-binding`
  - Files: `apps/access_control/gateway_binding/routes.py`
  - Summary: Gateway-binding reconciliation and route mutation endpoints are exposed without authentication, allowing arbitrary callers to sync, delete, or roll back gateway routes.
  - Evidence: `gateway_binding/routes.py` defines reconcile and service-route mutation endpoints with only DB/service dependencies and no auth guard.

- `BUG-059` — **high** — `backend/authn`
  - Files: `apps/access_control/authn/service.py`, `libs/db_models.py`
  - Summary: PAT issuance and validation ignore `User.is_active`, so disabled users can keep using existing PATs and can still receive new PATs if the routes are called.
  - Evidence: `User` stores `is_active`, but `AuthnService._validate_pat()` never checks it, and `_get_or_create_user()` / `create_pat()` do not reject inactive users.

### Frontend

- `BUG-006` — **high** — `frontend/auth`
  - Files: `apps/web-ui/src/stores/auth-store.ts`, `apps/web-ui/src/lib/api-client.ts`, `apps/web-ui/src/lib/hooks/use-sse.ts`
  - Summary: Frontend auth state is persisted under `auth-storage`, but request helpers read `auth_token`, so logged-in sessions lose auth headers on normal API and SSE calls.
  - Evidence: The Zustand auth store persists with key `auth-storage`; `getAuthToken()` and `useCompilationEvents()` read `localStorage.getItem("auth_token")`.

- `BUG-007` — **high** — `frontend/login`
  - Files: `apps/web-ui/src/app/(auth)/login/page.tsx`, `apps/web-ui/src/lib/api-client.ts`
  - Summary: The login page bypasses the configured access-control base URL and posts to same-origin `/api/v1/authn/validate`, which breaks split-origin deployments.
  - Evidence: `login/page.tsx` uses `fetch("/api/v1/authn/validate")` directly instead of `ACCESS_CONTROL_API`.

- `BUG-008` — **high** — `frontend/login`
  - Files: `apps/web-ui/src/app/(auth)/login/page.tsx`, `apps/access_control/authn/routes.py`, `apps/access_control/authn/models.py`
  - Summary: Both login tabs call the validate endpoint with only `Authorization` headers, but the backend validate API requires JSON `{token}` in the body.
  - Evidence: The login page sends header-only POSTs; backend `validate_token(payload: TokenValidationRequest)` requires `token`.

- `BUG-009` — **high** — `frontend/login`
  - Files: `apps/web-ui/src/app/(auth)/login/page.tsx`, `apps/access_control/authn/models.py`
  - Summary: Password login stores the Basic credential blob as the session token, so later API calls send `Bearer <base64(username:password)>` instead of a PAT/JWT.
  - Evidence: `handlePasswordLogin()` uses `const token = data.token ?? basicToken`, but the backend validation response does not include a `token` field.

- `BUG-044` — **medium** — `frontend/login`
  - Files: `apps/web-ui/src/app/(auth)/login/page.tsx`, `apps/access_control/authn/models.py`
  - Summary: PAT login maps the validation response to the wrong fields and falls back to a literal username of `user`, so downstream flows operate on bogus identity data.
  - Evidence: PAT login builds `user` from `data.username ?? "user"`, `data.email`, and `data.roles`; backend `TokenPrincipalResponse` exposes only `subject`, `token_type`, and `claims`.

- `BUG-010` — **high** — `frontend/auth-api`
  - Files: `apps/web-ui/src/lib/api-client.ts`, `apps/access_control/authn/routes.py`
  - Summary: The shared auth API client points at `/api/v1/auth/*`, but the backend exposes `/api/v1/authn/*`.
  - Evidence: `authApi` targets `/api/v1/auth/validate` and `/api/v1/auth/pats`; backend mounts `APIRouter(prefix="/api/v1/authn")`.

- `BUG-011` — **medium** — `frontend/pats`
  - Files: `apps/web-ui/src/lib/api-client.ts`, `apps/access_control/authn/routes.py`
  - Summary: PAT listing omits the required `username` query parameter.
  - Evidence: `authApi.listPATs()` issues a plain GET, while backend `list_pats(username: str)` requires `username`.

- `BUG-012` — **high** — `frontend/pats`
  - Files: `apps/web-ui/src/types/api.ts`, `apps/web-ui/src/app/(dashboard)/pats/page.tsx`, `apps/access_control/authn/models.py`
  - Summary: The PAT page expects response fields `pats` and `pat_id`, but the backend returns `items` and `id`.
  - Evidence: PAT page reads `data?.pats` and `revokeTarget.pat_id`; backend models expose `items` and `id`.

- `BUG-013` — **high** — `frontend/policies`
  - Files: `apps/web-ui/src/lib/api-client.ts`, `apps/access_control/authz/routes.py`
  - Summary: Policy API calls drop the `/authz` prefix and use `PATCH` for updates while the backend expects `/api/v1/authz/...` and `PUT`.
  - Evidence: `policyApi` targets `/api/v1/policies`; backend mounts `APIRouter(prefix="/api/v1/authz")` and defines `PUT /policies/{policy_id}`.

- `BUG-014` — **high** — `frontend/policies`
  - Files: `apps/web-ui/src/types/api.ts`, `apps/web-ui/src/app/(dashboard)/policies/page.tsx`, `apps/access_control/authz/models.py`
  - Summary: The policies UI expects list field `policies` and item field `policy_id`, but the backend returns `items` and `id`.
  - Evidence: Policies page renders `policy.policy_id`; backend `PolicyResponse` exposes `id` and `PolicyListResponse` exposes `items`.

- `BUG-045` — **high** — `frontend/policies`
  - Files: `apps/web-ui/src/app/(dashboard)/policies/page.tsx`, `apps/access_control/authz/models.py`
  - Summary: Policy evaluation requests omit the required `risk_level` field, so the backend evaluation endpoint rejects the request before policy logic runs.
  - Evidence: `PolicyEvalSection` sends `subject_type`, `subject_id`, `action`, and `resource_id` only; backend `PolicyEvaluationRequest` requires `risk_level`.

- `BUG-015` — **high** — `frontend/audit`
  - Files: `apps/web-ui/src/lib/api-client.ts`, `apps/access_control/audit/routes.py`
  - Summary: Audit API calls use `/api/v1/audit` and `since/until` filters, but the backend only exposes `/api/v1/audit/logs` with `start_at/end_at`.
  - Evidence: `auditApi.list()` targets `/api/v1/audit`; backend defines only `GET /api/v1/audit/logs`.

- `BUG-016` — **high** — `frontend/audit`
  - Files: `apps/web-ui/src/types/api.ts`, `apps/web-ui/src/app/(dashboard)/audit/page.tsx`, `apps/web-ui/src/app/(dashboard)/page.tsx`, `apps/access_control/audit/models.py`
  - Summary: Audit screens expect `entries` and string `detail`, but the backend returns `items` and structured `detail` objects.
  - Evidence: UI falls back to `data?.entries ?? []`; backend `AuditLogListResponse` exposes `items`.

- `BUG-017` — **high** — `frontend/gateway`
  - Files: `apps/web-ui/src/lib/api-client.ts`, `apps/access_control/gateway_binding/routes.py`
  - Summary: Gateway management calls point at nonexistent compiler API `/api/v1/gateway/*` routes, while the real endpoints live on access control under `/api/v1/gateway-binding/*`.
  - Evidence: `gatewayApi` uses `COMPILER_API`; backend gateway binding routes are mounted under `ACCESS_CONTROL` with `gateway-binding`.

- `BUG-018` — **high** — `frontend/compilations`
  - Files: `apps/web-ui/src/lib/api-client.ts`, `apps/compiler_api/routes/compilations.py`
  - Summary: The frontend calls list, retry, and rollback compilation endpoints that the backend never implemented.
  - Evidence: Backend compilation routes provide only create, get, and events.

- `BUG-019` — **high** — `frontend/compilations`
  - Files: `apps/web-ui/src/types/api.ts`, `apps/web-ui/src/app/(dashboard)/compilations/page.tsx`, `apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx`, `apps/compiler_api/models.py`
  - Summary: Compilation pages expect `job_id`, `failed_stage`, `progress_pct`, `completed_at`, `error_message`, and `artifacts` fields that are not present in backend responses.
  - Evidence: Frontend job pages dereference these fields directly; backend `CompilationJobResponse` exposes `id`, `status`, `current_stage`, `error_detail`, `created_at`, and `updated_at`.

- `BUG-020` — **high** — `frontend/compilations`
  - Files: `apps/web-ui/src/types/api.ts`, `apps/web-ui/src/components/compilations/status-badge.tsx`, `apps/web-ui/src/components/compilations/stage-timeline.tsx`, `apps/compiler_worker/models.py`
  - Summary: Compilation status and stage enums do not match between UI and backend, so status badges, running-state detection, and timelines mis-handle real backend values.
  - Evidence: Frontend expects uppercase states like `PENDING` and `PUBLISHED`; backend emits lowercase lifecycle values like `pending`, `running`, and `succeeded`.

- `BUG-021` — **high** — `frontend/sse`
  - Files: `apps/web-ui/src/lib/hooks/use-sse.ts`, `apps/web-ui/src/components/compilations/event-log.tsx`, `apps/web-ui/src/types/api.ts`, `apps/compiler_api/models.py`
  - Summary: The SSE consumer parses events as `{type, timestamp, ...}`, but the server streams `CompilationEventResponse` objects with `event_type` and `created_at`.
  - Evidence: `EventLog` reads `evt.type` and `evt.timestamp`; backend SSE emits `event.model_dump(...)` using backend field names.

- `BUG-022` — **high** — `frontend/services`
  - Files: `apps/web-ui/src/lib/api-client.ts`, `apps/compiler_api/routes/services.py`
  - Summary: Service detail queries call `GET /api/v1/services/{serviceId}`, but the compiler API only implements list services.
  - Evidence: `serviceApi.get()` targets `/api/v1/services/{serviceId}`; backend defines only `@router.get("")`.

- `BUG-023` — **high** — `frontend/services`
  - Files: `apps/web-ui/src/types/api.ts`, `apps/web-ui/src/components/services/service-card.tsx`, `apps/web-ui/src/app/(dashboard)/services/page.tsx`, `apps/web-ui/src/app/(dashboard)/page.tsx`, `apps/compiler_api/models.py`
  - Summary: Service summary types do not match backend payloads, so dashboard and registry pages render/search on nonexistent fields like `name`, `version_count`, and `last_compiled`.
  - Evidence: Backend `ServiceSummaryResponse` exposes `service_name`, `tool_count`, and `created_at`, while the UI expects `name`, `version_count`, and `last_compiled`.

- `BUG-024` — **high** — `frontend/artifacts`
  - Files: `apps/web-ui/src/lib/api-client.ts`, `apps/compiler_api/routes/artifacts.py`
  - Summary: Artifact client routes are wired under `/api/v1/services/{serviceId}/...`, but the backend artifact registry is mounted under `/api/v1/artifacts/{service_id}/...`.
  - Evidence: `artifactApi.listVersions/getVersion/diff` target `/services/...`; backend registers `APIRouter(prefix="/api/v1/artifacts")`.

- `BUG-042` — **high** — `frontend/artifacts`
  - Files: `apps/web-ui/src/types/api.ts`, `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`, `apps/web-ui/src/app/(dashboard)/services/[serviceId]/versions/page.tsx`, `libs/registry_client/models.py`
  - Summary: Artifact version responses do not match frontend expectations: the UI expects `ir`, but the backend returns `ir_json`, so service/review/version pages treat real versions as missing IR.
  - Evidence: Frontend `ArtifactVersionResponse` defines `ir: ServiceIR` and pages read `v.ir`; backend `ArtifactVersionResponse` exposes `ir_json` and `raw_ir_json`.

- `BUG-043` — **high** — `frontend/artifact-diff`
  - Files: `apps/web-ui/src/types/api.ts`, `apps/web-ui/src/components/services/version-diff.tsx`, `libs/registry_client/models.py`
  - Summary: Artifact diff responses do not match the UI contract: the frontend expects added/removed operations as full `Operation` objects and change keys named `field`, but the backend returns operation-id strings and `field_name`.
  - Evidence: `VersionDiff` renders `diff.added_operations.map((op) => op.name)` and `ChangeDetail` reads `change.field`; backend diff models expose `list[str]` plus `ArtifactDiffChange.field_name`.

- `BUG-046` — **medium** — `frontend/service-detail`
  - Files: `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`
  - Summary: The service detail gateway tab renders `Sync`, `Reconcile`, and `Sync Routes` buttons with no click handlers, so the gateway controls are cosmetic only.
  - Evidence: These buttons are plain `<Button>` elements with labels only and no `onClick` or navigation wiring.

- `BUG-047` — **medium** — `frontend/service-detail`
  - Files: `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`
  - Summary: The service detail header renders `Recompile`, `View IR`, and `Manage Access` buttons with no click handlers, so key actions appear available but do nothing.
  - Evidence: The header action buttons are plain `<Button>` elements without `onClick` or `render` props.

- `BUG-025` — **medium** — `frontend/versions`
  - Files: `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`, `apps/web-ui/src/app/(dashboard)/services/[serviceId]/versions/page.tsx`
  - Summary: Inactive version rows show an Activate button, but there is no click handler or API call behind it.
  - Evidence: Both versions tables render Activate buttons without `onClick` behavior.

- `BUG-026` — **medium** — `frontend/versions`
  - Files: `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`, `apps/web-ui/src/app/(dashboard)/services/[serviceId]/versions/page.tsx`
  - Summary: Version delete flows are placeholders that never issue the backend `DELETE` request.
  - Evidence: One page uses a timeout placeholder; the other calls `artifactApi.getVersion()` inside `handleDelete()` and then closes the dialog.

- `BUG-027` — **medium** — `frontend/review`
  - Files: `apps/web-ui/src/components/review/review-panel.tsx`, `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
  - Summary: Per-operation review checkboxes and notes live only in component state, so refresh/navigation loses all review progress.
  - Evidence: `ReviewPanel` keeps `reviewed`, `notes`, and `overallNote` only in `useState`.

- `BUG-028` — **medium** — `frontend/review`
  - Files: `apps/web-ui/src/components/services/ir-editor.tsx`, `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
  - Summary: IR editing in the review flow is effectively a no-op because the editor save callback is optional and the review page never passes one.
  - Evidence: `IREditor.handleSave()` only invokes `onSave?.(parsed)`; the review page renders `<IREditor ... />` with no `onSave`.

- `BUG-029` — **medium** — `frontend/workflow`
  - Files: `apps/web-ui/src/stores/workflow-store.ts`, `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
  - Summary: Approval workflow state is stored only in a client-side persisted Zustand store, so different browsers/users do not share review or approval status.
  - Evidence: `workflow-store.ts` persists transitions to `workflow-storage` and exposes only local transition helpers; the review page reads/writes that store directly.

- `BUG-030` — **medium** — `frontend/compilation-wizard`
  - Files: `apps/web-ui/src/components/compilations/compilation-wizard.tsx`
  - Summary: The compilation wizard accepts any dropped or selected file as text without validating extension or MIME type.
  - Evidence: `handleFileRead()` unconditionally uses `FileReader.readAsText(file)`, and the accepted extension list is not enforced.

- `BUG-040` — **high** — `frontend/compilation-wizard`
  - Files: `apps/web-ui/src/components/compilations/compilation-wizard.tsx`, `apps/web-ui/src/types/api.ts`, `apps/compiler_api/models.py`
  - Summary: The compilation wizard can include `auth_config` in create requests, but the backend create model forbids extra fields and does not accept `auth_config`, so authenticated-source submissions fail with `422`.
  - Evidence: `buildRequest()` sets `req.auth_config`; frontend `CompilationCreateRequest` includes it; backend `CompilationCreateRequest` uses `extra="forbid"` and defines no such field.

- `BUG-041` — **high** — `frontend/compilation-wizard`
  - Files: `apps/web-ui/src/components/compilations/compilation-wizard.tsx`, `apps/compiler_api/models.py`
  - Summary: After a successful compilation submission, the wizard redirects using `result.job_id`, but the backend returns `id`, so the success path navigates to `/compilations/undefined`.
  - Evidence: `handleSubmit()` does `router.push(\`/compilations/${result.job_id}\`)`; backend `CompilationJobResponse` exposes `id` rather than `job_id`.

### Backend (continued)

- `BUG-060` — **critical** — `backend/compiler-api`
  - Files: `apps/compiler_api/routes/artifacts.py`
  - Summary: Artifact registry CRUD, activation, and diff routes are exposed without authentication, allowing arbitrary callers to read and mutate stored service versions.
  - Evidence: `artifacts.py` defines create/list/get/update/delete/activate/diff handlers with only DB session dependencies and no authentication or authorization guard.

- `BUG-061` — **critical** — `backend/compiler-api`
  - Files: `apps/compiler_api/routes/compilations.py`
  - Summary: Compilation trigger, job lookup, and event streaming routes are exposed without authentication, allowing arbitrary callers to enqueue work and inspect job state.
  - Evidence: `compilations.py` defines create/get/events handlers with DB and dispatcher dependencies only; there is no caller auth dependency on any route.

- `BUG-062` — **high** — `backend/compiler-api`
  - Files: `apps/compiler_api/routes/services.py`
  - Summary: The compiled service catalog is exposed without authentication, so any caller can enumerate published services and deployment metadata.
  - Evidence: `services.py` defines `list_services()` with only a DB session dependency and no authentication guard.

- `BUG-063` — **high** — `backend/audit`
  - Files: `apps/compiler_api/routes/compilations.py`
  - Summary: Compilation audit entries trust caller-supplied `created_by`, so an unauthenticated caller can forge audit attribution for job submissions.
  - Evidence: `create_compilation()` writes `actor=payload.created_by or "system"` into `AuditLogService` before enqueue, and the route has no caller authentication.

- `BUG-069` — **high** — `backend/authz`
  - Files: `apps/access_control/authz/service.py`
  - Summary: Policy evaluation performs an unbounded query over every policy for the subject type and filters subject matches in Python, which can blow up latency and memory for large rule sets.
  - Evidence: `evaluate()` runs `select(Policy).where(Policy.subject_type == payload.subject_type).order_by(Policy.id)` with no limit and then builds candidates from `result.all()`.

- `BUG-070` — **high** — `backend/authz`
  - Files: `apps/access_control/authz/models.py`, `apps/access_control/authz/service.py`, `apps/access_control/authz/routes.py`
  - Summary: Policy create/update trust caller-supplied `created_by` and also log audit actor from that same payload, allowing audit attribution to be forged.
  - Evidence: `PolicyCreateRequest` / `PolicyUpdateRequest` expose `created_by`; `AuthzService` persists it directly; authz routes append audit entries with `actor=payload.created_by or "system"` on create/update.

- `BUG-071` — **medium** — `backend/validator`
  - Files: `libs/validator/llm_judge.py`
  - Summary: A malformed non-object entry in the LLM judge JSON array aborts parsing for the whole batch instead of being skipped.
  - Evidence: `_parse_judge_response()` iterates `for item in data` and immediately calls `item.get(...)`; a string or `null` item raises `TypeError`, which is caught only by the outer parser and returns `[]`.

- `BUG-072` — **medium** — `backend/enhancer`
  - Files: `libs/enhancer/enhancer.py`
  - Summary: A malformed non-object entry in the LLM enhancement JSON array discards the entire enhancement batch instead of skipping the bad item.
  - Evidence: `_parse_llm_response()` iterates `for item in data` and immediately calls `item.get("operation_id")`; a scalar or `null` item raises `TypeError` and the outer `except` returns `{}` for the whole batch.

### Frontend (continued)

- `BUG-064` — **medium** — `frontend/gateway`
  - Files: `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
  - Summary: Gateway status badges are fabricated from list-index heuristics instead of real gateway state, so the page can mark healthy services as drifted/error or vice versa.
  - Evidence: `GatewayPage` explicitly comments `Simulate route status based on service data` and assigns status via `i % 5` / `i % 3` when building `serviceRoutes`.

- `BUG-065` — **high** — `frontend/gateway`
  - Files: `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`, `apps/web-ui/src/types/api.ts`, `apps/access_control/gateway_binding/routes.py`
  - Summary: Gateway reconciliation results use the wrong response contract, so the UI reads nonexistent `synced` / `deleted` / `errors` fields from the backend payload.
  - Evidence: Frontend `ReconcileResponse` defines `synced`, `deleted`, and `errors` and renders those fields directly, but backend `ReconcileResponse` returns only `consumers_*`, `policy_bindings_*`, and `service_routes_*` counters.

- `BUG-066` — **high** — `frontend/gateway`
  - Files: `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`, `apps/web-ui/src/lib/api-client.ts`, `apps/access_control/gateway_binding/routes.py`
  - Summary: The gateway rollback action calls the sync route helper instead of any rollback endpoint, so rollback requests can only republish rather than restore prior routes.
  - Evidence: `handleRollback()` calls `gatewayApi.setRoute(...)`; the API client exposes only `setRoute` / `deleteRoute`, while the backend provides a dedicated `POST /service-routes/rollback` handler.

- `BUG-067` — **high** — `frontend/gateway`
  - Files: `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`, `apps/access_control/gateway_binding/service.py`
  - Summary: Gateway sync and rollback dialogs submit an empty `route_config` object, but backend route publication requires route metadata and definitions, so the request cannot produce valid routes.
  - Evidence: `handleSyncRoutes()` and `handleRollback()` send `route_config: {}`; backend `_service_route_documents()` immediately indexes `route_config["service_id"]`, `route_config["service_name"]`, and `route_config["namespace"]`.

- `BUG-068` — **medium** — `frontend/dashboard`
  - Files: `apps/web-ui/src/components/dashboard/compilation-metrics.tsx`
  - Summary: The dashboard protocol-distribution card is hardcoded to return no data, so protocol metrics never render even when compilations exist.
  - Evidence: `buildProtocolDistribution()` contains a stub comment and unconditionally returns `new Map()`; `protocolEntries` therefore stays empty and the Protocol Distribution block never appears.

- `BUG-073` — **medium** — `frontend/gateway`
  - Files: `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
  - Summary: Gateway deployment history is seeded with hard-coded sample entries during the initial empty-data render and never syncs to real backend data afterward.
  - Evidence: `deploymentHistory` is created with `React.useState(() => services.length > 0 ? [] : [hard-coded entries])` while `services` is initially empty during query loading, and the state is later rendered through `<DeploymentHistory entries={deploymentHistory} />` without any update path.

- `BUG-074` — **medium** — `frontend/dashboard`
  - Files: `apps/web-ui/src/app/(dashboard)/page.tsx`
  - Summary: The dashboard health card ignores audit API failures and can report the system as healthy while the audit surface is down.
  - Evidence: `DashboardPage` fetches `useAuditLogs()` but computes `apisHealthy = !servicesError && !compilationsError`; the System Status card then says `All APIs responding` without considering `auditError`.
