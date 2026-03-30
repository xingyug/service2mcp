import type {
  CompilationCreateRequest,
  CompilationJobResponse,
  CompilationStage,
  CompilationStatus,
  ServiceScope,
  ServiceSummary,
  ArtifactVersionResponse,
  ArtifactVersionUpdateRequest,
  ArtifactDiffResponse,
  TokenValidationRequest,
  TokenPrincipal,
  PATCreateRequest,
  PATResponse,
  PolicyCreateRequest,
  PolicyUpdateRequest,
  PolicyResponse,
  PolicyEvaluationRequest,
  PolicyEvaluationResponse,
  AuditLogEntry,
  GatewayRouteDocument,
  GatewayRouteListResponse,
  ReconcileResponse,
  ServiceRouteRequest,
  ServiceRouteResponse,
  Operation,
  ServiceIR,
} from "@/types/api";
import {
  appendServiceScope,
  normalizeServiceScope,
  serviceScopeSearchParams,
} from "@/lib/service-scope";

type RawCompilationJobResponse = {
  id: string;
  status: string;
  protocol?: string | null;
  current_stage?: string | null;
  error_detail?: string | null;
  created_at: string;
  updated_at: string;
  service_id?: string | null;
  service_name?: string | null;
  tenant?: string | null;
  environment?: string | null;
};

type RawServiceSummary = {
  service_id: string;
  active_version: number;
  version_count: number;
  service_name: string;
  service_description?: string | null;
  tool_count: number;
  protocol?: string | null;
  tenant?: string | null;
  environment?: string | null;
  deployment_revision?: string | null;
  created_at: string;
};

type RawServiceListResponse = {
  services: RawServiceSummary[];
};

type RawArtifactVersionResponse = {
  service_id: string;
  version_number: number;
  is_active: boolean;
  ir_json: ServiceIR;
  created_at: string;
  route_config?: Record<string, unknown> | null;
  tenant?: string | null;
  environment?: string | null;
};

type RawArtifactVersionListResponse = {
  service_id: string;
  versions: RawArtifactVersionResponse[];
};

type RawArtifactDiffChange = {
  field_name: string;
  old_value?: unknown;
  new_value?: unknown;
  param_name?: string | null;
};

type RawArtifactDiffOperation = {
  operation_id: string;
  operation_name: string;
  changes: RawArtifactDiffChange[];
  added_params: string[];
  removed_params: string[];
};

type RawArtifactDiffResponse = {
  service_id: string;
  from_version: number;
  to_version: number;
  added_operations: string[];
  removed_operations: string[];
  changed_operations: RawArtifactDiffOperation[];
};

type RawTokenPrincipal = {
  subject: string;
  username?: string | null;
  token_type: string;
  claims: Record<string, unknown>;
};

type RawPATResponse = {
  id: string;
  username: string;
  name: string;
  token?: string;
  created_at: string;
  revoked_at?: string | null;
};

type RawPATListResponse = {
  items: RawPATResponse[];
  total?: number;
  page?: number;
  page_size?: number;
};

type RawPolicyResponse = {
  id: string;
  subject_type: string;
  subject_id: string;
  resource_id: string;
  action_pattern: string;
  risk_threshold: string;
  decision: string;
  created_by?: string | null;
  created_at: string;
};

type RawPolicyListResponse = {
  items: RawPolicyResponse[];
};

type RawPolicyEvaluationResponse = {
  decision: string;
  matched_policy_id?: string | null;
  reason: string;
};

type RawAuditLogEntry = {
  id: string;
  actor: string;
  action: string;
  resource?: string | null;
  detail?: Record<string, unknown> | null;
  timestamp: string;
};

type RawAuditLogListResponse = {
  items: RawAuditLogEntry[];
};

type RawGatewayRouteListResponse = {
  items: GatewayRouteDocument[];
};

// ---------------------------------------------------------------------------
// Base URLs
// ---------------------------------------------------------------------------

const COMPILER_API =
  process.env.NEXT_PUBLIC_COMPILER_API_URL || "http://localhost:8000";
const ACCESS_CONTROL_API =
  process.env.NEXT_PUBLIC_ACCESS_CONTROL_URL || "http://localhost:8001";

// ---------------------------------------------------------------------------
// Error class
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ---------------------------------------------------------------------------
// Auth token helper
// ---------------------------------------------------------------------------

function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("auth_token");
}

// ---------------------------------------------------------------------------
// Generic fetch wrapper
// ---------------------------------------------------------------------------

async function fetchAPI<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getAuthToken();

  const headers = new Headers(options.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, { ...options, headers });

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      // response body may not be JSON
    }
    throw new ApiError(
      res.status,
      `API error ${res.status}: ${res.statusText}`,
      detail,
    );
  }

  // 204 No Content – nothing to parse
  if (res.status === 204) return undefined as unknown as T;

  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// SSE helper
// ---------------------------------------------------------------------------

function createEventSource(url: string): EventSource {
  const token = getAuthToken();
  const sep = url.includes("?") ? "&" : "?";
  const authUrl = token ? `${url}${sep}token=${encodeURIComponent(token)}` : url;
  return new EventSource(authUrl);
}

function readStringClaim(
  claims: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = claims[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function readStringArrayClaim(
  claims: Record<string, unknown>,
  key: string,
): string[] | undefined {
  const value = claims[key];
  if (!Array.isArray(value)) return undefined;
  const strings = value.filter((item): item is string => typeof item === "string");
  return strings.length > 0 ? strings : undefined;
}

function normalizeTokenPrincipal(raw: RawTokenPrincipal): TokenPrincipal {
  const username =
    raw.username ??
    readStringClaim(raw.claims, "preferred_username") ??
    readStringClaim(raw.claims, "username") ??
    readStringClaim(raw.claims, "cognito:username") ??
    readStringClaim(raw.claims, "login") ??
    (raw.token_type === "pat" ? raw.subject : undefined);
  return {
    subject: raw.subject,
    token_type: raw.token_type,
    claims: raw.claims,
    username,
    email: readStringClaim(raw.claims, "email"),
    roles: readStringArrayClaim(raw.claims, "roles"),
  };
}

function normalizePAT(raw: RawPATResponse): PATResponse {
  return {
    pat_id: raw.id,
    username: raw.username,
    name: raw.name,
    token: raw.token,
    created_at: raw.created_at,
    revoked_at: raw.revoked_at ?? undefined,
  };
}

function normalizePolicy(raw: RawPolicyResponse): PolicyResponse {
  return {
    policy_id: raw.id,
    subject_type: raw.subject_type as PolicyResponse["subject_type"],
    subject_id: raw.subject_id,
    resource_id: raw.resource_id,
    action_pattern: raw.action_pattern,
    risk_threshold: raw.risk_threshold as PolicyResponse["risk_threshold"],
    decision: raw.decision as PolicyResponse["decision"],
    created_at: raw.created_at,
    updated_at: undefined,
  };
}

function normalizeAuditLogEntry(raw: RawAuditLogEntry): AuditLogEntry {
  return {
    id: raw.id,
    actor: raw.actor,
    action: raw.action,
    resource: raw.resource ?? "",
    detail: raw.detail ? JSON.stringify(raw.detail, null, 2) : undefined,
    timestamp: raw.timestamp,
  };
}

function normalizeCompilationStage(
  rawStage?: string | null,
): CompilationStage | undefined {
  if (!rawStage) return undefined;
  return rawStage as CompilationStage;
}

function normalizeCompilationStatus(
  rawStatus: string,
): CompilationStatus {
  switch (rawStatus) {
    case "pending":
      return "pending";
    case "running":
      return "running";
    case "succeeded":
      return "succeeded";
    case "failed":
      return "failed";
    case "rolled_back":
      return "rolled_back";
    default:
      return "pending";
  }
}

function normalizeCompilationJob(
  raw: RawCompilationJobResponse,
): CompilationJobResponse {
  const currentStage = normalizeCompilationStage(raw.current_stage);
  const status = normalizeCompilationStatus(raw.status);
  const scope = normalizeServiceScope({
    tenant: raw.tenant ?? undefined,
    environment: raw.environment ?? undefined,
  });
  const serviceId = raw.service_id ?? raw.service_name ?? undefined;
  const isTerminal =
    raw.status === "succeeded" ||
    raw.status === "failed" ||
    raw.status === "rolled_back";

  return {
    job_id: raw.id,
    protocol: raw.protocol ?? undefined,
    status,
    current_stage: currentStage,
    failed_stage: raw.status === "failed" ? currentStage : undefined,
    created_at: raw.created_at,
    completed_at: isTerminal ? raw.updated_at : undefined,
    error_message: raw.error_detail ?? undefined,
    service_id: serviceId,
    service_name: raw.service_name ?? undefined,
    tenant: scope?.tenant,
    environment: scope?.environment,
    artifacts: serviceId
      ? {
          ir_id: serviceId,
        }
      : undefined,
  };
}

function normalizeServiceSummary(raw: RawServiceSummary): ServiceSummary {
  return {
    service_id: raw.service_id,
    name: raw.service_name,
    protocol: raw.protocol ?? "unknown",
    tool_count: raw.tool_count,
    active_version: raw.active_version,
    version_count: Math.max(raw.version_count ?? 0, 0),
    last_compiled: raw.created_at,
    tenant: raw.tenant ?? undefined,
    environment: raw.environment ?? undefined,
  };
}

function normalizeArtifactVersion(
  raw: RawArtifactVersionResponse,
): ArtifactVersionResponse {
  const scope = normalizeServiceScope({
    tenant: raw.tenant ?? undefined,
    environment: raw.environment ?? undefined,
  });
  return {
    service_id: raw.service_id,
    version_number: raw.version_number,
    ir: raw.ir_json,
    is_active: raw.is_active,
    created_at: raw.created_at,
    route_config: applyScopeToRouteConfig(raw.route_config, scope),
    tenant: scope?.tenant,
    environment: scope?.environment,
  };
}

function applyScopeToRouteConfig(
  routeConfig: Record<string, unknown> | null | undefined,
  scope?: ServiceScope,
): Record<string, unknown> | undefined {
  if (!routeConfig) {
    return undefined;
  }
  const normalizedScope = normalizeServiceScope(scope);
  if (!normalizedScope) {
    return routeConfig;
  }
  return {
    ...routeConfig,
    tenant: normalizedScope.tenant,
    environment: normalizedScope.environment,
  };
}

function placeholderOperation(id: string): Operation {
  return {
    id,
    name: id,
    description: "",
    params: [],
    risk: {
      risk_level: "unknown",
      confidence: 0,
      source: "extractor",
    },
    tags: [],
    source: "extractor",
    confidence: 0,
    enabled: true,
  };
}

function operationIndex(
  version?: RawArtifactVersionResponse,
): Map<string, Operation> {
  return new Map(
    (version?.ir_json?.operations ?? []).map((operation) => [operation.id, operation]),
  );
}

function normalizeArtifactDiff(
  raw: RawArtifactDiffResponse,
  fromVersion: RawArtifactVersionResponse,
  toVersion: RawArtifactVersionResponse,
): ArtifactDiffResponse {
  const fromOperations = operationIndex(fromVersion);
  const toOperations = operationIndex(toVersion);

  return {
    from_version: raw.from_version,
    to_version: raw.to_version,
    added_operations: raw.added_operations.map(
      (operationId) => toOperations.get(operationId) ?? placeholderOperation(operationId),
    ),
    removed_operations: raw.removed_operations.map(
      (operationId) =>
        fromOperations.get(operationId) ?? placeholderOperation(operationId),
    ),
    changed_operations: raw.changed_operations.map((operation) => ({
      operation_id: operation.operation_id,
      diff_type: "modify",
      changes: [
        ...operation.changes.map((change) => ({
          field: change.param_name
            ? `${change.field_name}.${change.param_name}`
            : change.field_name,
          old_value: change.old_value,
          new_value: change.new_value,
        })),
        ...operation.added_params.map((paramName) => ({
          field: `param.${paramName}`,
          new_value: "added",
        })),
        ...operation.removed_params.map((paramName) => ({
          field: `param.${paramName}`,
          old_value: "removed",
        })),
      ],
    })),
  };
}

function fetchRawArtifactVersion(
  serviceId: string,
  version: number,
  scope?: ServiceScope,
) {
  return fetchAPI<RawArtifactVersionResponse>(
    appendServiceScope(
      `${COMPILER_API}/api/v1/artifacts/${serviceId}/versions/${version}`,
      scope,
    ),
  );
}

// ---------------------------------------------------------------------------
// Compilation API  (Compiler service)
// ---------------------------------------------------------------------------

export const compilationApi = {
  create(req: CompilationCreateRequest) {
    return fetchAPI<RawCompilationJobResponse>(`${COMPILER_API}/api/v1/compilations`, {
      method: "POST",
      body: JSON.stringify(req),
    }).then(normalizeCompilationJob);
  },

  get(jobId: string) {
    return fetchAPI<RawCompilationJobResponse>(
      `${COMPILER_API}/api/v1/compilations/${jobId}`,
    ).then(normalizeCompilationJob);
  },

  list() {
    return fetchAPI<RawCompilationJobResponse[]>(
      `${COMPILER_API}/api/v1/compilations`,
    ).then((rawJobs) =>
      (Array.isArray(rawJobs) ? rawJobs : []).map(normalizeCompilationJob),
    );
  },

  /** Returns an EventSource that emits CompilationEvent JSON payloads. */
  streamEvents(jobId: string): EventSource {
    return createEventSource(
      `${COMPILER_API}/api/v1/compilations/${jobId}/events`,
    );
  },

  retry(jobId: string, fromStage?: string) {
    const params = fromStage ? `?from_stage=${encodeURIComponent(fromStage)}` : "";
    return fetchAPI<RawCompilationJobResponse>(
      `${COMPILER_API}/api/v1/compilations/${jobId}/retry${params}`,
      { method: "POST" },
    ).then(normalizeCompilationJob);
  },

  rollback(jobId: string) {
    return fetchAPI<RawCompilationJobResponse>(
      `${COMPILER_API}/api/v1/compilations/${jobId}/rollback`,
      { method: "POST" },
    ).then(normalizeCompilationJob);
  },
};

// ---------------------------------------------------------------------------
// Service API  (Compiler service)
// ---------------------------------------------------------------------------

export const serviceApi = {
  list(filters?: { tenant?: string; environment?: string }) {
    return fetchAPI<RawServiceListResponse>(
      appendServiceScope(`${COMPILER_API}/api/v1/services`, filters),
    ).then((raw) => ({
      services: (Array.isArray(raw.services) ? raw.services : []).map(
        normalizeServiceSummary,
      ),
    }));
  },

  get(serviceId: string, scope?: ServiceScope) {
    return fetchAPI<RawServiceSummary>(
      appendServiceScope(`${COMPILER_API}/api/v1/services/${serviceId}`, scope),
    ).then(normalizeServiceSummary);
  },
};

// ---------------------------------------------------------------------------
// Artifact API  (Compiler service)
// ---------------------------------------------------------------------------

export const artifactApi = {
  listVersions(serviceId: string, scope?: ServiceScope) {
    return fetchAPI<RawArtifactVersionListResponse>(
      appendServiceScope(
        `${COMPILER_API}/api/v1/artifacts/${serviceId}/versions`,
        scope,
      ),
    ).then((raw) => ({
      versions: (Array.isArray(raw.versions) ? raw.versions : []).map(
        normalizeArtifactVersion,
      ),
    }));
  },

  getVersion(serviceId: string, version: number, scope?: ServiceScope) {
    return fetchRawArtifactVersion(serviceId, version, scope).then(
      normalizeArtifactVersion,
    );
  },

  updateVersion(
    serviceId: string,
    version: number,
    payload: ArtifactVersionUpdateRequest,
    scope?: ServiceScope,
  ) {
    return fetchAPI<RawArtifactVersionResponse>(
      appendServiceScope(
        `${COMPILER_API}/api/v1/artifacts/${serviceId}/versions/${version}`,
        scope,
      ),
      { method: "PUT", body: JSON.stringify(payload) },
    ).then(normalizeArtifactVersion);
  },

  activateVersion(serviceId: string, version: number, scope?: ServiceScope) {
    return fetchAPI<RawArtifactVersionResponse>(
      appendServiceScope(
        `${COMPILER_API}/api/v1/artifacts/${serviceId}/versions/${version}/activate`,
        scope,
      ),
      { method: "POST" },
    ).then(normalizeArtifactVersion);
  },

  deleteVersion(serviceId: string, version: number, scope?: ServiceScope) {
    return fetchAPI<void>(
      appendServiceScope(
        `${COMPILER_API}/api/v1/artifacts/${serviceId}/versions/${version}`,
        scope,
      ),
      { method: "DELETE" },
    );
  },

  diff(serviceId: string, from: number, to: number, scope?: ServiceScope) {
    const params = serviceScopeSearchParams(scope);
    params.set("from", String(from));
    params.set("to", String(to));

    return Promise.all([
      fetchAPI<RawArtifactDiffResponse>(
        `${COMPILER_API}/api/v1/artifacts/${serviceId}/diff?${params.toString()}`,
      ),
      fetchRawArtifactVersion(serviceId, from, scope),
      fetchRawArtifactVersion(serviceId, to, scope),
    ]).then(([rawDiff, fromVersion, toVersion]) =>
      normalizeArtifactDiff(rawDiff, fromVersion, toVersion),
    );
  },
};

// ---------------------------------------------------------------------------
// Auth API  (Access-control service)
// ---------------------------------------------------------------------------

export const authApi = {
  validateToken(req: TokenValidationRequest) {
    return fetchAPI<RawTokenPrincipal>(
      `${ACCESS_CONTROL_API}/api/v1/authn/validate`,
      {
        method: "POST",
        body: JSON.stringify(req),
      },
    ).then(normalizeTokenPrincipal);
  },

  createPAT(req: PATCreateRequest) {
    return fetchAPI<RawPATResponse>(`${ACCESS_CONTROL_API}/api/v1/authn/pats`, {
      method: "POST",
      body: JSON.stringify(req),
    }).then(normalizePAT);
  },

  listPATs(username: string, page = 1, pageSize = 100) {
    const params = new URLSearchParams({
      username,
      page: String(page),
      page_size: String(pageSize),
    });
    return fetchAPI<RawPATListResponse>(
      `${ACCESS_CONTROL_API}/api/v1/authn/pats?${params.toString()}`,
    ).then((raw) => ({
      pats: raw.items.map(normalizePAT),
      total: raw.total ?? raw.items.length,
      page: raw.page ?? 1,
      pageSize: raw.page_size ?? pageSize,
    }));
  },

  revokePAT(patId: string) {
    return fetchAPI<RawPATResponse>(
      `${ACCESS_CONTROL_API}/api/v1/authn/pats/${patId}/revoke`,
      { method: "POST" },
    ).then(normalizePAT);
  },
};

// ---------------------------------------------------------------------------
// Policy API  (Access-control service)
// ---------------------------------------------------------------------------

export const policyApi = {
  create(req: PolicyCreateRequest) {
    return fetchAPI<RawPolicyResponse>(`${ACCESS_CONTROL_API}/api/v1/authz/policies`, {
      method: "POST",
      body: JSON.stringify(req),
    }).then(normalizePolicy);
  },

  list(filters?: { subject_type?: string; subject_id?: string; resource_id?: string }) {
    const params = new URLSearchParams();
    if (filters?.subject_type) params.set("subject_type", filters.subject_type);
    if (filters?.subject_id) params.set("subject_id", filters.subject_id);
    if (filters?.resource_id) params.set("resource_id", filters.resource_id);
    const qs = params.toString();
    return fetchAPI<RawPolicyListResponse>(
      `${ACCESS_CONTROL_API}/api/v1/authz/policies${qs ? `?${qs}` : ""}`,
    ).then((raw) => ({
      policies: raw.items.map(normalizePolicy),
    }));
  },

  get(policyId: string) {
    return fetchAPI<RawPolicyResponse>(
      `${ACCESS_CONTROL_API}/api/v1/authz/policies/${policyId}`,
    ).then(normalizePolicy);
  },

  update(policyId: string, req: PolicyUpdateRequest) {
    return fetchAPI<RawPolicyResponse>(
      `${ACCESS_CONTROL_API}/api/v1/authz/policies/${policyId}`,
      { method: "PUT", body: JSON.stringify(req) },
    ).then(normalizePolicy);
  },

  delete(policyId: string) {
    return fetchAPI<void>(
      `${ACCESS_CONTROL_API}/api/v1/authz/policies/${policyId}`,
      { method: "DELETE" },
    );
  },

  evaluate(req: PolicyEvaluationRequest) {
    return fetchAPI<RawPolicyEvaluationResponse>(
      `${ACCESS_CONTROL_API}/api/v1/authz/evaluate`,
      {
        method: "POST",
        body: JSON.stringify(req),
      },
    ).then((raw) => ({
      decision: raw.decision as PolicyEvaluationResponse["decision"],
      matched_policy_id: raw.matched_policy_id ?? undefined,
      reason: raw.reason,
    }));
  },
};

// ---------------------------------------------------------------------------
// Audit API  (Access-control service)
// ---------------------------------------------------------------------------

export const auditApi = {
  list(
    filters?: { actor?: string; action?: string; resource?: string; since?: string; until?: string },
    options?: { include_all?: boolean },
  ) {
    const params = new URLSearchParams();
    if (filters?.actor) params.set("actor", filters.actor);
    if (filters?.action) params.set("action", filters.action);
    if (filters?.resource) params.set("resource", filters.resource);
    if (filters?.since) params.set("start_at", filters.since);
    if (filters?.until) params.set("end_at", filters.until);
    if (options?.include_all) params.set("include_all", "true");
    const qs = params.toString();
    return fetchAPI<RawAuditLogListResponse>(
      `${ACCESS_CONTROL_API}/api/v1/audit/logs${qs ? `?${qs}` : ""}`,
    ).then((raw) => ({
      entries: raw.items.map(normalizeAuditLogEntry),
    }));
  },

  get(entryId: string) {
    return fetchAPI<RawAuditLogEntry>(
      `${ACCESS_CONTROL_API}/api/v1/audit/logs/${encodeURIComponent(entryId)}`,
    )
      .then(normalizeAuditLogEntry);
  },
};

// ---------------------------------------------------------------------------
// Gateway API  (Access-control service)
// ---------------------------------------------------------------------------

export const gatewayApi = {
  reconcile() {
    return fetchAPI<ReconcileResponse>(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/reconcile`,
      { method: "POST" },
    );
  },

  listRoutes() {
    return fetchAPI<RawGatewayRouteListResponse>(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/service-routes`,
    ).then((raw): GatewayRouteListResponse => ({
      routes: raw.items,
    }));
  },

  syncRoutes(req: ServiceRouteRequest) {
    return fetchAPI<ServiceRouteResponse>(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/service-routes/sync`,
      { method: "POST", body: JSON.stringify(req) },
    );
  },

  deleteRoutes(req: ServiceRouteRequest) {
    return fetchAPI<ServiceRouteResponse>(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/service-routes/delete`,
      { method: "POST", body: JSON.stringify(req) },
    );
  },

  rollbackRoutes(req: ServiceRouteRequest) {
    return fetchAPI<ServiceRouteResponse>(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/service-routes/rollback`,
      { method: "POST", body: JSON.stringify(req) },
    );
  },
};

// ---------------------------------------------------------------------------
// Workflow API  (Compiler service)
// ---------------------------------------------------------------------------

export interface WorkflowHistoryEntry {
  from: string;
  to: string;
  actor: string;
  comment?: string;
  timestamp: string;
}

export interface WorkflowResponse {
  id: string;
  service_id: string;
  version_number: number;
   tenant?: string | null;
   environment?: string | null;
  state: string;
  review_notes:
    | {
        operation_notes?: Record<string, string>;
        overall_note?: string;
        reviewed_operations?: string[];
      }
    | null;
  history: WorkflowHistoryEntry[];
  created_at: string;
  updated_at: string;
}

export const workflowApi = {
  get(serviceId: string, versionNumber: number, scope?: ServiceScope) {
    return fetchAPI<WorkflowResponse>(
      appendServiceScope(
        `${COMPILER_API}/api/v1/workflows/${encodeURIComponent(serviceId)}/v/${versionNumber}`,
        scope,
      ),
    );
  },

  transition(
    serviceId: string,
    versionNumber: number,
    to: string,
    actor: string,
    comment?: string,
    scope?: ServiceScope,
  ) {
    return fetchAPI<WorkflowResponse>(
      appendServiceScope(
        `${COMPILER_API}/api/v1/workflows/${encodeURIComponent(serviceId)}/v/${versionNumber}/transition`,
        scope,
      ),
      { method: "POST", body: JSON.stringify({ to, actor, comment }) },
    );
  },

  saveNotes(
    serviceId: string,
    versionNumber: number,
    notes: Record<string, string>,
    overallNote?: string,
    reviewedOperations?: string[],
    scope?: ServiceScope,
  ) {
    return fetchAPI<WorkflowResponse>(
      appendServiceScope(
        `${COMPILER_API}/api/v1/workflows/${encodeURIComponent(serviceId)}/v/${versionNumber}/notes`,
        scope,
      ),
      {
        method: "PUT",
        body: JSON.stringify({
          notes,
          overall_note: overallNote,
          reviewed_operations: reviewedOperations ?? [],
        }),
      },
    );
  },

  history(serviceId: string, versionNumber: number, scope?: ServiceScope) {
    return fetchAPI<WorkflowHistoryEntry[]>(
      appendServiceScope(
        `${COMPILER_API}/api/v1/workflows/${encodeURIComponent(serviceId)}/v/${versionNumber}/history`,
        scope,
      ),
    );
  },
};
