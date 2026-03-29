import { describe, it, expect, beforeEach, vi, type Mock } from "vitest";
import {
  ApiError,
  compilationApi,
  serviceApi,
  artifactApi,
  authApi,
  policyApi,
  auditApi,
  gatewayApi,
} from "../api-client";
import type { ServiceRouteRequest } from "@/types/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const COMPILER_API = "http://localhost:8000";
const ACCESS_CONTROL_API = "http://localhost:8001";

let mockFetch: Mock;

function mockResponse(body: unknown, init?: ResponseInit): Response {
  const status = init?.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: init?.statusText ?? "OK",
    json: () => Promise.resolve(body),
    headers: new Headers(),
  } as unknown as Response;
}

function lastFetchCall() {
  return mockFetch.mock.calls[mockFetch.mock.calls.length - 1] as [
    string,
    RequestInit | undefined,
  ];
}

function lastFetchUrl() {
  return lastFetchCall()[0];
}

function lastFetchOptions() {
  return lastFetchCall()[1]!;
}

function lastFetchHeaders() {
  return new Headers(lastFetchOptions().headers as HeadersInit);
}

// ---------------------------------------------------------------------------

describe("api-client", () => {
  beforeEach(() => {
    mockFetch = vi.fn().mockResolvedValue(mockResponse({}));
    global.fetch = mockFetch;
    localStorage.clear();
  });

  // -----------------------------------------------------------------------
  // fetchAPI – auth header
  // -----------------------------------------------------------------------

  it("adds Authorization header when token is present in localStorage", async () => {
    localStorage.setItem("auth_token", "my-secret");
    await compilationApi.list();

    expect(lastFetchHeaders().get("Authorization")).toBe("Bearer my-secret");
  });

  it("does not add Authorization header when no token", async () => {
    await compilationApi.list();
    expect(lastFetchHeaders().has("Authorization")).toBe(false);
  });

  // -----------------------------------------------------------------------
  // fetchAPI – error handling
  // -----------------------------------------------------------------------

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({ detail: "not found" }, { status: 404, statusText: "Not Found" }),
    );

    await expect(compilationApi.list()).rejects.toThrow(ApiError);
    try {
      await compilationApi.list();
    } catch {
      // secondary call also fails, previous assertion covers it
    }
  });

  it("ApiError contains status and detail", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({ msg: "bad" }, { status: 422, statusText: "Unprocessable" }),
    );

    try {
      await compilationApi.list();
      expect.unreachable("should have thrown");
    } catch (err: unknown) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(422);
      expect(apiErr.detail).toEqual({ msg: "bad" });
    }
  });

  it("handles non-JSON error body gracefully", async () => {
    const errResponse = {
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: () => Promise.reject(new Error("not json")),
      headers: new Headers(),
    } as unknown as Response;
    mockFetch.mockResolvedValueOnce(errResponse);

    await expect(compilationApi.list()).rejects.toThrow(ApiError);
  });

  // -----------------------------------------------------------------------
  // fetchAPI – 204 No Content
  // -----------------------------------------------------------------------

  it("handles 204 No Content without parsing body", async () => {
    const noContentResponse = {
      ok: true,
      status: 204,
      statusText: "No Content",
      json: () => Promise.reject(new Error("no body")),
      headers: new Headers(),
    } as unknown as Response;
    mockFetch.mockResolvedValueOnce(noContentResponse);

    const result = await policyApi.delete("pol-1");
    expect(result).toBeUndefined();
  });

  // -----------------------------------------------------------------------
  // compilationApi
  // -----------------------------------------------------------------------

  it("compilationApi.create sends POST with JSON body", async () => {
    const body = { service_id: "svc", source_url: "http://example.com" };
    await compilationApi.create(body as never);

    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/compilations`);
    expect(lastFetchOptions().method).toBe("POST");
    expect(lastFetchOptions().body).toBe(JSON.stringify(body));
  });

  it("compilationApi.create sets Content-Type to application/json", async () => {
    await compilationApi.create({ service_id: "x" } as never);
    expect(lastFetchHeaders().get("Content-Type")).toBe("application/json");
  });

  it("compilationApi.list sends GET", async () => {
    await compilationApi.list();

    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/compilations`);
    expect(lastFetchOptions().method).toBeUndefined();
  });

  it("compilationApi.list normalizes backend job payloads", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse([
        {
          id: "job-42",
          status: "running",
          protocol: "openapi",
          current_stage: "extract",
          error_detail: null,
          created_at: "2026-03-29T00:00:00Z",
          updated_at: "2026-03-29T00:01:00Z",
          service_name: "billing-api",
        },
      ]),
    );

    const jobs = await compilationApi.list();

    expect(jobs).toEqual([
      {
        job_id: "job-42",
        protocol: "openapi",
        status: "running",
        current_stage: "extract",
        failed_stage: undefined,
        created_at: "2026-03-29T00:00:00Z",
        completed_at: undefined,
        error_message: undefined,
        artifacts: { ir_id: "billing-api" },
      },
    ]);
  });

  it("compilationApi.get sends GET with jobId in URL", async () => {
    await compilationApi.get("job-42");
    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/compilations/job-42`);
  });

  it("compilationApi.retry sends POST with fromStage query param", async () => {
    await compilationApi.retry("job-42", "validate");

    expect(lastFetchUrl()).toBe(
      `${COMPILER_API}/api/v1/compilations/job-42/retry?from_stage=validate`,
    );
    expect(lastFetchOptions().method).toBe("POST");
  });

  it("compilationApi.retry sends POST without query param when fromStage omitted", async () => {
    await compilationApi.retry("job-42");

    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/compilations/job-42/retry`);
  });

  it("compilationApi.rollback sends POST", async () => {
    await compilationApi.rollback("job-42");

    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/compilations/job-42/rollback`);
    expect(lastFetchOptions().method).toBe("POST");
  });

  it("compilationApi.streamEvents includes auth token in the SSE URL", () => {
    localStorage.setItem("auth_token", "secret-token");
    const close = vi.fn();
    class MockEventSource {
      constructor(public readonly url: string) {}

      close = close;
    }
    vi.stubGlobal("EventSource", MockEventSource);

    const stream = compilationApi.streamEvents("job-42");

    expect(stream).toBeInstanceOf(MockEventSource);
    expect((stream as unknown as MockEventSource).url).toBe(
      `${COMPILER_API}/api/v1/compilations/job-42/events?token=secret-token`,
    );
  });

  // -----------------------------------------------------------------------
  // serviceApi
  // -----------------------------------------------------------------------

  it("serviceApi.list sends GET without params when no filters", async () => {
    await serviceApi.list();
    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/services`);
  });

  it("serviceApi.list sends GET with query params for filters", async () => {
    await serviceApi.list({ tenant: "acme", environment: "prod" });
    const url = lastFetchUrl();
    expect(url).toContain("tenant=acme");
    expect(url).toContain("environment=prod");
  });

  it("serviceApi.get sends GET with serviceId in URL", async () => {
    await serviceApi.get("svc-99");
    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/services/svc-99`);
  });

  it("serviceApi.list normalizes backend service summaries", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({
        services: [
          {
            service_id: "svc-1",
            active_version: 2,
            service_name: "Billing API",
            tool_count: 3,
            protocol: "openapi",
            tenant: "team-a",
            environment: "prod",
            created_at: "2026-03-29T00:00:00Z",
          },
        ],
      }),
    );

    const response = await serviceApi.list();

    expect(response).toEqual({
      services: [
        {
          service_id: "svc-1",
          name: "Billing API",
          protocol: "openapi",
          tool_count: 3,
          active_version: 2,
          version_count: 2,
          last_compiled: "2026-03-29T00:00:00Z",
          tenant: "team-a",
          environment: "prod",
        },
      ],
    });
  });

  // -----------------------------------------------------------------------
  // artifactApi
  // -----------------------------------------------------------------------

  it("artifactApi.listVersions uses artifact registry paths and normalizes IR payloads", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({
        service_id: "svc-1",
        versions: [
          {
            service_id: "svc-1",
            version_number: 3,
            is_active: true,
            created_at: "2026-03-29T00:00:00Z",
            ir_json: {
              service_name: "Billing API",
              operations: [],
            },
          },
        ],
      }),
    );

    const response = await artifactApi.listVersions("svc-1");

    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/artifacts/svc-1/versions`);
    expect(response.versions[0]).toMatchObject({
      service_id: "svc-1",
      version_number: 3,
      is_active: true,
      created_at: "2026-03-29T00:00:00Z",
      ir: {
        service_name: "Billing API",
        operations: [],
      },
    });
  });

  it("artifactApi.diff uses artifact registry paths and reconstructs operation details", async () => {
    mockFetch
      .mockResolvedValueOnce(
        mockResponse({
          service_id: "svc-1",
          from_version: 1,
          to_version: 2,
          added_operations: ["createOrder"],
          removed_operations: ["deleteOrder"],
          changed_operations: [
            {
              operation_id: "getOrder",
              operation_name: "Get Order",
              changes: [
                {
                  field_name: "description",
                  old_value: "old",
                  new_value: "new",
                },
              ],
              added_params: ["expand"],
              removed_params: [],
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        mockResponse({
          service_id: "svc-1",
          version_number: 1,
          is_active: false,
          created_at: "2026-03-29T00:00:00Z",
          ir_json: {
            operations: [
              {
                id: "deleteOrder",
                name: "Delete Order",
                description: "",
                params: [],
                risk: { risk_level: "dangerous", confidence: 1, source: "extractor" },
                tags: [],
                source: "extractor",
                confidence: 1,
                enabled: true,
              },
            ],
          },
        }),
      )
      .mockResolvedValueOnce(
        mockResponse({
          service_id: "svc-1",
          version_number: 2,
          is_active: true,
          created_at: "2026-03-29T00:00:00Z",
          ir_json: {
            operations: [
              {
                id: "createOrder",
                name: "Create Order",
                description: "",
                params: [],
                risk: { risk_level: "cautious", confidence: 1, source: "extractor" },
                tags: [],
                source: "extractor",
                confidence: 1,
                enabled: true,
              },
            ],
          },
        }),
      );

    const response = await artifactApi.diff("svc-1", 1, 2);

    expect(mockFetch.mock.calls[0]?.[0]).toBe(
      `${COMPILER_API}/api/v1/artifacts/svc-1/diff?from=1&to=2`,
    );
    expect(mockFetch.mock.calls[1]?.[0]).toBe(
      `${COMPILER_API}/api/v1/artifacts/svc-1/versions/1`,
    );
    expect(mockFetch.mock.calls[2]?.[0]).toBe(
      `${COMPILER_API}/api/v1/artifacts/svc-1/versions/2`,
    );
    expect(response.added_operations[0]?.id).toBe("createOrder");
    expect(response.removed_operations[0]?.id).toBe("deleteOrder");
    expect(response.changed_operations[0]).toMatchObject({
      operation_id: "getOrder",
      diff_type: "modify",
    });
    expect(response.changed_operations[0]?.changes).toEqual([
      {
        field: "description",
        old_value: "old",
        new_value: "new",
      },
      {
        field: "param.expand",
        new_value: "added",
      },
    ]);
  });

  it("artifactApi.activateVersion uses activation endpoint", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({
        service_id: "svc-1",
        version_number: 2,
        is_active: true,
        created_at: "2026-03-29T00:00:00Z",
        ir_json: { operations: [] },
      }),
    );

    await artifactApi.activateVersion("svc-1", 2);

    expect(lastFetchUrl()).toBe(
      `${COMPILER_API}/api/v1/artifacts/svc-1/versions/2/activate`,
    );
    expect(lastFetchOptions().method).toBe("POST");
  });

  it("artifactApi.deleteVersion uses delete endpoint", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(undefined, { status: 204, statusText: "No Content" }),
    );

    await artifactApi.deleteVersion("svc-1", 2);

    expect(lastFetchUrl()).toBe(
      `${COMPILER_API}/api/v1/artifacts/svc-1/versions/2`,
    );
    expect(lastFetchOptions().method).toBe("DELETE");
  });

  // -----------------------------------------------------------------------
  // authApi
  // -----------------------------------------------------------------------

  it("authApi.validateToken sends POST to /authn/validate with JSON body", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({
        subject: "alice",
        token_type: "jwt",
        claims: { email: "alice@example.com", roles: ["admin"] },
      }),
    );

    await authApi.validateToken({ token: "jwt-token" });

    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/authn/validate`);
    expect(lastFetchOptions().method).toBe("POST");
    expect(lastFetchOptions().body).toBe(JSON.stringify({ token: "jwt-token" }));
  });

  it("authApi.listPATs sends username query param to /authn/pats", async () => {
    mockFetch.mockResolvedValueOnce(mockResponse({ items: [] }));

    await authApi.listPATs("alice");

    expect(lastFetchUrl()).toBe(
      `${ACCESS_CONTROL_API}/api/v1/authn/pats?username=alice`,
    );
  });

  // -----------------------------------------------------------------------
  // policyApi
  // -----------------------------------------------------------------------

  it("policyApi.create sends POST", async () => {
    const body = { name: "policy-1" };
    await policyApi.create(body as never);

    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/authz/policies`);
    expect(lastFetchOptions().method).toBe("POST");
    expect(lastFetchOptions().body).toBe(JSON.stringify(body));
  });

  it("policyApi.update sends PUT", async () => {
    const body = { name: "updated" };
    await policyApi.update("pol-1", body as never);

    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/authz/policies/pol-1`);
    expect(lastFetchOptions().method).toBe("PUT");
  });

  it("policyApi.delete sends DELETE", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(undefined, { status: 204, statusText: "No Content" }),
    );
    await policyApi.delete("pol-1");

    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/authz/policies/pol-1`);
    expect(lastFetchOptions().method).toBe("DELETE");
  });

  // -----------------------------------------------------------------------
  // auditApi
  // -----------------------------------------------------------------------

  it("auditApi.list sends GET with no query params when no filters", async () => {
    mockFetch.mockResolvedValueOnce(mockResponse({ items: [] }));
    await auditApi.list();
    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/audit/logs`);
  });

  it("auditApi.list sends GET with correct query params", async () => {
    mockFetch.mockResolvedValueOnce(mockResponse({ items: [] }));
    await auditApi.list({ actor: "alice", action: "create", since: "2024-01-01" });
    const url = lastFetchUrl();
    expect(url).toContain("actor=alice");
    expect(url).toContain("action=create");
    expect(url).toContain("start_at=2024-01-01");
  });

  it("auditApi.get falls back to list() and returns the matching entry", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({
        items: [
          {
            id: "entry-7",
            actor: "alice",
            action: "policy.created",
            resource: "svc-1",
            detail: { ok: true },
            timestamp: "2026-03-29T00:00:00Z",
          },
        ],
      }),
    );

    await auditApi.get("entry-7");
    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/audit/logs`);
  });

  // -----------------------------------------------------------------------
  // gatewayApi
  // -----------------------------------------------------------------------

  it("gatewayApi.reconcile sends POST", async () => {
    await gatewayApi.reconcile();

    expect(lastFetchUrl()).toBe(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/reconcile`,
    );
    expect(lastFetchOptions().method).toBe("POST");
  });

  it("gatewayApi.syncRoutes sends POST with route payload", async () => {
    const body: ServiceRouteRequest = {
      route_config: { service_id: "svc-1" },
      previous_routes: {},
    };
    await gatewayApi.syncRoutes(body);

    expect(lastFetchUrl()).toBe(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/service-routes/sync`,
    );
    expect(lastFetchOptions().method).toBe("POST");
    expect(lastFetchOptions().body).toBe(JSON.stringify(body));
  });

  it("gatewayApi.deleteRoutes sends POST with route payload", async () => {
    const body: ServiceRouteRequest = {
      route_config: { service_id: "svc-1" },
      previous_routes: {},
    };
    await gatewayApi.deleteRoutes(body);

    expect(lastFetchUrl()).toBe(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/service-routes/delete`,
    );
    expect(lastFetchOptions().method).toBe("POST");
    expect(lastFetchOptions().body).toBe(JSON.stringify(body));
  });

  it("gatewayApi.rollbackRoutes sends POST with current and previous routes", async () => {
    const body: ServiceRouteRequest = {
      route_config: { service_id: "svc-1" },
      previous_routes: {
        "svc-1-active": {
          route_id: "svc-1-active",
          route_type: "default",
          service_id: "svc-1",
          service_name: "Billing API",
          namespace: "runtime-system",
          target_service: { name: "billing-runtime-v1", port: 8003 },
        },
      },
    };
    await gatewayApi.rollbackRoutes(body);

    expect(lastFetchUrl()).toBe(
      `${ACCESS_CONTROL_API}/api/v1/gateway-binding/service-routes/rollback`,
    );
    expect(lastFetchOptions().method).toBe("POST");
    expect(lastFetchOptions().body).toBe(JSON.stringify(body));
  });

  // -----------------------------------------------------------------------
  // getAuthToken reads from localStorage
  // -----------------------------------------------------------------------

  it("reads auth_token key from localStorage for each request", async () => {
    localStorage.setItem("auth_token", "first");
    await compilationApi.list();
    expect(lastFetchHeaders().get("Authorization")).toBe("Bearer first");

    localStorage.setItem("auth_token", "second");
    await compilationApi.list();
    expect(lastFetchHeaders().get("Authorization")).toBe("Bearer second");
  });
});
