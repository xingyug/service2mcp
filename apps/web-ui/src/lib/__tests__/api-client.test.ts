import { describe, it, expect, beforeEach, vi, type Mock } from "vitest";
import {
  ApiError,
  compilationApi,
  serviceApi,
  policyApi,
  auditApi,
  gatewayApi,
} from "../api-client";

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
    } catch (e) {
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
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      const err = e as ApiError;
      expect(err.status).toBe(422);
      expect(err.detail).toEqual({ msg: "bad" });
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

  // -----------------------------------------------------------------------
  // policyApi
  // -----------------------------------------------------------------------

  it("policyApi.create sends POST", async () => {
    const body = { name: "policy-1" };
    await policyApi.create(body as never);

    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/policies`);
    expect(lastFetchOptions().method).toBe("POST");
    expect(lastFetchOptions().body).toBe(JSON.stringify(body));
  });

  it("policyApi.update sends PATCH", async () => {
    const body = { name: "updated" };
    await policyApi.update("pol-1", body as never);

    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/policies/pol-1`);
    expect(lastFetchOptions().method).toBe("PATCH");
  });

  it("policyApi.delete sends DELETE", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(undefined, { status: 204, statusText: "No Content" }),
    );
    await policyApi.delete("pol-1");

    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/policies/pol-1`);
    expect(lastFetchOptions().method).toBe("DELETE");
  });

  // -----------------------------------------------------------------------
  // auditApi
  // -----------------------------------------------------------------------

  it("auditApi.list sends GET with no query params when no filters", async () => {
    await auditApi.list();
    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/audit`);
  });

  it("auditApi.list sends GET with correct query params", async () => {
    await auditApi.list({ actor: "alice", action: "create", since: "2024-01-01" });
    const url = lastFetchUrl();
    expect(url).toContain("actor=alice");
    expect(url).toContain("action=create");
    expect(url).toContain("since=2024-01-01");
  });

  it("auditApi.get sends GET with entryId in URL", async () => {
    await auditApi.get("entry-7");
    expect(lastFetchUrl()).toBe(`${ACCESS_CONTROL_API}/api/v1/audit/entry-7`);
  });

  // -----------------------------------------------------------------------
  // gatewayApi
  // -----------------------------------------------------------------------

  it("gatewayApi.reconcile sends POST", async () => {
    await gatewayApi.reconcile();

    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/gateway/reconcile`);
    expect(lastFetchOptions().method).toBe("POST");
  });

  it("gatewayApi.setRoute sends POST with body", async () => {
    const body = { service_id: "svc-1", path: "/api" };
    await gatewayApi.setRoute(body as never);

    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/gateway/routes`);
    expect(lastFetchOptions().method).toBe("POST");
    expect(lastFetchOptions().body).toBe(JSON.stringify(body));
  });

  it("gatewayApi.deleteRoute sends DELETE with serviceId", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(undefined, { status: 204, statusText: "No Content" }),
    );
    await gatewayApi.deleteRoute("svc-1");

    expect(lastFetchUrl()).toBe(`${COMPILER_API}/api/v1/gateway/routes/svc-1`);
    expect(lastFetchOptions().method).toBe("DELETE");
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
