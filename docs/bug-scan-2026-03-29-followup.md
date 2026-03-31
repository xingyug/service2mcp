# Tool Compiler v2 Bug Scan Follow-up — 2026-03-29

Created during a follow-up whole-repo scan requested after the earlier bug-fix batches.

## Scope

- Record new high-confidence bugs only; do not modify repository source in this pass.
- Focus on residual logic/contract drift after the previous bug-sweep work.
- Findings below were produced by manual code review plus small targeted reproductions.

## Baseline checks during this scan

- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q` → passed
- `cd apps/web-ui && npm test` → passed
- `cd apps/web-ui && npm run build` → passed

These findings therefore describe bugs that are currently latent or scenario-dependent rather than already-red baseline failures.

## Findings

### BUG-145 — Service detail navigation and caching drop tenant/environment context (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/page.tsx`
  - `apps/web-ui/src/components/services/service-card.tsx`
  - `apps/web-ui/src/hooks/use-api.ts`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/lib/query-keys.ts`
  - `apps/compiler_api/routes/services.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - The services list UI displays `tenant` and `environment`, but all navigation and detail fetching collapse the identity down to bare `service_id`.
  - Links go to `/services/${service_id}` only.
  - `useService()` caches by `["services", serviceId]` only.
  - `serviceApi.get()` calls `GET /api/v1/services/{serviceId}` without `tenant` or `environment`.
  - The backend detail route and repository both support `tenant` and `environment` filters.
- Evidence:
  - `apps/web-ui/src/app/(dashboard)/services/page.tsx` links rows to `/services/${s.service_id}` even while rendering tenant/environment columns.
  - `apps/web-ui/src/components/services/service-card.tsx` also links cards to `/services/${service.service_id}` only.
  - `apps/web-ui/src/hooks/use-api.ts` uses `queryKeys.services.detail(serviceId)` with no scope fields.
  - `apps/web-ui/src/lib/api-client.ts` `serviceApi.get(serviceId)` sends no scope query params.
  - `apps/compiler_api/routes/services.py` and `apps/compiler_api/repository.py` explicitly accept and apply `tenant` / `environment`.
- Why it is a bug:
  - The repo already models scoped services. If the same `service_id` exists in multiple tenant/environment combinations, the detail page request is ambiguous and React Query will cache all variants under the same key.
- Suggested validation:
  - Seed two active service versions with the same `service_id` but different `tenant`/`environment`.
  - Open each row from the services page and observe that both routes collapse to the same URL and cache key.

### BUG-146 — Artifact version pages and mutations also drop tenant/environment, so version views and actions can target the wrong scoped record (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/hooks/use-api.ts`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/lib/query-keys.ts`
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/versions/page.tsx`
  - `apps/web-ui/src/components/services/version-diff-dialog.tsx`
  - `apps/compiler_api/routes/artifacts.py`
  - `apps/compiler_api/repository.py`
  - `tests/integration/test_artifact_registry.py`
- Summary:
  - The frontend artifact/version flows use only `serviceId` and `version`.
  - `useArtifactVersions()` and `useArtifactDiff()` have no scope parameters.
  - `queryKeys.artifacts.*` contain no tenant/environment component.
  - `artifactApi.listVersions/getVersion/activateVersion/deleteVersion/diff` send no scope filters even though the backend routes accept them.
- Evidence:
  - `apps/web-ui/src/lib/api-client.ts` builds artifact URLs without `tenant` or `environment`.
  - `apps/web-ui/src/hooks/use-api.ts` caches version/diff queries only by `serviceId` and version numbers.
  - `apps/compiler_api/routes/artifacts.py` exposes `tenant` and `environment` query parameters on list/get/update/delete/activate/diff routes.
  - `apps/compiler_api/repository.py` only narrows registry queries when those filters are supplied.
  - `tests/integration/test_artifact_registry.py` already demonstrates scoped artifact records (`tenant="team-a"` / `environment="prod"`).
- Why it is a bug:
  - In a real multi-tenant registry, the versions page, diff dialog, activate button, and delete button can fetch or mutate the wrong scoped record because the request identity is incomplete.
- Suggested validation:
  - Create the same `service_id` in two scopes, then compare versions, activate, or delete from the UI and inspect which backend row is actually touched.

### BUG-147 — Artifact repository create flow re-reads the created version without tenant/environment, so it can return the wrong row after commit (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/repository.py`
- Summary:
  - `ArtifactRegistryRepository.create_version()` writes `tenant` and `environment` into the new `ServiceVersion`, commits, and then calls `_require_version(service_id, version_number)`.
  - `_require_version()` in turn calls `get_version(service_id, version_number)` without the original scope filters.
- Evidence:
  - `create_version()` passes `tenant=payload.tenant` / `environment=payload.environment` into the inserted row.
  - The return path is `return await self._require_version(payload.service_id, payload.version_number)`.
  - `_require_version()` calls `get_version(service_id, version_number)` with no tenant/environment arguments.
  - `_get_version_record()` only applies scope filtering when those parameters are supplied.
- Why it is a bug:
  - If `(service_id, version_number)` already exists in another tenant/environment, the create call can successfully write one row and then return a different row in the response body.
- Suggested validation:
  - Create `billing-api:v1` in two different scopes through the repository or API and compare the response payload from the second create with the row actually inserted.

### BUG-148 — `RegistryClient` cannot scope update/activate/delete operations by tenant/environment (fixed)

- Severity: High
- Files:
  - `libs/registry_client/client.py`
  - `libs/registry_client/models.py`
  - `apps/compiler_api/routes/artifacts.py`
- Summary:
  - `RegistryClient.list_versions()`, `get_version()`, and `diff_versions()` accept `tenant` / `environment`.
  - `RegistryClient.update_version()`, `activate_version()`, and `delete_version()` do not.
  - The backend routes for update/activate/delete do accept scope query params.
- Evidence:
  - `libs/registry_client/client.py` method signatures for update/activate/delete have no `tenant` / `environment` parameters and therefore never send them.
  - `apps/compiler_api/routes/artifacts.py` accepts `tenant` / `environment` on update/delete/activate routes.
  - `libs/registry_client/models.py` also models scoped artifact versions (`tenant`, `environment`), so the client surface is incomplete relative to the API.
- Why it is a bug:
  - Any caller using `RegistryClient` against a multi-tenant registry can list/get the correct scoped row but cannot safely mutate that same row.
- Suggested validation:
  - Create the same `(service_id, version_number)` in two scopes, then call `RegistryClient.update_version()` or `delete_version()` and inspect which record changed.

### BUG-149 (fixed) — Compilation wizard auth overrides are ignored because the frontend writes `options.auth_config` but the worker only reads `options["auth"]`

- Severity: High
- Files:
  - `apps/web-ui/src/components/compilations/compilation-wizard.tsx`
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - The wizard serializes user-entered auth overrides under `options.auth_config`.
  - `_apply_auth_override()` on the worker side only looks at `options.get("auth")`.
  - As a result, UI-specified auth overrides are silently ignored during compilation.
- Evidence:
  - `compilation-wizard.tsx` writes `options.auth_config = authConfig`.
  - `apps/compiler_worker/activities/production.py` reads only `raw_auth = options.get("auth")`.
  - Targeted reproduction from this scan:
    - `auth_config key -> {'type': <AuthType.none: 'none'>}`
    - `auth key -> {'type': <AuthType.basic: 'basic'>}`
- Why it is a bug:
  - The UI exposes a real auth-override flow, but the compiler worker never sees the value under the key it expects.
- Suggested validation:
  - Submit a compilation with a non-default auth override from the web UI and inspect the resulting `ServiceIR.auth` after extraction/enhancement.

### BUG-150 (fixed) — The frontend `auth_config` object shape is incompatible with the backend `AuthConfig`, so basic and OAuth2 secrets are silently discarded

- Severity: High
- Files:
  - `apps/web-ui/src/types/api.ts`
  - `apps/web-ui/src/components/compilations/compilation-wizard.tsx`
  - `apps/web-ui/src/components/compilations/__tests__/compilation-wizard.test.ts`
  - `libs/ir/models.py`
- Summary:
  - The frontend builds auth objects with fields such as `username`, `password_secret_ref`, `token_url`, `client_id`, and `client_secret_ref`.
  - The backend `libs.ir.models.AuthConfig` does not define those fields; it expects a different OAuth2 structure and no basic username/password fields.
  - `AuthConfig.model_validate(...)` therefore accepts the payload but drops the unsupported fields.
- Evidence:
  - Frontend `AuthConfig` type and tests explicitly assert those frontend-only fields.
  - Backend `AuthConfig` only defines fields like `header_name`, `header_prefix`, `api_key_param`, `oauth2_token_url`, and nested `oauth2`.
  - Targeted reproduction from this scan:
    - `basic {'type': <AuthType.basic: 'basic'>}`
    - `oauth2 {'type': <AuthType.oauth2: 'oauth2'>}`
    - `custom_header {'type': <AuthType.custom_header: 'custom_header'>, 'header_name': 'X-Token', 'compile_time_secret_ref': 'secret://token'}`
- Why it is a bug:
  - Even if BUG-149 were fixed by renaming the option key, the current frontend payload shape would still lose most of the actual auth material for basic and OAuth2 configurations.
- Suggested validation:
  - Run `AuthConfig.model_validate()` against the exact frontend payloads and compare the dumped model to the original object.

### BUG-151 (fixed) — Compilation wizard `tenant` / `environment` inputs are never propagated into `ServiceIR` or `compilation_jobs`

- Severity: High
- Files:
  - `apps/web-ui/src/components/compilations/compilation-wizard.tsx`
  - `apps/compiler_api/repository.py`
  - `apps/compiler_worker/repository.py`
  - `apps/compiler_worker/workflows/compile_workflow.py`
  - `apps/compiler_worker/activities/production.py`
  - `libs/ir/models.py`
  - `libs/generator/generic_mode.py`
  - `libs/db_models.py`
  - `migrations/versions/002_add_review_workflows.py`
- Summary:
  - The wizard stores `tenant` and `environment` under `options`.
  - `ServiceIR` has top-level `tenant` / `environment` fields.
  - The generator uses those fields to emit manifest annotations, and the register stage forwards them into artifact storage.
  - But there is no Python code reading `options["tenant"]` or `options["environment"]`, no code copying them into `ServiceIR`, and both compilation-job persistence layers ignore the `CompilationJob.tenant` / `environment` columns entirely.
- Evidence:
  - `compilation-wizard.tsx` sets `options.tenant` and `options.environment`.
  - `libs/ir/models.py` defines `ServiceIR.tenant` and `ServiceIR.environment`.
  - `libs/generator/generic_mode.py` emits annotations from `service_ir.environment` / `service_ir.tenant`.
  - `apps/compiler_worker/activities/production.py` forwards `service_ir.tenant` / `service_ir.environment` into `ArtifactVersionCreate`.
  - A repo-wide search during this scan found no Python reads of `options.get("tenant")` or `options.get("environment")`.
  - `apps/compiler_api/repository.py` and `apps/compiler_worker/repository.py` create `CompilationJob(...)` rows without populating the `tenant` / `environment` columns added in `libs/db_models.py` and `migrations/versions/002_add_review_workflows.py`.
- Why it is a bug:
  - The UI invites users to choose a tenant/environment, and downstream storage/generation code supports those fields, but the selected scope is dropped before it can influence the IR, registry entry, or manifest annotations.
- Suggested validation:
  - Submit a compilation with `tenant` and `environment` set, then inspect the resulting job row, `ServiceIR`, registry version, and generated manifest annotations.

### BUG-152 (fixed) — Compilation detail page links to `/services/{service_name}` because it fabricates `artifacts.ir_id` from `service_name` instead of real `service_id`

- Severity: High
- Files:
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx`
  - `apps/compiler_api/models.py`
  - `tests/integration/test_compiler_api.py`
- Summary:
  - The frontend compatibility layer fabricates `job.artifacts.ir_id` from `raw.service_name`.
  - The compilation detail page then builds a link to `/services/${job.artifacts.ir_id}`.
  - But backend service routing is keyed by `service_id`, not `service_name`.
- Evidence:
  - `apps/web-ui/src/lib/api-client.ts` sets `artifacts.ir_id = raw.service_name`.
  - `apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx` links to `/services/${job.artifacts.ir_id}`.
  - Backend service detail route is `/api/v1/services/{service_id}`.
  - `tests/integration/test_compiler_api.py` shows real responses where `service_id="billing-api"` while `service_name="Billing API"`.
- Why it is a bug:
  - Successful compilation pages can generate links like `/services/Billing API`, which do not correspond to the real service identifier and can 404 or open the wrong page.
- Suggested validation:
  - Complete a compilation whose `service_name` contains spaces or differs from `service_id`, then click the “IR ID” link from the compilation detail page.

### BUG-153 — The frontend fakes `version_count` from `active_version`, so service pages misreport how many versions actually exist (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/components/services/service-card.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`
  - `apps/compiler_api/models.py`
- Summary:
  - Backend service summaries do not return a true version count.
  - The frontend currently synthesizes `version_count` as `Math.max(raw.active_version ?? 1, 1)`.
  - That equates “currently active version number” with “number of stored versions”, which is not the same thing.
- Evidence:
  - `apps/web-ui/src/lib/api-client.ts` computes `version_count: Math.max(raw.active_version ?? 1, 1)`.
  - Multiple UI surfaces render `service.version_count` directly in cards, tables, and detail pages.
  - `apps/compiler_api/models.py` `ServiceSummaryResponse` has `active_version` but no `version_count`.
- Why it is a bug:
  - If versions are sparse, deleted, or otherwise not contiguous, the UI can show “5 versions” when only 2 exist, or any other incorrect count implied by the active revision number.
- Suggested validation:
  - Create a service where the active version number is not equal to the total number of stored versions and compare the UI count with the registry data.

### BUG-154 — JWT login treats `subject` as `username`, so PAT management can target the wrong user identity (fixed)

- Severity: Medium
- Files:
  - `apps/access_control/authn/service.py`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/app/(auth)/login/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/pats/page.tsx`
- Summary:
  - Backend JWT validation returns only `subject`, `token_type`, and raw `claims`.
  - The frontend normalizes that into `username: raw.subject`.
  - PAT management later uses `user.username` for `listPATs()` and `createPAT()`.
  - Nothing in backend JWT validation requires `sub` to equal the real platform username.
- Evidence:
  - `apps/access_control/authn/service.py` accepts any non-empty JWT `sub` and returns it as `subject`.
  - `apps/web-ui/src/lib/api-client.ts` maps `username: raw.subject`.
  - `apps/web-ui/src/app/(auth)/login/page.tsx` persists `principal.username` into the auth store.
  - `apps/web-ui/src/app/(dashboard)/pats/page.tsx` uses `user?.username` as the backend PAT username.
- Why it is a bug:
  - If JWT `sub` is an email address, opaque IdP subject, or service principal identifier rather than the platform username, PAT list/create operations will be executed against the wrong user key.
- Suggested validation:
  - Log in with a JWT whose `sub` differs from the intended username (for example, email or opaque subject) and compare the PAT page requests with the expected user account.

### BUG-155 — `auditApi.get()` can falsely return 404 for existing older audit entries because its fallback only scans the newest 1000 rows (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/access_control/audit/service.py`
- Summary:
  - The frontend first calls a nonexistent single-entry endpoint (`/api/v1/audit/logs/{id}`).
  - On failure, it falls back to `auditApi.list()` and searches the returned entries in memory.
  - Backend list retrieval is capped at 1000 newest rows.
  - Therefore any valid audit entry older than the newest 1000 records is reported as “not found”.
- Evidence:
  - `apps/web-ui/src/lib/api-client.ts` `auditApi.get()` catches the direct request and falls back to `auditApi.list()`, then `find()`s the entry in that page only.
  - `apps/access_control/audit/service.py` `list_entries()` orders by `timestamp.desc()` and applies `.limit(1000)`.
- Why it is a bug:
  - The fallback is incomplete pagination-wise, so existence of an entry no longer implies retrievability through the frontend helper.
- Suggested validation:
  - Seed more than 1000 audit rows, request an older entry ID through `auditApi.get()`, and observe that it throws 404 even though the row still exists.

### BUG-156 — Retry and rollback actions return a new job, but the frontend neither navigates to it nor invalidates the compilations list (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/hooks/use-api.ts`
  - `apps/web-ui/src/app/(dashboard)/compilations/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx`
  - `apps/compiler_api/routes/compilations.py`
- Summary:
  - Backend retry/rollback endpoints create and return a brand-new compilation job.
  - The frontend mutation hooks ignore the returned job payload and only invalidate the original job’s detail query.
  - The list query is not invalidated.
  - The detail page does not redirect to the new job.
- Evidence:
  - `apps/compiler_api/routes/compilations.py` `retry_compilation()` and `rollback_compilation()` both create `new_job` and `return new_job`.
  - `apps/web-ui/src/hooks/use-api.ts` invalidates only `queryKeys.compilations.detail(jobId)` on success.
  - `apps/web-ui/src/app/(dashboard)/compilations/page.tsx` and `[jobId]/page.tsx` success handlers only show a toast; they do not navigate to the returned job ID.
  - The compilations list only auto-refetches while it already has an in-progress job; after retrying from a failed/succeeded job, that condition may be false until a manual refresh.
- Why it is a bug:
  - Users can trigger retry/rollback successfully but remain on the old job page and may not see the newly queued job in the list without manual refresh.
- Suggested validation:
  - Retry a failed job or roll back a succeeded job from the UI and observe that the toast succeeds while the page stays on the old job and the list may remain stale.

### BUG-157 — Dashboard can report overall system status as “Healthy” even when audit loading has failed (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/page.tsx`
- Summary:
  - The dashboard fetches services, compilations, and audit logs.
  - The Recent Activity card correctly shows an audit failure.
  - But the top-level System Status card computes health from only `servicesError` and `compilationsError`, ignoring `auditError`.
- Evidence:
  - `auditError` is populated from `useAuditLogs(...)`.
  - The Recent Activity section renders “Failed to load audit logs.” when `auditError` is true.
  - `apisHealthy` is currently `!servicesError && !compilationsError`.
- Why it is a bug:
  - The page can simultaneously show “Failed to load audit logs.” and “Healthy / All APIs responding”, which is contradictory and hides partial outages.
- Suggested validation:
  - Force the audit endpoint to fail while service and compilation endpoints still succeed and observe the conflicting dashboard state.

### BUG-158 — Gateway rollback defaults to `active_version - 1`, which breaks when version numbers are sparse rather than contiguous (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
  - `libs/registry_client/models.py`
- Summary:
  - The rollback dialog auto-selects the target version as `active_version - 1`.
  - The artifact model only requires `version_number >= 1`; it does not enforce contiguous numbering.
  - If versions are sparse (for example 1, 3, 7), the default rollback target can point to a nonexistent version.
- Evidence:
  - `rollbackVersionForService()` returns `String(activeVersion - 1)`.
  - `handleRollback()` falls back to `Math.max(currentVersion.version_number - 1, 0)`.
  - `ArtifactVersionCreate.version_number` is only validated with `Field(ge=1)`.
- Why it is a bug:
  - The default rollback target is derived from arithmetic instead of from the actual stored version list, so valid services with non-contiguous versions fail rollback unless the user manually corrects the version.
- Suggested validation:
  - Create versions `1`, `3`, and `7`, mark `7` active, open the rollback dialog, and observe that it suggests `6`, which cannot be resolved.

### BUG-159 — Service `last_compiled` actually means “active version created_at”, so newer inactive compilations are hidden from dashboard/services recency (fixed)

- Severity: Medium
- Files:
  - `apps/compiler_api/repository.py`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/app/(dashboard)/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/page.tsx`
- Summary:
  - The backend service summary is built from the active version row only and exposes that row’s `created_at`.
  - The frontend maps that field directly to `last_compiled`.
  - If a newer inactive version exists, the UI still shows the older active version timestamp as the “last compiled” time.
- Evidence:
  - `ServiceCatalogRepository._to_service_summary()` returns `created_at=version.created_at` from the active `ServiceVersion`.
  - `normalizeServiceSummary()` maps `raw.created_at` to `last_compiled`.
  - Dashboard and services pages render this value as recency metadata.
- Why it is a bug:
  - “Last compiled” becomes semantically wrong whenever review/inactive versions are created after the currently active version.
- Suggested validation:
  - Keep version `1` active, create version `2` as inactive, and observe that service list/dashboard still report the timestamp for version `1` as the most recent compilation.

### BUG-160 — Gateway “Sync Routes” can leave stale version-pinned routes behind when switching a service to an older version (fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/service.py`
  - `apps/access_control/gateway_binding/routes.py`
  - `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
  - `libs/generator/generic_mode.py`
- Summary:
  - Generated route configs use a stable default route ID (`{service}-active`) but version-specific pinned route IDs (`{service}-v{n}`).
  - The gateway sync service only upserts the route IDs present in the target config; it does not delete route IDs that disappear.
  - The HTTP sync route ignores the `previous_routes` field completely.
  - The frontend sync action sends `previous_routes: {}` anyway.
- Evidence:
  - `libs/generator/generic_mode.py` builds `default_route.route_id = "{service}-active"` and `version_route.route_id = "{service}-v{config.version_number}"`.
  - `GatewayBindingService.sync_service_routes()` upserts only `route_documents.items()` and never deletes `set(existing) - set(target)`.
  - `apps/access_control/gateway_binding/routes.py` calls `gateway_binding.sync_service_routes(request.route_config)` and drops `request.previous_routes`.
  - `apps/web-ui/src/app/(dashboard)/gateway/page.tsx` always posts `previous_routes: {}` for sync.
- Why it is a bug:
  - Syncing from a newer version to an older one updates the active route, but the newer version’s pinned route can remain published, leaving stale version-specific traffic paths in the gateway.
- Suggested validation:
  - Publish version `7`, then sync routes for version `3`, and inspect gateway routes for the same service. The active route will point at v3, but the old `-v7` route can still remain.

### BUG-161 — Multiple discovery/list UIs silently truncate after 1000 records because backend list endpoints hard-cap results without pagination (fixed)

- Severity: Medium
- Files:
  - `apps/compiler_api/repository.py`
  - `apps/web-ui/src/app/(dashboard)/compilations/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/versions/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
- Summary:
  - Backend list methods for compilation jobs, services, and artifact versions all apply `.limit(1000)`.
  - The corresponding frontend pages assume the returned arrays are complete and only do client-side filtering/pagination.
  - Once any collection exceeds 1000 rows, older items silently disappear from the UI/API surface.
- Evidence:
  - `CompilationRepository.list_jobs()` defaults to `limit=1000`.
  - `ServiceCatalogRepository.list_services()` uses `.limit(1000)`.
  - `ArtifactRegistryRepository.list_versions()` uses `.limit(1000)`.
  - `compilations/page.tsx` paginates only the returned `jobs` array in memory.
  - `services/page.tsx`, service version pages, and gateway version helpers all consume full list responses as if they are exhaustive.
- Why it is a bug:
  - Counts, search results, version history, and operator actions become incomplete without any warning once data volume exceeds 1000 records.
- Suggested validation:
  - Seed more than 1000 compilation jobs, services, or artifact versions and observe that older rows cannot be found from the web UI even though they still exist in storage.

### BUG-162 — Gateway page labels compile timestamps as “Last Synced”, so operators are shown artifact age instead of actual gateway sync time (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
- Summary:
  - The gateway table presents a `Last Synced` column.
  - But that field is populated from `activeVersion.created_at` or `service.last_compiled`, both of which are compile/version timestamps rather than gateway publication timestamps.
- Evidence:
  - `serviceRoutes` assigns `lastSynced: activeVersion?.created_at ?? service.last_compiled`.
  - The table header and cell render this value as `Last Synced`.
- Why it is a bug:
  - Route reconciliation, manual sync, drift, or delayed publication can all happen after compilation time, so the UI can confidently display the wrong operational timestamp.
- Suggested validation:
  - Compile a service, wait, then run a later manual gateway sync or reconcile. The table still shows the compile time rather than the actual sync time.

### BUG-163 — Review page accepts invalid `?version=` values and can render/use `vNaN`, `v0`, or negative workflow versions instead of rejecting them (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
  - `apps/web-ui/src/stores/workflow-store.ts`
  - `apps/web-ui/src/stores/__tests__/workflow-store.test.ts`
- Summary:
  - The review page parses `version` with `Number(versionParam)` and never validates the result.
  - `NaN`, `0`, and negative values all flow into `versionNumber`.
  - The workflow store then suppresses API errors by returning a synthetic draft workflow.
  - The page can therefore show a bogus review target like `vNaN` or `v0` instead of rejecting the request.
- Evidence:
  - `review/page.tsx` sets `requestedVersion = versionParam ? Number(versionParam) : undefined`.
  - `versionNumber = requestedVersion ?? service?.active_version ?? versions[0]?.version_number ?? 1`, so `NaN` and `0` are treated as valid non-nullish inputs.
  - `workflow-store.ts` catches any `workflowApi.get()` failure and returns an uncached default draft record.
  - `workflow-store.test.ts` explicitly codifies “loadWorkflow falls back to draft on API error”.
- Why it is a bug:
  - Invalid query parameters should surface as invalid review targets, not silently degrade into fake draft state while the UI still renders a version badge and workflow controls.
- Suggested validation:
  - Open `/services/{serviceId}/review?version=foo` or `/services/{serviceId}/review?version=0` and observe that the page renders a bogus version badge / empty review state instead of rejecting the request.

### BUG-164 — Review diff selection assumes version numbers are contiguous, so sparse histories lose the “previous version” diff tab (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
  - `libs/registry_client/models.py`
- Summary:
  - The review page looks for the previous version with `versionNumber - 1`.
  - Registry version numbers are only constrained to `>= 1`, not to a contiguous sequence.
  - If stored versions are sparse, the diff tab disappears or points at the wrong version even though a real earlier version exists.
- Evidence:
  - `review/page.tsx` computes `prevVersion = versions.find((v) => v.version_number === versionNumber - 1)`.
  - The diff tab label and `VersionDiff` props also hardcode `versionNumber - 1`.
  - `libs/registry_client/models.py` validates `version_number` with only `Field(ge=1)`.
- Why it is a bug:
  - Reviewers lose the most relevant historical comparison whenever version numbers skip.
- Suggested validation:
  - Create versions `1`, `3`, and `7`, review version `7`, and observe that the diff tab for the real previous version (`3`) is missing.

### BUG-165 — Workflow endpoints auto-create orphan review records for nonexistent service/version pairs, including invalid version numbers (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/routes/workflows.py`
  - `libs/db_models.py`
  - `migrations/versions/002_add_review_workflows.py`
  - `apps/compiler_api/tests/test_routes_workflows.py`
- Summary:
  - Every workflow route calls `_get_or_create()` before validating that the referenced service version exists.
  - The workflow table has no foreign key to registry versions and no positive-version constraint.
  - As a result, reads and writes against arbitrary service/version pairs create persistent workflow rows for things that do not exist.
- Evidence:
  - `_get_or_create()` selects only from `ReviewWorkflow` and inserts a new row when missing.
  - Route handlers `get_workflow`, `transition_workflow`, `save_review_notes`, and `get_workflow_history` all call `_get_or_create()` directly.
  - `ReviewWorkflow` stores only `service_id` and `version_number` with a uniqueness constraint; there is no FK to `ServiceVersion`.
  - The migration creates `review_workflows` without a foreign key or check constraint.
  - `test_routes_workflows.py` explicitly treats “creates draft when missing” as expected route behavior.
- Why it is a bug:
  - Simple reads or mistyped URLs can permanently create orphan workflow state for nonexistent artifacts, polluting review history and allowing operations on invalid versions.
- Suggested validation:
  - Request `/api/v1/workflows/nonexistent/v/-1` or open a review page for a missing version, then inspect the `compiler.review_workflows` table for the newly inserted orphan row.

### BUG-166 — Review workflow API is unauthenticated and trusts caller-supplied `actor`, so anyone who can reach it can forge approval history (fixed)

- Severity: Critical
- Files:
  - `apps/compiler_api/routes/workflows.py`
  - `apps/compiler_api/main.py`
- Summary:
  - Workflow endpoints are mounted directly into the compiler API without any authentication or authorization dependency.
  - Transition requests accept an arbitrary `actor` string from the body and persist it into workflow history.
  - This allows unauthenticated callers to read, transition, and annotate review workflows while impersonating any reviewer name.
- Evidence:
  - `apps/compiler_api/main.py` includes `workflows_router` without a wrapper dependency.
  - `apps/compiler_api/routes/workflows.py` route signatures have no `Depends(require_...)` caller checks.
  - `TransitionRequest` contains `actor: str`.
  - `transition_workflow()` writes `payload.actor` directly into the history entry.
- Why it is a bug:
  - Review state and reviewer attribution become forgeable over the network, which undermines the entire approval workflow.
- Suggested validation:
  - Call `POST /api/v1/workflows/{service}/v/{version}/transition` directly without credentials, set `actor` to another user’s name, and observe that the transition and forged history entry are accepted.

### BUG-167 — Review “Deploy” action sends only `{service_id, version_number}` to gateway sync, so it can mark a version as deployed without publishing any real routes (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/components/review/approval-workflow.tsx`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The review workflow’s Deploy button does not fetch the selected artifact’s actual `route_config`.
  - It calls gateway sync with a skeletal object containing only `service_id` and `version_number`.
  - Gateway sync derives route documents from `default_route` and `version_route`; if those fields are absent, there is nothing to publish.
- Evidence:
  - `approval-workflow.tsx` calls `gatewayApi.syncRoutes({ route_config: { service_id: serviceId, version_number: versionNumber } })`.
  - `GatewayBindingService.sync_service_routes()` builds route documents via `_service_route_documents(route_config)`.
  - `_service_route_documents()` only emits documents when `default_route` or `version_route` are present.
- Why it is a bug:
  - The workflow can transition to “deployed” even though no gateway routes for that version were actually synced.
- Suggested validation:
  - Click Deploy from the review workflow for a published version and inspect the gateway sync response / route list; the workflow can advance even though zero real routes were published from that action.

### BUG-168 — Review workflow performs publish/deploy side effects before recording the workflow transition, so failures can leave system state ahead of workflow state (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/components/review/approval-workflow.tsx`
- Summary:
  - Publish calls `artifactApi.activateVersion(...)` before transitioning to `published`.
  - Deploy calls `gatewayApi.syncRoutes(...)` before transitioning to `deployed`.
  - If the subsequent workflow transition fails, the side effect is not rolled back.
- Evidence:
  - `executeTransition()` explicitly runs side effects before `await transition(...)`.
  - The catch path only shows an error toast; it does not compensate for a successful activation or gateway sync that already happened.
- Why it is a bug:
  - Concurrent updates, network failures, or backend validation errors can leave an artifact active or routes synced while the recorded workflow state still says `approved` or `published`.
- Suggested validation:
  - Force the side effect to succeed but make the workflow transition request fail, then compare the registry/gateway state with the persisted workflow state.

### BUG-169 — Review page exposes an editable IR editor, but no save handler is wired, so edits are discarded silently (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
  - `apps/web-ui/src/components/services/ir-editor.tsx`
- Summary:
  - Draft/rejected review states mark the IR tab as editable.
  - `IREditor` only persists changes through its optional `onSave` callback.
  - The review page renders `<IREditor ... readOnly={!isEditable} />` without providing `onSave`.
  - Clicking Save therefore only exits edit mode locally; no backend update or parent state change occurs.
- Evidence:
  - `review/page.tsx` sets `isEditable = currentState === "draft" || currentState === "rejected"` and renders `IREditor ir={ir} readOnly={!isEditable}`.
  - `IREditor.handleSave()` calls `onSave?.(parsed)` and then `setEditing(false)`.
  - No `artifactApi.updateVersion(...)` or equivalent persistence path is wired from the review page.
- Why it is a bug:
  - The UI advertises editable IR during revision, but user edits vanish as soon as the component reloads or the page is revisited.
- Suggested validation:
  - Open a rejected review, edit the IR JSON, click Save, then refresh or navigate away/back. The original IR returns unchanged.

### BUG-170 — Review panel never hydrates saved backend notes, so reopening a review resets progress and hides prior annotations (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/components/review/review-panel.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
  - `apps/web-ui/src/stores/workflow-store.ts`
- Summary:
  - Workflow records fetched from the backend include `reviewNotes`.
  - The review page loads that workflow state, but `ReviewPanel` does not consume it.
  - Instead, the panel initializes `reviewed`, `notes`, and `overallNote` to empty local state on every mount.
- Evidence:
  - `workflow-store.ts` stores `reviewNotes: resp.review_notes`.
  - `review/page.tsx` loads the workflow and uses only `state` and `history`.
  - `ReviewPanel` initializes `reviewed = new Set()`, `notes = {}`, and `overallNote = ""` with no hydration from the workflow record.
- Why it is a bug:
  - Previously saved review annotations are invisible after reload, making the review appear incomplete and inviting accidental overwrite.
- Suggested validation:
  - Save operation notes, reload the review page, and observe that the checklist and overall note area come back empty.

### BUG-171 — Review completion swallows note-save failures and still reports success, causing silent review-note loss (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/components/review/review-panel.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
- Summary:
  - Completing a review calls `saveNotes(...)`.
  - Any error is swallowed as “best-effort”.
  - The panel then unconditionally calls `onCompleteReview(...)`, and the page shows a success toast.
- Evidence:
  - `ReviewPanel.handleComplete()` catches all errors from `saveNotes(...)` and does nothing with them.
  - After the catch/finally, it still calls `onCompleteReview?.(notes, overallNote)`.
  - `review/page.tsx` `handleCompleteReview()` always emits a success toast.
- Why it is a bug:
  - Users can be told their review completed successfully even though none of their notes persisted to the backend.
- Suggested validation:
  - Make `workflowApi.saveNotes` fail, click Complete Review, and observe that the success toast still appears while no notes are stored.

### BUG-172 — Service detail “Recompile” seeds the wizard with `service.name`, so recompiling can fork a new service lineage instead of updating the existing `service_id` (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/compilations/new/page.tsx`
  - `apps/web-ui/src/components/compilations/compilation-wizard.tsx`
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - The Recompile button passes `service.name` in the `service_name` query parameter.
  - The new compilation page uses that value as the wizard’s initial `serviceName`.
  - The wizard sends `service_name` in the API request.
  - The worker then uses `request.service_name` as the resulting `service_id`.
- Evidence:
  - `services/[serviceId]/page.tsx` routes to `/compilations/new?service_name=${encodeURIComponent(service.name)}`.
  - `compilations/new/page.tsx` reads `searchParams.get("service_name")`.
  - `compilation-wizard.tsx` maps that field to `req.service_name`.
  - `production.py` sets `service_id = context.request.service_name or service_ir.service_name`.
- Why it is a bug:
  - If display name and stable service ID differ (for example `Billing API` vs `billing-api`), recompiling from the service page can create a new lineage keyed by the display name instead of creating a new version for the existing service.
- Suggested validation:
  - Open a service whose `service_id` differs from `service.name`, click Recompile, submit without correcting the name, and compare the resulting registry entry/service ID.

### BUG-173 — Service detail Gateway tab shows `Active` based only on stored route config, even if the gateway is drifted or missing the routes (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`
- Summary:
  - The per-service Gateway tab does not query live gateway state.
  - If the active artifact version has a `route_config`, the UI always renders a green `Active` badge.
  - That badge reflects stored desired configuration, not actual publication status in APISIX.
- Evidence:
  - `GatewayTab` derives `routeConfig` from `activeVersion?.route_config`.
  - When `routeConfig` exists, it renders `Status: Active`.
  - The tab never calls `gatewayApi.listRoutes()` or compares the gateway’s actual route documents.
- Why it is a bug:
  - Operators can be told a service’s gateway routes are active even when they were deleted, drifted, or never synced successfully.
- Suggested validation:
  - Remove or alter the live gateway routes for a service while leaving the stored `route_config` intact, then open the service detail Gateway tab and observe that it still reports `Active`.

### BUG-174 — Gateway overview silently converts per-service artifact fetch failures into empty version histories (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
- Summary:
  - The gateway overview eagerly loads artifact versions for every service.
  - If any `artifactApi.listVersions(serviceId)` call fails, that service is silently assigned `[]`.
  - The page then treats the service as having no versions/route config instead of surfacing a fetch failure.
- Evidence:
  - `loadArtifactVersions()` wraps each `artifactApi.listVersions(...)` in `try/catch`.
  - The catch branch returns `[service.service_id, []]`.
  - Downstream status and history rendering consume `artifactVersionsByService` without any per-service error marker.
- Why it is a bug:
  - Registry/API failures are misrepresented as missing deployment history or missing route data, which hides real outages and distorts route status.
- Suggested validation:
  - Force `artifactApi.listVersions()` to fail for one service and observe that the gateway page shows that service as if it simply had no versions rather than reporting a fetch error.

### BUG-175 — Gateway “Deployment History” is fabricated from artifact creation timestamps, not actual gateway deployment/sync events (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/lib/gateway-route-config.ts`
  - `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
- Summary:
  - The Gateway page renders a `Deployment History` timeline with `deploy` actions.
  - That history is synthesized entirely from artifact versions and their `created_at` timestamps.
  - It does not consult actual gateway sync, rollback, or delete events.
- Evidence:
  - `buildDeploymentHistory()` iterates stored artifact versions, sets `timestamp: version.created_at`, and hardcodes `action: "deploy"`.
  - `gateway/page.tsx` passes this synthetic list directly into the `Deployment History` UI.
- Why it is a bug:
  - Artifact creation does not prove gateway deployment happened, so the timeline can invent deployments that never occurred and omit real later sync/rollback operations.
- Suggested validation:
  - Create artifact versions without syncing them to the gateway, then open the Gateway page and observe that the timeline still shows deployment entries for those versions.

### BUG-176 — Updating a policy overwrites `created_by`, so edits erase the original policy author (fixed)

- Severity: Medium
- Files:
  - `apps/access_control/authz/routes.py`
  - `apps/access_control/authz/service.py`
  - `apps/access_control/authz/models.py`
- Summary:
  - The policy update route injects `created_by=caller.subject` into every update request.
  - The service then writes that value back into the stored policy row.
  - This mutates author metadata on edit instead of preserving the original creator.
- Evidence:
  - `update_policy()` builds `request_payload = payload.model_copy(update={"created_by": caller.subject})`.
  - `AuthzService.update_policy()` sets `policy.created_by = payload.created_by` when present.
  - `PolicyUpdateRequest` exposes `created_by` as an updatable field.
- Why it is a bug:
  - “Created by” should be immutable creation metadata. After one edit, the UI/gateway mirrors can no longer tell who originally authored the rule.
- Suggested validation:
  - Create a policy as one admin, update it as another, then inspect the stored `created_by` field and returned policy payload.

### BUG-177 — Policy evaluation audit logs attribute evaluations to the tested subject and action instead of the real caller and resource (fixed)

- Severity: High
- Files:
  - `apps/access_control/authz/routes.py`
- Summary:
  - The evaluation endpoint requires an authenticated caller.
  - But the audit entry records `actor=payload.subject_id` and `resource=payload.action`.
  - The actual caller identity and evaluated `resource_id` are only partially or indirectly represented.
- Evidence:
  - `evaluate_policy()` accepts `_caller: TokenPrincipalResponse = Depends(require_authenticated_caller)`.
  - The appended audit entry uses `actor=payload.subject_id`.
  - The audit entry uses `resource=payload.action`, while the actual resource ID is tucked into `detail["resource_id"]`.
- Why it is a bug:
  - Audit history can claim that another subject performed the evaluation and can hide which resource was evaluated in the primary resource field, undermining forensic usefulness.
- Suggested validation:
  - Evaluate a policy as user `alice` for subject `bob` and resource `svc-1`; inspect the audit log entry and observe that it attributes the action to `bob` and sets the resource to the action string.

### BUG-178 — Policy page `subject_type` filter is client-side only, so results become incomplete once the backend’s 1000-row cap is exceeded (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/policies/page.tsx`
  - `apps/web-ui/src/hooks/use-api.ts`
  - `apps/access_control/authz/routes.py`
  - `apps/access_control/authz/service.py`
- Summary:
  - The backend policy list API supports `subject_type`.
  - The frontend `usePolicies()` / `policyApi.list()` path never sends that filter.
  - Policies page applies `filterSubjectType` only after fetching the list.
  - Backend listing is capped to 1000 rows.
- Evidence:
  - `list_policies()` accepts `subject_type`.
  - `AuthzService.list_policies()` orders by created date and applies `.limit(1000)`.
  - `PoliciesPage` builds `apiFilters` from only `subject_id` and `resource_id`.
  - `filterSubjectType` is applied locally via `list.filter((p) => p.subject_type === filterSubjectType)`.
- Why it is a bug:
  - If more than 1000 policies exist, switching to a subject-type filter can miss valid matches that were excluded before the client-side filter ever ran.
- Suggested validation:
  - Seed more than 1000 mixed user/group policies, select one subject type in the UI, and compare the rendered list with a server-side filtered query.

### BUG-179 — Web UI login writes auth state to `auth-storage`, but API/SSE clients read `auth_token`, so “logged-in” sessions still issue unauthenticated requests (fixed)

- Severity: Critical
- Files:
  - `apps/web-ui/src/stores/auth-store.ts`
  - `apps/web-ui/src/app/(auth)/login/page.tsx`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/lib/hooks/use-sse.ts`
  - `apps/web-ui/src/components/auth-guard.tsx`
- Summary:
  - Login persists token/user/isAuthenticated through the Zustand store under `auth-storage`.
  - The generic fetch wrapper and SSE hook do not read that store; they read `localStorage["auth_token"]`.
  - No login code writes `auth_token`.
  - As a result, the AuthGuard can admit the user into the dashboard while subsequent API and SSE requests omit the Authorization header.
- Evidence:
  - `auth-store.ts` persists under `{ name: "auth-storage" }`.
  - `login/page.tsx` calls `useAuthStore(...).login(token, user)` and redirects, but never writes `auth_token`.
  - `api-client.ts#getAuthToken()` returns `localStorage.getItem("auth_token")`.
  - `use-sse.ts` likewise reads `localStorage.getItem("auth_token")`.
  - `AuthGuard` gates only on `useAuthStore((s) => s.isAuthenticated)`.
- Why it is a bug:
  - The UI and the network layer disagree about authentication state, so users can appear signed in while protected API calls fail with 401 and live streams stay disconnected.
- Suggested validation:
  - Log in through the UI, then inspect subsequent fetch/SSE requests. The dashboard loads under AuthGuard, but Authorization headers are absent unless `auth_token` is manually injected into localStorage.

### BUG-180 — Sidebar “Logout” control is inert and never clears auth state or redirects (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/components/app-sidebar.tsx`
  - `apps/web-ui/src/stores/auth-store.ts`
- Summary:
  - The dashboard sidebar renders a prominent “Logout” button.
  - That control is not wired to `useAuthStore().logout()`, route navigation, or any other action.
  - Clicking it therefore leaves the current session untouched.
- Evidence:
  - `app-sidebar.tsx` renders `<SidebarMenuButton ...><LogOut ... /><span>Logout</span></SidebarMenuButton>` with no `onClick`, no `render={<Link .../>}`, and no auth-store usage.
  - `auth-store.ts` does define a `logout()` action, but the sidebar never calls it.
- Why it is a bug:
  - The UI advertises a logout action that does nothing, so users cannot terminate their session from the primary navigation and may assume they signed out when they did not.
- Suggested validation:
  - Log in, click the sidebar Logout control, and observe that the app remains authenticated and stays on the dashboard.

### BUG-181 — PAT, Policies, and Audit pages treat initial query failures as empty states instead of surfacing load errors (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/pats/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/policies/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/audit/page.tsx`
- Summary:
  - These pages destructure only `{ data, isLoading }` from their React Query hooks.
  - If the underlying request fails, `data` remains `undefined`, the derived list becomes `[]`, and the page renders its “No … found” empty state.
  - No error banner, retry affordance, or diagnostic is shown.
- Evidence:
  - `PATTokensPage` uses `const { data, isLoading } = useQuery(...)` and derives `pats = data?.pats ?? []`.
  - `PoliciesPage` uses `const { data, isLoading } = usePolicies(...)` and derives `list = data?.policies ?? []`.
  - `AuditLogPage` uses `const { data, isLoading } = useAuditLogs(...)` and derives `entries = data?.entries ?? []`.
  - Each page then branches on `list.length === 0` / `entries.length === 0` to render “No personal access tokens”, “No policies found”, or “No audit events found”.
- Why it is a bug:
  - 401/403/500/load failures are misrepresented as successful empty datasets, which hides outages and makes auth/config problems look like missing data.
- Suggested validation:
  - Force any of those API calls to fail (for example by removing credentials or returning 500) and observe that the page shows an empty-state illustration instead of a request error.

### BUG-182 — Access-control mutation routes collapse arbitrary local failures into fake “Gateway sync failed” 502 responses (fixed)

- Severity: High
- Files:
  - `apps/access_control/authn/routes.py`
  - `apps/access_control/authz/routes.py`
- Summary:
  - PAT creation/revocation and policy create/update/delete all wrap broad multi-step transaction blocks in `except Exception`.
  - Any exception from local DB writes, audit logging, serialization, or final commit is turned into a `502 Bad Gateway` with a “Gateway sync failed …” message.
  - This misdiagnoses failures that have nothing to do with the gateway.
- Evidence:
  - `authn/routes.py#create_pat()` catches all exceptions around `service.create_pat(...)`, `sync_pat_creation(...)`, audit logging, and `session.commit()`, then raises `HTTPException(502, "Gateway sync failed after PAT creation: ...")`.
  - `authn/routes.py#revoke_pat()` does the same for revocation.
  - `authz/routes.py#create_policy()`, `update_policy()`, and `delete_policy()` apply the same pattern for policy mutations.
- Why it is a bug:
  - Clients and operators receive the wrong status code and root cause, which makes troubleshooting much harder and can trigger incorrect retry/alert behavior.
- Suggested validation:
  - Force a non-gateway failure inside one of those routes (for example an audit-log write failure or DB commit failure) and observe that the API still returns a gateway-sync 502.

### BUG-183 — Policy evaluation panel keeps showing the last decision after inputs change or a later evaluation fails (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/policies/page.tsx`
- Summary:
  - The in-page policy evaluation helper stores the last successful result in local state.
  - Changing subject/action/resource inputs does not clear that state.
  - Failed evaluations only toast an error and also do not clear the prior result.
- Evidence:
  - `PolicyEvalSection` stores `const [result, setResult] = useState<PolicyEvaluationResponse | null>(null)`.
  - `onSuccess` sets the result, but no input `onChange` / `onValueChange` handler resets it.
  - `onError` only calls `toast.error("Policy evaluation failed")`.
- Why it is a bug:
  - The panel can display an old allow/deny decision for inputs that are no longer on screen, misleading admins into trusting a stale authorization outcome.
- Suggested validation:
  - Run one successful evaluation, edit one of the inputs without re-running, or trigger a subsequent failing evaluation, and observe that the previous decision remains visible.

### BUG-184 — Service detail and versions pages treat artifact-version load failures as “no versions / no IR / no route config” (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/versions/page.tsx`
- Summary:
  - Several service-detail subviews read `useArtifactVersions(serviceId)` but ignore query errors.
  - When the request fails, they derive `data?.versions ?? []` and branch into empty states meant for genuinely versionless services.
  - This affects the dedicated Versions page and the detail page’s Versions, IR, and Gateway tabs.
- Evidence:
  - `versions/page.tsx` uses `const { data, isLoading } = useArtifactVersions(serviceId); const versions = data?.versions ?? [];` and renders “No versions found.” when `versions.length === 0`.
  - `services/[serviceId]/page.tsx#VersionsTab` follows the same pattern and also renders “No versions found.”
  - `IRTab` derives `activeVersion` from `data?.versions?.find(...)` and renders “No active IR available.” when the fetch fails.
  - `GatewayTab` derives `routeConfig` from the same query result and renders “No route configuration found for this service.”
- Why it is a bug:
  - Authorization errors, backend outages, or request failures are misreported as missing artifact history, which sends users down the wrong debugging path and can trigger destructive retries.
- Suggested validation:
  - Force `artifactApi.listVersions(serviceId)` to fail for an existing service and observe that the affected views show empty/missing-data messages instead of a fetch error.

### BUG-185 — Audit CSV export silently exports only the currently loaded (capped) subset, not the full matching history (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/audit/page.tsx`
  - `apps/access_control/audit/service.py`
- Summary:
  - The audit page exports whatever is currently loaded into the React Query result.
  - The backend audit list is hard-capped to the newest 1000 rows.
  - The export action provides no warning that the CSV may be truncated and presents the result as the dataset for the selected filters.
- Evidence:
  - `AuditLogService.list_entries()` applies `.order_by(AuditLog.timestamp.desc()).limit(1000)`.
  - `AuditLogPage.exportCSV()` serializes `entries`, which come directly from `data?.entries ?? []`.
  - The success toast says `Exported ${entries.length} entries` with no indication of truncation.
- Why it is a bug:
  - Audit exports are often used for compliance or incident review. Returning only the first 1000 matching rows while presenting the file as the export for the active filter set produces incomplete evidence.
- Suggested validation:
  - Seed more than 1000 audit rows matching the current filters, export CSV from the page, and compare the file row count with the true result count.

### BUG-186 — Compilations and Gateway pages also mask failed list queries as empty datasets (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/compilations/page.tsx`
  - `apps/web-ui/src/app/(dashboard)/gateway/page.tsx`
- Summary:
  - Both pages read query data and loading state but ignore query errors.
  - Failed responses therefore fall through to the same branches used for genuinely empty compilation/service lists.
  - On the gateway page this also drives misleading zero-value overview cards.
- Evidence:
  - `CompilationsPage` uses `const { data: jobs, isLoading, refetch } = useCompilations(...)`; when `jobs` is undefined, `filtered` becomes `[]` and the page renders “No compilation jobs found”.
  - `GatewayPage` uses `const { data: servicesData, isLoading, refetch } = useServices(); const services = servicesData?.services ?? [];`.
  - When `services` is empty, `serviceRoutes` becomes `[]`, overview `counts` are all zero, and the page renders “No service routes found.”
- Why it is a bug:
  - Backend/auth failures are presented as empty inventory states, which hides outages and makes users believe there is simply nothing to manage.
- Suggested validation:
  - Force the compilations list or services list request to fail and observe that the page renders its empty-state UI instead of any fetch error.

### BUG-187 — Policy evaluation UI has no `risk_level` control and silently evaluates every request as `safe` (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/policies/page.tsx`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/access_control/authz/service.py`
- Summary:
  - The policy evaluation panel only lets the user enter subject type, subject ID, action, and resource ID.
  - The frontend request adapter silently defaults a missing `risk_level` to `"safe"`.
  - Backend policy matching explicitly compares `payload.risk_level` against each policy’s `risk_threshold`.
- Evidence:
  - `PolicyEvalSection` renders no risk selector and calls `policyApi.evaluate({ subject_type, subject_id, action, resource_id })`.
  - `policyApi.evaluate()` sends `risk_level: req.risk_level ?? "safe"`.
  - `AuthzService._matches()` returns `True` only when `_RISK_ORDER[payload.risk_level] <= _RISK_ORDER[threshold]`.
- Why it is a bug:
  - The UI’s “Test Policy Evaluation” tool cannot accurately simulate cautious/dangerous requests, so it can show an allow/deny decision for the wrong risk context.
- Suggested validation:
  - Create two policies that differ only by risk threshold, then evaluate the same subject/action/resource in the UI and compare it with a direct API call that sets `risk_level="dangerous"`.

### BUG-188 — The policy UI hardcodes subject types to `user` and `group`, even though the backend accepts other valid policy subject types such as `role` (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/policies/page.tsx`
  - `apps/web-ui/src/types/api.ts`
  - `apps/access_control/authz/models.py`
  - `apps/access_control/tests/test_models.py`
  - `apps/access_control/tests/test_authz_service.py`
- Summary:
  - The backend policy model accepts any non-empty `subject_type` string.
  - Existing tests explicitly use `subject_type="role"`.
  - The web UI and TypeScript API types restrict subject types to `"user" | "group"`.
- Evidence:
  - `PolicyCreateRequest.subject_type` in `authz/models.py` is just `Field(min_length=1)`.
  - `test_models.py` constructs a valid `PolicyCreateRequest(subject_type="role", ...)`.
  - `test_authz_service.py` covers listing policies filtered by `subject_type="role"`.
  - `apps/web-ui/src/types/api.ts` defines `type SubjectType = "user" | "group"`.
  - `PoliciesPage` uses `const SUBJECT_TYPES = ["user", "group"]` for create, filter, and evaluation selectors.
- Why it is a bug:
  - Policies created through the backend for `role` (or any future subject type) cannot be created, filtered, or accurately evaluated from the UI, despite being valid server-side records.
- Suggested validation:
  - Create a policy with `subject_type="role"` via API, then open the policies UI and verify that the filter/create/evaluation controls cannot represent that subject type.

### BUG-189 — Compilation detail page treats fetch failures as “job not found” (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx`
- Summary:
  - The compilation detail page reads `{ data: job, isLoading }` from `useCompilation(jobId)` but does not inspect query errors.
  - If the request fails, `job` stays `undefined` and the page renders “Compilation job not found.”
- Evidence:
  - `CompilationDetailPage` uses `const { data: job, isLoading } = useCompilation(jobId, ...)`.
  - After the loading branch, it checks only `if (!job)` and renders `Compilation job not found.`.
- Why it is a bug:
  - A 401/403/500/network failure is misreported as a missing record, which hides outages and sends users to the wrong conclusion.
- Suggested validation:
  - Force `GET /api/v1/compilations/{jobId}` to fail for an existing job and observe that the page shows “Compilation job not found.” instead of a request error.

### BUG-190 — Review page masks version-load failures as “No IR available” while still loading workflow state/actions for an unverified version (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/[serviceId]/review/page.tsx`
  - `apps/web-ui/src/stores/workflow-store.ts`
- Summary:
  - The review page ignores errors from `useArtifactVersions(serviceId)`.
  - When version loading fails, it falls back to `requestedVersion ?? service?.active_version ?? versions[0]?.version_number ?? 1`, leaves `ir` undefined, and still calls `loadWorkflow(serviceId, versionNumber)`.
  - The page then shows workflow controls and a default `"draft"` state next to “No IR available for this version.”
- Evidence:
  - `ReviewPage` reads only `{ data: versionsData, isLoading: versionsLoading }` from `useArtifactVersions(serviceId)`.
  - `versions` falls back to `[]`; `versionNumber` falls back to the query param, active version, or `1`.
  - `loadWorkflow(serviceId, versionNumber)` runs unconditionally in `useEffect`.
  - `currentState` falls back to `workflow?.state ?? "draft"`.
  - The Review/IR tabs render “No IR available for this version.” when `ir` is missing, but the Workflow card is still shown.
- Why it is a bug:
  - A transient artifact fetch failure can look like a real version with no IR plus an editable draft workflow, inviting users to act on data the page never successfully loaded.
- Suggested validation:
  - Make the versions request fail while the service request succeeds, then open the review page and observe that workflow controls still render alongside “No IR available for this version.”

### BUG-191 — Most compiler API routes are completely unauthenticated, so anonymous callers can read and mutate compilation/service/artifact state (fixed)

- Severity: Critical
- Files:
  - `apps/compiler_api/main.py`
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_api/routes/services.py`
  - `apps/compiler_api/routes/artifacts.py`
- Summary:
  - The compiler API mounts compilation, service, and artifact routers without any app-level authentication middleware.
  - Those route handlers have only DB/dispatcher/publisher dependencies, not caller/authz dependencies.
  - The lone exception in this area is the SSE events endpoint, which does require `require_sse_caller`.
- Evidence:
  - `create_app()` in `compiler_api/main.py` simply `include_router(...)`s the routers; there is no auth middleware or shared dependency.
  - `routes/compilations.py` exposes create/list/get/retry/rollback without `require_authenticated_caller` or `require_admin_caller`.
  - `routes/services.py` exposes list/get without auth dependencies.
  - `routes/artifacts.py` exposes create/list/get/update/delete/activate/diff without auth dependencies.
  - Only `GET /api/v1/compilations/{job_id}/events` includes `_caller: TokenPrincipalResponse = Depends(require_sse_caller)`.
- Why it is a bug:
  - Anyone who can reach the compiler API can enumerate services and artifacts, trigger new compilations, retry/rollback jobs, activate versions, and delete artifact versions without presenting credentials.
- Suggested validation:
  - Call one of the compiler API mutation routes (for example `POST /api/v1/compilations` or `POST /api/v1/artifacts/{service_id}/versions/{version}/activate`) without an `Authorization` header and confirm that it proceeds.

### BUG-192 — Failed compilation dispatch leaves behind committed audit history for jobs that were deleted (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_api/repository.py`
  - `apps/access_control/audit/service.py`
- Summary:
  - Compilation creation/retry/rollback commit the new job record before dispatch.
  - They also call `audit_log.append_entry(...)` with the default `commit=True` before `dispatcher.enqueue(...)`.
  - If enqueue fails, the exception path deletes the job, but the already-committed audit entry remains.
- Evidence:
  - `CompilationRepository.create_job()` commits immediately.
  - `create_compilation()`, `retry_compilation()`, and `rollback_compilation()` all call `audit_log.append_entry(...)` before `dispatcher.enqueue(...)`, without `commit=False`.
  - `AuditLogService.append_entry()` commits when `commit` is not overridden.
  - On failure, those routes call `repository.delete_job(...)`, which separately commits the delete.
- Why it is a bug:
  - Audit history can claim a compilation was triggered/retried/rollback-requested even though the route returned an error and the corresponding job record was deleted.
- Suggested validation:
  - Force `dispatcher.enqueue(...)` to fail, then inspect the audit log and verify that the “compilation.*” entry remains even though the new job no longer exists.

### BUG-193 — Artifact create/update can fail after the version change is already committed, leaving clients with a false 500 (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/routes/artifacts.py`
  - `apps/compiler_api/repository.py`
  - `apps/access_control/audit/service.py`
- Summary:
  - Artifact creation and update persist the version record before attempting audit logging.
  - If audit logging fails, the route raises an error after the data change is already committed.
  - The client sees a failure even though the create/update actually took effect.
- Evidence:
  - `ArtifactRegistryRepository.create_version()` flushes, replaces artifacts, and `commit()`s before returning.
  - `create_artifact_version()` calls `repository.create_version(...)` first and only then invokes `audit_log.append_entry(...)`.
  - `ArtifactRegistryRepository.update_version()` commits before returning.
  - `update_artifact_version()` also invokes `audit_log.append_entry(...)` only after the repository commit has completed.
- Why it is a bug:
  - A 500 response no longer means “nothing changed.” Clients may retry a request that actually succeeded, creating confusion or duplicate follow-up actions.
- Suggested validation:
  - Simulate an audit logging failure during artifact creation or update, then verify that the route errors while the artifact version change is still persisted.

### BUG-194 — `require_authenticated_caller()` accepts raw token strings in the `Authorization` header even without the `Bearer ` scheme (fixed)

- Severity: Medium
- Files:
  - `apps/access_control/security.py`
- Summary:
  - The authentication helper does not require the `Authorization` header to start with `Bearer `.
  - It simply calls `auth_header.removeprefix("Bearer ").strip()` and validates whatever remains.
  - A valid JWT/PAT sent as the raw header value will therefore authenticate.
- Evidence:
  - `require_authenticated_caller()` reads `auth_header = request.headers.get("Authorization", "")`.
  - It derives the token as `auth_header.removeprefix("Bearer ").strip()`.
  - There is no branch that rejects non-Bearer schemes before token validation.
- Why it is a bug:
  - The helper’s behavior does not match its own “Bearer token is required” semantics and weakens protocol expectations for downstream clients, proxies, and security controls.
- Suggested validation:
  - Send a valid JWT or PAT as the literal `Authorization` header value without the `Bearer ` prefix and observe that protected routes still authenticate it.

### BUG-195 — Compilation event streaming is broken because the frontend sends `Authorization` headers while the backend only accepts `?token=...` (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/lib/hooks/use-sse.ts`
  - `apps/web-ui/src/lib/hooks/__tests__/use-sse.test.tsx`
  - `apps/access_control/security.py`
  - `apps/compiler_api/routes/compilations.py`
- Summary:
  - The web UI’s compilation SSE hook performs a `fetch()` request with `Authorization: Bearer ...`.
  - The compiler API event stream route depends on `require_sse_caller`, which ignores headers and requires a `token` query parameter.
  - Even correctly authenticated browser sessions therefore receive 401s when subscribing to compilation events.
- Evidence:
  - `useCompilationEvents()` builds `url = .../compilations/${jobId}/events` and, when a token exists, sets `headers["Authorization"] = \`Bearer ${token}\``.
  - The hook’s unit test explicitly asserts the Authorization header, not a `token` query param.
  - `require_sse_caller()` reads only `request.query_params.get("token", "")` and returns 401 with `Query parameter 'token' is required for SSE endpoints.` when absent.
  - `GET /api/v1/compilations/{job_id}/events` uses `_caller: TokenPrincipalResponse = Depends(require_sse_caller)`.
- Why it is a bug:
  - The live event stream for in-progress compilations cannot authenticate via the current web UI hook, so the detail page loses real-time updates even when the user has a valid token.
- Suggested validation:
  - Put a valid token into `localStorage["auth_token"]`, open an in-progress compilation detail page, and observe that the SSE request returns 401 unless the token is manually added to the URL query string.

### BUG-196 — `from_stage` retry requests are a no-op because the worker never reads that option and always reruns the full pipeline (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_api/tests/test_routes_compilations.py`
  - `apps/compiler_worker/executor.py`
  - `apps/compiler_worker/workflows/compile_workflow.py`
- Summary:
  - The retry API accepts an optional `from_stage` parameter and stores it in `request.options`.
  - The web UI/client tests exercise that parameter, but the worker runtime never consults it.
  - `CompilationWorkflow.run()` always iterates every stage definition from the beginning.
- Evidence:
  - `retry_compilation()` documents `from_stage` as a way to “resume” from a pipeline stage and writes `options["from_stage"] = from_stage`.
  - `test_retry_includes_from_stage()` asserts only that the new request contains that option.
  - `DatabaseWorkflowCompilationExecutor.execute()` always constructs a plain `CompilationWorkflow`.
  - `CompilationWorkflow.run()` enters `for stage_definition in self._stage_definitions:` with no branch that skips ahead based on `request.options`.
  - A repo-wide search during this scan found `from_stage` usage only in the retry route, client/tests, and not in worker execution logic.
- Why it is a bug:
  - Operators are told they can resume from a failed stage, but the retry actually reruns the whole pipeline, repeating earlier work and any side effects tied to earlier stages.
- Suggested validation:
  - Fail a job at `validate_ir`, retry it with `from_stage=validate_ir`, and inspect the new job’s event stream. It will emit the earlier detect/extract/enhance stages again instead of starting at `validate_ir`.

### BUG-197 — Compilation rollback requests never invoke the dedicated rollback workflow and instead enqueue a normal compilation (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_worker/executor.py`
  - `apps/compiler_worker/workflows/rollback_workflow.py`
  - `apps/compiler_worker/tests/test_rollback_workflow.py`
- Summary:
  - The rollback route clones the original compilation request and adds `options["rollback_from_job_id"]`.
  - The worker executor always instantiates `CompilationWorkflow`, not `RollbackWorkflow`.
  - The dedicated rollback workflow expects a different input shape (`RollbackRequest(service_id, target_version)`) and appears only in tests/workflow code, not in the live execution path.
- Evidence:
  - `rollback_compilation()` builds a fresh `CompilationRequest` from the original job and only annotates it with `rollback_from_job_id`.
  - `DatabaseWorkflowCompilationExecutor.execute()` imports/builds `CompilationWorkflow` and directly calls `workflow.run(request)`.
  - `RollbackWorkflow.run()` expects `RollbackRequest(service_id=..., target_version=...)`, not `CompilationRequest`.
  - A repo-wide search during this scan found `rollback_from_job_id` only in the rollback route; no worker code consumes it.
  - The dedicated rollback workflow is otherwise exercised only by rollback-specific tests.
- Why it is a bug:
  - `POST /api/v1/compilations/{job_id}/rollback` does not actually drive rollback semantics; it just launches another ordinary compilation job, so the API contract and audit wording are misleading.
- Suggested validation:
  - Request a rollback for a succeeded compilation and inspect the new job. It will run the normal compilation stages rather than executing the dedicated rollback flow against a target version.

### BUG-198 — Retry/rollback cannot faithfully replay pasted or uploaded specs because job persistence drops `source_content` and `filename` (fixed)

- Severity: High
- Files:
  - `apps/web-ui/src/components/compilations/compilation-wizard.tsx`
  - `libs/db_models.py`
  - `apps/compiler_api/repository.py`
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_api/dispatcher.py`
  - `apps/compiler_worker/models.py`
- Summary:
  - The web UI supports `paste` and `upload` source modes that submit inline `source_content` (and, for uploads, a filename).
  - Compilation job persistence stores only `source_url` and `source_hash`; it does not retain the inline source or filename needed to replay the request.
  - Retry/rollback clone only the persisted fields, so inline-source jobs lose their original spec when requeued.
- Evidence:
  - `compilation-wizard.tsx` explicitly supports `sourceMode === "paste" | "upload"` and tests that pasted/uploaded sources use `source_content`.
  - `CompilationJob` stores `source_url`, `source_hash`, `created_by`, `service_name`, and `options`, but has no `source_content` or `filename` columns.
  - `CompilationRepository.create_job()` persists only `request.source_url` and `request.source_hash`.
  - `retry_compilation()` and `rollback_compilation()` rebuild requests from `original.source_url` / `original.source_hash` and do not restore `source_content` or `filename`.
  - `CeleryCompilationDispatcher.enqueue()` serializes with `request.to_payload()`, and `CompilationRequest.to_payload()` raises when both `source_url` and `source_content` are missing.
- Why it is a bug:
  - Jobs created from pasted/uploaded specs cannot be retried or rolled back reliably. In Celery mode they can fail before dispatch; in other modes they still rerun without the original source payload.
- Suggested validation:
  - Create a compilation from pasted or uploaded content with no `source_url`, then click Retry or Rollback and observe that the new request errors or executes without the original spec content.

### BUG-199 — Compilation creation trusts caller-supplied `created_by`, so job provenance and audit actors are trivially spoofable (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/models.py`
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - The create-compilation API accepts `created_by` in the request body and copies it straight into both the persisted job record and the audit log actor field.
  - There is no server-side derivation of the actor from an authenticated caller.
  - A client can therefore submit work while claiming any creator identity it wants.
- Evidence:
  - `CompilationCreateRequest` exposes `created_by: str | None`.
  - `CompilationCreateRequest.to_workflow_request()` copies `created_by` unchanged into `CompilationRequest`.
  - `CompilationRepository.create_job()` persists `created_by=request.created_by`.
  - `create_compilation()` writes the audit entry with `actor=payload.created_by or "system"`.
- Why it is a bug:
  - Compilation history and audit trails no longer provide trustworthy provenance; callers can impersonate teammates or service accounts simply by choosing a different `created_by` string.
- Suggested validation:
  - Submit a compilation with `created_by="admin"` from another client context, then inspect the returned job row and audit log entry. Both will attribute the action to `admin`.

### BUG-200 — Retry and rollback preserve the original submitter as the creator/actor, so new actions are misattributed to the wrong person (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - When a job is retried or rolled back, the new request copies `created_by=original.created_by`.
  - The audit entry also uses `actor=original.created_by`.
  - The person who actually initiated the retry/rollback is never represented.
- Evidence:
  - `retry_compilation()` builds `CompilationRequest(..., created_by=original.created_by, ...)` and logs `actor=original.created_by or "system"`.
  - `rollback_compilation()` does the same for rollback jobs.
  - `CompilationRepository.create_job()` persists `request.created_by` into the new job row.
- Why it is a bug:
  - New operational actions appear to have been taken by the original submitter even when a different operator triggered the retry or rollback, corrupting both job provenance and audit history.
- Suggested validation:
  - Have one user create a compilation and another trigger Retry or Rollback, then inspect the new job’s `created_by` and the audit entry actor. They will still show the original submitter.

### BUG-201 — Artifact create/update/delete/activate audit entries always record `actor="system"`, so user-triggered registry changes lose all actor attribution (fixed)

- Severity: Medium
- Files:
  - `apps/compiler_api/routes/artifacts.py`
- Summary:
  - All artifact mutation routes append audit entries with a hardcoded `actor="system"`.
  - None of those routes attempt to bind the change to the initiating user or caller identity.
  - Manual registry operations therefore look indistinguishable from automated background work.
- Evidence:
  - `create_artifact_version()` logs `actor="system"` for `artifact.created`.
  - `update_artifact_version()` logs `actor="system"` for `artifact.updated`.
  - `delete_artifact_version()` logs `actor="system"` for `artifact.deleted`.
  - `activate_artifact_version()` logs `actor="system"` for `artifact.activated`.
- Why it is a bug:
  - Audit history cannot answer who created, edited, deleted, or activated a version, which weakens traceability and incident review for one of the most operator-sensitive surfaces in the repo.
- Suggested validation:
  - Perform create/update/delete/activate operations from the UI or API and inspect the audit log. Every entry will show `system` as the actor regardless of who initiated it.

### BUG-202 — Service Registry search only matches display names, so searching by stable `service_id` returns false negatives (fixed)

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/services/page.tsx`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/compiler_api/models.py`
- Summary:
  - Service summaries expose both `service_id` and `service_name`.
  - The frontend normalizes `service_name` into `service.name` and searches only that field.
  - Links and routes, however, are keyed by `service_id`, so operators cannot reliably find a service by the identifier the rest of the system uses.
- Evidence:
  - `ServiceSummaryResponse` includes both `service_id` and `service_name`.
  - `normalizeServiceSummary()` maps `name: raw.service_name` while keeping `service_id` separately.
  - `services/page.tsx` filters search results with `s.name.toLowerCase().includes(q)`.
  - The same page links rows/cards using `s.service_id`.
- Why it is a bug:
  - If `service_id` and display name diverge (for example `billing-api` vs `Billing API`), entering the stable ID in the registry search box shows no match even though that is the canonical identifier used elsewhere.
- Suggested validation:
  - Compile a service with `service_id="billing-api"` and `service_name="Billing API"`, then search the registry page for `billing-api`. The service will not appear.

### BUG-203 — A single malformed active IR can make the service catalog list/detail endpoints fail with 500 instead of isolating the bad record (fixed)

- Severity: High
- Files:
  - `apps/compiler_api/repository.py`
  - `apps/compiler_api/routes/services.py`
- Summary:
  - Service catalog responses are built by validating each active version’s `ir_json` into `ServiceIR`.
  - That validation is not wrapped or isolated.
  - One bad active record can therefore abort `/api/v1/services` or `/api/v1/services/{service_id}` entirely.
- Evidence:
  - `ServiceCatalogRepository.list_services()` returns `[self._to_service_summary(version) for version in versions]`.
  - `_to_service_summary()` immediately calls `ServiceIR.model_validate(version.ir_json)`.
  - `get_service()` returns the same `_to_service_summary(version)` path for detail requests.
  - `routes/services.py` does not catch validation errors from the repository; it only maps `None` to 404.
- Why it is a bug:
  - Service discovery becomes brittle: one malformed active version can break the entire registry listing or a service detail lookup instead of being quarantined or reported as a per-record issue.
- Suggested validation:
  - Insert one active `service_versions` row whose `ir_json` is missing required `ServiceIR` fields, then call `GET /api/v1/services` or `GET /api/v1/services/{service_id}` and observe the 500.

### BUG-204 — The compiler worker defaults to deferred route publishing, so successful compilations can report route publication without touching the gateway (fixed)

- Severity: High
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - Route publication is configured via `ROUTE_PUBLISH_MODE`.
  - The default mode is `deferred`, and the default activity registry silently chooses `DeferredRoutePublisher` when that env var is unset.
  - That publisher does not call the gateway at all; it only returns route IDs as metadata.
  - The `route` stage still records success and the compilation can proceed to completion.
- Evidence:
  - `_DEFAULT_ROUTE_PUBLISH_MODE = "deferred"`.
  - `ProductionActivitySettings.from_env()` uses `os.getenv("ROUTE_PUBLISH_MODE", _DEFAULT_ROUTE_PUBLISH_MODE)`.
  - `create_default_activity_registry()` resolves `DeferredRoutePublisher(mode="deferred")` whenever `route_publish_mode == "deferred"`.
  - `DeferredRoutePublisher.publish()` returns only `{mode, default_route_id, version_route_id}` and never contacts any external service.
  - `route_stage()` treats that return value as a successful publication and emits `event_detail["publication_mode"] = resolved_settings.route_publish_mode`.
- Why it is a bug:
  - A compilation can look fully successful, with route metadata attached, even though no routes were ever published to the live gateway.
- Suggested validation:
  - Run a compilation with `ROUTE_PUBLISH_MODE` unset, inspect the job events for a successful `route` stage with `publication_mode="deferred"`, and confirm that no gateway routes were created.

### BUG-205 — Access-control silently falls back to a process-local fake gateway client when `GATEWAY_ADMIN_URL` is unset (fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/client.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The access-control service auto-configures its gateway binding layer from environment.
  - If `GATEWAY_ADMIN_URL` is missing, it silently uses `InMemoryAPISIXAdminClient` instead of failing fast.
  - PAT sync, policy sync, route sync, and reconcile operations then “succeed” only against process-local memory and disappear on restart.
- Evidence:
  - `load_gateway_admin_client_from_env()` returns `HTTPGatewayAdminClient(...)` only when `GATEWAY_ADMIN_URL` is non-empty.
  - Otherwise it returns `InMemoryAPISIXAdminClient()`.
  - `configure_gateway_binding_service()` uses `client or load_gateway_admin_client_from_env()` without any environment guard or warning.
  - All gateway-binding routes and PAT/policy sync paths use that configured service.
- Why it is a bug:
  - A missing environment variable turns critical control-plane operations into success-shaped no-ops rather than an explicit startup/configuration failure.
- Suggested validation:
  - Start access-control without `GATEWAY_ADMIN_URL`, create a PAT or sync routes, then restart the service and observe that the “gateway state” existed only in memory and never reached a real admin API.

### BUG-206 — Gateway reconcile deletes unrelated consumers, policy bindings, and routes because it assumes exclusive ownership of the whole gateway (fixed)

- Severity: Critical
- Files:
  - `apps/access_control/gateway_binding/service.py`
  - `apps/access_control/gateway_binding/client.py`
- Summary:
  - Reconcile builds expected gateway state only from local PATs, policies, and stored service route configs.
  - It separately lists all existing consumers, policy bindings, and routes from the gateway admin client.
  - Any existing object not present in the local expected set is deleted, regardless of whether the compiler platform created it.
- Evidence:
  - `reconcile()` builds `expected_consumers`, `expected_policy_bindings`, and `expected_routes` from database rows only.
  - It calls `self._client.list_consumers()`, `list_policy_bindings()`, and `list_routes()` to fetch the full current gateway contents.
  - It then deletes every `consumer_id in set(existing_consumers) - set(expected_consumers)`.
  - It does the same for `binding_id in set(existing_policy_bindings) - set(expected_policy_bindings)`.
  - It also deletes every `route_id in set(existing_routes) - set(expected_routes)`.
- Why it is a bug:
  - In any shared gateway, running reconcile can wipe unrelated manually managed or third-party resources that this control plane does not own.
- Suggested validation:
  - Seed the gateway with an extra consumer, policy binding, or route that is not represented in the local database, run `/api/v1/gateway-binding/reconcile`, and observe that the foreign object is deleted.

### BUG-207 — `GET /api/v1/gateway-binding/service-routes` can 500 when the gateway contains routes that are not compiler-managed service documents (fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/service.py`
  - `apps/access_control/gateway_binding/routes.py`
  - `apps/access_control/gateway_binding/client.py`
- Summary:
  - The gateway-binding service returns every raw route document from the admin client.
  - The HTTP route then validates each document against `GatewayRouteDocumentResponse`, which requires compiler-specific fields like `route_type`, `service_id`, `service_name`, `namespace`, and `target_service`.
  - A route created outside this system can therefore break the entire listing endpoint.
- Evidence:
  - `GatewayBindingService.list_service_routes()` returns `[routes[route_id].document for route_id in sorted(routes)]`, i.e. all documents from `list_routes()`.
  - `list_service_routes()` wraps each entry as `GatewayRouteDocumentResponse(**document)`.
  - `GatewayRouteDocumentResponse` requires `route_id`, `route_type`, `service_id`, `service_name`, `namespace`, and `target_service`.
  - `HTTPGatewayAdminClient.list_routes()` passes through each gateway item’s raw `document`.
- Why it is a bug:
  - One unrelated or differently shaped gateway route can turn the admin listing API into a server error, taking down the gateway overview for all services.
- Suggested validation:
  - Add a gateway route whose document lacks the compiler-specific metadata fields, then call `GET /api/v1/gateway-binding/service-routes` and observe the validation failure / 500.

### BUG-208 — Review workflows are keyed only by `service_id` and `version_number`, so tenant/environment variants share one approval state and notes (Agent A fixed)

- Severity: High
- Files:
  - `libs/db_models.py`
  - `apps/compiler_api/routes/workflows.py`
  - `apps/web-ui/src/stores/workflow-store.ts`
- Summary:
  - Artifact/service versions are scoped by `service_id`, `version_number`, `tenant`, and `environment`.
  - Review workflows are not: they are uniquely keyed only by `service_id` and `version_number`.
  - The workflow routes and frontend cache keys use the same reduced identity.
  - Different scoped variants therefore collapse onto the same review record.
- Evidence:
  - `ServiceVersion` is unique on `("service_id", "version_number", "tenant", "environment")`.
  - `ReviewWorkflow` is unique only on `("service_id", "version_number")`.
  - `_get_or_create()` in `routes/workflows.py` selects by only `ReviewWorkflow.service_id == service_id` and `ReviewWorkflow.version_number == version_number`.
  - `workflowKey(serviceId, version)` in `workflow-store.ts` is `${serviceId}-v${version}` with no scope component.
- Why it is a bug:
  - Two service variants that legitimately differ by tenant/environment cannot maintain independent approval state, history, or review notes; reviewing one scope mutates the other.
- Suggested validation:
  - Create `billing-api` version `1` in two tenant/environment scopes, transition one workflow to `approved`, then load the other scope’s workflow record and observe that it reuses the same state/history.

### BUG-209 — Disabling a user has no effect on PAT authentication or gateway mirroring because `User.is_active` is never consulted (Agent A fixed)

- Severity: High
- Files:
  - `libs/db_models.py`
  - `apps/access_control/authn/service.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The auth schema models `User.is_active`, implying accounts can be disabled.
  - But PAT authentication checks only token hash and `revoked_at`.
  - Gateway reconciliation likewise mirrors all unrevoked PATs without filtering on the owning user’s active flag.
  - A disabled user’s PAT can therefore continue authenticating and remain published to the gateway.
- Evidence:
  - `User` in `libs/db_models.py` defines `is_active`.
  - `_validate_pat()` joins `PersonalAccessToken` to `User` but checks only whether the PAT exists and whether `pat.revoked_at` is set.
  - `GatewayBindingService.reconcile()` selects active PAT rows with `.where(PersonalAccessToken.revoked_at.is_(None))` and does not filter on `User.is_active`.
  - A repo-wide search during this scan found no access-control code paths that consult `User.is_active`.
- Why it is a bug:
  - Deactivating an account does not actually cut off that user’s long-lived PAT access, which undermines a basic account-disable control.
- Suggested validation:
  - Mark a user row `is_active = false`, then validate one of that user’s existing PATs or run gateway reconcile and observe that the PAT still authenticates and is still mirrored as an active consumer.

### BUG-210 — Gateway route identities ignore tenant/environment, so scoped variants with the same `service_id` overwrite each other (Agent A fixed)

- Severity: High
- Files:
  - `libs/db_models.py`
  - `libs/generator/generic_mode.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The registry allows the same `service_id` / `version_number` to exist in different tenant/environment scopes.
  - Gateway route IDs, however, are generated solely from the unscoped `service_id`.
  - Publishing routes for one scope therefore targets the same gateway IDs as another scope with the same service ID.
- Evidence:
  - `ServiceVersion` is uniquely scoped by `service_id`, `version_number`, `tenant`, and `environment`.
  - `_route_base_name()` returns only `config.service_id` (or `service_ir.service_name`) after DNS sanitization; it never incorporates tenant/environment.
  - `build_route_config()` sets `default_route.route_id = f"{resolved_route_base}-active"` and `version_route.route_id = f"{resolved_route_base}-v{config.version_number}"`.
  - `GatewayBindingService.sync_service_routes()` upserts routes by those `route_id` values.
- Why it is a bug:
  - In a multi-tenant/multi-environment registry, scoped variants that share the same stable service ID cannot coexist safely in the gateway; publishing one scope can overwrite or drift the other.
- Suggested validation:
  - Create two scoped artifact versions with the same `service_id`, generate/publish both route configs, and compare the resulting route IDs. They will collide on the same `*-active` / `*-vN` identities.

### BUG-211 — Access-control falls back to the known `dev-secret` when `ACCESS_CONTROL_JWT_SECRET` is missing and `ENV` is unset/dev-like (Agent A fixed)

- Severity: Critical
- Files:
  - `apps/access_control/authn/service.py`
  - `apps/access_control/main.py`
  - `apps/access_control/security.py`
- Summary:
  - The access-control app loads JWT settings at startup.
  - If `ACCESS_CONTROL_JWT_SECRET` is absent and `ENV` is unset (defaulting to `"dev"`) or another dev-like value, the service does not fail fast.
  - It silently uses the hardcoded secret `dev-secret`.
  - Admin authorization trusts role claims from the JWT payload, so a forged token signed with that known secret can pass admin checks.
- Evidence:
  - `create_app()` sets `app.state.jwt_settings = jwt_settings or load_jwt_settings()`.
  - `load_jwt_settings()` reads `ACCESS_CONTROL_JWT_SECRET`; when missing, it defaults `env = os.getenv("ENV", "dev")` and sets `secret = "dev-secret"` unless the env is explicitly non-dev.
  - `caller_is_admin()` derives admin status from `caller.claims["roles"]`.
  - `require_admin_caller()` admits callers purely through that JWT-derived role check.
- Why it is a bug:
  - A missing secret becomes an authentication fail-open with a publicly known signing key instead of a startup error.
- Suggested validation:
  - Start access-control without `ACCESS_CONTROL_JWT_SECRET` and with `ENV` unset, mint an HS256 JWT signed with `dev-secret` and `roles=["admin"]`, then call an admin-protected route such as `/api/v1/gateway-binding/reconcile`.

### BUG-212 — PAT creation doubles as a hidden user upsert, silently creating local accounts and overwriting stored email (Agent A fixed)

- Severity: High
- Files:
  - `apps/access_control/authn/routes.py`
  - `apps/access_control/authn/models.py`
  - `apps/access_control/authn/service.py`
  - `apps/access_control/security.py`
  - `apps/web-ui/src/app/(dashboard)/pats/page.tsx`
- Summary:
  - PAT creation accepts both `username` and optional `email`.
  - The authn service does not require that user to already exist.
  - Instead, PAT issuance calls `_get_or_create_user()`, which creates a new `User` row when missing and rewrites `user.email` when the provided email differs.
  - The PAT UI always submits the current stored email, so token issuance is also a profile-write path.
- Evidence:
  - `PATCreateRequest` exposes `username` and `email`.
  - `create_pat()` accepts that payload after only `require_self_or_admin(...)` string matching on username.
  - `AuthnService.create_pat()` calls `_get_or_create_user(username=username, email=email, ...)`.
  - `_get_or_create_user()` updates `user.email` when `email and user.email != email`, and otherwise inserts `User(username=username, email=email)` if no row exists.
  - `pats/page.tsx` sends `email: user?.email` on every PAT creation request.
- Why it is a bug:
  - Minting a token should not implicitly provision accounts or mutate user profile data; here it silently does both.
- Suggested validation:
  - Create a PAT for a previously unseen username or for an existing user with a different email, then inspect `auth.users` and observe the inserted/modified row.

### BUG-213 — PAT create/revoke can leave gateway consumers and the database out of sync when a later audit/commit step fails (Agent A fixed)

- Severity: High
- Files:
  - `apps/access_control/authn/routes.py`
  - `apps/access_control/authn/service.py`
  - `apps/access_control/gateway_binding/service.py`
  - `apps/access_control/audit/service.py`
- Summary:
  - PAT creation and revocation stage the database change with `commit=False`.
  - They then mutate the external gateway consumer state.
  - Only after that do they append the audit entry and `session.commit()`.
  - If audit logging or the final commit fails, `session.rollback()` reverts the DB change but there is no compensating gateway operation.
- Evidence:
  - `create_pat()` calls `service.create_pat(..., commit=False)`, then `gateway_binding.sync_pat_creation(...)`, then `audit_log.append_entry(..., commit=False)`, then `session.commit()`.
  - `revoke_pat()` calls `service.revoke_pat(..., commit=False)`, then `gateway_binding.sync_pat_revocation(...)`, then audit append, then `session.commit()`.
  - Both exception paths do only `await session.rollback()` before returning a 502.
- Why it is a bug:
  - The gateway and DB can diverge: a PAT can exist in gateway but not in DB after creation failure, or remain active in DB but already be deleted from gateway after revocation failure.
- Suggested validation:
  - Force `audit_log.append_entry(...)` or `session.commit()` to fail after gateway sync/deletion, then compare gateway consumers with the PAT row in Postgres.

### BUG-214 — Policy create/update/delete can leave gateway policy bindings out of sync when a later audit/commit step fails (Agent A fixed)

- Severity: High
- Files:
  - `apps/access_control/authz/routes.py`
  - `apps/access_control/authz/service.py`
  - `apps/access_control/gateway_binding/service.py`
  - `apps/access_control/audit/service.py`
- Summary:
  - Policy mutations write to the database with `commit=False`, then sync/delete the gateway binding, then append audit history, and only then commit the SQL transaction.
  - If the audit write or final commit fails after gateway sync succeeds, the session is rolled back locally but the external gateway binding is left changed.
- Evidence:
  - `create_policy()` calls `service.create_policy(..., commit=False)`, `gateway_binding.sync_policy(created)`, audit append with `commit=False`, then `session.commit()`.
  - `update_policy()` follows the same ordering with `service.update_policy(..., commit=False)`.
  - `delete_policy()` calls `service.delete_policy(..., commit=False)`, then `gateway_binding.delete_policy(policy_id)`, then audit append, then commit.
  - All three routes catch broad exceptions, run only `await session.rollback()`, and do not compensate the external gateway state.
- Why it is a bug:
  - Gateway policy enforcement can reflect a create/update/delete that the database transaction ultimately rolled back, producing a hidden split-brain between source of truth and live enforcement.
- Suggested validation:
  - Make the final audit write or `session.commit()` fail after `sync_policy()`/`delete_policy()` succeeds, then compare Postgres policy rows with gateway policy bindings.

### BUG-215 — Artifact activate/delete can leave registry state and live routes out of sync when a later audit/commit step fails (Agent A fixed)

- Severity: High
- Files:
  - `apps/compiler_api/routes/artifacts.py`
  - `apps/compiler_api/repository.py`
  - `apps/access_control/audit/service.py`
- Summary:
  - Artifact activation and deletion are staged in SQL with `commit=False`.
  - The route then mutates live gateway routes.
  - Only after that does it append audit history and commit the DB transaction.
  - A later failure rolls back the DB transaction but does not restore the already changed route state.
- Evidence:
  - `ArtifactRegistryRepository.activate_version(..., commit=False)` deactivates the old active record, marks the target active, and `flush()`es before commit.
  - `activate_artifact_version()` then calls `route_publisher.sync(version.route_config)`, audit append with `commit=False`, and `session.commit()`.
  - `ArtifactRegistryRepository.delete_version(..., commit=False)` deletes the row and may promote a replacement active version before commit.
  - `delete_artifact_version()` then calls `route_publisher.delete(...)` / `route_publisher.sync(...)`, audit append, and `session.commit()`.
  - Both handlers handle failures with only `await session.rollback()`.
- Why it is a bug:
  - The registry can say one version is active or undeleted while the gateway has already switched or removed routes for a different state.
- Suggested validation:
  - Force `audit_log.append_entry(...)` or `session.commit()` to fail after the route publisher call in activate/delete, then compare the database row state with the live gateway routes.

### BUG-216 — PAT-authenticated sessions never receive role claims, so admin PATs can log in but fail every admin-protected API (Agent A fixed)

- Severity: High
- Files:
  - `apps/access_control/authn/service.py`
  - `apps/access_control/security.py`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/components/auth-guard.tsx`
- Summary:
  - PAT validation returns a principal with only `sub`, `pat_id`, and token `name` in its claims.
  - The frontend derives roles only from `claims["roles"]`.
  - The dashboard admits any authenticated session, but admin routes require `caller_is_admin()` based on those role claims.
  - A user can therefore sign into the UI with a PAT and appear logged in, yet all admin-only operations fail because the PAT principal carries no roles.
- Evidence:
  - `_validate_pat()` returns `claims={"sub": user.username, "pat_id": ..., "name": ...}` with no `roles`.
  - `normalizeTokenPrincipal()` maps `roles: readStringArrayClaim(raw.claims, "roles")`.
  - `AuthGuard` checks only `isAuthenticated`, not roles.
  - `require_admin_caller()` and `caller_is_admin()` authorize only via `caller.claims["roles"]`.
- Why it is a bug:
  - PAT login is not privilege-equivalent to JWT login for the same user, even though the UI treats both as valid sign-in methods.
- Suggested validation:
  - Log in as an admin user with a PAT, then try an admin action such as creating a policy or listing gateway routes and observe the 403s.

### BUG-217 — PAT management silently truncates after 1000 tokens because the backend hard-caps results and the UI has no pagination (Agent A fixed)

- Severity: Medium
- Files:
  - `apps/access_control/authn/service.py`
  - `apps/web-ui/src/app/(dashboard)/pats/page.tsx`
- Summary:
  - PAT listing is capped to the newest 1000 rows on the backend.
  - The PAT page treats the returned array as the complete dataset and renders it directly.
  - There is no pagination, cursoring, or warning that older tokens have been omitted.
- Evidence:
  - `AuthnService.list_pats()` orders by `PersonalAccessToken.created_at.desc()` and applies `.limit(1000)`.
  - `PATTokensPage` derives `const pats = useMemo(() => data?.pats ?? [], [data])`.
  - The page branches only on `pats.length === 0` and otherwise renders `pats.map(...)` in one table.
  - There are no page controls or follow-up requests for additional results.
- Why it is a bug:
  - Older PATs disappear from the management surface and cannot be reviewed or revoked from the UI once a user exceeds 1000 tokens.
- Suggested validation:
  - Seed more than 1000 PATs for one user, open the PAT page, and observe that only the newest 1000 are visible/revocable.

### BUG-218 — `service_versions` uniqueness constraints do not protect unscoped rows because `tenant` and `environment` are nullable (Agent A fixed)

- Severity: Critical
- Files:
  - `libs/db_models.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - The registry intends one active row per `(service_id, tenant, environment)` scope and one row per `(service_id, version_number, tenant, environment)` identity.
  - But `tenant` and `environment` are nullable.
  - In PostgreSQL, unique constraints/indexes treat `NULL` values as distinct, so duplicate unscoped rows can bypass both constraints.
  - Downstream repository code assumes uniqueness and uses `limit(1)` lookups.
- Evidence:
  - `ServiceVersion.tenant` and `.environment` are nullable columns.
  - `uq_service_versions_one_active` is a unique index on `("service_id", "tenant", "environment")` with `postgresql_where=text("is_active = true")`.
  - `uq_service_version` is a `UniqueConstraint("service_id", "version_number", "tenant", "environment")`.
  - `get_service()`, `get_active_version()`, and similar repository paths resolve a single row with `.limit(1)`, assuming one match.
- Why it is a bug:
  - The default unscoped case can admit duplicate version rows or multiple active rows for the same service, leading to nondeterministic reads and broken activation semantics.
- Suggested validation:
  - Insert two rows for the same `service_id`/`version_number` with `tenant=NULL` and `environment=NULL`, or two active unscoped rows for the same service, and observe that the DB accepts them while repository reads become ambiguous.

### BUG-219 — Service summary `tool_count` counts disabled operations, so the UI can overstate how many tools a service actually exposes (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_api/repository.py`
  - `libs/ir/models.py`
  - `libs/generator/generic_mode.py`
  - `apps/web-ui/src/app/(dashboard)/page.tsx`
- Summary:
  - Service summaries compute `tool_count` as the raw length of `service_ir.operations`.
  - Individual operations have an `enabled` flag.
  - Runtime capability generation filters out disabled operations.
  - Dashboard/service metrics can therefore claim more tools than the service actually exposes.
- Evidence:
  - `Operation` in `libs/ir/models.py` defines `enabled: bool = True`.
  - `ServiceCatalogRepository._to_service_summary()` sets `tool_count=len(service_ir.operations)`.
  - `build_capability_manifest()` generates tools with `for op in service_ir.operations if op.enabled`.
  - The dashboard totals tools from `service.tool_count`.
- Why it is a bug:
  - Operators are shown inflated tool counts whenever an IR keeps disabled operations around for history or staged rollout.
- Suggested validation:
  - Create a service IR with some operations marked `enabled=false`, then compare the service summary/dashboard count with the runtime capability manifest.

### BUG-220 — Compiler API silently stops publishing artifact routes when `ACCESS_CONTROL_URL` is unset (Agent c fixed)

- Severity: High
- Files:
  - `apps/compiler_api/route_publisher.py`
  - `apps/compiler_api/routes/artifacts.py`
  - `apps/compiler_api/tests/test_route_publisher.py`
- Summary:
  - The compiler API resolves its default artifact route publisher from `ACCESS_CONTROL_URL`.
  - When that environment variable is unset or blank, the resolver returns `NoopArtifactRoutePublisher`.
  - Artifact activation/deletion still runs the publisher path, appends audit rows, and commits the transaction.
  - The unit tests explicitly lock in the no-op publisher as the default behavior.
- Evidence:
  - `_resolve_default_route_publisher()` returns `NoopArtifactRoutePublisher()` when `ACCESS_CONTROL_URL` is empty.
  - `activate_artifact_version()` calls `await route_publisher.sync(...)` and then `await session.commit()`.
  - `delete_artifact_version()` calls `route_publisher.delete()/sync()` and then commits.
  - `apps/compiler_api/tests/test_route_publisher.py` asserts that the default resolution/caching path returns `NoopArtifactRoutePublisher`.
- Why it is a bug:
  - Artifact version changes can report success and become durable in the registry while access-control/gateway routes are never updated.
- Suggested validation:
  - Start compiler API without `ACCESS_CONTROL_URL`, activate or delete a version with `route_config`, and compare the committed DB state with the unchanged gateway/access-control routes.

### BUG-221 — MCP runtime disables DNS rebinding protection by default (Agent b fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/loader.py`
- Summary:
  - `create_runtime_server()` reads `MCP_DISABLE_DNS_REBINDING_PROTECTION`.
  - The default value is the string `"true"`.
  - That default is inverted into `enable_dns_rebinding_protection=False`.
  - Operators only get host-header rebinding protection if they explicitly opt back in.
- Evidence:
  - `disable_rebinding_protection = os.getenv("MCP_DISABLE_DNS_REBINDING_PROTECTION", "true") ...`
  - `TransportSecuritySettings(enable_dns_rebinding_protection=not disable_rebinding_protection)`
  - The adjacent comment says this is a convenience default for Kubernetes/DNS usage rather than a safe default for direct exposure.
- Why it is a bug:
  - A runtime that is exposed directly to untrusted clients becomes less safe out of the box and accepts an insecure transport posture unless operators discover and override the flag.
- Suggested validation:
  - Start the runtime without setting `MCP_DISABLE_DNS_REBINDING_PROTECTION`, inspect the resolved FastMCP transport security settings, and probe it with non-localhost Host headers that would normally be rejected.

### BUG-222 — Pre-deploy OAuth2 auth smoke uses `GET` and treats `400`/`401`/`405`/`3xx` token endpoints as healthy (Agent A fixed)

- Severity: High
- Files:
  - `libs/validator/pre_deploy.py`
  - `libs/validator/tests/test_pre_deploy.py`
- Summary:
  - The validator checks OAuth2 reachability with `GET token_url`.
  - It only marks the endpoint unhealthy for `404` or `>=500`.
  - Any other response code is treated as a pass.
  - Real client-credentials token endpoints commonly require `POST` and often return `400` or `405` to `GET`.
- Evidence:
  - `_validate_oauth2_endpoint()` performs `response = await self._client.get(token_url)`.
  - The only explicit failure status guard is `if response.status_code == 404 or response.status_code >= 500`.
  - A local probe using a mock token endpoint that always returns `405` produced `auth_smoke.passed == True` with details `OAuth2 client credentials endpoint reachable: HTTP 405.`
- Why it is a bug:
  - The validator can green-light a broken token endpoint configuration even though the runtime will later fail when it performs the real `POST` token exchange.
- Suggested validation:
  - Point `oauth2.token_url` at an endpoint that returns `405 Method Not Allowed` to `GET` but requires `POST`, and compare the validator result with an actual runtime OAuth2 tool invocation.

### BUG-223 — Pre-deploy auth smoke never verifies that advanced runtime secret refs actually resolve (Agent A fixed)

- Severity: High
- Files:
  - `libs/validator/pre_deploy.py`
  - `apps/mcp_runtime/proxy.py`
  - `libs/validator/tests/test_pre_deploy.py`
- Summary:
  - Nested OAuth2 client-credentials auth only checks that `token_url` is reachable.
  - mTLS and request-signing validation only append informational text saying the references are configured.
  - The validator never resolves `client_id_ref`, `client_secret_ref`, `cert_ref`, `key_ref`, `ca_ref`, or `request_signing.secret_ref`.
  - The runtime resolves all of those refs at call time and raises immediately if they are missing.
- Evidence:
  - `_validate_oauth2_endpoint()` never touches `auth.oauth2.client_id_ref` or `.client_secret_ref`.
  - `_validate_auth_smoke()` only appends `mTLS certificate references configured.` and `Request signing configuration present.`
  - `RuntimeProxy._resolve_secret_ref()` raises `ToolError` when the referenced env vars are absent.
  - Local reproduction with missing OAuth2, mTLS, and signing refs still returned `auth_smoke.passed == True`.
- Why it is a bug:
  - Pre-deploy validation can pass a deployment where every authenticated runtime call will fail on the first request because the required secrets do not exist.
- Suggested validation:
  - Build an IR that uses nested OAuth2, mTLS, or request signing with nonexistent secret refs, run pre-deploy validation, then invoke the runtime and observe the immediate secret-resolution failure.

### BUG-224 — The runtime silently drops dynamic MCP resources even though the IR schema and tests accept them (Agent A fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/loader.py`
  - `libs/ir/models.py`
  - `libs/ir/tests/test_resource_prompt_models.py`
  - `tests/integration/test_mcp_runtime_resources_prompts.py`
- Summary:
  - `ResourceDefinition` explicitly models `content_type="dynamic"` plus `operation_id`.
  - IR tests and integration fixtures construct dynamic resources as valid payloads.
  - `register_ir_resources()` does not reject them at load time.
  - Instead it logs and skips every non-static resource.
- Evidence:
  - `ResourceDefinition.content_type` allows `"static"` and `"dynamic"`.
  - `libs/ir/tests/test_resource_prompt_models.py` includes `test_resource_dynamic_with_operation_id`.
  - `tests/integration/test_mcp_runtime_resources_prompts.py` includes `test_dynamic_resources_skipped`.
  - A local probe emitted `Skipping non-static resource 'R2' (content_type=dynamic)` and registered/listed zero resources.
- Why it is a bug:
  - A valid IR capability disappears from the runtime without any schema failure or startup failure, so operators can think a dynamic resource was deployed when it is actually unavailable.
- Suggested validation:
  - Load a ServiceIR with one dynamic resource, call `/tools` or `list_resources`, and observe that the resource is missing even though the IR validated successfully.

### BUG-225 — Static MCP resources with missing content are registered as empty strings instead of being rejected (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/mcp_runtime/loader.py`
  - `libs/ir/models.py`
  - `libs/ir/tests/test_resource_prompt_models.py`
- Summary:
  - Static resources default to `content_type="static"` while `content` is optional.
  - There is no model validator requiring `content` when `content_type` is static.
  - The runtime coerces `None` content to `""` during registration.
  - The resource therefore appears healthy but serves an empty payload.
- Evidence:
  - `ResourceDefinition` defaults are `content_type="static"` and `content: str | None = None`.
  - `libs/ir/tests/test_resource_prompt_models.py` asserts that the default static resource shape allows `content is None`.
  - `register_ir_resources()` sets `static_content = resource_def.content or ""`.
  - A local probe registered the resource successfully and `read_resource()` returned `''`.
- Why it is a bug:
  - Broken static resource definitions degrade into silent empty responses instead of failing fast during validation or startup.
- Suggested validation:
  - Define a static resource with `content=None`, start the runtime, and read the resource to confirm it returns an empty string rather than raising a validation error.

### BUG-226 — Duplicate prompt names are not validated and FastMCP keeps only the first prompt (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/ir/models.py`
  - `apps/mcp_runtime/loader.py`
- Summary:
  - `ServiceIR` only enforces unique prompt definition IDs.
  - The runtime registers prompts under `Prompt(name=prompt_def.name, ...)`, not by IR id.
  - FastMCP treats prompt names as unique and keeps the first one.
  - The second prompt is silently dropped except for a log line.
- Evidence:
  - `ServiceIR.prompt_definition_ids_must_be_unique()` only checks `PromptDefinition.id`.
  - `register_ir_prompts()` creates each runtime prompt with `name=prompt_def.name`.
  - A local probe adding two prompts named `Same Prompt` logged `Prompt already exists: Same Prompt`, `list_prompts()` returned one prompt, and `get_prompt("Same Prompt", {})` resolved to the first definition.
- Why it is a bug:
  - Two distinct prompt definitions can validate successfully in IR form but collapse to one prompt at runtime, causing missing or stale prompt behavior.
- Suggested validation:
  - Create two `PromptDefinition` entries with different IDs but the same `name`, then load the runtime and compare the number of prompt definitions in the IR versus `list_prompts()`.

### BUG-227 — Duplicate resource URIs are not validated and FastMCP keeps only the first resource (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/ir/models.py`
  - `apps/mcp_runtime/loader.py`
- Summary:
  - `ServiceIR` only enforces unique resource definition IDs.
  - The runtime registers resources by `uri`.
  - FastMCP treats resource URIs as unique and keeps only the first registration.
  - The later resource silently disappears except for a log message.
- Evidence:
  - `ServiceIR.resource_definition_ids_must_be_unique()` checks `ResourceDefinition.id`, not `uri`.
  - `register_ir_resources()` creates `FunctionResource(uri=resource_def.uri, ...)`.
  - A local probe adding two resources with URI `service:///dup` logged `Resource already exists: service:///dup`, `list_resources()` returned one resource, and `read_resource("service:///dup")` returned the first content only.
- Why it is a bug:
  - IR validation can succeed even though multiple resource definitions collapse into a single runtime resource.
- Suggested validation:
  - Create two `ResourceDefinition` entries with different IDs but the same `uri`, then load the runtime and compare the IR resource count with `list_resources()`.

### BUG-228 — Drift detection ignores auth configuration changes unless the auth type itself changes (Agent c fixed)

- Severity: High
- Files:
  - `libs/validator/drift.py`
  - `libs/validator/tests/test_drift.py`
  - `libs/ir/models.py`
- Summary:
  - Schema-level drift comparison only checks `base_url`, `auth.type`, and `service_name`.
  - Changes to header names/prefixes, secret refs, API-key placement, OAuth2 config, mTLS config, and request-signing config are ignored.
  - The provided drift tests only assert detection for `auth type changed`, not deeper auth drift.
- Evidence:
  - `_compare_schema()` only appends changes for `base_url`, `auth.type`, and `service_name`.
  - `test_drift.py` covers `auth type changed` but has no cases for `runtime_secret_ref`, `header_name`, or nested auth config changes.
  - A local probe changing `runtime_secret_ref` and `header_name` between deployed/live IRs produced `schema_changes == []`.
- Why it is a bug:
  - A deployment can switch credentials or auth-header semantics without any drift alert even though runtime behavior changed materially.
- Suggested validation:
  - Compare two otherwise identical IRs that both use `AuthType.bearer` but differ in `runtime_secret_ref` and `header_name`, and verify that `detect_drift()` currently reports no schema changes.

### BUG-229 — Drift detection ignores parameter `required` and `default` changes (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/validator/drift.py`
  - `libs/ir/models.py`
- Summary:
  - Parameter comparison only detects added names, removed names, and type changes.
  - It does not compare `required`, `default`, descriptions, or other contract semantics.
  - Requiredness/default flips therefore disappear from the drift report.
- Evidence:
  - `_compare_params()` only inspects parameter presence and `type`.
  - A local probe comparing the same parameter name/type with `required=False, default=10` versus `required=True, default=None` produced `modified_operations == []`.
- Why it is a bug:
  - Tool callers can break when a formerly optional parameter becomes required (or its default changes), yet scheduled drift checks will still report no operation change.
- Suggested validation:
  - Compare deployed/live IRs with identical parameter names and types but different `required`/`default` values, and confirm that `detect_drift()` misses the change.

### BUG-230 — Black-box validation fails to emit `no_operations_extracted` when the IR only contains disabled operations (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/validator/black_box.py`
  - `libs/ir/models.py`
- Summary:
  - `evaluate_black_box()` excludes disabled operations when computing matches and `discovered_operations`.
  - But it passes the full `ir.operations` list into `_identify_failure_patterns()`.
  - That helper only emits `no_operations_extracted` when `not ops`.
  - An IR whose operations all exist but are disabled therefore gets zero discovered operations and no matching failure pattern.
- Evidence:
  - Matching/coverage loops use `if not op.enabled: continue`.
  - Failure-pattern identification is called with `list(ir.operations)`, not the filtered enabled list.
  - A local probe with one disabled operation produced `discovered_operations 0` and `failure_patterns []`.
- Why it is a bug:
  - Extraction runs that auto-disable every discovered operation are not surfaced as the “nothing usable was extracted” failure mode, which hides a meaningful discovery failure signal.
- Suggested validation:
  - Run `evaluate_black_box()` against a nonempty ground-truth set and an IR where all operations are disabled, then inspect the empty `failure_patterns` result.

### BUG-231 — Runtime response parsing treats mixed-case JSON content-types as plain text instead of JSON (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - `_parse_response_payload()` first checks `if "json" in content_type` before lowercasing the header.
  - A response such as `Content-Type: Application/JSON` misses that branch.
  - The later textual-content fallback returns `response.text` rather than parsed JSON.
  - Protocol-specific error extraction that expects a JSON object then stops working.
- Evidence:
  - `_parse_response_payload()` does a case-sensitive `"json" in content_type` test, then lowercases only afterward.
  - A local probe with `Content-Type: Application/JSON` and body `{"ok": true}` returned a Python `str`, not a parsed `dict`.
- Why it is a bug:
  - Valid but differently cased JSON responses can bypass JSON parsing, which breaks GraphQL/JSON-RPC/OData/SCIM error extraction and any downstream logic that expects structured JSON.
- Suggested validation:
  - Return a GraphQL or JSON-RPC error payload with `Content-Type: Application/JSON` and observe that the runtime returns plain text instead of protocol-aware parsed errors.

### BUG-232 — Async job polling returns `303` redirects as final results instead of following them (Agent c fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - The runtime's shared `httpx.AsyncClient` is created without `follow_redirects=True`.
  - `_poll_async_job()` treats any nonpending response as final and returns it unchanged.
  - A redirecting status URL therefore short-circuits polling.
  - The tool call can complete with `status="ok"` and an empty redirect body instead of the real terminal job result.
- Evidence:
  - `_get_client()` constructs `httpx.AsyncClient(**client_kwargs)` with no redirect setting.
  - `_poll_async_job()` only loops on pending status codes/values and otherwise returns `poll_response`.
  - A local probe where `/jobs/1` returned `303 Location: /jobs/1/result` produced `upstream_status 303`, never requested `/jobs/1/result`, and returned an empty binary payload as the final result.
- Why it is a bug:
  - Async-job APIs that redirect from a polling endpoint to the final artifact/status endpoint are reported as successful without ever fetching the actual result.
- Suggested validation:
  - Use an async-job endpoint whose status URL returns `303 See Other` to the final result URL, then compare the runtime result with a client that follows redirects.

### BUG-233 — Helm deploys `gateway-admin-mock` from the access-control image instead of its own image slot (Agent b fixed)

- Severity: Medium
- Files:
  - `deploy/helm/tool-compiler/templates/apps.yaml`
  - `deploy/helm/tool-compiler/values.yaml`
  - `tests/contract/test_observability_and_helm_assets.py`
- Summary:
  - The Helm template for the `gateway-admin-mock` deployment uses `.Values.images.accessControl.*`.
  - `values.yaml` does not define a dedicated `images.gatewayAdminMock` block.
  - The mock can therefore only track the access-control image tag/repository/pull policy.
  - Contract tests currently verify the template exists but not that it uses the correct image slot.
- Evidence:
  - `apps.yaml` sets the container image to `"{{ .Values.images.accessControl.repository }}:{{ .Values.images.accessControl.tag }}"`.
  - The same block also uses `.Values.images.accessControl.pullPolicy`.
  - `values.yaml` contains image entries for `compilerApi`, `migrations`, `accessControl`, `compilerWorker`, `mcpRuntime`, etc., but none for `gatewayAdminMock`.
- Why it is a bug:
  - The chart cannot independently version or harden the mock service, and any future divergence between the access-control image and the mock runtime will break `gateway-admin-mock` deployments.
- Suggested validation:
  - Enable `gatewayAdminMock` in Helm values, inspect the rendered manifest, and confirm that changing the access-control image tag also changes the mock deployment image even though they are separate components.

### BUG-234 — The gateway admin mock ignores stored route `match` documents and routes only by derived route ID (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/gateway_admin_mock/main.py`
  - `tests/integration/test_access_control_gateway_binding.py`
  - `tests/integration/test_compiler_api.py`
- Summary:
  - The mock gateway chooses a route solely from `/gateway/{service_id}` plus the optional `x-tool-compiler-version` header.
  - It forwards requests using only `route_document["target_service"]`.
  - It never evaluates `route_document["match"]`.
  - Compiler/API integration fixtures already build route configs that include concrete `match` clauses such as `prefix` and version headers.
- Evidence:
  - `_select_route_id()` returns `{service_id}-active` or `{service_id}-v{version}` from the request path/header only.
  - `_forward_request()` reads `target_service` from the route document and never consults `match`.
  - `tests/integration/test_access_control_gateway_binding.py` and `tests/integration/test_compiler_api.py` create route configs with `match` sections.
  - The gateway mock tests only assert the route-ID selection behavior and do not assert `match` evaluation.
- Why it is a bug:
  - Local/integration tests can go green even when generated route match conditions are wrong, because the mock bypasses the route-matching logic entirely and forwards based on derived route ID.
- Suggested validation:
  - Store a route document whose `match` would not satisfy the incoming request and observe that `/gateway/{service_id}` still forwards successfully as long as the derived route ID exists.

### BUG-235 — Post-deploy validation crashes on an invalid `expected_ir` payload instead of returning a failed report (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/validator/post_deploy.py`
- Summary:
  - `PreDeployValidator` converts schema problems into a structured failed `ValidationReport`.
  - `PostDeployValidator.validate()` and `.validate_with_audit()` do not.
  - They call `ServiceIR.model_validate(expected_ir)` before any error handling.
  - A malformed expected IR therefore aborts the whole validation call with an exception.
- Evidence:
  - `validate()` resolves `service_ir = ... else ServiceIR.model_validate(expected_ir)` outside any `try/except`.
  - `validate_with_audit()` does the same.
  - A local probe calling `PostDeployValidator.validate(..., {"protocol": "openapi"})` raised a raw `ValidationError` for missing `source_hash`, `service_name`, and `base_url`.
- Why it is a bug:
  - Post-deploy validation has a success/failure report API, but malformed expected IR input escapes as an exception instead of a normal failed validation result.
- Suggested validation:
  - Pass an incomplete IR dictionary into `PostDeployValidator.validate()` and confirm that the current implementation raises instead of returning a report with `stage="schema"` or equivalent failure details.

### BUG-236 — Post-deploy validation crashes when `/tools` returns JSON that is not an object (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/validator/post_deploy.py`
- Summary:
  - `_validate_tool_listing()` catches non-JSON responses.
  - But once `response.json()` succeeds, it assumes the decoded payload is a mapping.
  - It immediately calls `payload.get("tools", [])`.
  - A runtime that returns JSON with the wrong top-level shape crashes validation instead of producing a failed tool-listing result.
- Evidence:
  - `_validate_tool_listing()` has a `try/except` around `response.json()` but no `isinstance(payload, dict)` guard before `payload.get(...)`.
  - A local probe with `/tools` returning `["not", "an", "object"]` raised `AttributeError: 'list' object has no attribute 'get'`.
- Why it is a bug:
  - A malformed but JSON-encoded `/tools` response should become a deterministic validation failure, not an uncaught exception.
- Suggested validation:
  - Serve `/tools` as a JSON array or string and confirm that `PostDeployValidator.validate()` currently aborts with `AttributeError` instead of returning `tool_listing.passed=False`.

### BUG-237 — `validate_with_audit()` can report `overall_passed=True` even when the audit found failed generated tools (Agent c fixed)

- Severity: High
- Files:
  - `libs/validator/post_deploy.py`
  - `libs/validator/audit.py`
  - `libs/validator/tests/test_post_deploy.py`
- Summary:
  - `validate_with_audit()` returns both a standard `ValidationReport` and a `ToolAuditSummary`.
  - But `report.overall_passed` is computed only from `health`, `tool_listing`, and `invocation_smoke`.
  - Audit failures do not feed into `overall_passed`.
  - Callers who inspect only the returned report can see a green deployment while the audit summary says some generated tools failed.
- Evidence:
  - `validate_with_audit()` builds `results = [health_result, tool_listing_result, invocation_result]`.
  - `report.overall_passed=all(result.passed for result in results)` excludes `audit_summary`.
  - A local probe with two tools where smoke passed on `getX` but audit failed on `postY` returned `overall_passed True` and `audit_failed 1`.
- Why it is a bug:
  - The top-level post-deploy report can misrepresent the rollout as successful even though the accompanying audit already proved that at least one generated tool is broken.
- Suggested validation:
  - Run `validate_with_audit()` on a service where one safe smoke tool succeeds but another audited tool returns `status="error"`, then compare `report.overall_passed` with `audit_summary.failed`.

### BUG-238 — Capability-matrix lookup crashes on unknown protocol strings even though `ServiceIR.protocol` is unconstrained (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/capability_matrix.py`
  - `libs/ir/models.py`
- Summary:
  - `ServiceIR.protocol` is a free-form `str`.
  - `protocol_capability_for_service()` looks up `_CAPABILITY_ROWS[protocol_capability_key(service_ir)]`.
  - There is no fallback row or graceful error for unknown protocol names.
  - An IR with a new or mistyped protocol therefore crashes capability reporting with `KeyError`.
- Evidence:
  - `ServiceIR.protocol: str = Field(description="openapi, rest, graphql, sql, etc.")`
  - `protocol_capability_for_service()` directly indexes `_CAPABILITY_ROWS[...]`.
  - A local probe with `protocol="custom-proto"` raised `KeyError 'custom-proto'`.
- Why it is a bug:
  - Capability reporting is coupled to a hard-coded protocol table even though the IR model itself accepts arbitrary protocol strings, so unexpected protocols fail at reporting time instead of degrading gracefully.
- Suggested validation:
  - Construct a valid `ServiceIR` with an unrecognized `protocol` string and call `protocol_capability_for_service()`.

### BUG-239 — Capability reporting overclaims full `grpc_stream` support for bidirectional/client streaming IRs that pre-deploy validation rejects (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/capability_matrix.py`
  - `libs/validator/pre_deploy.py`
  - `libs/ir/models.py`
- Summary:
  - Capability selection treats any `grpc_stream` descriptor with `support=supported` as the `grpc_stream` row.
  - That row advertises `runtime=True`, `live_proof=True`, and `llm_e2e=True`.
  - Pre-deploy validation only allows native grpc streaming when `allow_native_grpc_stream` is enabled and the stream mode is `server`.
  - Bidirectional/client streaming IRs therefore inherit an overly optimistic capability row.
- Evidence:
  - `protocol_capability_key()` checks only `descriptor.transport is EventTransport.grpc_stream` and `descriptor.support is supported`.
  - `PreDeployValidator._validate_event_support()` explicitly fails `grpc_stream` descriptors whose mode is not `GrpcStreamMode.server`.
  - A local probe with a `bidirectional` supported grpc_stream descriptor returned capability row `grpc_stream` with `runtime True`, `live_proof True`, and `llm_e2e True`.
- Why it is a bug:
  - Concrete IRs can be reported as fully supported grpc_stream services even though the validator/runtime path rejects their stream mode.
- Suggested validation:
  - Build a `grpc` IR with a supported bidirectional grpc_stream descriptor and compare `protocol_capability_for_service()` with `PreDeployValidator(... allow_native_grpc_stream=True).validate(...)`.

### BUG-240 — The IR model allows multiple streaming descriptors for one operation, but the runtime rejects that operation as ambiguous (Agent c fixed)

- Severity: High
- Files:
  - `libs/ir/models.py`
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - `ServiceIR` validates only that event descriptors reference existing operations.
  - It does not enforce “at most one supported streaming descriptor per operation”.
  - The runtime does enforce that rule at invocation time.
  - As a result, an IR can validate and deploy but expose a tool that can never be invoked.
- Evidence:
  - `ServiceIR.event_descriptors_must_reference_valid_operations()` checks only that referenced operation IDs exist.
  - `RuntimeProxy._stream_descriptor_for_operation()` raises `ToolError` when `len(descriptors) > 1`.
  - A local probe with one operation bound to both SSE and WebSocket descriptors validated as IR but failed at runtime with `Operation watch has multiple streaming descriptors and cannot be invoked unambiguously.`
- Why it is a bug:
  - Schema validation and deployment-time handling accept an IR shape that the runtime itself treats as invalid during the first real tool call.
- Suggested validation:
  - Create one operation with two supported streaming descriptors, validate the IR, then invoke the tool through `RuntimeProxy` and observe the ambiguity error.

### BUG-241 — gRPC rpc-path parsing accepts malformed paths with extra slash segments instead of rejecting them (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/mcp_runtime/grpc_unary.py`
  - `apps/mcp_runtime/grpc_stream.py`
  - `apps/mcp_runtime/tests/test_grpc_unary.py`
- Summary:
  - The native gRPC helpers derive a method full name by stripping the leading slash and splitting on the first remaining slash only.
  - Paths like `/pkg.Svc/Method/Extra` are accepted.
  - The resulting method name still contains `/Extra`.
  - That malformed rpc-path survives helper validation and only fails later during descriptor lookup/invocation.
- Evidence:
  - Both `_method_full_name()` helpers use `trimmed.partition("/")`.
  - `apps/mcp_runtime/tests/test_grpc_unary.py` explicitly asserts that `"/pkg.Svc/Method/Extra"` becomes `"pkg.Svc.Method/Extra"`.
  - A local probe showed the unary and stream helpers returning `pkg.Svc.Method/Extra` and `pkg.Svc.Watch/Extra` respectively.
- Why it is a bug:
  - Invalid gRPC rpc paths are not rejected at the boundary, so configuration mistakes turn into deeper reflection/invocation failures with less actionable error messages.
- Suggested validation:
  - Configure a unary or grpc_stream operation with an rpc path containing an extra slash segment and observe that helper validation passes even though the path is malformed.

### BUG-242 — Native gRPC executors drop explicit `null` fields when tool arguments are supplied as flat params (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/mcp_runtime/grpc_unary.py`
  - `apps/mcp_runtime/grpc_stream.py`
  - `apps/mcp_runtime/tests/test_grpc_unary.py`
  - `apps/mcp_runtime/tests/test_grpc_stream.py`
- Summary:
  - When callers do not pass a top-level `payload` object, both native gRPC executors synthesize the request body from flat tool arguments.
  - That path filters out all `None` values.
  - The unit tests lock in the behavior.
  - Callers therefore cannot intentionally send `null`/unset wrapper fields via the flat-argument interface.
- Evidence:
  - Both `_request_payload()` helpers return `{key: value for key, value in arguments.items() if value is not None}`.
  - `apps/mcp_runtime/tests/test_grpc_unary.py` and `test_grpc_stream.py` assert that `{"field1": "value1", "field2": None}` becomes `{"field1": "value1"}`.
  - A local probe confirmed `unary_null_drop {'field1': 'value1'}` and `stream_null_drop {'field1': 'value1'}`.
- Why it is a bug:
  - Some protobuf contracts rely on explicit null/clearing semantics or wrapper presence, but the flat tool argument path silently removes those fields before serialization.
- Suggested validation:
  - Define a gRPC method with optional/wrapper fields, invoke it through the runtime with a flat argument set containing `None`, and compare the serialized request with one built from an explicit `payload` object.

### BUG-243 — SQL runtime silently ignores filter columns that are missing from the reflected table, widening the query (Agent b fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/sql.py`
  - `apps/mcp_runtime/tests/test_sql_executor.py`
- Summary:
  - SQL query execution iterates the IR's declared `filterable_columns`.
  - If a requested filter column is absent from the reflected table, the runtime quietly skips it.
  - The query still executes and returns rows.
  - The current unit tests explicitly encode the skip behavior.
- Evidence:
  - `_query()` does `column = table.c.get(column_name)` and `if column is None: continue`.
  - `apps/mcp_runtime/tests/test_sql_executor.py::test_column_not_in_table_skips_filter` asserts that the query still succeeds when one filter column is missing.
- Why it is a bug:
  - A stale IR or drifted database schema can silently drop caller-supplied filters and return a broader result set than requested, which is both correctness and data-exposure risk.
- Suggested validation:
  - Reflect a table that no longer contains one of the IR's `filterable_columns`, invoke the generated query tool with that filter, and observe that the runtime returns unfiltered results instead of failing fast.

### BUG-244 — SQL insert execution strips explicit `None` values, so callers cannot insert SQL `NULL`s (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/mcp_runtime/sql.py`
- Summary:
  - SQL insert execution builds its `values` dictionary only from arguments that are not `None`.
  - Columns passed explicitly as `None` are omitted from the insert entirely.
  - This is different from intentionally inserting `NULL`.
  - Nullable columns therefore cannot be set to `NULL` through the generated insert tool unless the DB default happens to match.
- Evidence:
  - `_insert()` builds `values = { ... if column_name in arguments and arguments[column_name] is not None }`.
  - A local probe invoking an insert with `{"name": "Alice", "nickname": None}` produced `values_kwargs {'name': 'Alice'}`.
- Why it is a bug:
  - The generated SQL insert tool cannot express an explicit SQL `NULL`, which breaks contracts where `NULL` is semantically different from “omit the column and use the default”.
- Suggested validation:
  - Create a nullable insertable column with a non-null default, invoke the generated insert tool with that column set to `None`, and compare the stored row with one inserted using explicit SQL `NULL`.

### BUG-245 — `setup_tracer()` permanently locks in the first service name for the entire process (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/observability/tracing.py`
  - `apps/mcp_runtime/main.py`
- Summary:
  - Tracer configuration is stored in module-level globals.
  - Once `_is_configured` and `_tracer_provider` are set, later `setup_tracer()` calls return immediately.
  - The resource attributes therefore stay bound to the first configured `service.name`.
  - Any later runtime/component in the same process inherits the wrong tracing identity.
- Evidence:
  - `setup_tracer()` begins with `if _is_configured and _tracer_provider is not None: return`.
  - `build_runtime_state()` calls `setup_tracer(service_ir.service_name, enable_local=True)`.
  - A local probe calling `setup_tracer("svc-a")` and then `setup_tracer("svc-b")` reported `tracing_service_names svc-a svc-a`.
- Why it is a bug:
  - Multi-service test runs or long-lived worker processes can emit spans labeled as the wrong service, which breaks trace attribution and observability debugging.
- Suggested validation:
  - Configure tracing twice in one process with two different service names and inspect the provider resource attributes and emitted spans.

### BUG-246 — `reset_metrics()` does not unregister collectors, so metric recreation still fails with duplicate-timeseries errors (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/observability/metrics.py`
- Summary:
  - `reset_metrics()` advertises itself as useful for testing.
  - But it only clears the in-memory `_registered_metrics` cache.
  - It does not remove the existing collectors from the underlying Prometheus registry.
  - Recreating the same metric name on the same registry after a reset still raises duplication errors.
- Evidence:
  - `reset_metrics()` only does `_registered_metrics.clear()`.
  - A local probe creating `demo_counter_total`, calling `reset_metrics()`, and then creating the same counter again on the same `CollectorRegistry` raised `ValueError: Duplicated timeseries in CollectorRegistry`.
- Why it is a bug:
  - The helper claims to reset metrics for tests, but it leaves the registry dirty and causes the exact duplicate-registration failures that the cache is supposed to avoid.
- Suggested validation:
  - Create a metric on a fresh `CollectorRegistry`, call `reset_metrics()`, then create the same metric again on that registry and observe the duplicate-timeseries failure.

### BUG-247 — Metrics helper caching ignores label sets, so same-name metrics can silently reuse the wrong label schema (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/observability/metrics.py`
- Summary:
  - Metrics are cached by `(id(registry), name)` only.
  - Label names are not part of the cache key.
  - Recreating the same metric name with different labels returns the original collector.
  - Callers therefore get a metric object whose label schema does not match the requested one.
- Evidence:
  - `_metric_key()` returns `(id(registry), name)`.
  - `create_counter()/create_histogram()/create_gauge()` all return the cached collector if the key matches.
  - A local probe creating `same_metric_total` first with labels `['a']` and then with labels `['b']` returned the same object and preserved `labelnames ('a',)`.
- Why it is a bug:
  - Components can believe they registered a metric with one label contract while the runtime actually reuses an older collector with different labels, leading to confusing label errors or silently wrong instrumentation.
- Suggested validation:
  - In one registry, call `create_counter()` twice with the same metric name but different label lists and inspect the returned object's label names.

### BUG-248 — Metrics helper caching ignores metric type, so `create_gauge()` can return a `Counter` (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/observability/metrics.py`
- Summary:
  - The metrics cache key distinguishes only registry ID and metric name.
  - Metric type is not part of the key.
  - If a counter already exists under a given name, `create_gauge()` (or `create_histogram()`) returns that existing counter object.
  - The caller receives the wrong collector class without any error.
- Evidence:
  - `create_counter()`, `create_histogram()`, and `create_gauge()` all share the same `_registered_metrics` map keyed only by name/registry.
  - A local probe created `same_metric_total` as a counter and then called `create_gauge("same_metric_total", ...)`; the second call returned the exact same object and `metric3_type Counter`.
- Why it is a bug:
  - Instrumentation code can believe it is updating a gauge or histogram while it is actually holding a counter, producing broken metrics behavior that is hard to diagnose.
- Suggested validation:
  - Create a counter for a name, then call `create_gauge()` for the same name on the same registry and inspect the returned object's type.

### BUG-249 — `setup_logging()` wipes existing root handlers, deleting host-process logging sinks (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/observability/logging.py`
- Summary:
  - Logging setup is performed on the root logger.
  - The helper clears `root.handlers` unconditionally before installing its own JSON handler.
  - Any handler that the embedding process or test harness installed beforehand is removed.
  - The process is left with only the tool-compiler handler.
- Evidence:
  - `setup_logging()` does `root.handlers.clear()` before `root.addHandler(handler)`.
  - A local probe adding a custom root handler before calling `setup_logging("runtime")` showed `before 1 True` and then `after 1 False`.
- Why it is a bug:
  - Library-style code should not silently delete the host application's logging sinks, because that breaks test capture, file logging, external collectors, and any multi-handler logging configuration.
- Suggested validation:
  - Install a custom root handler in a host process or test, call `setup_logging()`, and confirm that the original handler no longer receives records.

### BUG-250 — Repeated `setup_logging()` calls relabel the whole process to the most recent component (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/observability/logging.py`
- Summary:
  - The component name is stored on the formatter attached to the root logger's only handler.
  - Every new `setup_logging(component=...)` call replaces the root handler/formatter.
  - After that, all logs emitted through the shared root pipeline use the new component label.
  - Earlier components in the same process lose their original identity.
- Evidence:
  - `setup_logging()` always creates a fresh `StructuredFormatter(component=component)` and replaces the root handler set.
  - A local probe calling `setup_logging("component-a")` and then `setup_logging("component-b")` showed the root formatter component changing from `component-a` to `component-b`.
- Why it is a bug:
  - Multi-component processes or tests that initialize logging more than once end up with misattributed log records, making component-level debugging unreliable.
- Suggested validation:
  - Initialize logging for two different components in the same process and inspect emitted log lines after the second initialization.

### BUG-251 — Audit ratio thresholds are skipped entirely when `generated_tools == 0` (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/audit.py`
- Summary:
  - Threshold checking only evaluates `min_audited_ratio` inside `if summary.generated_tools > 0`.
  - When zero tools are generated, ratio validation is silently skipped.
  - A caller that relies only on ratio thresholds can therefore accept a zero-coverage audit.
  - There is no explicit “zero tools is a failure” branch.
- Evidence:
  - `check_thresholds()` wraps the ratio calculation in `if summary.generated_tools > 0:`.
  - A local probe with `generated_tools=0`, `audited_tools=0`, and `AuditThresholds(min_audited_ratio=1.0)` returned `[]` (no violations).
- Why it is a bug:
  - A deployment with zero generated tools can satisfy a strict audit-ratio policy by bypassing the ratio check altogether, which defeats the purpose of using coverage thresholds as a gate.
- Suggested validation:
  - Run `check_thresholds()` with `generated_tools=0` and `min_audited_ratio > 0`, and observe that the current implementation reports no ratio violation.

### BUG-252 — Generic manifest generation uses raw secret-ref strings as `secretKeyRef.key`, even for URL-like refs such as `secret://cid` (Agent A fixed)

- Severity: Medium
- Files:
  - `libs/generator/generic_mode.py`
  - `apps/web-ui/src/components/compilations/__tests__/compilation-wizard.test.ts`
- Summary:
  - Runtime secret env generation sanitizes secret refs into uppercase env var names.
  - But it keeps the original ref string as the Kubernetes `secretKeyRef.key`.
  - Frontend tests already use URL-like secret refs such as `secret://bearer` and `secret://oauth2`.
  - The rendered manifest therefore mixes normalized env names with raw URL-like secret keys.
- Evidence:
  - `_runtime_secret_envs()` returns `{"env_name": _secret_ref_env_name(secret_ref), ..., "secret_key": secret_ref}`.
  - `_secret_ref_env_name()` normalizes non-word characters to `_` and uppercases.
  - Web UI tests include refs like `secret://bearer`, `secret://password`, and `secret://oauth2`.
  - A local probe generated `{'env_name': 'SECRET_CID', 'secret_key': 'secret://cid'}` and `{'env_name': 'SECRET_CSEC', 'secret_key': 'secret://csec'}`.
- Why it is a bug:
  - The manifest and runtime env naming disagree about how secret references are normalized, so URL-like refs used by the UI cannot map cleanly onto the expected Kubernetes secret keys.
- Suggested validation:
  - Compile an IR whose auth config uses `secret://...` refs, render the generic manifests, and inspect the resulting `env[].valueFrom.secretKeyRef.key` values.

### BUG-253 — Generic manifest generation enables native `grpc_stream` runtime for unsupported bidirectional/client stream modes (Agent A fixed)

- Severity: Medium
- Files:
  - `libs/generator/generic_mode.py`
  - `libs/validator/pre_deploy.py`
- Summary:
  - Manifest generation turns on `ENABLE_NATIVE_GRPC_STREAM=true` whenever any supported grpc_stream descriptor exists.
  - That check does not verify the grpc_stream mode.
  - Pre-deploy validation only accepts native grpc_stream in `server` mode.
  - Bidirectional/client stream IRs therefore get deployment-time native-grpc enablement for a mode the validator/runtime path rejects.
- Evidence:
  - `_has_supported_native_grpc_stream()` checks only `transport is grpc_stream` and `support is supported`.
  - `PreDeployValidator._validate_event_support()` fails descriptors whose mode is not `GrpcStreamMode.server`.
  - A local probe with a supported bidirectional grpc_stream descriptor rendered `ENABLE_NATIVE_GRPC_STREAM=true` into the deployment env.
- Why it is a bug:
  - The generator produces a runtime configuration that implies native grpc_stream support even for IRs whose stream mode is not actually supported.
- Suggested validation:
  - Generate manifests for a `grpc` IR with a supported bidirectional grpc_stream descriptor and compare the rendered env vars with the pre-deploy validation result.

### BUG-254 — Generic manifest generation silently omits required runtime secret env wiring when `runtime_secret_name=None` (Agent A fixed)

- Severity: High
- Files:
  - `libs/generator/generic_mode.py`
- Summary:
  - `GenericManifestConfig` allows `runtime_secret_name=None`.
  - `_runtime_secret_envs()` immediately returns `[]` in that case.
  - There is no validation that the ServiceIR actually needs runtime secrets.
  - Authenticated runtimes can therefore be rendered without any secret env wiring at all.
- Evidence:
  - `_runtime_secret_envs()` starts with `if runtime_secret_name is None: return []`.
  - `generate_generic_manifests()` does not reject `runtime_secret_name=None` even when `_runtime_secret_refs(auth)` would be nonempty.
  - A local probe with bearer auth `runtime_secret_ref='billing-secret'` and `runtime_secret_name=None` rendered container env entries containing only `SERVICE_IR_PATH` and `TMPDIR`.
- Why it is a bug:
  - The generator can emit a deployment that looks valid but is guaranteed to fail at runtime because the required auth secrets were never wired into the pod environment.
- Suggested validation:
  - Generate manifests for an authenticated IR with `runtime_secret_name=None`, deploy them, and attempt a runtime tool call that requires the missing secret.

### BUG-255 — Generic manifest NetworkPolicy only allows the upstream API port and can block required OAuth token traffic (Agent A fixed)

- Severity: Medium
- Files:
  - `libs/generator/generic_mode.py`
  - `libs/generator/templates/networkpolicy.yaml.j2`
- Summary:
  - The generated NetworkPolicy always whitelists exactly one TCP egress port derived from `service_ir.base_url`, plus DNS.
  - It does not account for auxiliary auth endpoints such as OAuth2 token URLs.
  - Services whose API and token endpoint use different ports get manifests that block the token exchange required for authentication.
- Evidence:
  - `generate_generic_manifests()` passes only `"upstream_port": _upstream_port(service_ir.base_url)` into the template context.
  - `networkpolicy.yaml.j2` renders a single TCP egress rule for `{{ upstream_port }}` and nothing for auth-specific ports.
  - A local probe with `base_url='http://api.internal:8080'` and `oauth2.token_url='https://auth.example.com/token'` rendered egress rules for TCP `8080` and DNS only.
- Why it is a bug:
  - Authenticated runtimes often need to reach more than the upstream API port. The generated policy can therefore deploy a pod that is guaranteed to fail authentication even though the IR and runtime config are otherwise valid.
- Suggested validation:
  - Generate manifests for an OAuth2-protected service whose `base_url` and `token_url` use different ports, apply the NetworkPolicy, and verify that token requests fail while direct upstream traffic on the allowed port succeeds.

### BUG-256 — Custom manifest labels can override selector labels and make the Deployment select zero pods (Agent A fixed)

- Severity: High
- Files:
  - `libs/generator/generic_mode.py`
- Summary:
  - `generate_generic_manifests()` builds `labels = {**selector_labels, ..., **config.labels}`.
  - The Deployment selector still uses the original `selector_labels`.
  - If callers provide `config.labels` with `app.kubernetes.io/name` or `app.kubernetes.io/instance`, the pod-template labels no longer match the immutable selector.
- Evidence:
  - `selector_labels` is created first and used for `spec.selector.matchLabels`.
  - `labels` is merged with `**config.labels` last and then rendered into the pod template metadata.
  - A local probe with `labels={'app.kubernetes.io/name': 'overridden-name'}` produced `selector.matchLabels['app.kubernetes.io/name'] == 'svc'` while the pod template label became `'overridden-name'`.
- Why it is a bug:
  - The generated Deployment becomes invalid or permanently unready because its selector and pod-template labels disagree.
- Suggested validation:
  - Generate manifests with a caller-supplied `app.kubernetes.io/name` override, apply them to a cluster, and observe that the Deployment does not adopt its own pods.

### BUG-257 — Long `name_suffix` values generate Kubernetes resource names longer than the 63-character DNS label limit (Agent A fixed)

- Severity: High
- Files:
  - `libs/generator/generic_mode.py`
  - `libs/generator/tests/test_generic_mode.py`
- Summary:
  - `_resource_name()` truncates the base service name when a suffix is present, but it never truncates the suffix itself.
  - When `name_suffix` sanitizes to 63 characters, the resulting resource name exceeds the Kubernetes DNS-label limit.
  - The test suite currently codifies that oversized result as the expected behavior.
- Evidence:
  - `_resource_name()` computes `max_base_length = 63 - len(suffix_label) - 1` and returns `f"{trimmed_base}-{suffix_label}"` without bounding the final length.
  - `test_resource_name_falls_back_to_service_when_trimmed_base_is_empty()` asserts that a 63-character suffix returns `service-{suffix}`, which is already 71 characters long.
  - A local probe with `name_suffix='x'*63` generated Deployment/Service/NetworkPolicy names of length `66` and a ConfigMap name of length `69`.
- Why it is a bug:
  - The generator can emit manifests that Kubernetes rejects outright because resource names exceed the allowed DNS-label length.
- Suggested validation:
  - Generate manifests with a 63-character suffix and run `kubectl apply --dry-run=server` (or equivalent schema validation) on the rendered YAML.

### BUG-258 — Distinct secret refs collapse onto the same normalized env var name and become indistinguishable at runtime (Agent A fixed)

- Severity: Medium
- Files:
  - `libs/generator/generic_mode.py`
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - Generic-manifest secret env names are built with `re.sub(r"\W+", "_", secret_ref).upper()`.
  - Runtime secret lookup falls back to the same normalized env name when the raw ref is absent.
  - Distinct secret refs such as `client-id` and `client_id` therefore collapse onto the same environment variable.
- Evidence:
  - `_secret_ref_env_name()` returns `re.sub(r"\W+", "_", secret_ref).upper()`.
  - `_candidate_env_names()` in `apps/mcp_runtime/proxy.py` tries `[secret_ref, normalized]` with the same normalization rule.
  - A local probe of `_runtime_secret_envs()` for `client-id` and `client_id` produced two entries with the same env name: `CLIENT_ID`.
  - A local probe of `RuntimeProxy._resolve_secret_ref()` with only `CLIENT_ID=shared-secret` set resolved both `client-id` and `client_id` to that same secret value.
- Why it is a bug:
  - The system cannot preserve distinct secret references once they normalize to the same env name, so different credentials can silently alias to the same runtime secret.
- Suggested validation:
  - Compile an IR with two different secret refs that normalize to the same env name, render the manifest, and confirm that runtime secret resolution for both refs reads the same environment variable.

### BUG-259 — Drift detection ignores `operation.enabled` changes (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/drift.py`
- Summary:
  - `detect_drift()` compares operations with the same ID via `_compare_operation()`.
  - `_compare_operation()` only checks params, risk level, path, and method.
  - Enabling or disabling an operation therefore produces no drift signal.
- Evidence:
  - `_compare_operation()` has no comparison for `enabled`.
  - A local probe comparing two otherwise identical IRs where only `enabled` changed from `True` to `False` produced `has_drift=False` and empty change lists.
- Why it is a bug:
  - Flipping an operation between enabled and disabled changes the externally exposed tool surface, so drift detection should not treat that as “no change.”
- Suggested validation:
  - Compare deployed/live IRs that differ only in `operation.enabled` and verify that `detect_drift()` currently reports no modifications.

### BUG-260 — Drift detection ignores response-schema changes for existing operations (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/drift.py`
- Summary:
  - `_compare_operation()` does not inspect `response_schema`, `error_schema`, or examples.
  - Existing operations can therefore change their output contract without being reported as drift.
- Evidence:
  - `_compare_operation()` compares only params, risk level, path, and method.
  - A local probe comparing two IRs whose only difference was `response_schema` (`name` field vs `email` field) produced `has_drift=False`.
- Why it is a bug:
  - Output-schema drift is often the most important contract change for downstream agents and clients, so suppressing it yields false “no drift” reports.
- Suggested validation:
  - Compare deployed/live IRs with the same operation IDs but different `response_schema` values and confirm that `detect_drift()` does not report a change.

### BUG-261 — Drift detection ignores resource-definition changes entirely (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/drift.py`
- Summary:
  - `detect_drift()` only compares operations plus a few top-level schema fields.
  - It never compares `resource_definitions`.
  - Resource content or URI changes therefore never show up in drift reports.
- Evidence:
  - `detect_drift()` builds diffs only from `operations` and `_compare_schema()`.
  - `_compare_schema()` checks only `base_url`, `auth.type`, and `service_name`.
  - A local probe changing a resource’s static `content` from `v1` to `v2` produced `has_drift=False`.
- Why it is a bug:
  - MCP resources are part of the runtime capability surface, so changing or removing them should be treated as drift.
- Suggested validation:
  - Compare deployed/live IRs that differ only in `resource_definitions`, such as `content`, `uri`, or `mime_type`, and observe that `detect_drift()` reports no drift.

### BUG-262 — Drift detection ignores prompt-definition changes entirely (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/drift.py`
- Summary:
  - Prompt templates and arguments are never compared during drift detection.
  - A service can ship a materially different prompt catalog while `detect_drift()` still reports no change.
- Evidence:
  - `detect_drift()` never reads `prompt_definitions`.
  - A local probe changing a prompt template from `v1` to `v2` while keeping the rest of the IR identical produced `has_drift=False`.
- Why it is a bug:
  - Prompts are part of the exposed MCP contract; if they change, a “no drift” report is misleading.
- Suggested validation:
  - Compare deployed/live IRs whose only difference is a prompt template or prompt argument definition and confirm that `detect_drift()` returns no changes.

### BUG-263 — Drift detection ignores event-descriptor changes entirely (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/drift.py`
- Summary:
  - Event support metadata is never compared during drift detection.
  - Changes to stream support, direction, channel, or grpc_stream runtime settings are completely invisible.
- Evidence:
  - `detect_drift()` never reads `event_descriptors`.
  - A local probe changing an event descriptor’s `support` from `supported` to `unsupported` produced `has_drift=False`.
- Why it is a bug:
  - Event support is part of the runtime capability contract, so suppressing these changes yields incorrect drift reports for streaming/event-enabled services.
- Suggested validation:
  - Compare deployed/live IRs with identical operations but different `event_descriptors`, then verify that `detect_drift()` still reports no drift.

### BUG-264 — Artifact diff endpoint crashes when either stored version contains invalid `ir_json` (Agent c fixed)

- Severity: High
- Files:
  - `apps/compiler_api/repository.py`
- Summary:
  - `diff_versions()` validates both stored IR payloads with `ServiceIR.model_validate(...)` before computing the diff.
  - That validation is not wrapped.
  - A single malformed stored version therefore aborts the diff endpoint with an unhandled `ValidationError`.
- Evidence:
  - `diff_versions()` calls `ServiceIR.model_validate(from_record.ir_json)` and `ServiceIR.model_validate(to_record.ir_json)` directly.
  - A local probe using one invalid stored IR payload (`{'protocol': 'openapi'}`) caused `ArtifactRegistryRepository.diff_versions()` to raise `ValidationError`.
- Why it is a bug:
  - Operators cannot inspect or recover from bad stored versions through the diff API because the endpoint crashes instead of returning a structured failure.
- Suggested validation:
  - Store one malformed service version, then call the diff endpoint against a valid version and observe the unhandled validation error.

### BUG-265 — Artifact diff reports `no changes` for top-level ServiceIR changes such as `base_url` (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/ir/diff.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - `compute_diff()` only compares operations.
  - It never compares top-level IR fields like `base_url`, `service_name`, `protocol`, or auth config.
  - The registry diff endpoint can therefore claim two versions are identical even when the deployment target itself changed.
- Evidence:
  - `compute_diff()` builds maps from `old.operations` and `new.operations` and derives `IRDiff` entirely from those maps.
  - A local probe changing only `base_url` from `https://old.example.com` to `https://new.example.com` returned `summary='no changes'` and `is_empty=True`.
- Why it is a bug:
  - Top-level IR fields materially change how the runtime behaves, so a diff that suppresses them gives false confidence during review or rollback decisions.
- Suggested validation:
  - Compare two ServiceIR payloads that differ only in `base_url` (or another top-level field) and confirm that the artifact diff endpoint reports an empty diff.

### BUG-266 — Artifact diff ignores operation response-contract changes (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/ir/diff.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - Operation diffs only compare a few top-level fields, risk metadata, and params.
  - They do not compare `response_schema`, `error_schema`, or examples.
  - Output contract changes therefore disappear from artifact diffs.
- Evidence:
  - `_diff_operations()` compares `_OP_COMPARE_FIELDS`, `_RISK_COMPARE_FIELDS`, and param-level changes only.
  - A local probe changing just `response_schema` from a `name` field to an `email` field returned `summary='no changes'` and `is_empty=True`.
- Why it is a bug:
  - Response-shape changes are among the most important contract differences for consumers, so hiding them makes the diff endpoint misleading.
- Suggested validation:
  - Compare two versions whose only difference is `response_schema` or `error_schema` on an existing operation and observe that the artifact diff endpoint stays empty.

### BUG-267 — Artifact diff ignores operation request/execution-contract changes such as `request_body_mode` and `body_param_name` (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/ir/diff.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - `compute_diff()` does not compare execution-contract fields beyond method/path/params.
  - Changes to `request_body_mode`, `body_param_name`, and other execution-specific metadata are omitted from the diff.
  - The registry can therefore report “no changes” even when the request encoding contract changed.
- Evidence:
  - `_OP_COMPARE_FIELDS` is limited to `("name", "description", "method", "path", "enabled")`.
  - A local probe changing an operation from `request_body_mode=json, body_param_name='body'` to `request_body_mode=multipart, body_param_name='payload'` returned `summary='no changes'` and `is_empty=True`.
- Why it is a bug:
  - Request encoding and execution-contract changes alter how clients must call the tool, so omitting them from diffs can hide breaking changes.
- Suggested validation:
  - Compare two versions whose only differences are execution-contract fields like `request_body_mode` or `body_param_name`, then inspect the artifact diff response.

### BUG-268 — Artifact diff ignores resource, prompt, and event-definition changes entirely (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/ir/diff.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - `compute_diff()` never reads `resource_definitions`, `prompt_definitions`, or `event_descriptors`.
  - Changes to those MCP-facing capability surfaces are therefore invisible to the artifact diff endpoint.
- Evidence:
  - `compute_diff()` only traverses `old.operations` and `new.operations`.
  - A local probe that changed a resource’s content, a prompt template, and an event descriptor’s support level still returned `summary='no changes'` and `is_empty=True`.
- Why it is a bug:
  - The registry diff endpoint is supposed to help reviewers understand version changes, but today it cannot surface MCP resource/prompt/event changes at all.
- Suggested validation:
  - Compare two versions that differ only in `resource_definitions`, `prompt_definitions`, or `event_descriptors` and verify that the diff response is empty.

### BUG-269 — Proof-runner audit summaries count `generated_tools` from runtime `/tools`, yielding self-contradictory coverage metrics (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_audit_generated_tools()` iterates enabled IR operations and records failures for missing runtime tools.
  - But it sets `generated_tools = len(runtime_tool_names)` instead of the number of generated/enabled IR tools under audit.
  - A runtime that exposes zero tools can therefore produce summaries with `generated_tools=0` and `audited_tools>0`.
- Evidence:
  - `_audit_generated_tools()` computes `enabled_operations` from `service_ir.operations`, but returns `generated_tools=len(runtime_tool_names)`.
  - A local probe with one enabled IR operation and `available_tool_names=set()` produced `ToolAuditSummary(discovered_operations=1, generated_tools=0, audited_tools=1, failed=1, ...)`.
- Why it is a bug:
  - Coverage metrics become internally inconsistent and can interact badly with threshold logic that treats `generated_tools == 0` as a special case.
- Suggested validation:
  - Run `_audit_generated_tools()` with a non-empty IR and an empty runtime tool listing, then inspect the returned `ToolAuditSummary`.

### BUG-270 — Proof-runner `forced_skip_tool_ids` cannot suppress missing-tool failures because the skip is checked too late (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_audit_generated_tools()` first fails any enabled IR operation that is absent from the runtime `/tools` listing.
  - Only after that does it check `forced_skip_tool_ids`.
  - Proof cases therefore cannot intentionally skip a tool that is missing from the target deployment.
- Evidence:
  - The code checks `if operation.id not in runtime_tool_names` before `if operation.id in forced_skip_tool_id_set`.
  - A local probe with `forced_skip_tool_ids=('oauth',)` and `available_tool_names=set()` still returned a failed result: `"Runtime /tools listing does not expose this generated tool."`
- Why it is a bug:
  - The proof-case override claims a tool is intentionally disabled, but the implementation still records it as a failure.
- Suggested validation:
  - Run `_audit_generated_tools()` with a forced-skip tool ID that is absent from the runtime tool listing and confirm the result is `failed` instead of `skipped`.

### BUG-271 — Proof-runner `_fetch_runtime_tool_names()` crashes on valid JSON `/tools` responses that are not objects (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_fetch_runtime_tool_names()` assumes `response.json()` returns a mapping.
  - It immediately calls `payload.get("tools", [])` without checking the JSON shape.
  - A valid JSON array/string/null response crashes the proof runner with `AttributeError`.
- Evidence:
  - `_fetch_runtime_tool_names()` performs `payload = response.json()` and then `payload.get("tools", [])`.
  - A local probe with a mocked `/tools` response body `["not", "a", "dict"]` raised `AttributeError: 'list' object has no attribute 'get'`.
- Why it is a bug:
  - The proof harness should report a structured runtime-tool-listing failure instead of crashing on a malformed-but-JSON payload.
- Suggested validation:
  - Mock a runtime `/tools` endpoint that returns JSON `[]` or `"tools"` and observe the unhandled `AttributeError`.

### BUG-272 — Capability matrix overclaims `grpc_stream` support even when the descriptor is invalid because `operation_id` is missing (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/capability_matrix.py`
  - `libs/validator/pre_deploy.py`
- Summary:
  - `protocol_capability_key()` upgrades any `grpc` IR with a supported `grpc_stream` descriptor to the `grpc_stream` capability row.
  - It does not require the descriptor to be runnable.
  - Pre-deploy validation, however, rejects `grpc_stream` descriptors whose `operation_id` is missing.
- Evidence:
  - `protocol_capability_key()` checks only `descriptor.transport is grpc_stream` and `descriptor.support is supported`.
  - `PreDeployValidator._validate_event_support()` rejects `grpc_stream` descriptors with `operation_id is None`.
  - A local probe produced `capability_key='grpc_stream'` while pre-deploy validation failed with `e1=missing_operation_id`.
- Why it is a bug:
  - The capability matrix advertises a fully supported native grpc_stream surface for IRs that cannot actually pass validation or run.
- Suggested validation:
  - Build a `grpc` ServiceIR with a supported `grpc_stream` descriptor whose `operation_id` is `None`, then compare `protocol_capability_key()` with the pre-deploy validation result.

### BUG-273 — `RegistryClient` leaks raw `ValidationError` on malformed but successful JSON responses instead of raising `RegistryClientError` (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/registry_client/client.py`
- Summary:
  - `_parse_model()` wraps HTTP status errors and non-JSON responses in `RegistryClientError`.
  - But it does not catch schema-validation failures from `model_validate(...)`.
  - Successful `200` responses with malformed JSON bodies therefore raise raw Pydantic exceptions.
- Evidence:
  - `_parse_model()` calls `model_type.model_validate(data)` outside any `try/except`.
  - A local probe passing a `200` JSON response `{"service_id": "svc"}` to `_parse_model(..., ArtifactVersionResponse)` raised raw `ValidationError`.
- Why it is a bug:
  - Callers of the client library cannot consistently handle registry failures via `RegistryClientError`; malformed success responses escape as a different exception class.
- Suggested validation:
  - Mock a registry endpoint to return HTTP 200 with an incomplete JSON payload and observe that the client raises `ValidationError` instead of `RegistryClientError`.

### BUG-274 — Audit placeholder heuristics falsely skip legitimate path values such as `"sample"` and `1` (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/validator/audit.py`
- Summary:
  - The audit skip logic treats path-parameter argument values equal to `"sample"` as synthetic placeholders.
  - Failed invocations also treat numeric `1` (or string `"1"`) as a synthetic placeholder fallback.
  - Real APIs whose valid identifiers are `"sample"` or `1` therefore get skipped instead of audited/failed.
- Evidence:
  - `_contains_synthetic_placeholder_sample()` returns `True` for string `"sample"` and, when `include_numeric_fallbacks=True`, for `1` / `"1"`.
  - `AuditPolicy.skip_reason()` uses that helper for normal audit decisions; `failure_skip_reason()` uses it with numeric fallbacks enabled.
  - A local probe for `/users/{id}` returned `Skipped tool because path parameters still use synthetic placeholder samples.` for arguments `{'id': 'sample'}` and returned the failure-skip variant for `{'id': 1}`.
- Why it is a bug:
  - The audit policy can silently suppress legitimate tools or failed audits just because the real identifier happens to match the heuristic placeholder values.
- Suggested validation:
  - Audit a tool whose real path parameter sample is `"sample"` or `1` and verify that it is skipped rather than evaluated normally.

### BUG-275 — Black-box risk validation ignores `external_side_effect` and `idempotent` mismatches (Agent b fixed)

- Severity: High
- Files:
  - `libs/validator/black_box.py`
  - `tests/fixtures/ground_truth/jsonplaceholder.py`
- Summary:
  - `_risk_matches()` only compares `writes_state` and `destructive`.
  - Ground-truth endpoints also define `external_side_effect` and `idempotent`.
  - Misclassified side-effect or idempotency metadata therefore still counts as a “correct” risk match.
- Evidence:
  - `EndpointTruth` includes `external_side_effect` and `idempotent`.
  - `_risk_matches()` checks only `risk.writes_state` and `risk.destructive`.
  - A local probe returned `True` even when the operation’s `external_side_effect` and `idempotent` values disagreed with ground truth.
- Why it is a bug:
  - Black-box reports can overstate risk-classification accuracy by treating materially wrong risk metadata as correct.
- Suggested validation:
  - Compare an extracted operation against ground truth that differs only in `external_side_effect` or `idempotent`, then verify that `evaluate_black_box()` still treats the match as risk-correct.

### BUG-276 — Proof GraphQL mock rejects valid single-operation requests when `operationName` is omitted (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/http_mock.py`
- Summary:
  - The GraphQL proof mock dispatches application behavior entirely by `operationName`.
  - If a client sends a valid document with only one operation and omits `operationName`, the handler falls through to the unsupported-operation error path.
- Evidence:
  - `graphql_endpoint()` computes `operation_name = str(payload.get("operationName", "") or "")`.
  - It only handles `searchProducts` and `adjustInventory` when that exact string is present.
  - A local probe posting a single-operation `searchProducts` query without `operationName` returned `200` with `{"errors":[{"message":"Unsupported GraphQL operation unknown."}]}`.
- Why it is a bug:
  - GraphQL clients are allowed to omit `operationName` when the document contains just one operation, so the proof mock is less compatible than the protocol it is supposed to emulate.
- Suggested validation:
  - Send a single-operation GraphQL request without `operationName` to `/graphql` and observe the unsupported-operation error.

### BUG-277 — Proof runner SSE parser drops all but the last `data:` line from a multi-line event (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_parse_sse_events()` overwrites `current_event["data"]` for each `data:` line.
  - It does not concatenate multiple `data:` lines as required by the SSE format.
  - Multi-line SSE messages are therefore truncated to their last line.
- Evidence:
  - The parser handles each `data:` line independently and assigns `current_event["data"] = ...`.
  - A local probe with payload `event: message\\ndata: hello\\ndata: world\\n\\n` returned `[{'event': 'message', 'data': 'world'}]`.
- Why it is a bug:
  - Valid SSE streams can split one event payload across multiple `data:` lines, so the proof runner can misparse or lose event content.
- Suggested validation:
  - Feed `_parse_sse_events()` an SSE event with multiple `data:` lines and verify that only the last line survives.

### BUG-278 — Proof-runner audit silently ignores unexpected extra runtime tools while still counting them in `generated_tools` (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_audit_generated_tools()` only iterates enabled IR operations when building audit results.
  - But it sets `generated_tools` from the full runtime `/tools` listing.
  - Extra runtime-only tools inflate the denominator while never appearing in `results` as passes or failures.
- Evidence:
  - Audit results are produced only inside `for operation in enabled_operations`.
  - The returned summary uses `generated_tools=len(runtime_tool_names)`.
  - A local probe with one IR operation and `available_tool_names={'getUser', 'extraTool'}` returned `generated_tools=2`, `audited_tools=1`, and results containing only `getUser`.
- Why it is a bug:
  - The summary hides unexpected exposed runtime tools instead of surfacing them, even though they alter the reported coverage numbers.
- Suggested validation:
  - Run `_audit_generated_tools()` with extra names in the runtime tool listing that are absent from the IR and inspect the returned `ToolAuditSummary`.

### BUG-279 — Proof GraphQL mock returns HTTP 500 on invalid JSON request bodies instead of a GraphQL-style error response (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/http_mock.py`
- Summary:
  - `graphql_endpoint()` directly calls `await request.json()` without guarding JSON decode errors.
  - Malformed JSON bodies therefore bubble up as framework-level 500s.
- Evidence:
  - The handler does not wrap `await request.json()` in any error handling.
  - A local probe posting `content='not-json'` with `Content-Type: application/json` returned `500 Internal Server Error`.
- Why it is a bug:
  - The proof mock is supposed to emulate a GraphQL endpoint, but malformed client payloads crash it instead of returning a controlled GraphQL/HTTP error response.
- Suggested validation:
  - POST invalid JSON to `/graphql` and observe the 500 instead of a structured error payload.

### BUG-280 — Proof GraphQL mock crashes with HTTP 500 when `adjustInventory.delta` is not numeric (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/http_mock.py`
- Summary:
  - The `adjustInventory` branch coerces `delta` via `int(variables.get("delta", 0) or 0)`.
  - Non-numeric values raise `ValueError`, which is not caught.
  - The mock therefore returns HTTP 500 instead of a GraphQL validation/error response.
- Evidence:
  - `graphql_endpoint()` calls `int(...)` directly in the `adjustInventory` handler.
  - A local probe posting `variables={'sku': 'sku-1', 'delta': 'oops'}` returned `500 Internal Server Error`.
- Why it is a bug:
  - Invalid client input should produce a controlled GraphQL error, not crash the proof mock process.
- Suggested validation:
  - POST an `adjustInventory` request whose `delta` variable is a non-numeric string and observe the 500 response.

### BUG-281 — Kubernetes manifest deployment leaks partially created resources when apply fails mid-deploy (Agent c fixed)

- Severity: High
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `KubernetesManifestDeployer.deploy()` applies ConfigMap, Deployment, Service, and NetworkPolicy sequentially.
  - If one later apply fails, the method raises immediately without deleting anything already created.
  - Because `deploy_stage()` never receives a successful `StageExecutionResult`, the workflow does not get rollback metadata for that partial deployment either.
- Evidence:
  - `deploy()` has no compensating delete logic around the sequential `_apply_manifest(...)` calls.
  - `deploy_stage()` only records rollback payload after `deployer.deploy(manifest_set)` returns successfully.
  - A local probe that forced the Service apply to fail showed successful ConfigMap and Deployment create calls followed by the error, with no DELETE requests issued.
- Why it is a bug:
  - Failed deployments can leave orphaned runtime resources behind even though the stage reports failure.
- Suggested validation:
  - Simulate a Kubernetes API failure after the first one or two manifest applies and inspect the cluster for leaked ConfigMap/Deployment resources.

### BUG-282 — Kubernetes manifest rollback aborts after the first delete failure and leaves the rest of the resources behind (Agent c fixed)

- Severity: High
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `KubernetesManifestDeployer.rollback()` deletes NetworkPolicy, Service, Deployment, and ConfigMap in sequence.
  - A single delete error aborts the whole rollback.
  - Remaining resources are never attempted.
- Evidence:
  - `rollback()` awaits each `_delete_manifest(...)` sequentially without per-resource exception handling.
  - A local probe forcing the NetworkPolicy delete to return HTTP 500 produced an exception after the first DELETE, and no further DELETE calls were issued for Service/Deployment/ConfigMap.
- Why it is a bug:
  - Cleanup becomes brittle: one transient delete failure can strand the rest of the deployment resources indefinitely.
- Suggested validation:
  - Simulate one Kubernetes DELETE failure during rollback and verify that later resources are not even attempted.

### BUG-283 — Runtime readiness timeout excludes request time and can overrun the configured deadline by a large margin (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `_wait_for_runtime_http_ready()` tracks elapsed time only by adding the sleep interval.
  - Time spent performing the health and ready HTTP requests is not counted against the timeout budget.
  - Slow failing probes can therefore exceed the configured timeout by many multiples before the helper raises.
- Evidence:
  - The function increments `elapsed` only after `await _sleep_seconds(...)`; request time is ignored.
  - A local probe with `timeout_seconds=0.1` and each request taking `0.25s` raised only after about `0.854s`, even though the configured timeout was `0.1s`.
- Why it is a bug:
  - Deployment stages can block much longer than configured, which makes workflow timeout controls misleading and slow failure recovery.
- Suggested validation:
  - Use a slow or hanging HTTP client factory with a very small timeout and measure the actual wall-clock duration before `_wait_for_runtime_http_ready()` returns or raises.

### BUG-284 — Proof GraphQL mock accepts completely invalid query text as long as `operationName` matches (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/http_mock.py`
- Summary:
  - The GraphQL proof mock dispatches business logic purely from `operationName` and variables.
  - It does not parse or validate the GraphQL document for the supported application operations.
  - Invalid query text can therefore return a successful data payload.
- Evidence:
  - `graphql_endpoint()` branches on `operation_name == "searchProducts"` / `"adjustInventory"` and never validates the query syntax in those branches.
  - A local probe posting `operationName='searchProducts'` with query text `this is not valid graphql` still returned `200` with a normal `data.searchProducts` response.
- Why it is a bug:
  - The proof mock can hide broken GraphQL documents or parser regressions because it reports success for requests a real GraphQL server would reject.
- Suggested validation:
  - POST a syntactically invalid GraphQL document with a supported `operationName` and confirm that the proof mock still returns a success payload.

### BUG-285 — Proof SOAP mock accepts malformed non-XML request bodies if they merely contain an operation marker substring (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/http_mock.py`
- Summary:
  - The SOAP proof mock does not parse XML envelopes.
  - It decides which operation to run by checking `SOAPAction` and by searching the raw request body for substrings like `GetOrderStatusRequest`.
  - Arbitrary non-XML text can therefore produce a successful SOAP response.
- Evidence:
  - `soap_order_service()` checks `b"GetOrderStatusRequest" in body` and `b"SubmitOrderRequest" in body`.
  - A local probe posting plain-text body `totally not xml GetOrderStatusRequest garbage` returned `200` with a successful SOAP envelope.
- Why it is a bug:
  - The proof mock can mask XML serialization/envelope regressions because malformed payloads still look successful.
- Suggested validation:
  - POST a non-XML body that contains `GetOrderStatusRequest` or `SubmitOrderRequest` and observe the success response.

### BUG-286 — `LLMJudge.evaluate()` silently drops failed batches and reports quality only for the surviving subset (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/llm_judge.py`
- Summary:
  - `evaluate()` processes operations in batches.
  - If one batch throws, the exception is only logged.
  - The final `JudgeEvaluation` is then computed from whatever batches happened to succeed.
- Evidence:
  - `evaluate()` wraps `_evaluate_batch(...)` in `try/except Exception` and only logs on failure.
  - A local probe with two one-operation batches where the first response was unparsable and the second succeeded returned `tools_evaluated=1` and a perfect score, silently dropping the failed batch.
- Why it is a bug:
  - A partial LLM outage or parse failure can make overall quality metrics look healthier than they really are because missing tools disappear from the denominator.
- Suggested validation:
  - Evaluate a service with multiple batches and force one batch to fail; inspect the resulting `JudgeEvaluation` and verify it only reflects the successful subset.

### BUG-287 — `LLMJudge` accepts partial JSON responses and omits missing operations without recording any failure (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/llm_judge.py`
- Summary:
  - `_parse_judge_response()` returns scores only for operation IDs present in the LLM output.
  - Missing operations are silently ignored.
  - A partial JSON response therefore yields an apparently successful evaluation over only a subset of the batch.
- Evidence:
  - `_parse_judge_response()` iterates returned items and appends scores only for matching `operation_id`s; there is no completeness check against the batch.
  - A local probe with two operations and an LLM response containing only `op1` returned `tools_evaluated=1` with no error or warning about the missing `op2`.
- Why it is a bug:
  - Missing tool scores reduce coverage of the judge evaluation while still producing a success-shaped aggregate report.
- Suggested validation:
  - Return a valid JSON array that scores only some operations in a batch and inspect the resulting `JudgeEvaluation`.

### BUG-288 — `LLMJudge` crashes when configured with `batch_size=0` (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/llm_judge.py`
- Summary:
  - `LLMJudge.__init__()` does not validate `batch_size`.
  - `_batch_operations()` passes that value directly as the `range(..., step=...)` step.
  - `batch_size=0` therefore raises `ValueError` at runtime.
- Evidence:
  - `_batch_operations()` returns `[operations[i : i + self._batch_size] for i in range(0, len(operations), self._batch_size)]`.
  - A local probe constructing `LLMJudge(..., batch_size=0)` and calling `evaluate()` raised `ValueError: range() arg 3 must not be zero`.
- Why it is a bug:
  - A bad configuration value causes an unexpected runtime crash instead of being rejected up front.
- Suggested validation:
  - Instantiate `LLMJudge` with `batch_size=0` and call `evaluate()`.

### BUG-289 — `JudgeEvaluation.quality_passed` ignores configured thresholds and can disagree with `low_quality_tools` (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/llm_judge.py`
- Summary:
  - `JudgeEvaluation.quality_passed` is hardcoded to `average_overall >= 0.6`.
  - `LLMJudge` separately accepts `low_quality_threshold` and computes `low_quality_tools` from it.
  - The pass/fail property can therefore say “passed” while every tool is considered low quality by the configured threshold.
- Evidence:
  - `JudgeEvaluation.quality_passed` returns `self.average_overall >= 0.6`.
  - `LLMJudge` stores `self._low_quality_threshold` and uses it for `low_quality_tools`.
  - A local probe with `low_quality_threshold=0.95` and one tool scoring `0.7` produced `low_quality_tools=['op1']` while `quality_passed` was still `True`.
- Why it is a bug:
  - The evaluation exposes conflicting signals about whether quality checks passed.
- Suggested validation:
  - Evaluate a service with a strict `low_quality_threshold` above `0.6` and compare `low_quality_tools` against `quality_passed`.

### BUG-290 — `LLMJudge` double-counts duplicate scores for the same operation ID (Agent b fixed)

- Severity: Medium
- Files:
  - `libs/validator/llm_judge.py`
- Summary:
  - `_parse_judge_response()` appends every matching item from the LLM response.
  - It does not deduplicate by `operation_id`.
  - A duplicate entry in the LLM output therefore counts the same tool multiple times in averages and `tools_evaluated`.
- Evidence:
  - `_parse_judge_response()` appends scores to a list with no seen-set or overwrite-by-ID logic.
  - A local probe where the LLM returned two entries for `op1` produced `tools_evaluated=2` and averaged both copies.
- Why it is a bug:
  - One duplicated tool score can skew aggregate metrics and make the evaluation depend on LLM output duplication artifacts.
- Suggested validation:
  - Return a JSON response containing the same `operation_id` twice and inspect the resulting `JudgeEvaluation`.

### BUG-291 — Kubernetes rollout timeout excludes request time and can overrun the configured deadline (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `_wait_for_rollout()` tracks elapsed time only by adding `poll_seconds` after each sleep.
  - Time spent issuing rollout status requests is not counted.
  - Slow rollout polls can therefore exceed the configured timeout by a large margin before the helper fails.
- Evidence:
  - `_wait_for_rollout()` increments `elapsed` only after `_sleep_seconds(poll_seconds)`.
  - A local probe with `rollout_timeout_seconds=0.1` and each GET taking `0.25s` raised only after about `0.602s`.
- Why it is a bug:
  - Deployment timeout configuration becomes misleading, and failed rollouts can block the workflow much longer than expected.
- Suggested validation:
  - Inject a slow Kubernetes client into `_wait_for_rollout()` with a very small timeout and compare wall-clock runtime to the configured timeout.

### BUG-292 — Kubernetes rollout helper can declare success before the new ReplicaSet is updated (Agent c fixed)

- Severity: High
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `_wait_for_rollout()` treats a rollout as successful once `observedGeneration >= generation` and `availableReplicas >= expected_replicas`.
  - It does not require `updatedReplicas` to reach the expected count.
  - Old replicas can therefore satisfy the success condition before the new rollout is actually complete.
- Evidence:
  - The helper only reads `observedGeneration`, `generation`, and `availableReplicas`.
  - A local probe with `generation=2`, `observedGeneration=2`, `availableReplicas=1`, `updatedReplicas=0`, and `unavailableReplicas=1` returned success immediately.
- Why it is a bug:
  - The workflow can proceed to post-deploy validation against a deployment that has not actually rolled out the new pod template yet.
- Suggested validation:
  - Feed `_wait_for_rollout()` a deployment status where old pods are still providing availability but `updatedReplicas` is zero and confirm the helper still returns success.

### BUG-293 — Proof runner constructs runtime service DNS names from raw `service_id`, which disagrees with generator DNS sanitization (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
  - `libs/generator/generic_mode.py`
- Summary:
  - The proof runner builds the runtime base URL as `{service_id}-v{version}`.
  - The generator derives Kubernetes resource names by lowercasing, sanitizing, and truncating the service name.
  - Service IDs containing characters like `_` or uppercase letters therefore produce proof-runner URLs that do not match the actual deployed Service name.
- Evidence:
  - `_run_case()` uses `_cluster_http_url(namespace, f"{case.service_id}-v{active_version}", 8003)`.
  - `generate_generic_manifests()` uses `_resource_name()` / `_versioned_resource_name()` with DNS sanitization.
  - A local probe with `service_id='Billing_API'` produced proof-runner URL host `Billing_API-v2...` while the generated Service name was `billing-api-v2`.
- Why it is a bug:
  - Custom proof cases can target a non-existent runtime Service even when the deployment succeeded, purely because naming logic diverged.
- Suggested validation:
  - Run a proof case whose service ID contains uppercase letters or underscores and compare the constructed runtime host with the generated Kubernetes Service name.

### BUG-294 — Proof runner cannot target tenant/environment-scoped services because its registry lookups omit scope entirely (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `ProofCase` carries only `service_id`.
  - `_active_version_for_service()` queries `/api/v1/services` without `tenant` / `environment` filters.
  - `_artifact_version()` also fetches artifact versions without scope parameters.
- Evidence:
  - `ProofCase` defines no tenant/environment fields.
  - `_active_version_for_service()` calls `client.get("/api/v1/services")`.
  - `_artifact_version()` calls `/api/v1/artifacts/{service_id}/versions/{version_number}` with no query params.
- Why it is a bug:
  - In multi-tenant or multi-environment registries, proof runs can pick the wrong active version or artifact payload when the same service ID exists in multiple scopes.
- Suggested validation:
  - Create the same `service_id` in multiple tenant/environment scopes, then run the proof helper logic and observe that it cannot disambiguate which scoped service to validate.

### BUG-295 — Async-job polling treats terminal failure states as successful tool invocations (Agent c fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - `_poll_async_job()` stops polling when the extracted status enters either `success_status_values` or `failure_status_values`.
  - It returns the raw `poll_response` unchanged in both cases.
  - The outer `invoke()` path then reports a normal `status="ok"` result whenever that terminal failure payload used HTTP 200 and does not trip a protocol-specific error helper.
- Evidence:
  - `_poll_async_job()` returns `poll_response` immediately when `normalized_status in success_states or normalized_status in failure_states`.
  - A local probe with initial `202 Location: /jobs/1`, followed by poll body `{"job":{"state":"failed","error":"boom"}}` and `failure_status_values=["failed"]`, made `proxy.invoke()` log completion and return `status='ok'` with the failed payload.
- Why it is a bug:
  - Async jobs that explicitly report terminal failure states are surfaced as successful tool calls, which hides failed background work behind a success-shaped MCP response.
- Suggested validation:
  - Configure an operation with `async_job.failure_status_values=["failed"]`, have the status endpoint return HTTP 200 with a matching failed state, and observe that the runtime still returns `status="ok"`.

### BUG-296 — Response-body async-job startup can leak `_InvalidJsonPayloadError` instead of returning a controlled tool failure (Agent c fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - For `status_url_source="response_body"`, `_poll_async_job()` calls `_extract_async_status_url()` before entering the poll loop.
  - That helper uses `_maybe_parse_json_payload()`, which raises `_InvalidJsonPayloadError` when the upstream body claims JSON but is malformed.
  - `_poll_async_job()` only wraps invalid JSON around later `_extract_async_status_value()` calls, so malformed initial JSON escapes as an internal exception.
- Evidence:
  - `_poll_async_job()` calls `_extract_async_status_url(async_job, response)` before its `try/except _InvalidJsonPayloadError` block.
  - A local probe whose initial `202` response had `Content-Type: application/json` and body `not-json` caused `proxy.invoke()` to raise `_InvalidJsonPayloadError` directly.
- Why it is a bug:
  - A malformed async-job kickoff payload can crash the runtime path with an internal exception instead of producing a normal `ToolError` that callers can handle.
- Suggested validation:
  - Configure an async job that reads `status_url` from the initial JSON body, then return invalid JSON from the kickoff response and observe the leaked exception type.

### BUG-297 — Access-control authentication rejects lowercase `bearer` schemes even though HTTP auth schemes are case-insensitive (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/access_control/security.py`
- Summary:
  - `require_authenticated_caller()` removes only the exact prefix `"Bearer "`.
  - Lowercase or differently cased scheme variants leave the scheme text in the token string.
  - The downstream JWT/PAT validation then fails even when the underlying token is valid.
- Evidence:
  - The helper derives the token as `auth_header.removeprefix("Bearer ").strip()`.
  - A local probe using `Authorization: bearer <valid-jwt>` returned HTTP `401` with detail `Malformed JWT header.`.
- Why it is a bug:
  - HTTP authentication schemes are case-insensitive, so standards-compliant clients or intermediaries that lowercase the scheme break authentication unexpectedly.
- Suggested validation:
  - Send a valid JWT or PAT with `Authorization: bearer <token>` and observe that access-control rejects it.

### BUG-298 — JWT validation mishandles NumericDate float claims, rejecting valid `exp` values while accepting future `nbf` values early (Agent c fixed)

- Severity: High
- Files:
  - `apps/access_control/authn/service.py`
- Summary:
  - `_validate_jwt()` requires `exp` to be an `int`.
  - It only enforces `nbf` when that claim is also an `int`.
  - Valid JWTs that use float NumericDate values are therefore rejected as expired for `exp`, while future float `nbf` claims are ignored and accepted early.
- Evidence:
  - The code checks `if not isinstance(exp, int) or exp <= now_ts` and `if isinstance(nbf, int) and nbf > now_ts`.
  - A local probe that changed a valid HS256 token to use `exp=<float>` raised `AuthenticationError: JWT is expired.`.
  - A second probe that set `nbf` to a future float timestamp was accepted successfully.
- Why it is a bug:
  - JWT NumericDate claims are numeric, not integer-only. The current implementation both rejects valid tokens and can admit tokens that should not yet be active.
- Suggested validation:
  - Validate one token whose `exp` is a float timestamp and another whose `nbf` is a future float timestamp; compare the current behavior with the expected NumericDate semantics.

### BUG-299 — Gateway admin mock crashes when a stored route document is missing `target_service` (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/gateway_admin_mock/main.py`
- Summary:
  - `proxy_gateway_request()` catches only `httpx.HTTPError` around `_forward_request()`.
  - `_forward_request()` reads `route_document["target_service"]` with no validation.
  - A malformed or partially written route document therefore raises `KeyError` and crashes the request path.
- Evidence:
  - `_forward_request()` starts with `target_service = cast(dict[str, Any], route_document["target_service"])`.
  - A local probe that stored `{"document": {}}` via `PUT /admin/routes/svc-active` succeeded with HTTP `200`, and the subsequent `GET /gateway/svc` raised `KeyError: 'target_service'`.
- Why it is a bug:
  - Bad admin/test data should produce a controlled gateway error, not an unhandled exception from the proxy path.
- Suggested validation:
  - Store a route whose `document` omits `target_service`, then proxy a request through that route and observe the crash path.

### BUG-300 — Rollback restore errors can mask the original rollback validation failure (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_worker/workflows/rollback_workflow.py`
- Summary:
  - When post-deploy validation fails, `RollbackWorkflow.run()` tries to reactivate the previous active version before raising `Rollback validation failed ...`.
  - If that restore `activate_version()` call throws, its exception escapes immediately.
  - The caller never sees the original validation failure reason.
- Evidence:
  - The restore call in the validation-failure branch is not wrapped in any error handling before the final `RuntimeError(...)`.
  - A local probe with `validator.validate()` returning `{"overall_passed": False}` and `store.activate_version()` raising `RuntimeError("db down during restore")` surfaced exactly `db down during restore`.
- Why it is a bug:
  - Rollback callers can misdiagnose a failed validation as a storage/restore outage because the workflow masks the real terminal condition.
- Suggested validation:
  - Force rollback validation to fail and make the restore activation path raise, then inspect the surfaced exception message.

### BUG-301 — Compiler API route publisher leaks raw `JSONDecodeError` on malformed success bodies (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_api/route_publisher.py`
- Summary:
  - `AccessControlArtifactRoutePublisher._post()` raises for non-2xx responses.
  - But for successful responses it calls `response.json()` directly.
  - If access-control returns HTTP `200` with invalid JSON, the raw decoder exception escapes.
  - Callers therefore see a low-level parser error instead of a controlled publication failure.
- Evidence:
  - `_post()` does `response.raise_for_status()` and then `payload = response.json()` with no `try/except`.
  - A local probe with a fake `200 application/json` response body of `not-json` raised `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`.
- Why it is a bug:
  - Route publication is already a cross-service integration seam.
  - Malformed success payloads should turn into a deterministic route-publisher failure, not an uncaught JSON parser exception leaking transport internals.
- Suggested validation:
  - Make the access-control sync/delete endpoint return HTTP `200` with invalid JSON and observe that `AccessControlArtifactRoutePublisher.sync()` currently aborts with `JSONDecodeError`.

### BUG-302 — Gateway admin HTTP client leaks raw `JSONDecodeError` on malformed success bodies (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/access_control/gateway_binding/client.py`
- Summary:
  - `HTTPGatewayAdminClient._request()` handles HTTP status errors and checks for `204`.
  - But for other successful responses it calls `response.json()` directly.
  - A malformed JSON body therefore escapes as a raw `JSONDecodeError`.
  - Any reconciliation path using `list_routes()`, `list_policy_bindings()`, or `list_consumers()` can crash on a malformed gateway-admin success response.
- Evidence:
  - `_request()` does `response.raise_for_status()`, then `payload = response.json()` with no JSON parse guard.
  - A local probe with `list_routes()` against a fake `200 application/json` body of `not-json` raised `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`.
- Why it is a bug:
  - The binding layer should surface gateway-admin contract failures as controlled reconciliation errors.
  - Leaking raw parser exceptions makes malformed upstream responses look like local coding crashes.
- Suggested validation:
  - Serve `/admin/routes` or `/admin/policy-bindings` with HTTP `200` plus invalid JSON and confirm that the current client raises `JSONDecodeError`.

### BUG-303 — Proof runner leaks raw `JSONDecodeError` when compiler API returns malformed successful JSON (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - The proof runner’s compiler-control-plane helpers call `response.json()` directly after `raise_for_status()`.
  - `_submit_compilation()` and `_artifact_version()` do not guard JSON parsing at all.
  - If the compiler API returns HTTP `200` with malformed JSON, the proof run aborts with a raw parser exception.
  - The failure is reported as an internal crash instead of a controlled proof failure about an invalid compiler response.
- Evidence:
  - `_submit_compilation()` returns `cast(dict[str, Any], response.json())`.
  - `_artifact_version()` returns `cast(dict[str, Any], response.json())`.
  - Local probes against fake `200 application/json` responses with body `not-json` raised `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` in both helpers.
- Why it is a bug:
  - Proof runs are supposed to validate end-to-end compiler/runtime behavior.
  - A malformed control-plane response should become a deterministic failed proof with actionable context, not an uncaught JSON parser stack.
- Suggested validation:
  - Return invalid JSON from `POST /api/v1/compilations` or `GET /api/v1/artifacts/{service_id}/versions/{version}` and observe that the proof runner currently crashes with `JSONDecodeError`.

### BUG-304 — Proof runner trusts decoded control-plane JSON shapes and crashes on arrays or missing keys (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - Several proof-runner helpers cast decoded JSON to `dict[str, Any]` without verifying the top-level shape or required fields.
  - `_wait_for_terminal_job()` indexes `payload["status"]` directly.
  - `_active_version_for_service()` assumes the `/services` payload has `.get("services", ...)`.
  - Downstream callers also immediately index `job["id"]` and `artifact["ir_json"]` after `_submit_compilation()` / `_artifact_version()`.
  - JSON-encoded but wrong-shaped control-plane responses therefore crash the proof run with `TypeError`, `KeyError`, or `AttributeError`.
- Evidence:
  - `_wait_for_terminal_job()` does `payload = cast(dict[str, Any], response.json())` and then `if payload["status"] in _TERMINAL_JOB_STATUSES`.
  - `_active_version_for_service()` does `payload = cast(dict[str, Any], response.json())` and then `for service in payload.get("services", [])`.
  - A local probe with `_wait_for_terminal_job()` receiving `["not", "a", "dict"]` raised `TypeError: list indices must be integers or slices, not str`.
  - A local probe with `_wait_for_terminal_job()` receiving `{"id": "job-1"}` raised `KeyError: 'status'`.
  - A local probe with `_active_version_for_service()` receiving `["not", "a", "dict"]` raised `AttributeError: 'list' object has no attribute 'get'`.
  - A local probe with `_submit_compilation()` / `_artifact_version()` returning JSON arrays crashed downstream reads of `job["id"]` and `artifact["ir_json"]` with `TypeError: list indices must be integers or slices, not str`.
- Why it is a bug:
  - Wrong-shaped but JSON-encoded compiler API responses should become explicit proof failures that point at the bad contract.
  - Today they surface as unhandled Python exceptions from helper internals, which obscures root cause and makes the proof harness brittle.
- Suggested validation:
  - Return JSON arrays or objects missing `status`, `id`, or `ir_json` from the compiler API endpoints consumed by the proof runner and observe the current crash paths.

### BUG-305 — Compiler API silently falls back to a dead-end in-memory dispatcher, so accepted jobs can remain pending forever (Agent c fixed)

- Severity: High
- Files:
  - `apps/compiler_api/dispatcher.py`
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_api/repository.py`
  - `apps/compiler_api/tests/test_dispatcher.py`
- Summary:
  - The compiler API uses a real worker path only when `WORKFLOW_ENGINE=celery`.
  - If `WORKFLOW_ENGINE` is unset or misspelled, `_resolve_default_dispatcher()` silently returns `InMemoryCompilationDispatcher()`.
  - That dispatcher merely appends requests to an in-process list and has no consumer outside tests.
  - The compilation routes still persist a job row and return HTTP `202`.
  - As a result, the API can acknowledge jobs that will never be executed and remain `pending` indefinitely.
- Evidence:
  - `_resolve_default_dispatcher()` returns `CeleryCompilationDispatcher()` only for `"celery"` and otherwise returns `InMemoryCompilationDispatcher()`.
  - `InMemoryCompilationDispatcher.enqueue()` only does `self.submitted_requests.append(request)`.
  - `CompilationRepository.create_job()` persists jobs with `status="pending"` before dispatch.
  - `create_compilation()`, `retry_compilation()`, and `rollback_compilation()` all treat any successful `dispatcher.enqueue(...)` call as a successful submission and return the created job.
  - `apps/compiler_api/tests/test_dispatcher.py` explicitly codifies both the default and unknown-engine fallback to `InMemoryCompilationDispatcher`.
  - A local probe with `WORKFLOW_ENGINE` unset printed `dispatcher_type InMemoryCompilationDispatcher` and `submitted_count 1` after `enqueue()`.
  - A repository-wide search for `submitted_requests` found no consumer outside `apps/compiler_api/dispatcher.py` itself and its tests.
- Why it is a bug:
  - A configuration typo or omitted env var turns the compile API into a success-shaped sink: requests are accepted, jobs are created, but nothing drives them toward `running` or a terminal state.
  - This is much harder to diagnose than a startup/configuration failure because the control plane appears healthy while work is silently stranded.
- Suggested validation:
  - Start the compiler API without `WORKFLOW_ENGINE=celery`, submit a compilation, and observe that the API returns `202` while the job stays `pending` with no worker activity.

### BUG-306 — Gateway admin HTTP client trusts nested `document` / `metadata` shapes and leaks raw `ValueError` (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/access_control/gateway_binding/client.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - `HTTPGatewayAdminClient._items_from_payload()` checks only that each top-level `item` is a dict.
  - The per-resource list methods then coerce nested fields with `dict(item["document"])` or `dict(item.get("metadata", {}))`.
  - If the gateway returns a success response whose `document` or `metadata` is a JSON string, list, or other non-mapping value, the client leaks Python’s raw `ValueError`.
  - Because `GatewayBindingService.sync_service_routes()` and `reconcile()` call these list methods before diffing, one malformed stored object can abort the entire sync/reconcile pass.
- Evidence:
  - `list_routes()` constructs `GatewayRoute(..., document=dict(item["document"]))`.
  - `list_policy_bindings()` constructs `GatewayPolicyBinding(..., document=dict(item["document"]))`.
  - `list_consumers()` constructs `GatewayConsumer(..., metadata=dict(item.get("metadata", {})))`.
  - A local probe with successful JSON payloads such as `{"items":[{"route_id":"r1","document":"oops"}]}` and analogous binding/consumer payloads raised `ValueError: dictionary update sequence element #0 has length 1; 2 is required`.
  - `GatewayBindingService.sync_service_routes()` calls `await self._client.list_routes()`, and `GatewayBindingService.reconcile()` calls `list_consumers()`, `list_policy_bindings()`, and `list_routes()` before doing any work.
- Why it is a bug:
  - The gateway-binding layer should reject malformed upstream objects with a controlled contract error.
  - Today a single bad consumer, policy binding, or route can crash every route sync or reconciliation run with a low-level `ValueError`.
- Suggested validation:
  - Make the gateway admin API return HTTP `200` with an item whose nested `document` or `metadata` is a JSON string instead of an object, then call `list_routes()`, `list_policy_bindings()`, or `reconcile()` and observe the raw `ValueError`.

### BUG-307 — Request signing uses an empty path for origin-only URLs even though httpx actually sends `/` (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - `_build_signing_payload()` signs `urlsplit(url).path` exactly as-is.
  - For a URL like `https://example.com`, that path is the empty string `""`.
  - But httpx normalizes the actual outbound request path to `"/"`.
  - When request signing is enabled for an operation that resolves to an origin-only URL, the runtime signs one path and sends another.
  - Upstreams that verify the signature against the received request path will reject the request.
- Evidence:
  - `_build_signing_payload()` sets `path = urlsplit(url).path` and joins it directly into the HMAC payload.
  - The URL-resolution logic can return the original `base_url` unchanged when the resolved path is empty or `/`, preserving a base URL like `https://example.com` without a trailing slash.
  - A local probe showed `_build_signing_payload(method='GET', url='https://example.com', ...)` produced `signed_path ''`.
  - The same probe sent `httpx.AsyncClient().get('https://example.com')` through a mock transport and captured `sent_path '/'`.
- Why it is a bug:
  - Request-signing correctness depends on canonicalizing the exact request that will be sent.
  - Here the signed path and the transmitted path diverge, so valid signing configurations can fail purely because the service base URL omits a trailing slash.
- Suggested validation:
  - Configure request signing on an operation whose resolved URL is the bare service origin (for example, base URL without a trailing slash plus a root path), then compare the generated signature with one computed from the upstream’s received `"/"` path.

### BUG-308 — JWT validation trusts non-object header/payload JSON and leaks raw `AttributeError` instead of returning `401` (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/access_control/authn/service.py`
  - `apps/access_control/security.py`
- Summary:
  - `_validate_jwt()` decodes the JWT header and payload with `json.loads(...)` but never verifies that either result is a JSON object.
  - It immediately calls `header.get(...)` and `claims.get(...)`.
  - A token whose header or payload is a JSON array therefore raises raw `AttributeError` instead of `AuthenticationError`.
  - `_validate_token()` only converts `AuthenticationError` into HTTP `401`, so these malformed JWTs escape the normal auth failure path and become server errors on protected routes.
- Evidence:
  - `authn/service.py` assigns `header = json.loads(...)` and then calls `header.get("alg")`; later it assigns `claims = json.loads(...)` and calls `claims.get("exp")`, `claims.get("aud")`, and `claims.get("sub")` with no shape check.
  - A local probe using an HS256-signed token whose payload decoded to `["alice"]` raised `AttributeError: 'list' object has no attribute 'get'`.
  - A local probe through `apps.access_control.security._validate_token()` with that same token also leaked `AttributeError: 'list' object has no attribute 'get'` instead of translating it into an HTTP `401`.
- Why it is a bug:
  - Malformed JWTs should be rejected as authentication failures, not allowed to crash the route-level auth helper.
  - As written, an unauthenticated caller can turn an invalid-but-signed token into an internal error rather than a clean `401`.
- Suggested validation:
  - Send an HS256 JWT whose header or payload segment is valid JSON but not an object (for example, `[]`) to any protected access-control route and observe the server error instead of `401 Unauthorized`.

### BUG-309 — Kubernetes deployer accepts wrong-shaped JSON success payloads and later crashes on `.get(...)` (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `_apply_manifest()` and `_wait_for_rollout()` only guard against non-JSON Kubernetes responses.
  - They do not verify that a successful JSON payload is actually an object before casting it to `dict[str, Any]`.
  - If the Kubernetes API (or a proxy in front of it) returns a JSON array on a `200` response, the deployer carries that value forward and later calls `.get(...)` on it.
  - The deployment then fails with raw `AttributeError` instead of surfacing a clear Kubernetes protocol error.
- Evidence:
  - `_apply_manifest()` returns `cast(dict[str, Any], response.json())` after checking only `response.is_error`; `deploy()` later reads `deployment_response.get("metadata", {})`.
  - `_wait_for_rollout()` similarly casts `response.json()` and immediately does `deployment.get("metadata", {})` / `deployment.get("status", {})`.
  - A local probe where `_apply_manifest()` received HTTP `200` with body `[]` printed `apply_type list`.
  - A local probe that ran `deploy()` with the deployment apply response set to `[]` raised `AttributeError: 'list' object has no attribute 'get'`.
  - A local probe where `_wait_for_rollout()` received HTTP `200` with body `[]` also raised `AttributeError: 'list' object has no attribute 'get'`.
- Why it is a bug:
  - Success-path integrations still need payload-shape validation.
  - Without it, wrong-shaped but JSON-valid Kubernetes responses crash the worker in opaque ways and make rollout failures much harder to diagnose.
- Suggested validation:
  - Mock the Kubernetes API to return HTTP `200` plus a JSON array for either the deployment apply response or rollout-status GET, then run a deployment and observe the raw `AttributeError`.

### BUG-310 — Async job polling treats mixed-case JSON content-types as missing response-body status metadata (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - Response-body async polling relies on `_maybe_parse_json_payload()` to read `status_url_field` and `status_field`.
  - That helper tests `if "json" not in content_type` case-sensitively.
  - A valid header such as `Content-Type: Application/JSON` is therefore treated as non-JSON.
  - `_extract_async_status_url()` / `_extract_async_status_value()` then return `None`, so `_poll_async_job()` can fail before it ever starts polling.
- Evidence:
  - `_extract_async_status_url()` and `_extract_async_status_value()` both call `_maybe_parse_json_payload(response)`.
  - `_maybe_parse_json_payload()` checks `"json" not in content_type` before any normalization.
  - `_poll_async_job()` raises `ToolError("Async job operation ... did not provide a pollable status URL.")` when `_extract_async_status_url()` returns no URL.
  - A local probe with HTTP `202`, `Content-Type: Application/JSON`, and body `{"job": {"status_url": "/jobs/123", "state": "queued"}}` returned `status_url None` and `status_value None`.
  - Running `RuntimeProxy._poll_async_job(...)` against that probe raised `ToolError: Async job operation demo.op did not provide a pollable status URL.`
- Why it is a bug:
  - HTTP media types are case-insensitive.
  - Async APIs that legitimately return mixed-case JSON content-types can no longer drive response-body polling, even when they provide the correct status URL and state fields.
- Suggested validation:
  - Configure an async operation whose initial `202` response uses `Content-Type: Application/JSON` and includes the poll URL/status in the JSON body, then observe that the runtime errors out with “did not provide a pollable status URL.”

### BUG-311 — Gateway admin HTTP client trusts required item keys and leaks raw `KeyError` on partial success payloads (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/access_control/gateway_binding/client.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - `_items_from_payload()` only verifies that the admin response contains a list of dict items.
  - `list_consumers()`, `list_policy_bindings()`, and `list_routes()` then index required fields like `consumer_id`, `credential`, `binding_id`, `route_id`, and `document` with `item[...]`.
  - A successful JSON response that omits one of those keys therefore raises raw `KeyError` instead of a controlled client/protocol error.
  - The service layer calls these methods from reconcile, route sync, route rollback, and route listing, so one partial admin payload can abort the whole operation.
- Evidence:
  - `list_consumers()` reads `item["consumer_id"]`, `item["username"]`, and `item["credential"]` directly.
  - `list_policy_bindings()` reads `item["binding_id"]` and `item["document"]` directly.
  - `list_routes()` reads `item["route_id"]` and `item["document"]` directly.
  - `GatewayBindingService.reconcile()` calls `list_consumers()`, `list_policy_bindings()`, and `list_routes()`, while `sync_service_routes()` / `list_service_routes()` also depend on `list_routes()`.
  - A local probe that mocked `/admin/consumers` as `{"items":[{"consumer_id":"c1","username":"alice"}]}` raised `KeyError: 'credential'`.
- Why it is a bug:
  - External admin APIs can return partial or drifted payloads.
  - Those cases should surface a clear gateway-client validation error, not a low-level `KeyError` that aborts reconcile/sync flows with little context.
- Suggested validation:
  - Mock `/admin/consumers`, `/admin/policy-bindings`, or `/admin/routes` to return HTTP `200` plus an item missing one required field such as `credential`, `binding_id`, or `route_id`, then call the corresponding list/reconcile flow and observe the raw `KeyError`.

### BUG-312 — Gateway route sync trusts arbitrary `route_config` dicts and crashes on missing required fields (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/access_control/gateway_binding/routes.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The HTTP route model accepts `route_config: dict[str, Any]` with no nested schema.
  - `sync_service_routes()`, `delete_service_routes()`, `rollback_service_routes()`, and reconcile all pass that dict into `_service_route_documents()`.
  - `_service_route_documents()` and `_route_document()` then index required keys like `service_id`, `service_name`, `namespace`, `route_id`, and `target_service` directly.
  - Malformed route configs therefore crash the gateway-binding flow with raw `KeyError` instead of being rejected as invalid route configuration.
- Evidence:
  - `ServiceRouteRequest` declares `route_config: dict[str, Any]`.
  - `_service_route_documents()` reads `route_config["service_id"]`, `route_config["service_name"]`, `route_config["namespace"]`, `default_route["route_id"]`, and `version_route["route_id"]` directly.
  - `_route_document()` reads `route_definition["target_service"]` directly.
  - A local probe with `{"service_id":"svc","service_name":"Svc"}` raised `KeyError: 'namespace'`.
  - A local probe with `default_route={"target_service":{"name":"svc-v1"}}` raised `KeyError: 'route_id'`.
  - A local probe with `default_route={"route_id":"svc-active"}` raised `KeyError: 'target_service'`, and running `GatewayBindingService.sync_service_routes(...)` with that payload also leaked `KeyError: 'target_service'`.
- Why it is a bug:
  - Admin callers and reconcile code should get a deterministic validation failure for malformed route configs.
  - Instead, a partial request body or partially written stored `route_config` can take down sync/delete/rollback flows with an opaque exception.
- Suggested validation:
  - Call `POST /api/v1/gateway-binding/service-routes/sync` (or trigger reconcile against a stored `route_config`) with a route config that omits `namespace`, `route_id`, or `target_service`, and observe the server error instead of a clear validation response.

### BUG-313 — Artifact create/update accept malformed `route_config` and persist latent gateway-sync crashes (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/registry_client/models.py`
  - `apps/compiler_api/repository.py`
  - `apps/compiler_api/routes/artifacts.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The artifact registry API validates `ir_json` and `raw_ir_json`, but `route_config` is declared as an arbitrary `dict[str, Any]` with no structural validation.
  - `ArtifactRegistryRepository.create_version()` and `update_version()` then persist that dict unchanged.
  - A malformed route config can therefore be written successfully and sit in the registry until a later activate/delete/reconcile flow tries to sync routes.
  - At that point the stored config hits the missing-key crash path described in `BUG-312`.
- Evidence:
  - `ArtifactVersionCreate.route_config` and `ArtifactVersionUpdate.route_config` are plain `dict[str, Any] | None`.
  - Their validators only call `ServiceIR.model_validate(...)`; they never validate `route_config`.
  - `ArtifactRegistryRepository.create_version()` stores `route_config=payload.route_config`, and `update_version()` does `record.route_config = payload.route_config`.
  - A local probe successfully constructed `ArtifactVersionCreate(..., route_config={'service_id':'svc-1','service_name':'Svc 1'})` and `ArtifactVersionUpdate(route_config={'default_route': {'route_id': 'svc-active'}})` with no validation error.
  - Feeding that accepted create payload into `_service_route_documents(...)` then raised `KeyError: 'namespace'`.
- Why it is a bug:
  - The write boundary should reject malformed route metadata before it is committed.
  - As written, the registry can persist poison-pill `route_config` values that only fail later during activation, deletion, or reconcile, which makes the root cause harder to diagnose and repair.
- Suggested validation:
  - `POST` or `PUT` an artifact version whose `route_config` omits fields such as `namespace` or `target_service`, confirm the registry accepts the write, then activate/reconcile that version and observe the later route-sync crash.

### BUG-314 — Deploy rollback only type-checks rollback payload shells and then crashes on missing keys (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `deploy_rollback()` checks only that `rollback_payload["manifest_set"]` and `rollback_payload["deployment"]` are dicts.
  - It then calls `_deserialize_manifest_set(...)` and constructs `DeploymentResult(...)` by indexing required subkeys directly.
  - Partial rollback payloads therefore raise raw `KeyError` during failure handling instead of logging and skipping the malformed rollback payload.
  - Because this runs on the rollback path, the secondary `KeyError` can mask the original deploy failure and prevent cleanup.
- Evidence:
  - `deploy_rollback()` returns early only when `manifest_payload` or `deployment_payload` are not dicts.
  - `_deserialize_manifest_set()` directly indexes `payload["config_map"]`, `payload["deployment"]`, `payload["service"]`, `payload["network_policy"]`, `payload["route_config"]`, and `payload["yaml"]`.
  - `deploy_rollback()` then reads `deployment_payload["deployment_revision"]`, `deployment_payload["runtime_base_url"]`, and `deployment_payload["manifest_storage_path"]`.
  - A local probe calling the deploy rollback handler with `manifest_set` missing `config_map` raised `KeyError: 'config_map'`.
  - A second local probe with a complete `manifest_set` but `deployment` missing `runtime_base_url` raised `KeyError: 'runtime_base_url'`.
- Why it is a bug:
  - Rollback handlers should be resilient because they execute after something has already gone wrong.
  - Here a partially populated rollback payload turns a recoverable cleanup path into another opaque failure that obscures the original cause.
- Suggested validation:
  - Trigger the deploy rollback handler with a rollback payload whose `manifest_set` or `deployment` dict is present but missing one required key, and observe the raw `KeyError` instead of a logged skip or controlled rollback failure.

### BUG-315 — Gateway route rollback blindly restores arbitrary caller-supplied `previous_routes` (Agent c fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/routes.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The rollback API accepts `previous_routes: dict[str, dict[str, Any]]` from the request body with no schema or ownership validation beyond “outer value is a dict”.
  - `rollback_service_routes()` computes the target service’s expected route IDs from `route_config`, but then restores every `(route_id, document)` pair from `previous_routes.items()` without checking that those route IDs belong to the same service.
  - One rollback request can therefore inject or overwrite unrelated gateway routes instead of only restoring the service being rolled back.
  - The same blind restore path can also reintroduce malformed route documents that later poison route listing / proxy paths.
- Evidence:
  - `ServiceRouteRequest.previous_routes` is declared as `dict[str, dict[str, Any]]`.
  - `GatewayBindingService.rollback_service_routes()` skips deletion only for target route IDs present in `previous_routes`, then unconditionally runs `await self._client.upsert_route(route_id=route_id, document=document)` for every entry in `previous_routes.items()`.
  - A local probe called `rollback_service_routes()` with `route_config.service_id == "svc-1"` but `previous_routes={"unrelated-admin": {...service_id: "admin-ui"...}}`.
  - That probe returned `{"route_ids": ["svc-1"], "service_routes_synced": 1, "service_routes_deleted": 1, ...}` while the in-memory gateway ended up storing only the unrelated `unrelated-admin` route document.
- Why it is a bug:
  - A route rollback endpoint should restore only the routes that previously belonged to the service/version being rolled back.
  - As written, any authorized caller can use a service-scoped rollback request to inject or overwrite arbitrary gateway route IDs, crossing service boundaries and corrupting gateway state.
- Suggested validation:
  - Call `POST /api/v1/gateway-binding/service-routes/rollback` with a normal `route_config` for service `A` but a `previous_routes` map containing a route document for service `B`, then inspect the gateway state and observe that the unrelated route was restored or overwritten.

### BUG-316 — Malformed JSON-RPC error envelopes are reported as successful runtime results (Agent c fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - The runtime checks `_jsonrpc_error_message()` before sanitizing a JSON-RPC response.
  - `_jsonrpc_error_message()` only treats the envelope as a failure when `payload["error"]` is a dict; otherwise it returns `None`.
  - `_unwrap_jsonrpc_payload()` then returns `payload["result"]` if present, but if no `result` exists it returns the whole payload unchanged.
  - A protocol-invalid error envelope like `{"jsonrpc":"2.0","error":"boom","id":1}` therefore bubbles out as a normal `status="ok"` tool result instead of a `ToolError`.
- Evidence:
  - `_jsonrpc_error_message()` does `error = payload.get("error")` followed by `if not isinstance(error, dict): return None`.
  - `_unwrap_jsonrpc_payload()` returns `payload["result"]` only when the key exists and otherwise falls back to `return payload`.
  - A local probe with `RuntimeProxy._jsonrpc_error_message(...)` against HTTP `200` / `application/json` / `{"jsonrpc":"2.0","error":"boom","id":1}` returned `None`.
  - The same probe showed `_sanitize_response(...)` returning the raw error envelope, and a full `RuntimeProxy.invoke(...)` call returned `{"status": "ok", ..., "result": {"jsonrpc": "2.0", "error": "boom", "id": 1}}`.
- Why it is a bug:
  - Once an operation is marked as JSON-RPC, an upstream error envelope should never be reported as a successful tool invocation.
  - Treating malformed JSON-RPC failures as success can mislead callers, automations, and post-deploy validation into accepting failed upstream executions as good results.
- Suggested validation:
  - Mock a JSON-RPC upstream to return HTTP `200` with `{"jsonrpc":"2.0","error":"boom","id":1}` and observe that the runtime returns `status="ok"` instead of surfacing an invalid-response or protocol error.

### BUG-317 — Gateway admin client silently treats missing `items` collections as “no resources” (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/access_control/gateway_binding/client.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The HTTP gateway admin client normalizes list responses through `_items_from_payload(payload)`.
  - That helper does `payload.get("items", [])`, so a successful object response that omits the `items` field is silently interpreted as an empty resource list instead of a malformed admin response.
  - `list_consumers()`, `list_policy_bindings()`, and `list_routes()` all inherit this behavior.
  - As a result, bad successful admin payloads can make gateway state appear empty, hide drift from operators, and cause higher-level list/reconcile flows to operate on false “nothing exists” assumptions.
- Evidence:
  - `_items_from_payload()` assigns `items = payload.get("items", [])` and only raises when the resolved value exists but is not a list.
  - `HTTPGatewayAdminClient.list_consumers()`, `list_policy_bindings()`, and `list_routes()` all build their return maps from `_items_from_payload(payload)`.
  - A local probe with fake HTTP `200` `{}` responses made all three list methods return empty dicts: `routes {}`, `consumers {}`, `bindings {}`.
  - A second probe wired through `GatewayBindingService.list_service_routes()` returned `[]` for that same malformed `{}` admin payload instead of surfacing a contract error.
- Why it is a bug:
  - A successful admin response that omits its required collection field is malformed and should not be conflated with “there are currently zero resources”.
  - The current behavior silently hides upstream regressions or partial proxy responses, which can make the gateway overview appear empty and cause reconcile logic to miss real existing resources/drift.
- Suggested validation:
  - Make `/admin/routes`, `/admin/consumers`, or `/admin/policy-bindings` return HTTP `200` with `{}` and observe that the current client/service stack reports an empty state instead of raising a gateway admin response error.

### BUG-318 — Malformed OData error envelopes are reported as successful runtime results (Agent c fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - The runtime checks `_odata_error_message()` before sanitizing an OData response.
  - `_odata_error_message()` only recognizes failures when `payload["error"]` is an object; otherwise it returns `None`.
  - `_unwrap_odata_payload()` only unwraps collection payloads and otherwise leaves the body unchanged.
  - A protocol-invalid but clearly failure-shaped OData response like `{"error":"boom"}` therefore bubbles out as a normal `status="ok"` tool result instead of a `ToolError`.
- Evidence:
  - `_odata_error_message()` does `error = payload.get("error")` followed by `if not isinstance(error, dict): return None`.
  - `_sanitize_response()` later calls `_unwrap_odata_payload(payload)`, which returns the original payload unchanged unless it finds a list-valued `"value"` field.
  - A local probe using an OData `ServiceIR` and HTTP `200 application/json` body `{"error":"boom"}` returned `{"status":"ok","result":{"error":"boom"}}` from `RuntimeProxy.invoke(...)`.
- Why it is a bug:
  - Once an operation is declared OData, a response carrying the protocol’s error envelope key should never be surfaced as a successful tool invocation just because the envelope is malformed.
  - This can mislead callers and validation flows into treating upstream failures as successful results.
- Suggested validation:
  - Mock an OData upstream to return HTTP `200` with `{"error":"boom"}` and observe that the runtime currently returns `status="ok"` instead of surfacing an invalid-response or OData error.

### BUG-319 — Malformed SCIM error envelopes are reported as successful runtime results (Agent c fixed)

- Severity: High
- Files:
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - The runtime’s SCIM error helper requires `payload["schemas"]` to be a list containing an `...Error` URN.
  - If a failure-shaped SCIM payload arrives with a malformed `schemas` field, `_scim_error_message()` returns `None`.
  - `_unwrap_scim_payload()` only unwraps list responses and otherwise leaves the payload untouched.
  - A protocol-invalid SCIM error envelope like `{"schemas":"urn:ietf:params:scim:api:messages:2.0:Error","detail":"boom"}` is therefore reported as a normal successful result.
- Evidence:
  - `_scim_error_message()` does `schemas = payload.get("schemas")` and immediately returns `None` when `schemas` is not a list.
  - `_sanitize_response()` later routes SCIM payloads through `_unwrap_scim_payload(payload)`, which returns the original payload when there is no list-valued `"Resources"` field.
  - A local probe using a SCIM `ServiceIR` and HTTP `200 application/json` body `{"schemas":"urn:ietf:params:scim:api:messages:2.0:Error","detail":"boom"}` returned `{"status":"ok","result":{"schemas":"urn:ietf:params:scim:api:messages:2.0:Error","detail":"boom"}}` from `RuntimeProxy.invoke(...)`.
- Why it is a bug:
  - Once an operation is declared SCIM, a failure payload should not be accepted as a successful tool invocation merely because the error envelope is slightly malformed.
  - This can mask upstream SCIM failures and mislead callers or automated validation that trust `status="ok"`.
- Suggested validation:
  - Mock a SCIM upstream to return HTTP `200` with `{"schemas":"urn:ietf:params:scim:api:messages:2.0:Error","detail":"boom"}` and observe that the runtime currently returns `status="ok"` instead of surfacing a protocol error.

### BUG-320 — Route rollback trusts non-dict `publication` payloads and crashes with raw `AttributeError` (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `route_rollback()` checks only that `rollback_payload["route_config"]` is a dict.
  - It then casts `rollback_payload.get("publication")` to `dict[str, Any] | None` without validating the runtime type.
  - `AccessControlRoutePublisher.rollback()` immediately evaluates `(publication or {}).get("previous_routes", {})`.
  - A truthy non-dict `publication` value such as a string or list therefore raises raw `AttributeError` during rollback handling.
- Evidence:
  - `route_rollback()` does `publication = cast(dict[str, Any] | None, rollback_payload.get("publication"))` and passes it straight to `resolved_route_publisher.rollback(...)`.
  - `AccessControlRoutePublisher.rollback()` then does `(publication or {}).get("previous_routes", {})`.
  - A local probe invoking the route rollback handler with a valid `route_config` and `publication="published"` raised `AttributeError: 'str' object has no attribute 'get'`.
- Why it is a bug:
  - Rollback handlers run after another stage has already failed and should be defensive about malformed stored rollback payloads.
  - Here a bad `publication` value turns cleanup into another opaque exception that can mask the original route-stage failure.
- Suggested validation:
  - Trigger the route rollback handler with a rollback payload whose `route_config` is valid but whose `publication` is a truthy non-dict value, and observe the raw `AttributeError` instead of a logged skip or controlled rollback failure.

### BUG-321 — Gateway admin mock crashes when a stored route has a non-numeric `target_service.port` (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/gateway_admin_mock/main.py`
- Summary:
  - The gateway admin mock accepts arbitrary route documents because `RouteUpsertRequest.document` is just `dict[str, Any]` and `upsert_route()` stores it verbatim.
  - The proxy path later reads `route_document["target_service"]` and converts `target_service["port"]` with `int(...)` in both `_service_key()` and `_upstream_base_url()`.
  - A stored route whose nested port is a non-numeric string therefore crashes the proxy path with raw `ValueError`.
- Evidence:
  - `RouteUpsertRequest` declares only `document: dict[str, Any]`, and `upsert_route()` stores `request.document` without validating nested shape.
  - `_service_key()` does `port = int(target_service["port"])`, and `_upstream_base_url()` repeats the same conversion.
  - A local probe `PUT`ing a route document whose `target_service.port` was `"oops"` succeeded with HTTP `200`, and the subsequent `GET /gateway/svc` raised `ValueError: invalid literal for int() with base 10: 'oops'`.
- Why it is a bug:
  - Bad admin/test route documents should produce a controlled contract error rather than an unhandled exception from the proxy path.
  - Because the invalid document is accepted on write and fails only on use, the mock can hide the root cause until requests begin routing through it.
- Suggested validation:
  - Store a route document whose `target_service.port` is a non-numeric string and then proxy a request through that route; the current mock will crash with `ValueError`.

### BUG-322 — Proof/validator `/tools` consumers silently treat a missing `tools` field as an empty listing (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
  - `libs/validator/post_deploy.py`
- Summary:
  - Both the proof runner and the post-deploy validator call `response.json()` for `/tools` and then iterate `payload.get("tools", [])`.
  - A successful object payload that omits the required `tools` collection is therefore silently interpreted as “the runtime exposes zero tools” instead of being rejected as malformed.
  - In the proof runner this produces misleading downstream audit failures based on an empty tool set.
  - In the validator it can even produce a false positive for zero-operation services, because `{}` is treated the same as a valid empty tool listing.
- Evidence:
  - `_fetch_runtime_tool_names()` in `live_llm_e2e.py` does `payload = response.json()` and then iterates `payload.get("tools", [])`.
  - `_validate_tool_listing()` in `post_deploy.py` builds `listed_tools` from `payload.get("tools", [])` with no required-field check.
  - A local probe with a mocked HTTP `200` `{}` `/tools` response made `_fetch_runtime_tool_names()` return `set()`.
  - The same probe made `PostDeployValidator._validate_tool_listing(...)` report `passed=True` with details `Runtime exposes expected tools: [].` for an otherwise valid zero-operation `ServiceIR`.
- Why it is a bug:
  - Omitting the primary collection from a successful `/tools` response is a contract violation, not a legitimate empty listing.
  - Treating it as empty state can create misleading proof failures, hide runtime regressions, and even let malformed control-plane responses pass validation for zero-operation services.
- Suggested validation:
  - Return HTTP `200` with `{}` from a runtime `/tools` endpoint and observe that the proof runner interprets it as an empty tool set while the post-deploy validator can accept it as a valid listing for a zero-operation service.

### BUG-323 — Gateway sync/rollback routes access route_config mandatory fields without validation, raising raw KeyError (Agent c fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/service.py`
  - `apps/access_control/gateway_binding/routes.py`
- Summary:
  - `sync_service_routes()` and `_service_route_documents()` access `route_config["service_id"]`, `route_config["service_name"]`, and `route_config["namespace"]` with direct dictionary indexing.
  - These keys are never validated to exist before access, so a malformed `route_config` dict that is missing any of these required fields raises raw `KeyError` instead of a controlled validation error.
  - The route publication endpoints (`/service-routes/sync`, `/service-routes/delete`, `/service-routes/rollback`) accept arbitrary `ServiceRouteRequest` payloads and pass them to the service layer, which then crashes on missing keys.
- Evidence:
  - `sync_service_routes()` at line 76 does `service_id = str(route_config["service_id"])` without checking if the key exists.
  - `_service_route_documents()` at lines 323–325 accesses `route_config["service_id"]`, `route_config["service_name"]`, and `route_config["namespace"]` with direct indexing.
  - A local probe with `{"service_name": "test", "namespace": "default"}` (missing `service_id`) to `/api/v1/gateway-binding/service-routes/sync` returned HTTP 500 with raw `KeyError: 'service_id'` instead of a `422` validation error.
- Why it is a bug:
  - Route configuration payloads that are structurally incomplete should produce a 422 contract error at the HTTP layer, not an unhandled 500 exception.
  - Callers expect documented request shapes and should not see internal exception traces for malformed input.
- Suggested validation:
  - `POST /api/v1/gateway-binding/service-routes/sync` with a `route_config` missing `service_id`, `service_name`, or `namespace`, and observe raw `KeyError` instead of a 422 response.

### BUG-324 — _service_route_documents casts route_config fields to str without type validation, causing malformed document generation (Agent c fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - `_service_route_documents()` casts `route_config["service_id"]`, `route_config["service_name"]`, and `route_config["namespace"]` to `str()` without first validating their input types.
  - If any of these values are non-string types (e.g., `None`, a dict, a list), `str()` will silently convert them to their string representation (e.g., `"None"`, `"{'field': 'value'}"`).
  - This can cause route documents to contain malformed service identifiers or names that don't match the actual service, leading to routing failures or route conflicts.
- Evidence:
  - Lines 323–325 do `service_id = str(route_config["service_id"])`, `service_name = str(route_config["service_name"])`, `namespace = str(route_config["namespace"])` with no type guards.
  - A local probe with `{"service_id": None, "service_name": "test", "namespace": "default", "version_number": 1, "default_route": {...}}` to the sync endpoint created a route document with `service_id: "None"` (string literal) instead of rejecting the `None` value.
- Why it is a bug:
  - Downstream routing decisions and gateway reconciliation logic rely on exact service identifiers; silently stringifying non-string values can cause routes to target wrong or phantom services.
  - The `str()` conversion hides a type contract violation that should fail at input validation time.
- Suggested validation:
  - `POST /api/v1/gateway-binding/service-routes/sync` with `route_config` where `service_id` is `null`, `123`, or `[]`, and observe that a route document is created with the stringified representation instead of a 422 error.

### BUG-325 — Artifact create route doesn't use commit=False for audit log, causing double database commit (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_api/routes/artifacts.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - `create_artifact_version()` calls `repository.create_version()` (line 48), which internally commits the transaction (line 294 in repository.py).
  - After the commit succeeds and the version is returned, the route then calls `audit_log.append_entry()` without the `commit=False` flag (line 56).
  - `AuditLogService.append_entry()` defaults to `commit=True`, causing a second commit to occur after the artifact mutation has already been persisted.
  - This is inconsistent with `delete_artifact_version()` (line 174) and `activate_artifact_version()` (line 220), which both correctly use `commit=False` when appending audit entries within a try/except block.
- Evidence:
  - `repository.create_version()` does `await self._session.commit()` at line 294.
  - `create_artifact_version()` then calls `await audit_log.append_entry(...)` without `commit=False` at line 56; `append_entry()` defaults to `commit=True` and calls `await self._session.commit()` again at line 40 of audit/service.py.
  - A local transactional probe inserting an artifact and intercepting the session showed two distinct commits: one from `create_version()` and one from `append_entry()`.
- Why it is a bug:
  - Double commits can cause audit log entries to become visible to concurrent readers before the route handler completes, increasing the window for observing inconsistent state.
  - The pattern violates transactional atomicity and is at odds with the safer pattern used in delete/activate routes.
- Suggested validation:
  - Create an artifact version through the API, instrument the database to count transaction commits, and observe two commits instead of one.

### BUG-326 — Artifact update route doesn't use commit=False for audit log, causing double database commit (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_api/routes/artifacts.py`
  - `apps/compiler_api/repository.py`
- Summary:
  - `update_artifact_version()` calls `repository.update_version()` (line 106), which internally commits the transaction (line 394 in repository.py).
  - After the commit succeeds and the version is returned, the route then calls `audit_log.append_entry()` without the `commit=False` flag (line 116).
  - This causes a second commit to occur after the artifact update has already been persisted.
  - This mirrors the pattern bug in `create_artifact_version()` and is inconsistent with the safer `delete_artifact_version()` and `activate_artifact_version()` implementations.
- Evidence:
  - `repository.update_version()` does `await self._session.commit()` at line 394.
  - `update_artifact_version()` then calls `await audit_log.append_entry(...)` without `commit=False` at line 116; `append_entry()` commits again.
  - A local transactional probe updating an artifact showed two distinct commits.
- Why it is a bug:
  - Double commits split the mutation and its audit entry into separate transactions, violating atomicity and creating a window where the artifact is updated but the audit entry has not yet been logged.
- Suggested validation:
  - Update an artifact version through the API, instrument the database to count commits, and observe two commits instead of one.

### BUG-327 — SSE event stream has no exception handling for JSON serialization failures, causing silent stream termination (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/compiler_api/routes/compilations.py`
- Summary:
  - `stream_compilation_events()` creates an async generator `event_stream()` that yields SSE-formatted events.
  - The generator calls `_format_sse_event()` (line 236), which internally calls `json.dumps()` to serialize the event payload (line 255).
  - If the payload contains non-JSON-serializable objects (e.g., a `datetime` that was not properly converted to string, or a custom object), `json.JSONEncodeError` is raised inside the generator.
  - Because the event_stream generator has no try/except around the `json.dumps()` call, the exception propagates and terminates the stream without notifying the client.
  - Clients connected to the SSE stream simply see the connection close without an error message or reason.
- Evidence:
  - `event_stream()` at line 236 does `yield _format_sse_event(event.event_type, event.model_dump(mode="json"))` without exception handling.
  - `_format_sse_event()` at line 255 calls `json.dumps(payload, separators=(',', ':'))` without catching `JSONEncodeError`.
  - A local probe creating a compilation event with a non-JSON-serializable object in the detail field and streaming events caused the generator to raise `TypeError` during JSON encoding, terminating the stream.
- Why it is a bug:
  - JSON serialization failures in streaming responses should be caught and converted to a protocol-compliant error frame (or plain-text error) so clients understand why the stream ended.
  - Silently terminating the stream makes it difficult for clients to distinguish between a normal job completion and an internal server error.
- Suggested validation:
  - Create a compilation event with a non-JSON-serializable `detail` field, stream events via SSE, and observe that the connection terminates without an error message instead of surfacing a JSON encoding error.

### BUG-328 — _service_route_documents doesn't validate required nested route fields before accessing them, raising raw KeyError (Agent c fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - `_service_route_documents()` checks only `isinstance(route_config.get("default_route"), dict)` before extracting nested fields from `default_route` (lines 328–339).
  - Once it confirms `default_route` is a dict, it immediately accesses `default_route["route_id"]` and later `route_definition["target_service"]` without validating these keys exist.
  - If `default_route` is missing `"route_id"` or `target_service` is missing, raw `KeyError` is raised inside the helper function instead of a controlled validation error.
  - The same issue applies to `version_route` handling (lines 341–352).
- Evidence:
  - Line 330 does `route_id = str(default_route["route_id"])` immediately after the isinstance check, with no `.get()` fallback.
  - Lines 377–382 access `route_definition["target_service"]`, `route_definition.get("switch_strategy")`, and `route_definition.get("match")`, but `["target_service"]` uses direct indexing and will raise KeyError if the key is absent.
  - A local probe with a `default_route` object that is a dict but has no `"route_id"` field raises `KeyError: 'route_id'` at line 330.
- Why it is a bug:
  - Gateway route documents should have well-defined required fields enforced at the API boundary, not deep inside a service helper where they cause unhandled exceptions.
  - Missing required fields should produce a 422 validation error, not a 500 internal error.
- Suggested validation:
  - `POST /api/v1/gateway-binding/service-routes/sync` with a `route_config` where `default_route` or `version_route` is a dict but is missing `route_id`, or where `route_definition["target_service"]` is missing, and observe raw `KeyError` instead of a 422 response.

### BUG-329 — Authz evaluate route guards audit_log.append_entry with hasattr instead of always logging (Agent c fixed)

- Severity: Low
- Files:
  - `apps/access_control/authz/routes.py`
- Summary:
  - `evaluate_policy()` receives `audit_log: AuditLogService = Depends(get_audit_log_service)` as a required dependency injection.
  - However, before calling `audit_log.append_entry()`, the route checks `if hasattr(audit_log, "append_entry")` (line 175).
  - Because `audit_log` is always injected from a concrete dependency (never None or mocked in a way that lacks the method), this guard will always be true.
  - The pattern suggests either defensive programming against a guarantee that doesn't exist, or leftover code from a refactoring.
- Evidence:
  - `get_audit_log_service()` (line 20 of audit/routes.py) always returns a fresh `AuditLogService(session)` instance, which has `append_entry` method.
  - The `Depends()` injection will never return None or a duck-typed object lacking the method in the normal code path.
  - The hasattr guard at line 175 of authz/routes.py is therefore unreachable dead code or overly defensive.
- Why it is a bug:
  - Unnecessary type guards reduce code clarity and suggest uncertainty about the contract that shouldn't exist.
  - If audit logging should truly be optional, the dependency should be optional (e.g., `Depends(get_audit_log_service, skip_error=True)`), not guarded by hasattr after injection.
- Suggested validation:
  - Verify that `POST /api/v1/authz/evaluate` always logs audit entries by tracing the audit table; the hasattr guard never prevents the call.


### BUG-322 — Streaming lifecycle validated by type-only, silently accepts empty dict (Agent c fixed)

- Severity: High
- Files:
  - `apps/proof_runner/live_llm_e2e.py` line 989
  - `libs/validator/post_deploy.py` line 515
- Summary:
  - Both `_generated_tool_audit_failure_reason()` and `PostDeployValidator._validate_invocation_smoke()` validate streaming results with only a type check: `if not isinstance(events, list) or not isinstance(lifecycle, dict)`.
  - The check validates that `lifecycle` is a dict but NOT that it contains required fields.
  - According to `apps/mcp_runtime/grpc_stream.py`, lifecycle must contain: `termination_reason`, `messages_collected`, `rpc_path`, `mode`.
  - A malformed empty `lifecycle: {}` passes validation but lacks all required fields.
- Evidence:
  - `_generated_tool_audit_failure_reason()` at line 989 performs only type check: `if not isinstance(lifecycle, dict)`.
  - `PostDeployValidator._validate_invocation_smoke()` at line 515 repeats same pattern.
  - `apps/mcp_runtime/grpc_stream.py` returns lifecycle with four required fields defined in the return dict.
  - A local probe with streaming result `{"events": [], "lifecycle": {}}` passed both validators instead of being rejected.
- Why it is a bug:
  - Streaming lifecycle structure is a control-plane/runtime contract; malformed envelopes should not be accepted as valid.
  - Empty dict passes type check but violates the actual contract schema, enabling malformed results to propagate.
  - Validators are supposed to catch contract violations before results are trusted downstream.
- Suggested validation:
  - Mock a streaming tool invocation to return `{"events": [], "lifecycle": {}}`.
  - Observe that both validators currently accept this as valid and pass the smoke test instead of reporting structural error.

### BUG-323 — operations_enhanced field silently coerced from string without error handling (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py` line 734
- Summary:
  - `_operations_enhanced_from_events()` does: `return int(detail.get("operations_enhanced", 0) or 0)`.
  - No guard on the type of `operations_enhanced` before calling `int()`.
  - If the field is a non-numeric string like `"abc"`, `int("abc")` raises unhandled `ValueError`.
  - If field is validly parseable like `"3"`, `int("3")` succeeds but masks a schema violation where the field should be numeric.
- Evidence:
  - Line 734: `return int(detail.get("operations_enhanced", 0) or 0)` with no prior `isinstance(value, int)` check.
  - Compilation event with `{"detail": {"operations_enhanced": "not_a_number"}}` crashes with `ValueError: invalid literal for int() with base 10: 'not_a_number'`.
  - Compilation event with `{"detail": {"operations_enhanced": "3"}}` silently succeeds via coercion despite type mismatch.
- Why it is a bug:
  - The contract specifies `operations_enhanced` as an integer in the enhance-stage event payload.
  - Non-integer values should trigger a validation error, not crash with unhandled exception or silently coerce.
  - Hides upstream schema violations in event structures and prevents proper validation.
- Suggested validation:
  - Create a compilation with LLM enhancement that returns an `enhance` stage event with `"operations_enhanced": "not_a_number"`.
  - Observe `ValueError: invalid literal for int()` crashes the proof runner instead of reporting a validation error.

### BUG-324 — _fetch_runtime_tool_names response not type-cast, crashes if non-dict (Agent c fixed)

- Severity: High
- Files:
  - `apps/proof_runner/live_llm_e2e.py` line 953
- Summary:
  - `_fetch_runtime_tool_names()` does: `payload = response.json()` WITHOUT casting to `dict[str, Any]`.
  - Immediately after (line 956): `for tool in payload.get("tools", [])` assumes payload is a dict.
  - If runtime returns a list `[]` or string `"error"` or any non-dict JSON, the `.get()` call crashes with `AttributeError`.
  - Other similar response handlers in the same file (lines 681, 741, 755) properly cast to `dict[str, Any]`.
- Evidence:
  - Line 953: `payload = response.json()` — no cast, no type guard.
  - Line 956-957: `for tool in payload.get("tools", [])` — assumes dict without validation.
  - Runtime `/tools` endpoint returns JSON array `[]` instead of `{"tools": []}` → `AttributeError: 'list' object has no attribute 'get'`.
  - Other endpoints in the same file use pattern: `payload = cast(dict[str, Any], response.json())`.
- Why it is a bug:
  - `response.json()` can return any valid JSON type; the caller must validate and cast.
  - Inconsistent with other response handlers in same file that DO cast to dict.
  - Crashes with `AttributeError` instead of producing a controlled validation error.
- Suggested validation:
  - Mock runtime `/tools` endpoint to return `[]` (JSON array instead of object).
  - Observe `AttributeError: 'list' object has no attribute 'get'` crashes `_fetch_runtime_tool_names()` instead of returning validation error.

### BUG-325 — Missing 'active_version' key raises KeyError instead of controlled error (Agent b fixed)

- Severity: High
- Files:
  - `apps/proof_runner/live_llm_e2e.py` line 744
- Summary:
  - `_active_version_for_service()` does: `return int(service["active_version"])`.
  - Direct dict access without `.get()` means missing key raises unhandled `KeyError`.
  - Service in catalog might be missing the field due to partial response or control-plane contract violation.
  - Other accesses in the function properly use `.get()` with defaults.
- Evidence:
  - Line 743 uses safe access: `if isinstance(service, dict) and service.get("service_id") == service_id:`.
  - Line 744 uses unsafe access: `return int(service["active_version"])` — no fallback.
  - Service object from `/api/v1/services` without `active_version` field raises `KeyError: 'active_version'`.
- Why it is a bug:
  - Service catalog contract requires `active_version`; if missing, should be a validation error not unhandled exception.
  - Masks upstream/control-plane contract violations and prevents proper error reporting.
  - Inconsistent with safe access patterns used in the same function.
- Suggested validation:
  - Mock `/api/v1/services` to return service object without `active_version` field.
  - Observe `KeyError: 'active_version'` crashes instead of controlled error message.

### BUG-326 — Missing 'ir_json' key in artifact response raises KeyError (Agent b fixed)

- Severity: High
- Files:
  - `apps/proof_runner/live_llm_e2e.py` line 602
- Summary:
  - `live_llm_proof()` does: `artifact_ir = cast(dict[str, Any], artifact["ir_json"])`.
  - Cast only claims type safety but does not check if field exists.
  - Missing `ir_json` key raises unhandled `KeyError`.
  - Artifact endpoint might return partial response or missing nested fields due to upstream error or contract violation.
- Evidence:
  - Line 602: `artifact_ir = cast(dict[str, Any], artifact["ir_json"])` — direct access after cast without field validation.
  - Line 755 returns artifact from `response.json()` without prior field validation.
  - Artifact response without `ir_json` field (e.g., `{"version": 1, "errors": [...]}`) crashes with `KeyError: 'ir_json'`.
- Why it is a bug:
  - Artifact contract requires `ir_json` field; missing field indicates upstream error or response truncation.
  - Should produce validation error, not unhandled exception.
  - Prevents proper error diagnosis when upstream artifacts are malformed.
- Suggested validation:
  - Mock artifact endpoint to return object without `ir_json` field (e.g., `{"version": 1}`).
  - Observe `KeyError: 'ir_json'` crashes instead of controlled error message.

### BUG-327 — SSE parser overwrites multiline data fields instead of accumulating (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py` lines 711-716
- Summary:
  - SSE spec (RFC 6202 / WHATWG spec) allows multiple `data:` lines to form multiline content.
  - Standard accumulation: `data: line1` + `data: line2` should produce `"line1\nline2"`.
  - Current code (lines 712-714) assigns directly without accumulation: `current_event["data"] = json.loads(raw_data)`.
  - Only the LAST `data:` line is kept; earlier lines are overwritten and lost.
  - This breaks parsing of large JSON payloads chunked across multiple `data:` lines.
- Evidence:
  - Lines 711-716 perform: 
    ```python
    if line.startswith("data:"):
        raw_data = line.partition(":")[2].strip()
        current_event["data"] = json.loads(raw_data)  # OVERWRITES previous
    ```
  - Valid SSE with split JSON like:
    ```
    data: {"long_
    data: field":"value"}
    ```
    becomes `json.loads('"value"}')` → `JSONDecodeError` instead of `json.loads('{"long_field":"value"}')`
  - Test suite (line 216) only covers single-line invalid JSON; no multiline accumulation tests.
- Why it is a bug:
  - Large or multipart JSON payloads must be chunked across multiple `data:` lines per SSE standard.
  - Code silently accepts multiline SSE but cannot parse it correctly.
  - Prevents use of streaming responses with large event payloads.
- Suggested validation:
  - Send SSE event with multiline data:
    ```
    event: msg
    data: {"stage":"enhance"
    data: ,"event_type":"stage.succeeded"}
    (blank line)
    ```
  - Observe `JSONDecodeError` or incorrect parsing result from `_parse_sse_events()` instead of correct accumulation.

### BUG-330 — Post-deploy validator silently accepts ambiguous streaming descriptors that runtime and proof-runner reject (Agent c fixed)

- Severity: High
- Files:
  - `libs/validator/post_deploy.py`
  - `apps/proof_runner/live_llm_e2e.py`
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - `PostDeployValidator._supported_descriptor_for_operation()` returns the first supported descriptor when the same operation has multiple supported event descriptors.
  - The proof runner and runtime do not accept that ambiguity: `apps/proof_runner/live_llm_e2e.py` raises `ValueError`, and `apps/mcp_runtime/proxy.py` raises `ToolError`.
  - Post-deploy smoke validation can therefore report success for an IR that the rest of the stack considers invalid and non-invokable.
- Evidence:
  - `libs/validator/post_deploy.py` lines 545-555 collect supported descriptors and do `if len(descriptors) > 1: return descriptors[0]`.
  - `apps/proof_runner/live_llm_e2e.py` lines 998-1010 raise `ValueError("Generated-tool audit does not support multiple descriptors ...")` for the same condition.
  - `apps/mcp_runtime/proxy.py` lines 507-518 raise `ToolError("Operation ... has multiple streaming descriptors and cannot be invoked unambiguously.")`.
  - A local probe built a `ServiceIR` whose `op1` had both supported SSE and supported WebSocket descriptors. The validator helper returned `sse`, the proof helper raised `ValueError`, and `RuntimeProxy._stream_descriptor_for_operation(...)` raised `ToolError`.
  - A second local probe ran `PostDeployValidator._validate_invocation_smoke(...)` against that IR with an `sse` invocation result and got `passed=True` with `details="Invocation smoke test succeeded for op1 using sse."`
- Why it is a bug:
  - The validator is supposed to catch bad deployed contracts before publication, not bless an IR shape that the runtime and proof harness reject as ambiguous.
  - Returning the first descriptor makes validation order-dependent and can hide real transport mismatches in production.
- Suggested validation:
  - Construct a service IR with one enabled operation and two supported descriptors for that operation.
  - Run post-deploy smoke validation with a fake invoker result that matches only one transport.
  - Observe that the validator currently passes even though proof-runner and runtime reject the same IR as ambiguous.

### BUG-331 — Proof/validator `/tools` consumers silently discard malformed tool entries and still report success (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/validator/post_deploy.py`
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - Both the post-deploy validator and proof runner normalize `/tools` responses by keeping only entries that are dicts with a string `name`.
  - Any malformed entries in the same payload are silently dropped instead of causing a contract failure.
  - A runtime can therefore return a wrong-shaped tool listing and still be treated as healthy as long as one valid-looking entry remains for each expected tool.
- Evidence:
  - `libs/validator/post_deploy.py` lines 348-352 build `listed_tools` with `{tool["name"]: tool for tool in payload.get("tools", []) if isinstance(tool, dict) and isinstance(tool.get("name"), str)}`.
  - `apps/proof_runner/live_llm_e2e.py` lines 954-957 build a set with the same filter pattern.
  - A local probe mocked `/tools` as `{"tools": [{"name": "op1"}, {"id": "broken-entry"}]}`. `PostDeployValidator._validate_tool_listing(...)` returned `passed=True` with `details="Runtime exposes expected tools: ['op1']."`
  - A matching local probe against `_fetch_runtime_tool_names(...)` returned `['op1']`, silently discarding the malformed entry.
- Why it is a bug:
  - Wrong-shaped tool entries indicate a runtime contract violation and should fail validation explicitly.
  - Silently filtering them makes the validator and proof runner hide schema regressions in the live `/tools` payload.
- Suggested validation:
  - Make `/tools` return a mix of one valid tool entry and one malformed entry missing `name`.
  - Observe that the current validator and proof helper accept the payload instead of rejecting it as invalid.

### BUG-332 — Proof/validator `/tools` consumers silently deduplicate duplicate tool names (Agent c fixed)

- Severity: Medium
- Files:
  - `libs/validator/post_deploy.py`
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - The post-deploy validator stores `/tools` entries in a dict keyed by name, and the proof runner stores tool names in a set.
  - Duplicate tool entries with the same name are therefore collapsed before validation.
  - A runtime can expose duplicate tools and still be reported as matching the expected tool set.
- Evidence:
  - `libs/validator/post_deploy.py` lines 348-352 key the tool listing by `tool["name"]`, so later duplicates overwrite earlier ones.
  - `apps/proof_runner/live_llm_e2e.py` lines 954-957 build a set of names, which also removes duplicates.
  - A local probe mocked `/tools` as `{"tools": [{"name": "op1"}, {"name": "op1"}]}`. `PostDeployValidator._validate_tool_listing(...)` still returned `passed=True` with `details="Runtime exposes expected tools: ['op1']."`
  - A matching local probe against `_fetch_runtime_tool_names(...)` returned `['op1']`, proving the duplicate was silently collapsed.
- Why it is a bug:
  - Duplicate tool names are ambiguous for clients and should be rejected, not normalized into a success-shaped result.
  - The current logic can mask runtime regressions where the same tool is registered or exposed multiple times.
- Suggested validation:
  - Make `/tools` return two entries with the same `name`.
  - Observe that the current validator and proof helper report a clean tool listing instead of flagging the duplicate registration.

### BUG-333 — Gateway route sync can delete unrelated existing routes named in caller-supplied `previous_routes` (Agent c fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/routes.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - The HTTP sync route forwards raw `previous_routes` from the request body into `GatewayBindingService.sync_service_routes(...)`.
  - `sync_service_routes()` then adds every `route_id` from that caller-controlled map to `stale_route_ids` whenever the route already exists and is not part of the new target route set.
  - As a result, a caller can cause sync to delete unrelated routes that belong to other services just by naming them in `previous_routes`.
- Evidence:
  - `apps/access_control/gateway_binding/routes.py` lines 88-90 call `gateway_binding.sync_service_routes(request.route_config, request.previous_routes)`.
  - `apps/access_control/gateway_binding/service.py` lines 83-88 do `stale_route_ids.update(route_id for route_id in previous_routes if route_id not in route_documents and route_id in existing_routes)`.
  - The same method then deletes every `route_id in stale_route_ids` at lines 96-97 with no ownership check against the target service.
  - A local probe seeded the in-memory gateway with an existing route `foreign-admin` whose `service_id` was `admin-ui`, then called `sync_service_routes(...)` for `svc-1` with `previous_routes={"foreign-admin": {"route_id": "foreign-admin"}}`.
  - After the call, the foreign route was gone and the gateway contained only `svc-1-active` and `svc-1-v7`; the returned summary reported `service_routes_deleted: 1`.
- Why it is a bug:
  - Sync should only prune routes that actually belong to the service being synchronized or that came from trusted server-generated state.
  - Today the request body can be used to delete unrelated gateway routes across services.
- Suggested validation:
  - Seed the gateway with a route for service `B`.
  - Call `POST /api/v1/gateway-binding/service-routes/sync` for service `A` with `previous_routes` containing service `B`'s route ID.
  - Observe that the current sync path deletes the unrelated route.

### BUG-334 — Gateway admin mock accepts routes missing `target_service.name` and later crashes proxying them (Agent c fixed)

- Severity: High
- Files:
  - `apps/gateway_admin_mock/main.py`
- Summary:
  - The admin mock accepts any route document shape because `RouteUpsertRequest` only requires `document: dict[str, Any]`.
  - The proxy path later assumes `route_document["target_service"]["name"]` exists in both `_service_key()` and `_upstream_base_url()`.
  - `proxy_gateway_request()` catches only `httpx.HTTPError`, so a malformed stored route leaks a raw `KeyError` instead of a controlled response.
- Evidence:
  - `RouteUpsertRequest` at lines 27-30 accepts any dict and `upsert_route()` at lines 95-103 stores it unchanged.
  - `_forward_request()` at line 162 reads `route_document["target_service"]` and immediately calls `_service_key(target_service)`.
  - `_service_key()` at line 199 and `_upstream_base_url()` at line 208 both do `target_service["name"]` with no validation.
  - A local probe `PUT` `/admin/routes/svc-active` with `{"document": {"target_service": {"port": 8003}}}` succeeded with HTTP 200.
  - The subsequent `GET /gateway/svc` raised `KeyError: 'name'` from `_service_key(...)`.
- Why it is a bug:
  - The mock should reject malformed route documents at write time or turn them into a controlled gateway error at proxy time.
  - Raw `KeyError` crashes make local reconciliation/integration environments brittle and hide the actual contract problem.
- Suggested validation:
  - Store a route whose `target_service` omits `name`.
  - Proxy a request through `/gateway/{service_id}` and observe the current raw `KeyError` crash path.

### BUG-335 — Post-deploy health validation trusts HTTP 200 alone and accepts contradictory unhealthy bodies (Agent c fixed)

- Severity: High
- Files:
  - `libs/validator/post_deploy.py`
  - `apps/mcp_runtime/main.py`
- Summary:
  - `PostDeployValidator._validate_health()` decides success solely from `/healthz` and `/readyz` returning HTTP 200.
  - It never parses or validates the JSON body, even though the runtime health endpoints in this repo expose an explicit `{"status": "ok"}` contract.
  - A runtime can therefore return `200` plus a contradictory body such as `{"status":"down"}` and still be marked healthy for publication.
- Evidence:
  - `libs/validator/post_deploy.py` lines 289-297 compute `passed = health_response.status_code == 200 and ready_response.status_code == 200` and never inspect response bodies.
  - `apps/mcp_runtime/main.py` exposes `/healthz` and `/readyz` with `{"status": "ok"}` responses, and validator tests in `libs/validator/tests/test_post_deploy.py` also use that same body shape.
  - A local probe mocked `/healthz -> 200 {"status":"down"}` and `/readyz -> 200 {"status":"not-ready"}`.
  - `PostDeployValidator._validate_health(...)` still returned `passed=True` with `details="Runtime health endpoints are ready."`
- Why it is a bug:
  - The post-deploy gate should reject runtimes that explicitly report unhealthy/not-ready status, even if an intermediary still returns HTTP 200.
  - Ignoring the health payload lets contradictory or regressed readiness implementations slip through validation as success.
- Suggested validation:
  - Mock `/healthz` and `/readyz` to return HTTP 200 with non-`ok` status bodies.
  - Observe that the validator currently reports the runtime as ready.

### BUG-336 — Service-route sync/delete/rollback trust caller-supplied `route_id`s and can overwrite or delete unrelated routes (Agent c fixed)

- Severity: High
- Files:
  - `apps/access_control/gateway_binding/routes.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - All three service-route mutation endpoints accept arbitrary `route_config` payloads from the caller.
  - The service layer turns `default_route.route_id` / `version_route.route_id` into the authoritative target route IDs with no ownership or naming validation.
  - A caller can therefore pick another service's route ID and cause sync to overwrite it, or cause delete/rollback to remove it outright.
- Evidence:
  - `apps/access_control/gateway_binding/routes.py` lines 79-120 forward `request.route_config` directly into `sync_service_routes()`, `delete_service_routes()`, and `rollback_service_routes()`.
  - `sync_service_routes()` in `apps/access_control/gateway_binding/service.py` builds `route_documents = _service_route_documents(route_config)` and then blindly `upsert_route(...)`s every `(route_id, document)` pair at lines 98-99.
  - `delete_service_routes()` at lines 107-110 blindly deletes every route ID produced by `_service_route_documents(route_config)`.
  - `rollback_service_routes()` at lines 123-134 likewise deletes every route ID from `route_documents` unless it appears in `previous_routes`.
  - A local probe seeded the in-memory gateway with an existing route `foreign-admin` for `admin-ui`, then used a `svc-1` `route_config` whose `default_route.route_id` was also `foreign-admin`.
  - `sync_service_routes(...)` rewrote that route so its stored document `service_id` changed from `admin-ui` to `svc-1`.
  - Running `delete_service_routes(...)` with the same payload removed `foreign-admin` entirely, and `rollback_service_routes(..., previous_routes={})` also deleted it.
- Why it is a bug:
  - Route mutation endpoints should operate only on routes that actually belong to the target service/version or on trusted server-generated IDs.
  - Today a crafted route publication payload can clobber or delete unrelated gateway routes across services.
- Suggested validation:
  - Seed the gateway with a route for service `B`.
  - Call `/service-routes/sync`, `/service-routes/delete`, or `/service-routes/rollback` for service `A` using a `route_config` whose `route_id` matches service `B`'s route.
  - Observe the overwrite/delete of the unrelated route.

### BUG-337 — Gateway reconcile ignores consumer username and metadata drift when credential is unchanged (Agent c fixed)

- Severity: Medium
- Files:
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - Consumer reconciliation only checks whether the stored gateway consumer is missing or has a different credential hash.
  - If the gateway consumer keeps the same credential but its `username` or `metadata` drift, reconcile reports success and leaves the bad state untouched.
  - This means the "mirror PATs to gateway" repair path does not actually restore consumer identity metadata.
- Evidence:
  - `apps/access_control/gateway_binding/service.py` lines 199-207 gate the consumer upsert on `existing_consumer is None or existing_consumer.credential != expected["credential"]`.
  - There is no corresponding comparison for `existing_consumer.username` or `existing_consumer.metadata`.
  - A local probe seeded the in-memory gateway with consumer `pat-<id>` using the correct credential hash but a tampered username and metadata.
  - Running `GatewayBindingService.reconcile(...)` with a fake session containing the matching PAT/user rows returned `{'consumers_synced': 0, ...}`.
  - After reconcile, the gateway still stored the tampered username and metadata unchanged.
- Why it is a bug:
  - Reconciliation is supposed to repair drift, not just hash mismatches.
  - Leaving stale consumer usernames/metadata in place can misattribute PAT ownership in gateway-admin state and defeats the purpose of a full mirror/reconcile workflow.
- Suggested validation:
  - Seed a gateway consumer with the correct credential but the wrong username or metadata.
  - Run gateway reconcile and observe that the current implementation leaves the drifted consumer unchanged.

### BUG-338 — Post-deploy full generated-tool audit marks invalid streaming results as passed whenever `status=="ok"` (Agent b fixed)

- Severity: High
- Files:
  - `libs/validator/post_deploy.py`
- Summary:
  - `_audit_all_enabled_operations()` treats any dict result with `status == "ok"` as a passed audit.
  - Unlike invocation smoke and the proof runner audit helper, it never validates the declared streaming transport or the `events` / `lifecycle` structure for streaming operations.
  - A broken streaming tool can therefore be counted as audit-passed as long as it returns an `ok` status flag.
- Evidence:
  - `libs/validator/post_deploy.py` lines 226-258 check only `status = result.get("status")` and then append `ToolAuditResult(... outcome="passed", reason="Invocation succeeded.")`.
  - That code path never consults `_supported_descriptor_for_operation()` or validates `transport`, `result`, `events`, or `lifecycle`.
  - A local probe built an IR whose `op1` had a supported SSE descriptor and ran `_audit_all_enabled_operations(...)` with an invoker returning `{"status":"ok","transport":"websocket","result":{"events":"not-a-list","lifecycle":[]}}`.
  - The returned summary was `passed=1`, `failed=0`, and the tool result reason was exactly `"Invocation succeeded."`
- Why it is a bug:
  - The whole point of the full generated-tool audit is to catch broken generated tools beyond the one smoke sample.
  - Today it can greenlight streaming tools whose transport and stream envelope are incompatible with the IR contract.
- Suggested validation:
  - Run `_audit_all_enabled_operations()` on a service IR with a supported streaming descriptor.
  - Make the tool invoker return `status="ok"` but with the wrong `transport` or malformed `events` / `lifecycle`.
  - Observe that the current audit still marks the tool as passed.

### BUG-339 — Proof runner can return a successful proof result even when the representative runtime invocation reports `status="error"` (Agent b fixed)

- Severity: High
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_invoke_runtime_tools()` just records each invocation result without checking its status.
  - `_run_case()` stores those `invocation_results` in `ProofResult` and returns success as long as earlier compilation/artifact checks passed.
  - When `audit_all_generated_tools` is disabled, a failed representative runtime invocation does not fail the proof case.
- Evidence:
  - `apps/proof_runner/live_llm_e2e.py` lines 758-766 append raw `ToolInvocationResult(tool_name=..., result=result)` for each invocation.
  - `_run_case()` at lines 619-659 never inspects `invocation_results[*].result["status"]` before returning `ProofResult(...)`.
  - A local probe patched `_invoke_runtime_tools(...)` to return `[ToolInvocationResult(tool_name="op1", result={"status":"error","error":"boom"})]` while leaving the compilation/job/artifact path successful.
  - `_run_case(..., audit_all_generated_tools=False, require_llm_artifacts=False)` still returned a `ProofResult` with `job_id="job-1"`, `error=None`, `audit_summary=None`, and the failing invocation result preserved inside `invocation_results`.
- Why it is a bug:
  - A proof run should fail when its own representative runtime invocation says the tool errored.
  - Otherwise the proof harness can emit success-shaped results even though the deployed runtime invocation already failed.
- Suggested validation:
  - Patch or mock the runtime invoker used by `_invoke_runtime_tools()` so the representative call returns `{"status":"error", ...}`.
  - Run a proof case with `audit_all_generated_tools=False` and observe that the returned proof result still has no top-level error.

### BUG-340 — Proof runner does not fail the proof case even when the generated-tool audit summary contains failed tools (Agent b fixed)

- Severity: High
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_run_case()` computes `audit_summary` when `audit_all_generated_tools=True`, but it never turns audit failures into a case failure.
  - The returned `ProofResult` has no success/failure flag and uses `error=None` unless an exception was thrown.
  - As a result, a proof case can look successful even when the generated-tool audit explicitly reports failed tools.
- Evidence:
  - `apps/proof_runner/live_llm_e2e.py` lines 620-629 compute `audit_summary = await _audit_generated_tools(...)`.
  - Lines 647-659 then return `ProofResult(...)` without checking `audit_summary.failed`.
  - A local probe patched `_audit_generated_tools(...)` to return `ToolAuditSummary(... passed=0, failed=1, results=[ToolAuditResult(... outcome="failed", reason="boom")])`.
  - `_run_case(..., audit_all_generated_tools=True, require_llm_artifacts=False)` still returned `error=None` while `result.audit_summary.failed == 1`.
- Why it is a bug:
  - When the caller explicitly opts into full generated-tool audit, failed audited tools should fail the proof case rather than being buried in a nested field.
  - Otherwise downstream automation can treat a failed proof as successful unless it remembers to manually inspect `audit_summary`.
- Suggested validation:
  - Run a proof case with `audit_all_generated_tools=True` and make `_audit_generated_tools()` return at least one failed result.
  - Observe that the current proof result still has no top-level failure signal.

### BUG-341 — Default proof/validator probe selection falls back to destructive tools when no safe candidate exists (Agent b fixed)

- Severity: High
- Files:
  - `libs/validator/post_deploy.py`
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - The post-deploy validator and proof runner both prefer safe candidates, but neither refuses to proceed when only state-mutating or destructive tools remain.
  - The validator's smoke selector merely penalizes risky tools; the proof runner's invocation selector falls back to any enabled sampled tool after exhausting safe candidates.
  - On a service whose only invocable operation is destructive, the default validation/proof flow will execute that destructive tool against the live runtime.
- Evidence:
  - `libs/validator/post_deploy.py` lines 566-580 select the minimum-priority candidate among all enabled/sampleable tools; `_smoke_operation_priority()` at lines 612-624 only adds a penalty for `writes_state` / `destructive`, but does not exclude them.
  - `apps/proof_runner/live_llm_e2e.py` lines 790-799 first try safe candidates, then unconditionally fall back to `for tool_name in sorted(operation_by_id): return ...`.
  - A local probe with a single enabled `DELETE /thing/1` operation showed `_select_smoke_operation(...)` returning that destructive tool.
  - A second local probe ran `PostDeployValidator.validate(...)` against the same IR and confirmed that the validator actually invoked `deleteThing`.
  - A third local probe showed `_resolve_invocation_specs(...)` returning `ToolInvocationSpec(tool_name='deleteThing', arguments={})` when that destructive tool was the only candidate.
- Why it is a bug:
  - Deployment validation and proof runs should not default to executing destructive operations against live targets just because no safer endpoint exists.
  - This can mutate or delete real state in the environment that the validation harness is supposed to verify.
- Suggested validation:
  - Use a service IR whose only invocable tool is a destructive `DELETE` or state-mutating `POST`.
  - Run post-deploy validation or proof invocation selection with default settings.
  - Observe that the current code chooses and executes the destructive tool instead of refusing the probe.

### BUG-342 — Proof/validator accept status-only unary success envelopes even when the `result` payload is missing (Agent b fixed)

- Severity: High
- Files:
  - `libs/validator/post_deploy.py`
  - `apps/proof_runner/live_llm_e2e.py`
  - `apps/mcp_runtime/proxy.py`
- Summary:
  - For non-streaming operations, the validator and proof audit logic treat `{"status":"ok"}` as a successful invocation even if the success envelope has no `result` payload at all.
  - The runtime's own success envelopes consistently include a `result` field for SQL, unary gRPC, generic HTTP, and streaming operations.
  - A malformed or truncated unary success envelope can therefore be reported as valid by the proof/validator stack.
- Evidence:
  - In `libs/validator/post_deploy.py`, `_validate_invocation_smoke()` lines 475-533 only validate `status` for non-streaming operations; if `descriptor is None`, no `result` validation runs and the function returns `passed=True`.
  - `_audit_all_enabled_operations()` lines 226-258 likewise marks any dict with `status == "ok"` as `outcome="passed"` for audited tools.
  - In `apps/proof_runner/live_llm_e2e.py`, `_generated_tool_audit_failure_reason()` lines 969-975 return `None` for non-streaming operations as soon as `status == "ok"`, with no `result` check.
  - `apps/mcp_runtime/proxy.py` success paths at lines 201-205, 217-221, 246-250, and 410-414 all include a `"result": ...` field in the returned envelope.
  - A local probe using a unary `GET /op1` operation and an invoker result of only `{"status":"ok"}` produced:
    - `PostDeployValidator._validate_invocation_smoke(...) -> passed=True, "Invocation smoke test succeeded for op1."`
    - `PostDeployValidator._audit_all_enabled_operations(...) -> passed=1, failed=0, outcome="passed"`
    - `_generated_tool_audit_failure_reason(...) -> None`
- Why it is a bug:
  - A success envelope without a `result` payload violates the runtime response contract and should be rejected as malformed.
  - Accepting it as success hides truncated, partial, or wrongly-shaped runtime responses in both proof and post-deploy validation.
- Suggested validation:
  - Use a non-streaming operation and make the runtime/tool invoker return only `{"status":"ok"}`.
  - Observe that the current proof/validator flows accept the invocation instead of flagging the missing `result`.

### BUG-343 — Proof runner misreports malformed IR artifacts as “no llm-sourced fields” (Agent b fixed)

- Severity: Medium
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_run_case()` counts LLM-sourced fields on the raw `artifact["ir_json"]` and, when `require_llm_artifacts` is enabled, raises a “has no llm-sourced fields” error before validating the artifact as a `ServiceIR`.
  - A malformed artifact that is missing required IR fields can therefore be misdiagnosed as an LLM-enrichment absence instead of an IR/schema failure.
  - This sends debugging toward the wrong subsystem and hides the real artifact corruption.
- Evidence:
  - `apps/proof_runner/live_llm_e2e.py` lines 600-608 fetch `artifact_ir`, call `_count_llm_fields(artifact_ir)`, and raise `RuntimeError(...)` before `ServiceIR.model_validate(artifact_ir)`.
  - A local probe patched `_artifact_version(...)` to return `ir_json={"service_name":"Demo","base_url":"https://api.example.com","protocol":"openapi","operations":"oops"}` with the required `source_hash` field missing.
  - `_run_case(..., require_llm_artifacts=True)` raised `RuntimeError: openapi proof service svc-1 has no llm-sourced fields in IR.`
  - Running the same probe with `require_llm_artifacts=False` reached `ServiceIR.model_validate(...)` and surfaced the real schema errors instead: missing `source_hash` and non-list `operations`.
- Why it is a bug:
  - Invalid IR artifacts should be reported as schema corruption, not as a missing-LLM-fields condition.
  - The current ordering obscures the root cause and can waste debugging time on enhancement logic even when the artifact itself is malformed.
- Suggested validation:
  - Make `_artifact_version()` return an invalid `ir_json` that contains no countable LLM metadata.
  - Run a proof case with the default `require_llm_artifacts=True`.
  - Observe that the current code raises the misleading “no llm-sourced fields” error instead of the artifact validation error.

### BUG-344 — Proof runner CLI exits 0 even when proof cases fail (Agent b fixed)

- Severity: High
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `run_proofs()` catches per-case failures and converts them into `ProofResult(error=...)` entries instead of propagating the exception.
  - `_async_main()` then serializes those results to JSON and returns normally without inspecting whether any proof result contains an `error`.
  - The CLI therefore exits successfully even when proof cases failed.
- Evidence:
  - `apps/proof_runner/live_llm_e2e.py` lines 136-174 wrap each `_run_case(...)` call in `try/except` and append `ProofResult(..., error=str(exc))` on failure.
  - Lines 1219-1257 call `run_proofs(...)`, print the JSON array, and return without converting failed proof results into a non-zero exit status.
  - A local probe patched `run_proofs(...)` to return `[ProofResult(..., case_id="case-1", error="boom")]`.
  - Running `_async_main()` printed the failure-shaped JSON and the Python process still completed with exit code 0.
- Why it is a bug:
  - CI and shell automation usually rely on process exit status, not on parsing JSON output for embedded `error` fields.
  - The current CLI can falsely report success even when every proof case failed.
- Suggested validation:
  - Force one proof case to fail or patch `run_proofs()` to return a result with `error` set.
  - Invoke the proof runner CLI and observe that it still exits with status 0.

### BUG-345 — Proof runner silently succeeds when `--case-id` matches no proof cases (Agent b fixed)

- Severity: High
- Files:
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `_build_proof_cases()` simply filters the full case list down to the provided `selected_case_ids` and returns an empty list when none match.
  - `run_proofs()` then iterates zero cases and returns `[]`, and `_async_main()` prints that empty array and exits successfully.
  - A typo in `--case-id` can therefore skip all proofs without any warning or failure signal.
- Evidence:
  - `apps/proof_runner/live_llm_e2e.py` line 1186 exposes `--case-id` as an appendable CLI filter.
  - `_build_proof_cases()` at lines 202-208 returns only cases whose `case_id` is in `selected_case_ids`, with no guard for the empty-result case.
  - `run_proofs()` at lines 125-174 uses the filtered list as-is; if it is empty, it returns `results=[]`.
  - `_async_main()` at lines 1215-1257 prints the returned array and exits normally.
  - A local probe patched `_parse_args()` to behave like `--case-id does-not-exist`; `_async_main()` printed `[]` and the process still exited with code 0.
- Why it is a bug:
  - A misspelled or stale case ID can cause the proof runner to perform no work while still looking successful to both humans and automation.
  - This creates a silent false-green path for selective proof runs.
- Suggested validation:
  - Invoke the proof runner with an unknown `--case-id`.
  - Observe that the current CLI prints `[]` and exits successfully instead of warning that no proof cases matched.

### BUG-346 — Proof runner executes synthetic placeholder path samples against live runtimes (Agent b fixed)

- Severity: High
- Files:
  - `apps/compiler_worker/activities/production.py`
  - `apps/proof_runner/live_llm_e2e.py`
  - `libs/validator/audit.py`
- Summary:
  - Sample generation fills required path parameters with synthetic fallback values like `"1"` so URLs can be constructed.
  - The proof runner then reuses those synthetic samples as live representative invocations, even though the audit policy explicitly recognizes `"1"` path arguments as unresolved placeholder samples.
  - Default proof runs can therefore send arbitrary placeholder IDs to real services instead of refusing the probe.
- Evidence:
  - `apps/compiler_worker/activities/production.py` lines 1226-1236 include required path params in the generated sample invocation, and the current implementation produces fallback values such as `{"id":"1"}` for `/users/{id}`.
  - `apps/proof_runner/live_llm_e2e.py` lines 777-799 call `build_sample_invocations(service_ir)` and return the first preferred/safe/sampleable tool invocation directly as the live representative probe.
  - `libs/validator/audit.py` lines 99-107 and 150-155 explicitly treat `"1"` / `1` in unresolved path parameters as synthetic placeholder samples when deciding whether to skip failed audits.
  - A local probe with a single safe `GET /users/{id}` operation showed `_run_case(..., require_llm_artifacts=False)` invoking the runtime with `[('getUser', {'id': '1'})]`.
- Why it is a bug:
  - Synthetic path placeholders are not trustworthy live inputs; they can point at arbitrary records, produce misleading 404/403 failures, or hit real data that the proof harness never intended to touch.
  - The code already acknowledges in audit policy that these values are placeholders, but the representative live proof path still executes them anyway.
- Suggested validation:
  - Use a service IR whose only safe sampleable operation contains a required path parameter such as `/users/{id}`.
  - Run a proof case without explicit `tool_invocations`.
  - Observe that the current code sends a live probe with placeholder arguments like `{"id":"1"}` instead of skipping or requiring a real sample.

### BUG-347 — Production post-deploy validation executes synthetic placeholder path samples against live runtimes (Agent b fixed)

- Severity: High
- Files:
  - `apps/compiler_worker/activities/production.py`
  - `libs/validator/post_deploy.py`
  - `libs/validator/audit.py`
- Summary:
  - The production deployment activity builds default sample invocations from the compiled IR and passes them straight into `PostDeployValidator.validate(...)`.
  - The validator's smoke stage then executes those arguments directly against the live runtime.
  - For required path parameters, that means post-deploy validation can send synthetic fallback IDs like `{"id":"1"}` even though the audit policy already recognizes those values as unresolved placeholder samples.
- Evidence:
  - `apps/compiler_worker/activities/production.py` lines 435-448 and 1006-1019 build `sample_invocations = _build_sample_invocations(service_ir)` and pass them into `PostDeployValidator.validate(...)`.
  - `libs/validator/post_deploy.py` lines 426-458 select a smoke operation and invoke `self._tool_invoker(operation.id, arguments)` using `arguments = sample_invocations[operation.id]`.
  - `libs/validator/audit.py` lines 99-107 and 150-155 explicitly treat `"1"` / `1` in unresolved path parameters as synthetic placeholder samples for audit-skip decisions.
  - A local probe using `build_sample_invocations(service_ir)` for a safe `GET /users/{id}` operation produced `{'getUser': {'id': '1'}}`; feeding that into `PostDeployValidator._validate_invocation_smoke(...)` invoked `getUser` with `{'id': '1'}` and returned `passed=True`.
- Why it is a bug:
  - Post-deploy validation should not probe live services with synthetic placeholder IDs that may hit arbitrary records or produce meaningless failures.
  - The surrounding code already knows these path samples are placeholders, but the standard smoke-validation path still executes them anyway.
- Suggested validation:
  - Deploy or mock a service whose only smokeable operation contains a required path parameter such as `/users/{id}`.
  - Let production validation use its default generated `sample_invocations`.
  - Observe that the validator sends a live probe with a placeholder path value like `{"id":"1"}` instead of requiring a real sample or skipping the smoke test.

### BUG-348 — Compilation job metadata collapses `service_id` and `service_name`, so job APIs can return display names where stable IDs are expected

- Severity: High
- Files:
  - `libs/db_models.py`
  - `apps/compiler_worker/activities/production.py`
  - `apps/compiler_worker/repository.py`
  - `apps/compiler_api/repository.py`
  - `apps/compiler_api/models.py`
- Summary:
  - The persisted `compiler.compilation_jobs` row has only a `service_name` column and no separate `service_id`.
  - The worker then overloads that `service_name` field with the stable service identifier after extract/register stages by passing `service_name=service_id` through stage results.
  - The compiler API repository and response model in turn serialize `service_id=job.service_name` and `service_name=job.service_name`.
  - As a result, compilation job APIs cannot preserve both the stable identifier and the human-readable service name; callers can receive the display name in `service_id`, or lose the display name entirely once later stages overwrite the field with the stable ID.
- Evidence:
  - `libs/db_models.py` defines `CompilationJob.service_name` but no `CompilationJob.service_id`.
  - `apps/compiler_worker/activities/production.py` lines 939-955 compute `service_id = context.request.service_id or context.request.service_name or service_ir.service_name` and then return `_stage_result(..., service_name=service_id)`.
  - Later stages continue persisting `service_name=context.payload.get("service_id")` or `service_name=service_id`.
  - `apps/compiler_worker/repository.py` writes that stage result back with `job.service_name = service_name`.
  - `apps/compiler_api/repository.py` serializes `service_id=job.service_name` and `service_name=job.service_name`.
  - `apps/compiler_api/models.py` `CompilationJobResponse.from_record(...)` also does `service_id=record.service_name` and `service_name=record.service_name`.
  - A local probe using `CompilationJobResponse.from_record(...)` on a record with `service_name='Billing API'` produced `{'service_id': 'Billing API', 'service_name': 'Billing API', ...}`, i.e. no stable service ID can be represented at all.
- Why it is a bug:
  - The system models `service_id` and `service_name` as distinct concepts elsewhere, and UI/control-plane flows rely on the stable ID for links, rollbacks, and registry lookups.
  - Reusing one field for both meanings makes job responses semantically unstable across stages and can surface non-identifier display names where downstream code expects a stable service ID.
- Suggested validation:
  - Submit a compilation whose `service_id` and `service_name` differ (for example `billing-api` vs `Billing API`).
  - Inspect the returned job payloads and any later `/api/v1/compilations/{job_id}` response.
  - Observe that the API cannot expose both values distinctly and may report the display name where callers expect the stable service ID.

### BUG-349 — Distinct service IDs can collapse onto the same gateway `route_id` after DNS-label sanitization/truncation

- Severity: High
- Files:
  - `libs/generator/generic_mode.py`
  - `apps/access_control/gateway_binding/service.py`
- Summary:
  - Route IDs are generated from `_route_identity_base(...)`, which starts from `_route_base_name(...)`.
  - `_route_base_name(...)` sanitizes `config.service_id` with `_sanitize_dns_label(...)`, lowercasing it, converting non-alphanumerics to `-`, collapsing repeated separators, and truncating to 63 characters.
  - Different original service IDs can therefore normalize to the same route base even within the same tenant/environment scope.
  - Because gateway sync/upsert logic keys routes by `route_id`, two unrelated services whose IDs sanitize to the same value can overwrite each other’s active and version-pinned routes.
- Evidence:
  - `libs/generator/generic_mode.py` lines 272-289 implement `_sanitize_dns_label(...)` with lossy normalization and truncation.
  - Lines 330-345 derive `_route_base_name(...)` / `_route_identity_base(...)` from that sanitized value.
  - Lines 390-404 build `default_route.route_id = f"{resolved_route_base}-active"` and `version_route.route_id = f"{resolved_route_base}-v{config.version_number}"`.
  - `apps/access_control/gateway_binding/service.py` syncs routes by those `route_id` keys.
  - A local probe with `service_id='payment@api'` and `service_id='payment_api'` generated the same route IDs for both services:
    - `payment-api-active`
    - `payment-api-v1`
- Why it is a bug:
  - Route IDs are the gateway’s authoritative identity key.
  - If two distinct logical services collapse onto the same sanitized route IDs, publishing or deleting one service’s routes can overwrite or remove the other service’s traffic configuration even without any tenant/environment ambiguity.
- Suggested validation:
  - Create two services whose IDs differ before sanitization but normalize to the same DNS label (for example `payment@api` and `payment_api`).
  - Generate or activate both route configs.
  - Compare the emitted `default_route.route_id` / `version_route.route_id` values and observe the collision on the same gateway identities.

### BUG-350 — Post-deploy health validation still treats a JSON health payload with no `status` field as success

- Severity: Medium
- Files:
  - `libs/validator/post_deploy.py`
- Summary:
  - The health validator now rejects explicit non-`"ok"` status bodies, but it still returns success when `/healthz` or `/readyz` responds with JSON that omits `status` entirely.
  - That means a malformed or regressed runtime health implementation can pass post-deploy validation with `{}` even though this repo’s health contract is `{"status": "ok"}`.
- Evidence:
  - `PostDeployValidator._health_endpoint_failure_detail(...)` parses JSON and then does `status = payload.get("status")`.
  - It only fails when `"status" in payload and status != "ok"`.
  - If the body is `{}`, the helper falls through to `return None`, which is treated as a healthy endpoint.
  - A local probe showed:
    - `PostDeployValidator._health_endpoint_failure_detail('healthz', Response(200, json={})) -> None`
    - so an empty-object health body is currently accepted as healthy.
  - The same helper correctly rejects `{"status": None}` with `healthz reported status None`, showing that only the missing-key case slips through.
- Why it is a bug:
  - The post-deploy gate should confirm the declared readiness contract, not just “HTTP 200 plus any JSON object”.
  - Accepting `{}` as healthy can mark malformed or partially implemented runtimes ready for publication even though they are no longer returning the expected status signal.
- Suggested validation:
  - Mock `/healthz` and `/readyz` to return HTTP `200` with body `{}`.
  - Run `PostDeployValidator.validate(...)`.
  - Observe that the health stage still passes even though neither endpoint reported `{"status": "ok"}`.

### BUG-351 — Deferred route publishing crashes on valid version-only route configs because it unconditionally indexes `default_route`

- Severity: High
- Files:
  - `apps/compiler_worker/activities/production.py`
  - `libs/route_config.py`
- Summary:
  - The shared route-config schema explicitly allows `default_route` to be absent while `version_route` is present.
  - `DeferredRoutePublisher.publish(...)` does not honor that schema: it always reads `route_config["default_route"]["route_id"]` and only guards the optional `version_route`.
  - In the default `deferred` publication mode, a version-only route config therefore crashes the route stage with a raw `KeyError` instead of returning publication metadata.
- Evidence:
  - `libs/route_config.py` defines `default_route: GatewayRouteDefinition | None = None` and `version_route: GatewayRouteDefinition | None = None`.
  - `apps/compiler_worker/activities/production.py` returns `"default_route_id": route_config["default_route"]["route_id"]` with no `None`/type guard, while `version_route_id` is guarded with `isinstance(route_config.get("version_route"), dict)`.
  - A local probe calling `DeferredRoutePublisher().publish(...)` with a valid config containing only `version_route` printed `KeyError 'default_route'`.
- Why it is a bug:
  - The route publisher should accept every shape that the shared validator considers valid.
  - As written, the worker's default route-publication mode can crash on a schema-valid version-pinned deployment instead of reporting deferred route metadata.
- Suggested validation:
  - Feed `DeferredRoutePublisher.publish(...)` a route config with `service_id`, `service_name`, `namespace`, `version_number`, and `version_route`, but no `default_route`.
  - Observe that the current implementation raises `KeyError: 'default_route'`.

### BUG-352 — Shared route-config validation accepts malformed `target_service` objects missing required `name`/`port`

- Severity: High
- Files:
  - `libs/route_config.py`
  - `libs/registry_client/models.py`
  - `apps/access_control/gateway_binding/service.py`
  - `apps/gateway_admin_mock/main.py`
- Summary:
  - The shared `GatewayRouteDefinition` model treats `target_service` as an unstructured `dict[str, Any]`, so `validate_route_config(...)` only checks that the field exists, not that it contains the required service identity fields.
  - Both the registry-client DTOs and the access-control gateway-binding service rely on that helper, so they can accept and normalize route configs whose `target_service` omits `name` and/or `port`.
  - The gateway-binding path then copies that malformed dict straight into emitted route documents even though the downstream gateway admin schema requires `target_service.name` and `target_service.port`.
- Evidence:
  - `libs/route_config.py` defines `GatewayRouteDefinition.target_service: dict[str, Any]` with no nested schema for required keys.
  - `libs/registry_client/models.py` and `apps/access_control/gateway_binding/service.py` both call `validate_route_config(...)`.
  - `apps/access_control/gateway_binding/service.py` `_route_document(...)` copies `route_definition["target_service"]` directly into the outgoing document.
  - `apps/gateway_admin_mock/main.py` defines `RouteTargetService` with required `name: str` and `port: int`.
  - A local probe `validate_route_config({... "default_route": {"route_id": "svc-1-active", "target_service": {"namespace": "default"}}})` succeeded and returned the malformed nested dict unchanged.
- Why it is a bug:
  - The shared validation boundary is supposed to reject malformed route metadata before it reaches publication/reconcile paths.
  - Accepting `target_service` objects without `name`/`port` lets invalid route configs pass "validation" and fail only later when a downstream gateway expects a real target service identity.
- Suggested validation:
  - Call `validate_route_config(...)` or a route-sync path that uses it with `target_service={"namespace":"default"}`.
  - Observe that the payload is accepted even though downstream route documents require `target_service.name` and `target_service.port`.

### BUG-353 — Streaming contract validation never checks individual event objects, so malformed stream payloads pass proof and post-deploy validation

- Severity: High
- Files:
  - `libs/runtime_contracts.py`
  - `libs/validator/post_deploy.py`
  - `apps/proof_runner/live_llm_e2e.py`
- Summary:
  - `stream_result_failure_reason(...)` validates only the top-level `events` list plus the `lifecycle` object.
  - It never validates the shape of each event entry inside `events`, so strings or malformed objects are accepted as long as the lifecycle fields are present.
  - Both proof auditing and post-deploy validation reuse that helper, so malformed streaming envelopes can still be treated as successful invocations.
- Evidence:
  - `libs/runtime_contracts.py` checks `isinstance(events, list)` and validates lifecycle fields, but never inspects `events[index]` contents.
  - `libs/validator/post_deploy.py` `_tool_result_failure_reason(...)` returns `stream_result_failure_reason(result.get("result"), transport=...)`.
  - `apps/proof_runner/live_llm_e2e.py` `_generated_tool_audit_failure_reason(...)` does the same.
  - A local probe using an SSE event descriptor and a runtime result whose `events` was `['oops', {'message_type': 123, 'parsed_data': 'bad'}]` still produced `_tool_result_failure_reason(...) -> None` as long as `lifecycle` contained the expected fields.
- Why it is a bug:
  - Event items are part of the runtime response contract for streaming tools; malformed entries should not be counted as a successful proof/validator result.
  - The current contract lets obviously bad event payloads pass the control-plane checks, hiding broken stream encoders behind a success path.
- Suggested validation:
  - Return a streaming success envelope with a valid `lifecycle` object but malformed `events` items, such as a raw string or an object with non-string `message_type`.
  - Run proof or post-deploy validation and observe that the current code accepts the invocation instead of flagging the malformed event payload.

### BUG-354 — Compiler worker `/readyz` reports `not_ready` in the body but still returns HTTP 200, so Kubernetes probes never fail

- Severity: High
- Files:
  - `apps/compiler_worker/main.py`
  - `deploy/helm/tool-compiler/templates/apps.yaml`
- Summary:
  - The compiler worker computes a `missing` list and sets `status = "not_ready"` when required runtime/publication settings are absent.
  - But the `/readyz` handler always returns a plain dict, never a non-200 response.
  - The Helm chart uses `/readyz` for both startup and readiness probes.
- Evidence:
  - On `main`, `apps/compiler_worker/main.py` lines 37-53 build the readiness payload, set `checks["status"] = "ok" if ready else "not_ready"`, optionally add `checks["missing"]`, and then unconditionally `return checks`.
  - On `main`, `deploy/helm/tool-compiler/templates/apps.yaml` lines 287-296 configure both `startupProbe` and `readinessProbe` to call `/readyz`.
  - Local probe from this scan using the `main`-branch `apps/compiler_worker/main.py` with `ROUTE_PUBLISH_MODE=access-control` and `MCP_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ACCESS_CONTROL_URL` unset returned:
    - `{'workflow_engine': 'celery', 'compilation_queue': 'compiler.jobs', 'task_name': 'compiler_worker.execute_compilation', 'runtime_image': None, 'target_namespace': None, 'route_publish_mode': 'access-control', 'access_control_url': None, 'status': 'not_ready', 'missing': ['runtime_image', 'target_namespace', 'access_control_url']}`
- Why it is a bug:
  - FastAPI will serialize that dict as an HTTP 200 response, so Kubernetes sees the pod as healthy even when the worker has already declared itself `not_ready`.
  - In that state the worker can still become Ready and consume jobs without a runtime image, target namespace, or access-control endpoint configured.
- Suggested validation:
  - Start the worker with `ROUTE_PUBLISH_MODE=access-control` and leave `MCP_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ACCESS_CONTROL_URL` unset.
  - Call `/readyz` and observe that the payload says `status: not_ready`, but the HTTP status is still 200, so the Helm startup/readiness probes still pass.

### BUG-355 — Helm marks access-control Ready from `/healthz`, bypassing the service's real database readiness gate

- Severity: High
- Files:
  - `deploy/helm/tool-compiler/templates/apps.yaml`
  - `apps/access_control/main.py`
  - `apps/access_control/tests/test_main.py`
- Summary:
  - Access-control implements a real `/readyz` endpoint that performs `SELECT 1` and returns HTTP 503 when the database is unavailable.
  - But the Helm chart uses `/healthz` for both startup and readiness probes.
  - `/healthz` always returns `{"status":"ok"}` and never checks the database.
- Evidence:
  - On `main`, `apps/access_control/main.py` lines 55-68 define `/healthz` as an unconditional `{"status": "ok"}` response, while `/readyz` executes `SELECT 1` and returns `JSONResponse(status_code=503, content={"status":"not_ready"})` on failure.
  - On `main`, `apps/access_control/tests/test_main.py` lines 111-132 explicitly assert that a database failure on `/readyz` returns HTTP 503 and `{"status":"not_ready"}`.
  - On `main`, `deploy/helm/tool-compiler/templates/apps.yaml` lines 135-144 configure both `startupProbe` and `readinessProbe` to call `/healthz`, not `/readyz`.
  - Local probe from this scan loading the `main`-branch `apps/access_control/main.py` with explicit test `jwt_settings` / `gateway_admin_client` and a mocked DB session that raises printed:
    - `healthz= {'status': 'ok'}`
    - `readyz_status= 503`
    - `readyz_body= {"status":"not_ready"}`
- Why it is a bug:
  - Kubernetes can mark the access-control pod Ready while its database is unavailable, because the probe path ignores the service's actual readiness contract.
  - That sends traffic to a control-plane component that is alive enough to answer `/healthz` but not ready to serve authenticated stateful requests.
- Suggested validation:
  - Deploy access-control with a bad `DATABASE_URL` (or temporarily break DB connectivity).
  - Observe that `/healthz` still returns 200 while `/readyz` returns 503, yet the Helm startup/readiness probes continue to succeed because they target `/healthz`.

### BUG-356 — Compiler worker `/readyz` reports ready even when Celery has fallen back to the in-memory broker/backend

- Severity: High
- Files:
  - `apps/compiler_worker/main.py`
  - `apps/compiler_worker/celery_app.py`
  - `apps/compiler_worker/entrypoint.py`
- Summary:
  - The worker readiness endpoint checks only a small set of env-backed strings such as `runtime_image`, `target_namespace`, and `route_publish_mode`.
  - It never checks whether Celery has a real broker/backend configured.
  - Meanwhile `create_celery_app()` silently falls back to `memory://` and `cache+memory://` when `CELERY_BROKER_URL` / `REDIS_URL` are absent, and the entrypoint skips broker waiting entirely when no Redis-style broker URL is configured.
  - The pod can therefore report `status: ok` even though compilation jobs are running on an ephemeral in-process broker/result backend.
- Evidence:
  - `apps/compiler_worker/main.py` lines 39-63 build `/readyz` from `workflow_engine`, `compilation_queue`, `task_name`, `runtime_image`, `target_namespace`, `route_publish_mode`, and optional `access_control_url`; neither `REDIS_URL` nor `CELERY_BROKER_URL` is checked.
  - `apps/compiler_worker/celery_app.py` lines 30-47 resolve `broker_url` to `os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL") or "memory://"` and `result_backend` to `os.getenv("CELERY_RESULT_BACKEND") or os.getenv("REDIS_URL") or "cache+memory://"`.
  - `apps/compiler_worker/entrypoint.py` lines 53-76 return `None` from `_broker_endpoint()` when no Redis-style broker URL is configured, so `_wait_for_broker_socket()` becomes a no-op.
  - A local probe with `WORKFLOW_ENGINE=celery`, `MCP_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ROUTE_PUBLISH_MODE=deferred`, but no broker envs, printed:
    - `celery_ready_probe 200 {... 'status': 'ok'}`
    - `celery_broker_url memory://`
    - `celery_result_backend cache+memory://`
    - `broker_endpoint None`
- Why it is a bug:
  - A production worker should not be marked ready when accepted jobs will be processed through a process-local ephemeral broker/backend that loses tasks and results on restart.
  - The current readiness signal can give Kubernetes and operators a false green even though queue durability is gone.
- Suggested validation:
  - Start the worker with `MCP_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ROUTE_PUBLISH_MODE` set, but omit `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND`.
  - Call `/readyz` and observe it returns HTTP 200 with `status: ok`.
  - Inspect the Celery app config and confirm the broker/backend resolved to `memory://` and `cache+memory://`.

### BUG-357 — Setting `WORKFLOW_ENGINE=temporal` only changes worker status output; the entrypoint still always launches Celery

- Severity: High
- Files:
  - `apps/compiler_worker/main.py`
  - `apps/compiler_worker/entrypoint.py`
  - `apps/compiler_worker/workflows/compile_workflow.py`
- Summary:
  - The worker health shell reads `WORKFLOW_ENGINE` and reports it back from `/readyz`.
  - But the actual process supervisor ignores that setting entirely and always starts `celery -A apps.compiler_worker.celery_app:celery_app worker`.
  - There is no Temporal-specific startup branch in `apps/compiler_worker`; the only Temporal mention in source is a future-facing docstring in the workflow core plus a status test.
  - Operators can therefore configure `WORKFLOW_ENGINE=temporal`, see `/readyz` report `"workflow_engine": "temporal"`, and still be running a Celery worker.
- Evidence:
  - `apps/compiler_worker/main.py` line 23 sets `app.state.workflow_engine = os.getenv("WORKFLOW_ENGINE", "celery")`, and `/readyz` returns that field unchanged.
  - `apps/compiler_worker/entrypoint.py` lines 31-50 hard-code `_build_celery_command()` and lines 136-165 always launch that Celery worker before the HTTP shell; the file contains no `WORKFLOW_ENGINE` branch.
  - Repository search under `apps/compiler_worker` found no Temporal runtime implementation besides `tests/test_main.py` and the `compile_workflow.py` comment describing the workflow core as suitable for “future Temporal wrappers.”
  - A local probe with `WORKFLOW_ENGINE=temporal`, `MCP_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ROUTE_PUBLISH_MODE=deferred` printed:
    - `temporal_ready_probe 200 {... 'workflow_engine': 'temporal', 'status': 'ok'}`
    - `temporal_celery_command [..., '-m', 'celery', '-A', 'apps.compiler_worker.celery_app:celery_app', 'worker', ...]`
- Why it is a bug:
  - The worker exposes `WORKFLOW_ENGINE` as if alternate engines are supported, but the actual supervisor path only knows how to run Celery.
  - This creates a false-ready/false-configured state where operators believe they selected Temporal even though the worker will still run the Celery stack.
- Suggested validation:
  - Set `WORKFLOW_ENGINE=temporal` and start the compiler worker.
  - Call `/readyz` and observe it reports `workflow_engine: temporal`.
  - Inspect the launched subprocesses or the command builder and confirm the entrypoint still starts Celery with no Temporal branch.

### BUG-358 — Compiler worker `/readyz` accepts unsupported `ROUTE_PUBLISH_MODE` values as healthy even though production route resolution will crash later

- Severity: High
- Files:
  - `apps/compiler_worker/main.py`
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - `/readyz` only checks whether `route_publish_mode` is present, not whether it is one of the supported modes.
  - The production activity resolver supports only `deferred` and `access-control`; any other non-empty string raises `RuntimeError("Unsupported ROUTE_PUBLISH_MODE: ...")`.
  - A worker can therefore be marked ready with an invalid publication mode even though route publication will fail as soon as a compilation reaches that path.
- Evidence:
  - `apps/compiler_worker/main.py` lines 48-63 include `route_publish_mode` in the required key set and treat any non-`None` value as satisfying readiness.
  - `apps/compiler_worker/activities/production.py` lines 199-213 resolve publishers only for `deferred` and `access-control`, then raise `RuntimeError(f"Unsupported ROUTE_PUBLISH_MODE: {mode}.")` for any other value.
  - A local probe with `WORKFLOW_ENGINE=celery`, `MCP_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ROUTE_PUBLISH_MODE=bogus-mode` printed:
    - `ready_probe 200 {... 'route_publish_mode': 'bogus-mode', 'status': 'ok'}`
    - `RuntimeError Unsupported ROUTE_PUBLISH_MODE: bogus-mode.`
- Why it is a bug:
  - Readiness should reject configuration that the main production workflow cannot execute.
  - The current implementation can declare the pod healthy even though every route-publication path will fail on first use.
- Suggested validation:
  - Start the worker with `ROUTE_PUBLISH_MODE=bogus-mode` plus the other currently required readiness env vars.
  - Call `/readyz` and observe it still returns HTTP 200 with `status: ok`.
  - Then invoke the route publisher resolution path and observe `RuntimeError: Unsupported ROUTE_PUBLISH_MODE: bogus-mode.`


### BUG-359 — Helm marks compiler-api Ready from `/healthz`, but the service exposes no real readiness gate

- Severity: High
- Files:
  - `deploy/helm/tool-compiler/templates/apps.yaml`
  - `apps/compiler_api/main.py`
  - `apps/compiler_api/tests/test_init_main_uncovered.py`
- Summary:
  - The compiler API only exposes `/healthz`; it does not implement `/readyz` or any dependency-aware readiness check.
  - The Helm chart uses `/healthz` for startup, readiness, and liveness probes.
  - `/healthz` always returns `{"status": "ok"}` without checking database connectivity or any dispatcher/route-publisher dependency.
- Evidence:
  - On `main`, `apps/compiler_api/main.py` lines 37-51 configure the database, dispatcher, and route publisher, but lines 67-69 define `/healthz` as an unconditional `{"status": "ok"}` response and there is no `/readyz` route.
  - On `main`, `apps/compiler_api/tests/test_init_main_uncovered.py` lines 60-70 explicitly assert that `/healthz` always returns HTTP 200 with `{"status": "ok"}`.
  - On `main`, `deploy/helm/tool-compiler/templates/apps.yaml` lines 67-79 configure the compiler-api `startupProbe`, `readinessProbe`, and `livenessProbe` to all call `/healthz`.
  - Local probe from this scan using `create_app(database_url="postgresql+asyncpg://bad:bad@127.0.0.1:1/missing")` with `ACCESS_CONTROL_JWT_SECRET=test-secret` printed:
    - `healthz 200 {"status": "ok"}`
    - `readyz 404`
- Why it is a bug:
  - Kubernetes can mark compiler-api Ready even when there is no dependency-aware readiness endpoint and the configured database URL is unusable.
  - That means traffic can be routed to a process that is merely alive enough to answer `/healthz`, but has not proven it can serve stateful compilation and artifact requests safely.
- Suggested validation:
  - Start compiler-api with a bad `DATABASE_URL` but a valid `ACCESS_CONTROL_JWT_SECRET`.
  - Observe that `/healthz` still returns HTTP 200, `/readyz` is missing, and the Helm readiness probe still passes because it only checks `/healthz`.

### BUG-360 — Compilation requests accept both `source_url` and `source_content`, then silently compile the inline payload while preserving the URL as provenance metadata

- Severity: High
- Files:
  - `apps/compiler_api/models.py`
  - `apps/compiler_worker/activities/production.py`
  - `libs/extractors/base.py`
  - `libs/extractors/openapi.py`
- Summary:
  - The compiler API validates only that at least one source is present; it does not reject requests that provide both `source_url` and `source_content`.
  - The worker forwards both into `SourceConfig` unchanged.
  - At least the OpenAPI extractor prefers inline `file_content` over the remote URL, but still writes `source.url` into `ServiceIR.source_url`.
- Evidence:
  - On `main`, `apps/compiler_api/models.py` lines 37-41 reject only the case where both fields are absent, and lines 46-49 forward both `source_url` and `source_content` into the workflow request unchanged.
  - On `main`, `libs/extractors/base.py` lines 25-27 require only that one of `url`, `file_path`, or `file_content` be present; simultaneous values are allowed.
  - On `main`, `apps/compiler_worker/activities/production.py` lines 1289-1293 build `SourceConfig(url=context.request.source_url, file_content=context.request.source_content, ...)`, so dual-source requests reach extractors intact.
  - On `main`, `libs/extractors/openapi.py` lines 142-148 prefer `file_content` before `file_path` and `url`, while lines 127-129 still set `ServiceIR(source_url=source.url, source_hash=...)`.
  - Local probe from this scan created `CompilationCreateRequest(source_url="https://example.com/remote-spec.yaml", source_content=<inline OpenAPI titled "Inline Billing API">)` and printed:
    - `accepted_both https://example.com/remote-spec.yaml True`
    - `{"service_name": "inline-billing-api", "source_url": "https://example.com/remote-spec.yaml"}`
- Why it is a bug:
  - The system can compile one artifact while recording a different source location as its provenance, so audit logs, retries, and operator debugging no longer describe what was actually compiled.
  - Because extractor precedence is implementation-defined, the same dual-source request can resolve differently across protocols instead of failing fast as an invalid contract.
- Suggested validation:
  - Submit a compilation whose `source_url` points at one spec and whose `source_content` contains a different inline spec.
  - Observe that extraction uses the inline content, while persisted job/audit metadata still points at the remote URL.

### BUG-361 — Extract computes the canonical `source_hash`, but compilation jobs keep the request hash and replay that stale value

- Severity: Medium
- Files:
  - `apps/compiler_worker/activities/production.py`
  - `apps/compiler_worker/workflows/compile_workflow.py`
  - `apps/compiler_worker/repository.py`
  - `apps/compiler_api/routes/compilations.py`
- Summary:
  - The extract stage computes `service_ir.source_hash` from the actual extracted source and places it into the workflow payload.
  - But job persistence never writes that canonical hash back to `CompilationJob.source_hash`; it only stores the original request value and the checkpoint payload.
  - API responses and retry requests keep reading the stale job-column hash instead of the extracted hash.
- Evidence:
  - On `main`, `apps/compiler_worker/activities/production.py` lines 945-950 add `"source_hash": service_ir.source_hash` to the stage `context_updates`.
  - On `main`, `apps/compiler_worker/workflows/compile_workflow.py` lines 253-265 merge those context updates into `context.payload` and pass the payload to `update_checkpoint(...)`.
  - On `main`, `apps/compiler_worker/repository.py` lines 47-50 create jobs with `source_hash=request.source_hash`, lines 203-210 update only `job.options` during checkpoint persistence, and lines 231-235 serialize `CompilationJobRecord.source_hash` straight from `job.source_hash`.
  - On `main`, `apps/compiler_api/routes/compilations.py` lines 213-217 rebuild retry requests from `original.source_hash`, so retries inherit the stale job-level value.
  - Local probe from this scan built a `CompilationJob(source_hash="request-hash")`, stored a checkpoint payload containing `source_hash="extracted-hash"`, converted it through `_to_job_record()`, and printed:
    - `{"record_source_hash": "request-hash", "checkpoint_source_hash": "extracted-hash"}`
- Why it is a bug:
  - The API and Web UI can display a hash that never matched the artifact actually extracted and compiled.
  - Retry/replay paths propagate stale or user-supplied hashes instead of the canonical extracted hash, which breaks provenance, cache/debug assumptions, and source-change reasoning.
- Suggested validation:
  - Start a compilation with a bogus or missing `source_hash` and let extract complete successfully.
  - Compare the persisted checkpoint payload with `GET /compilations/{job_id}` or a retry request: the checkpoint contains the extracted hash, but the job response and replay request still use the stale column value.

### BUG-362 — Compiler worker `/readyz` checks raw env vars instead of effective production defaults, so it reports `not_ready` for valid deferred/default configs

- Severity: Medium
- Files:
  - `apps/compiler_worker/main.py`
  - `apps/compiler_worker/activities/production.py`
- Summary:
  - The worker readiness shell reads `MCP_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ACCESS_CONTROL_URL` straight from the environment and marks any missing raw env as `not_ready`.
  - But the actual production settings layer does not require that exact env shape:
    - `runtime_image` falls back to `COMPILER_RUNTIME_IMAGE` or the built-in default image.
    - `namespace` falls back to the service-account namespace or `"default"`.
    - `access_control_url` is only relevant when `ROUTE_PUBLISH_MODE=access-control`; deferred mode works with `None`.
  - As a result, `/readyz` can report a broken worker even when the worker's effective execution settings are valid.
- Evidence:
  - On `main`, `apps/compiler_worker/main.py` lines 28-31 set `runtime_image`, `target_namespace`, `route_publish_mode`, and `access_control_url` directly from `os.getenv(...)`, and lines 39-52 then treat every `None` value in that dict as a readiness failure.
  - On `main`, `apps/compiler_worker/activities/production.py` lines 138-145 derive `namespace` from `COMPILER_TARGET_NAMESPACE` or the service-account namespace or `"default"`, and derive `runtime_image` from `MCP_RUNTIME_IMAGE` or `COMPILER_RUNTIME_IMAGE` or `_DEFAULT_RUNTIME_IMAGE`.
  - On `main`, `apps/compiler_worker/activities/production.py` line 153 defaults `route_publish_mode` to `deferred`, while `access_control_url` remains optional unless the code later resolves the access-control publisher path.
  - Local probe from this scan loaded the exact `main` versions of both modules with `ROUTE_PUBLISH_MODE=deferred` and `MCP_RUNTIME_IMAGE`, `COMPILER_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ACCESS_CONTROL_URL` unset, and printed:
    - `main_readyz= {'workflow_engine': 'celery', 'compilation_queue': 'compiler.jobs', 'task_name': 'compiler_worker.execute_compilation', 'runtime_image': None, 'target_namespace': None, 'route_publish_mode': 'deferred', 'access_control_url': None, 'status': 'not_ready', 'missing': ['runtime_image', 'target_namespace', 'access_control_url']}`
    - `main_effective_settings= ProductionActivitySettings(runtime_image='tool-compiler/mcp-runtime:latest', namespace='default', image_pull_policy='IfNotPresent', route_publish_mode='deferred', access_control_url=None, proxy_timeout_seconds=10.0, route_publish_timeout_seconds=10.0, runtime_startup_timeout_seconds=10.0, runtime_startup_poll_seconds=1.0)`
- Why it is a bug:
  - The readiness endpoint is supposed to describe whether the worker can actually run its production pipeline, but here it disagrees with the settings object the worker itself uses later.
  - That gives operators a false negative signal in default/deferred deployments, and if `BUG-354` is fixed by simply honoring the `/readyz` body with non-200 responses, the current readiness logic would start blocking otherwise runnable worker pods.
- Suggested validation:
  - Start the worker with `ROUTE_PUBLISH_MODE=deferred` and leave `MCP_RUNTIME_IMAGE`, `COMPILER_RUNTIME_IMAGE`, `COMPILER_TARGET_NAMESPACE`, and `ACCESS_CONTROL_URL` unset.
  - Compare `/readyz` with `ProductionActivitySettings.from_env()`.
  - Observe that `/readyz` reports missing `runtime_image`, `target_namespace`, and `access_control_url`, while the effective settings still resolve to a default runtime image, default namespace, and no access-control requirement.

### BUG-363 — Compilation detail “Artifacts” card is wired to a synthesized service ID, so real artifact metadata can never appear

- Severity: Medium
- Files:
  - `apps/web-ui/src/app/(dashboard)/compilations/[jobId]/page.tsx`
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/web-ui/src/types/api.ts`
  - `apps/compiler_api/models.py`
- Summary:
  - The compilation detail page advertises an “Artifacts” card with `IR ID`, `Image Digest`, and `Deployment ID`.
  - But the backend `CompilationJobResponse` model has no `artifacts` field at all.
  - The frontend therefore fabricates `job.artifacts` from `service_id`/`service_name`, and only ever sets `artifacts.ir_id`; `image_digest` and `deployment_id` have no source in the API contract and can never render.
- Evidence:
  - `apps/compiler_api/models.py` lines 58-95 define `CompilationJobResponse` with `service_id`, `service_name`, etc., but no `artifacts` field.
  - `apps/web-ui/src/lib/api-client.ts` defines `RawCompilationJobResponse` at lines 35-47 with no `artifacts`, `image_digest`, or `deployment_id`.
  - The same file's `normalizeCompilationJob(...)` at lines 360-383 computes `const serviceId = raw.service_id ?? raw.service_name ?? undefined;` and then synthesizes `artifacts: serviceId ? { ir_id: serviceId } : undefined`.
  - `apps/web-ui/src/types/api.ts` declares `CompilationJobResponse.artifacts` with `ir_id`, `image_digest`, and `deployment_id`, and `page.tsx` lines 302-345 renders all three fields from that object.
  - Because the normalizer only populates `{ ir_id: serviceId }`, the `Image Digest` and `Deployment ID` branches are dead for every current backend response, and even the displayed “IR ID” is just the service key.
- Why it is a bug:
  - The UI promises artifact-level metadata that the backend/job contract cannot currently supply.
  - Successful compilations can never surface real image/deployment metadata in this view, and the single populated field is mislabeled because it is synthesized from the service identifier rather than a true artifact ID.
- Suggested validation:
  - Open a successful compilation in the dashboard and inspect the network response for `GET /api/v1/compilations/{jobId}`.
  - Observe that the payload has no `artifacts` object.
  - Then inspect the rendered “Artifacts” card and note that it can only show the synthesized `IR ID` link; `Image Digest` and `Deployment ID` never appear.

### BUG-364 — Compilation event SSE appends the full auth token to the URL query string, exposing JWTs/PATs to browser and proxy logs

- Severity: High
- Files:
  - `apps/web-ui/src/lib/api-client.ts`
  - `apps/compiler_api/routes/compilations.py`
  - `apps/access_control/security.py`
- Summary:
  - The web UI creates compilation event streams with `new EventSource(...)` and appends the current auth token as `?token=...` in the URL.
  - The backend SSE auth dependency explicitly accepts the token from the query string.
  - That means long-lived JWTs/PATs are propagated in request URLs instead of headers, making them visible to browser tooling/history and to intermediary logs.
- Evidence:
  - `apps/web-ui/src/lib/api-client.ts` lines 246-250 build `authUrl = token ? \`${url}${sep}token=${encodeURIComponent(token)}\` : url;` and pass that to `new EventSource(authUrl)`.
  - `apps/web-ui/src/lib/api-client.ts` lines 546-549 use that helper for `GET /api/v1/compilations/{jobId}/events`.
  - `apps/compiler_api/routes/compilations.py` line 483 protects the SSE endpoint with `Depends(require_sse_caller)`.
  - `apps/access_control/security.py` lines 49-56 read `request.query_params.get("token", "")` before falling back to the `Authorization` header.
- Why it is a bug:
  - Query-string credentials are routinely captured by browser history, devtools/network inspectors, reverse proxies, access logs, and other URL-based telemetry.
  - Using a PAT or JWT in the SSE URL widens the exposure surface for bearer credentials compared with header-based auth.
- Suggested validation:
  - Sign in to the web UI and open a compilation detail page that starts the SSE event stream.
  - Inspect the browser network panel or any reverse-proxy/access logs.
  - Observe that the full bearer token appears in the `/api/v1/compilations/{jobId}/events?token=...` request URL.


### BUG-365 — Compiler API control-plane routes authenticate tokens but never authorize tenant/environment access, so any valid caller can enumerate global jobs and target arbitrary scoped records

- Severity: Critical
- Files:
  - `apps/access_control/security.py`
  - `apps/compiler_api/routes/compilations.py`
  - `apps/compiler_api/routes/services.py`
  - `apps/compiler_api/routes/artifacts.py`
  - `apps/compiler_api/repository.py`
  - `apps/compiler_api/tests/test_route_auth.py`
  - `apps/compiler_api/tests/test_routes_services.py`
  - `apps/compiler_api/tests/test_routes_artifacts.py`
- Summary:
  - `require_authenticated_caller()` only proves that a token is valid; it does not enforce tenant, environment, or role-based access to compiler-api data.
  - Several compiler-api handlers either discard the caller entirely or use the caller only for audit logging, while trusting user-supplied `tenant` and `environment` query parameters.
  - Compilation job listing and lookup are worse: they have no scope parameters at all and read directly from the global job table.
- Evidence:
  - On `main`, `apps/access_control/security.py` lines 22-40 extract a bearer token and return `AuthnService.validate_token(...)`; there is no tenant/environment/role authorization logic in that dependency.
  - On `main`, `apps/compiler_api/routes/compilations.py` lines 315-340 define `list_compilations()` and `get_compilation()` with authentication dependencies only; the handlers accept no caller object, and lines 323-337 call `repository.list_jobs()` / `repository.get_job(job_id)` directly.
  - On `main`, `apps/compiler_api/routes/services.py` lines 24-30 and 38-50 forward raw `tenant` / `environment` query parameters into the repository without consulting caller claims.
  - On `main`, `apps/compiler_api/routes/artifacts.py` lines 89-145 do the same for list/get routes, and lines 128-180 keep using caller identity only for audit log attribution while still trusting caller-supplied scope values for update/delete paths.
  - On `main`, `apps/compiler_api/repository.py` lines 131-151 implement `get_job()` by UUID only and `list_jobs()` as an unrestricted `select(CompilationJob).order_by(...)`; there is no tenant/environment filter path for compilation jobs.
  - Local reflection probe from this scan printed route signatures showing no authorization inputs beyond raw query params: `list_compilations(session=...)`, `get_compilation(job_id, session=...)`, `list_services(tenant=None, environment=None, session=...)`, `list_artifact_versions(service_id, tenant=None, environment=None, session=...)`.
  - Test coverage currently reinforces authentication-only behavior: `apps/compiler_api/tests/test_route_auth.py` lines 24-64 assert only that routes depend on `require_authenticated_caller`, while `apps/compiler_api/tests/test_routes_services.py` lines 25-35 and `apps/compiler_api/tests/test_routes_artifacts.py` lines 116-152 assert that user-supplied `tenant` / `environment` values are forwarded unchanged into repository queries.
- Why it is a bug:
  - Any valid token can enumerate global compilation jobs, retrieve arbitrary job UUIDs, and read or mutate service/artifact records outside its intended tenant or environment by choosing the scope in the request.
  - This collapses multi-tenant control-plane isolation into a client-honesty convention rather than an enforced authorization boundary.
- Suggested validation:
  - Authenticate as a low-privilege caller from tenant A.
  - Call `GET /api/v1/compilations` and observe that no tenant/environment scoping is required.
  - Then call service/artifact endpoints with `tenant=team-b` / `environment=prod` and observe that the backend accepts those filters without checking caller claims or roles.

### BUG-366 — Review workflow transitions remain role-blind after auth hardening, so any authenticated user can approve, publish, or deploy services

- Severity: High
- Files:
  - `apps/compiler_api/routes/workflows.py`
  - `apps/access_control/security.py`
  - `apps/compiler_api/tests/test_routes_workflows.py`
- Summary:
  - Workflow endpoints now require authentication, but transition authorization is still based only on the current workflow state.
  - The route never inspects caller roles, reviewer identity, or admin status before allowing transitions such as `in_review -> approved`, `approved -> published`, and `published -> deployed`.
  - The shared security layer already provides role helpers like `caller_is_admin()`, but compiler-api workflow routes never use them.
- Evidence:
  - On `main`, `apps/compiler_api/routes/workflows.py` lines 200-245 accept `caller`, compute `allowed = VALID_TRANSITIONS.get(record.state, [])`, and then immediately update the workflow when `payload.to` is in that state-machine list; there is no role or ownership check.
  - On `main`, `apps/access_control/security.py` lines 82-111 define `caller_roles()` and `caller_is_admin()`, but repository search under `apps/compiler_api` finds no usages of `caller_is_admin` or `require_self_or_admin`.
  - Local probe from this scan patched the DB helpers, passed a token principal with `claims={"roles": ["viewer"]}`, called `transition_workflow(..., TransitionRequest(to="approved", ...))`, and printed: `{"state": "approved", "actor": "viewer-user", "roles": ["viewer"]}`.
  - Current unit tests reflect the same gap: `apps/compiler_api/tests/test_routes_workflows.py` lines 23-32 build `_caller()` with only `claims={"sub": subject}`, and the workflow tests exercise successful transitions and history writes without any `403` / role-gating coverage.
- Why it is a bug:
  - Any authenticated user who can reach the compiler API can move a service through approval, publication, and deployment states even if they are only a viewer or belong to the wrong operational role.
  - That undermines the review workflow as a governance control, because state integrity is enforced but reviewer authorization is not.
- Suggested validation:
  - Authenticate with a non-admin token such as a caller whose claims contain only `roles=["viewer"]`.
  - Invoke `POST /api/v1/workflows/{service_id}/v/{version_number}/transition` from `in_review` to `approved`, or from `approved` to `published`.
  - Observe that the transition succeeds and records the low-privilege caller in workflow history instead of returning `403 Forbidden`.

### BUG-367 — Compiler worker rebuilds a fresh SQLAlchemy engine for every Celery task, so the executor's engine cache never actually caches anything

- Severity: Medium
- Files:
  - `apps/compiler_worker/celery_app.py`
  - `apps/compiler_worker/executor.py`
- Summary:
  - The Celery task path resolves the default executor on every compilation task.
  - The default resolver returns a brand-new `DatabaseWorkflowCompilationExecutor` object each time.
  - That executor's `_engine_cache` is instance-local, so it only caches the engine inside that one short-lived executor object.
  - The result is one new SQLAlchemy async engine / pool per task instead of a process-reused engine.
- Evidence:
  - On `main`, `apps/compiler_worker/celery_app.py` lines 84-86 run `executor = resolve_compilation_executor(); await executor.execute(request)` for every queued task.
  - On `main`, `apps/compiler_worker/executor.py` lines 45-58 define `DatabaseWorkflowCompilationExecutor._engine_cache` and lazily populate it with `create_async_engine(...)`, but only on that executor instance.
  - On `main`, `apps/compiler_worker/executor.py` lines 88-96 show `resolve_compilation_executor()` returning a fresh `DatabaseWorkflowCompilationExecutor(database_url=database_url)` whenever no override is configured; there is no process-global executor cache and no explicit engine dispose path in this module.
  - Local probe from this scan loading the exact `main` version of `apps/compiler_worker/executor.py` with `DATABASE_URL=postgresql+asyncpg://u:p@127.0.0.1:5432/db` printed:
    - `main_same_executor_object= False`
    - `main_same_engine_object= False`
    - distinct engine object IDs for the first and second `resolve_compilation_executor()` calls
- Why it is a bug:
  - The code advertises an engine cache, but the cache is defeated by constructing a brand-new executor on every task.
  - Under steady worker load this creates unnecessary engine/pool churn, extra connection handshakes, and cleanup that depends on GC rather than an explicit lifecycle.
  - That can amplify database connection pressure and latency in the hot path of compilation execution.
- Suggested validation:
  - Run a worker process that executes many compilation tasks in sequence.
  - Instrument `resolve_compilation_executor()` / `_get_engine()` or database connection counts.
  - Observe that each task creates a new executor and a new engine/pool instead of reusing a process-scoped engine.

### BUG-368 — Replaying the same queued compilation payload reruns the full workflow for the same `job_id`, duplicating events and stage side effects

- Severity: High
- Files:
  - `apps/compiler_worker/celery_app.py`
  - `apps/compiler_worker/workflows/compile_workflow.py`
  - `apps/compiler_worker/repository.py`
- Summary:
  - The worker store tries to be idempotent at job creation time: if `request.job_id` already exists, `create_job(...)` just returns the existing ID.
  - But the workflow core does not treat that as "already executing/already executed"; it immediately appends `job.created`, `job.started`, and runs every stage again.
  - Because queued requests include the persisted `job_id`, a duplicate delivery of the same task payload can rerun deploy/route/register side effects for the same logical job instead of being short-circuited.
- Evidence:
  - On `main`, `apps/compiler_worker/celery_app.py` lines 67-86 rebuild `CompilationRequest` from the queued payload and pass it straight to `_execute_compilation(request)`, preserving any existing `job_id`.
  - On `main`, `apps/compiler_worker/repository.py` lines 36-42 and 55-62 return the existing job ID when `request.job_id` / `job_id` is already present in the database.
  - On `main`, `apps/compiler_worker/workflows/compile_workflow.py` lines 139-170 call `self._store.create_job(...)`, then unconditionally append `JOB_CREATED` / `JOB_STARTED`, mark the job running, and begin stage execution.
  - Local probe from this scan loading the exact `main` version of `apps/compiler_worker/workflows/compile_workflow.py` and running `workflow.run(request)` twice with the same fixed `request.job_id` printed:
    - two `job.created` events for the same UUID
    - two `job.started` events for the same UUID
    - `stage_call_count= 18` for the 9-stage default pipeline
    - the second run starting again at `detect`
- Why it is a bug:
  - Queue delivery is not guaranteed to be exactly-once. A duplicate delivery or operator replay of the same payload should not redeploy, reroute, and reregister the same job.
  - As written, the workflow duplicates side effects and corrupts the event history for one logical job instead of treating an already-known `job_id` as terminal or already in progress.
- Suggested validation:
  - Submit a compilation and capture the exact queued payload (including `job_id`).
  - Invoke the worker task twice with that same payload, or otherwise replay the same `CompilationRequest`.
  - Observe that the second execution appends a second `job.created` / `job.started` sequence and reruns the stage pipeline for the same job UUID.
