import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock the api-client module
// ---------------------------------------------------------------------------

vi.mock("@/lib/api-client", () => ({
  compilationApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    retry: vi.fn(),
    rollback: vi.fn(),
  },
  serviceApi: {
    list: vi.fn(),
    get: vi.fn(),
  },
  artifactApi: {
    listVersions: vi.fn(),
    diff: vi.fn(),
  },
  policyApi: {
    list: vi.fn(),
  },
  auditApi: {
    list: vi.fn(),
  },
}));

import {
  compilationApi,
  serviceApi,
  artifactApi,
  policyApi,
  auditApi,
} from "@/lib/api-client";
import {
  useCompilations,
  useCompilation,
  useCreateCompilation,
  useRetryCompilation,
  useRollbackCompilation,
  useServices,
  useService,
  useArtifactVersions,
  useArtifactDiff,
  usePolicies,
  useAuditLogs,
} from "../use-api";

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

// ---------------------------------------------------------------------------

describe("use-api hooks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -----------------------------------------------------------------------
  // useCompilations
  // -----------------------------------------------------------------------

  it("useCompilations calls compilationApi.list and returns data", async () => {
    const data = [{ id: "job-1" }, { id: "job-2" }];
    (compilationApi.list as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => useCompilations(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(compilationApi.list).toHaveBeenCalledOnce();
    expect(result.current.data).toEqual(data);
  });

  // -----------------------------------------------------------------------
  // useCompilation
  // -----------------------------------------------------------------------

  it("useCompilation calls compilationApi.get with jobId", async () => {
    const data = { id: "job-1", status: "completed" };
    (compilationApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => useCompilation("job-1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(compilationApi.get).toHaveBeenCalledWith("job-1");
    expect(result.current.data).toEqual(data);
  });

  it("useCompilation is disabled when jobId is empty string", async () => {
    const { result } = renderHook(() => useCompilation(""), {
      wrapper: createWrapper(),
    });

    // Should remain in idle/pending state since enabled = !!jobId = false
    expect(result.current.fetchStatus).toBe("idle");
    expect(compilationApi.get).not.toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // useCreateCompilation
  // -----------------------------------------------------------------------

  it("useCreateCompilation calls compilationApi.create with request body", async () => {
    const response = { id: "new-job" };
    (compilationApi.create as ReturnType<typeof vi.fn>).mockResolvedValue(response);

    const { result } = renderHook(() => useCreateCompilation(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ service_id: "svc" } as never);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(compilationApi.create).toHaveBeenCalledWith({ service_id: "svc" });
    expect(result.current.data).toEqual(response);
  });

  it("useCreateCompilation invalidates compilations queries on success", async () => {
    (compilationApi.create as ReturnType<typeof vi.fn>).mockResolvedValue({ id: "x" });
    (compilationApi.list as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    function Wrapper({ children }: { children: React.ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      );
    }

    const { result } = renderHook(() => useCreateCompilation(), {
      wrapper: Wrapper,
    });

    result.current.mutate({ service_id: "svc" } as never);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["compilations"] }),
    );
  });

  // -----------------------------------------------------------------------
  // useRetryCompilation
  // -----------------------------------------------------------------------

  it("useRetryCompilation calls compilationApi.retry", async () => {
    (compilationApi.retry as ReturnType<typeof vi.fn>).mockResolvedValue({ id: "job-1" });

    const { result } = renderHook(() => useRetryCompilation(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ jobId: "job-1", fromStage: "validate" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(compilationApi.retry).toHaveBeenCalledWith("job-1", "validate");
  });

  it("useRetryCompilation invalidates the list and original detail query", async () => {
    const newJob = { job_id: "job-2", status: "pending" };
    (compilationApi.retry as ReturnType<typeof vi.fn>).mockResolvedValue(newJob);

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const setQueryDataSpy = vi.spyOn(queryClient, "setQueryData");

    function Wrapper({ children }: { children: React.ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      );
    }

    const { result } = renderHook(() => useRetryCompilation(), {
      wrapper: Wrapper,
    });

    result.current.mutate({ jobId: "job-1", fromStage: "validate" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(setQueryDataSpy).toHaveBeenCalledWith(
      ["compilations", "job-2"],
      newJob,
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["compilations"], exact: true }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["compilations", "job-1"] }),
    );
  });

  it("useRollbackCompilation invalidates the list and original detail query", async () => {
    const newJob = { job_id: "job-3", status: "pending" };
    (compilationApi.rollback as ReturnType<typeof vi.fn>).mockResolvedValue(newJob);

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const setQueryDataSpy = vi.spyOn(queryClient, "setQueryData");

    function Wrapper({ children }: { children: React.ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      );
    }

    const { result } = renderHook(() => useRollbackCompilation(), {
      wrapper: Wrapper,
    });

    result.current.mutate("job-1");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(setQueryDataSpy).toHaveBeenCalledWith(
      ["compilations", "job-3"],
      newJob,
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["compilations"], exact: true }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["compilations", "job-1"] }),
    );
  });

  // -----------------------------------------------------------------------
  // useServices
  // -----------------------------------------------------------------------

  it("useServices calls serviceApi.list and returns data", async () => {
    const data = { services: [{ id: "s1" }] };
    (serviceApi.list as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => useServices(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(serviceApi.list).toHaveBeenCalledWith(undefined);
    expect(result.current.data).toEqual(data);
  });

  it("useServices passes filters to serviceApi.list", async () => {
    (serviceApi.list as ReturnType<typeof vi.fn>).mockResolvedValue({ services: [] });
    const filters = { tenant: "acme", environment: "prod" };

    const { result } = renderHook(() => useServices(filters), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(serviceApi.list).toHaveBeenCalledWith(filters);
  });

  // -----------------------------------------------------------------------
  // useService
  // -----------------------------------------------------------------------

  it("useService calls serviceApi.get with serviceId", async () => {
    const data = { id: "svc-1", name: "My Service" };
    (serviceApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => useService("svc-1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(serviceApi.get).toHaveBeenCalledWith("svc-1", undefined);
    expect(result.current.data).toEqual(data);
  });

  it("useService passes scope to serviceApi.get", async () => {
    const data = { id: "svc-1", name: "Scoped Service" };
    const scope = { tenant: "acme", environment: "prod" };
    (serviceApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => useService("svc-1", scope), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(serviceApi.get).toHaveBeenCalledWith("svc-1", scope);
  });

  it("useService is disabled when serviceId is empty", async () => {
    const { result } = renderHook(() => useService(""), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(serviceApi.get).not.toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // useArtifactVersions
  // -----------------------------------------------------------------------

  it("useArtifactVersions calls artifactApi.listVersions", async () => {
    const data = { versions: [{ version: 1 }] };
    (artifactApi.listVersions as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => useArtifactVersions("svc-1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(artifactApi.listVersions).toHaveBeenCalledWith("svc-1", undefined);
  });

  it("useArtifactVersions passes scope to artifactApi.listVersions", async () => {
    const data = { versions: [{ version: 1 }] };
    const scope = { tenant: "acme", environment: "prod" };
    (artifactApi.listVersions as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => useArtifactVersions("svc-1", scope), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(artifactApi.listVersions).toHaveBeenCalledWith("svc-1", scope);
  });

  it("useArtifactVersions is disabled when serviceId is empty", async () => {
    const { result } = renderHook(() => useArtifactVersions(""), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(artifactApi.listVersions).not.toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // useArtifactDiff
  // -----------------------------------------------------------------------

  it("useArtifactDiff is disabled when params invalid", async () => {
    const { result } = renderHook(() => useArtifactDiff("svc-1", 0, 0), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(artifactApi.diff).not.toHaveBeenCalled();
  });

  it("useArtifactDiff passes scope to artifactApi.diff", async () => {
    const data = { changed_operations: [] };
    const scope = { tenant: "acme", environment: "prod" };
    (artifactApi.diff as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(
      () => useArtifactDiff("svc-1", 1, 2, scope),
      {
        wrapper: createWrapper(),
      },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(artifactApi.diff).toHaveBeenCalledWith("svc-1", 1, 2, scope);
  });

  // -----------------------------------------------------------------------
  // usePolicies
  // -----------------------------------------------------------------------

  it("usePolicies calls policyApi.list", async () => {
    const data = { policies: [] };
    (policyApi.list as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => usePolicies(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(policyApi.list).toHaveBeenCalledWith(undefined);
  });

  it("usePolicies includes subject_type in filtered queries", async () => {
    const data = { policies: [] };
    const filters = { subject_type: "role", subject_id: "editor" };
    (policyApi.list as ReturnType<typeof vi.fn>).mockResolvedValue(data);

    const { result } = renderHook(() => usePolicies(filters), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(policyApi.list).toHaveBeenCalledWith(filters);
  });

  // -----------------------------------------------------------------------
  // useAuditLogs
  // -----------------------------------------------------------------------

  it("useAuditLogs calls auditApi.list with filters", async () => {
    const data = { entries: [] };
    (auditApi.list as ReturnType<typeof vi.fn>).mockResolvedValue(data);
    const filters = { actor: "bob" };

    const { result } = renderHook(() => useAuditLogs(filters), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(auditApi.list).toHaveBeenCalledWith(filters);
  });
});
