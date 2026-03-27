import type {
  CompilationCreateRequest,
  CompilationJobResponse,
  ServiceSummary,
  ServiceListResponse,
  ArtifactVersionResponse,
  ArtifactVersionListResponse,
  ArtifactDiffResponse,
  TokenValidationRequest,
  TokenPrincipal,
  PATCreateRequest,
  PATResponse,
  PATListResponse,
  PolicyCreateRequest,
  PolicyUpdateRequest,
  PolicyResponse,
  PolicyListResponse,
  PolicyEvaluationRequest,
  PolicyEvaluationResponse,
  AuditLogEntry,
  AuditLogListResponse,
  ReconcileResponse,
  ServiceRouteRequest,
  ServiceRouteResponse,
} from "@/types/api";

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
  // EventSource does not support custom headers natively.
  // Append the token as a query parameter so the backend can authenticate.
  const separator = url.includes("?") ? "&" : "?";
  const authedUrl = token ? `${url}${separator}token=${encodeURIComponent(token)}` : url;
  return new EventSource(authedUrl);
}

// ---------------------------------------------------------------------------
// Compilation API  (Compiler service)
// ---------------------------------------------------------------------------

export const compilationApi = {
  create(req: CompilationCreateRequest) {
    return fetchAPI<CompilationJobResponse>(`${COMPILER_API}/api/v1/compilations`, {
      method: "POST",
      body: JSON.stringify(req),
    });
  },

  get(jobId: string) {
    return fetchAPI<CompilationJobResponse>(
      `${COMPILER_API}/api/v1/compilations/${jobId}`,
    );
  },

  list() {
    return fetchAPI<CompilationJobResponse[]>(
      `${COMPILER_API}/api/v1/compilations`,
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
    return fetchAPI<CompilationJobResponse>(
      `${COMPILER_API}/api/v1/compilations/${jobId}/retry${params}`,
      { method: "POST" },
    );
  },

  rollback(jobId: string) {
    return fetchAPI<CompilationJobResponse>(
      `${COMPILER_API}/api/v1/compilations/${jobId}/rollback`,
      { method: "POST" },
    );
  },
};

// ---------------------------------------------------------------------------
// Service API  (Compiler service)
// ---------------------------------------------------------------------------

export const serviceApi = {
  list(filters?: { tenant?: string; environment?: string }) {
    const params = new URLSearchParams();
    if (filters?.tenant) params.set("tenant", filters.tenant);
    if (filters?.environment) params.set("environment", filters.environment);
    const qs = params.toString();
    return fetchAPI<ServiceListResponse>(
      `${COMPILER_API}/api/v1/services${qs ? `?${qs}` : ""}`,
    );
  },

  get(serviceId: string) {
    return fetchAPI<ServiceSummary>(
      `${COMPILER_API}/api/v1/services/${serviceId}`,
    );
  },
};

// ---------------------------------------------------------------------------
// Artifact API  (Compiler service)
// ---------------------------------------------------------------------------

export const artifactApi = {
  listVersions(serviceId: string) {
    return fetchAPI<ArtifactVersionListResponse>(
      `${COMPILER_API}/api/v1/services/${serviceId}/versions`,
    );
  },

  getVersion(serviceId: string, version: number) {
    return fetchAPI<ArtifactVersionResponse>(
      `${COMPILER_API}/api/v1/services/${serviceId}/versions/${version}`,
    );
  },

  diff(serviceId: string, from: number, to: number) {
    return fetchAPI<ArtifactDiffResponse>(
      `${COMPILER_API}/api/v1/services/${serviceId}/diff?from=${from}&to=${to}`,
    );
  },
};

// ---------------------------------------------------------------------------
// Auth API  (Access-control service)
// ---------------------------------------------------------------------------

export const authApi = {
  validateToken(req: TokenValidationRequest) {
    return fetchAPI<TokenPrincipal>(`${ACCESS_CONTROL_API}/api/v1/auth/validate`, {
      method: "POST",
      body: JSON.stringify(req),
    });
  },

  createPAT(req: PATCreateRequest) {
    return fetchAPI<PATResponse>(`${ACCESS_CONTROL_API}/api/v1/auth/pats`, {
      method: "POST",
      body: JSON.stringify(req),
    });
  },

  listPATs() {
    return fetchAPI<PATListResponse>(`${ACCESS_CONTROL_API}/api/v1/auth/pats`);
  },

  revokePAT(patId: string) {
    return fetchAPI<PATResponse>(
      `${ACCESS_CONTROL_API}/api/v1/auth/pats/${patId}/revoke`,
      { method: "POST" },
    );
  },
};

// ---------------------------------------------------------------------------
// Policy API  (Access-control service)
// ---------------------------------------------------------------------------

export const policyApi = {
  create(req: PolicyCreateRequest) {
    return fetchAPI<PolicyResponse>(`${ACCESS_CONTROL_API}/api/v1/policies`, {
      method: "POST",
      body: JSON.stringify(req),
    });
  },

  list(filters?: { subject_id?: string; resource_id?: string }) {
    const params = new URLSearchParams();
    if (filters?.subject_id) params.set("subject_id", filters.subject_id);
    if (filters?.resource_id) params.set("resource_id", filters.resource_id);
    const qs = params.toString();
    return fetchAPI<PolicyListResponse>(
      `${ACCESS_CONTROL_API}/api/v1/policies${qs ? `?${qs}` : ""}`,
    );
  },

  get(policyId: string) {
    return fetchAPI<PolicyResponse>(
      `${ACCESS_CONTROL_API}/api/v1/policies/${policyId}`,
    );
  },

  update(policyId: string, req: PolicyUpdateRequest) {
    return fetchAPI<PolicyResponse>(
      `${ACCESS_CONTROL_API}/api/v1/policies/${policyId}`,
      { method: "PATCH", body: JSON.stringify(req) },
    );
  },

  delete(policyId: string) {
    return fetchAPI<void>(
      `${ACCESS_CONTROL_API}/api/v1/policies/${policyId}`,
      { method: "DELETE" },
    );
  },

  evaluate(req: PolicyEvaluationRequest) {
    return fetchAPI<PolicyEvaluationResponse>(
      `${ACCESS_CONTROL_API}/api/v1/policies/evaluate`,
      { method: "POST", body: JSON.stringify(req) },
    );
  },
};

// ---------------------------------------------------------------------------
// Audit API  (Access-control service)
// ---------------------------------------------------------------------------

export const auditApi = {
  list(filters?: { actor?: string; action?: string; resource?: string; since?: string; until?: string }) {
    const params = new URLSearchParams();
    if (filters?.actor) params.set("actor", filters.actor);
    if (filters?.action) params.set("action", filters.action);
    if (filters?.resource) params.set("resource", filters.resource);
    if (filters?.since) params.set("since", filters.since);
    if (filters?.until) params.set("until", filters.until);
    const qs = params.toString();
    return fetchAPI<AuditLogListResponse>(
      `${ACCESS_CONTROL_API}/api/v1/audit${qs ? `?${qs}` : ""}`,
    );
  },

  get(entryId: string) {
    return fetchAPI<AuditLogEntry>(
      `${ACCESS_CONTROL_API}/api/v1/audit/${entryId}`,
    );
  },
};

// ---------------------------------------------------------------------------
// Gateway API  (Compiler service)
// ---------------------------------------------------------------------------

export const gatewayApi = {
  reconcile() {
    return fetchAPI<ReconcileResponse>(
      `${COMPILER_API}/api/v1/gateway/reconcile`,
      { method: "POST" },
    );
  },

  setRoute(req: ServiceRouteRequest) {
    return fetchAPI<ServiceRouteResponse>(
      `${COMPILER_API}/api/v1/gateway/routes`,
      { method: "POST", body: JSON.stringify(req) },
    );
  },

  deleteRoute(serviceId: string) {
    return fetchAPI<void>(
      `${COMPILER_API}/api/v1/gateway/routes/${serviceId}`,
      { method: "DELETE" },
    );
  },
};
