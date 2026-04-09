"use client";

import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import {
  compilationApi,
  serviceApi,
  artifactApi,
  policyApi,
  auditApi,
} from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

import type {
  CompilationCreateRequest,
  CompilationJobResponse,
  ServiceScope,
  ServiceListResponse,
  ServiceSummary,
  ArtifactVersionListResponse,
  ArtifactDiffResponse,
  PolicyListResponse,
  AuditLogListResponse,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Compilations
// ---------------------------------------------------------------------------

export function useCompilations(
  options?: Omit<
    UseQueryOptions<CompilationJobResponse[]>,
    "queryKey" | "queryFn"
  >,
) {
  return useQuery({
    queryKey: queryKeys.compilations.all,
    queryFn: () => compilationApi.list(),
    ...options,
  });
}

export function useCompilation(
  jobId: string,
  options?: Omit<
    UseQueryOptions<CompilationJobResponse>,
    "queryKey" | "queryFn"
  >,
) {
  return useQuery({
    queryKey: queryKeys.compilations.detail(jobId),
    queryFn: () => compilationApi.get(jobId),
    enabled: !!jobId,
    ...options,
  });
}

export function useCreateCompilation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (req: CompilationCreateRequest) => compilationApi.create(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.compilations.all });
    },
  });
}

export function useRetryCompilation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ jobId, fromStage }: { jobId: string; fromStage?: string }) =>
      compilationApi.retry(jobId, fromStage),
    onSuccess: async (newJob, { jobId }) => {
      queryClient.setQueryData(
        queryKeys.compilations.detail(newJob.job_id),
        newJob,
      );
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.compilations.all,
          exact: true,
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.compilations.detail(jobId),
        }),
      ]);
    },
  });
}

export function useRollbackCompilation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (jobId: string) => compilationApi.rollback(jobId),
    onSuccess: async (newJob, jobId) => {
      queryClient.setQueryData(
        queryKeys.compilations.detail(newJob.job_id),
        newJob,
      );
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.compilations.all,
          exact: true,
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.compilations.detail(jobId),
        }),
      ]);
    },
  });
}

// ---------------------------------------------------------------------------
// Services
// ---------------------------------------------------------------------------

export function useServices(
  filters?: { tenant?: string; environment?: string },
  options?: Omit<UseQueryOptions<ServiceListResponse>, "queryKey" | "queryFn">,
) {
  const filterKey: Record<string, string> = {};
  if (filters?.tenant) filterKey.tenant = filters.tenant;
  if (filters?.environment) filterKey.environment = filters.environment;

  return useQuery({
    queryKey:
      Object.keys(filterKey).length > 0
        ? queryKeys.services.filtered(filterKey)
        : queryKeys.services.all,
    queryFn: () => serviceApi.list(filters),
    ...options,
  });
}

export function useService(
  serviceId: string,
  scope?: ServiceScope,
  options?: Omit<UseQueryOptions<ServiceSummary>, "queryKey" | "queryFn">,
) {
  return useQuery({
    queryKey: queryKeys.services.detail(serviceId, scope),
    queryFn: () => serviceApi.get(serviceId, scope),
    enabled: !!serviceId,
    ...options,
  });
}

export function useDeleteService() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ serviceId, scope }: { serviceId: string; scope?: ServiceScope }) =>
      serviceApi.delete(serviceId, scope),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.services.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

export function useArtifactVersions(
  serviceId: string,
  scope?: ServiceScope,
  options?: Omit<
    UseQueryOptions<ArtifactVersionListResponse>,
    "queryKey" | "queryFn"
  >,
) {
  return useQuery({
    queryKey: queryKeys.artifacts.versions(serviceId, scope),
    queryFn: () => artifactApi.listVersions(serviceId, scope),
    enabled: !!serviceId,
    ...options,
  });
}

export function useArtifactDiff(
  serviceId: string,
  from: number,
  to: number,
  scope?: ServiceScope,
  options?: Omit<UseQueryOptions<ArtifactDiffResponse>, "queryKey" | "queryFn">,
) {
  return useQuery({
    queryKey: queryKeys.artifacts.diff(serviceId, from, to, scope),
    queryFn: () => artifactApi.diff(serviceId, from, to, scope),
    enabled: !!serviceId && from > 0 && to > 0 && from !== to,
    ...options,
  });
}

// ---------------------------------------------------------------------------
// Policies
// ---------------------------------------------------------------------------

export function usePolicies(
  filters?: { subject_type?: string; subject_id?: string; resource_id?: string },
  options?: Omit<UseQueryOptions<PolicyListResponse>, "queryKey" | "queryFn">,
) {
  const filterKey: Record<string, string> = {};
  if (filters?.subject_type) filterKey.subject_type = filters.subject_type;
  if (filters?.subject_id) filterKey.subject_id = filters.subject_id;
  if (filters?.resource_id) filterKey.resource_id = filters.resource_id;

  return useQuery({
    queryKey:
      Object.keys(filterKey).length > 0
        ? queryKeys.policies.filtered(filterKey)
        : queryKeys.policies.all,
    queryFn: () => policyApi.list(filters),
    ...options,
  });
}

// ---------------------------------------------------------------------------
// Audit Logs
// ---------------------------------------------------------------------------

export function useAuditLogs(
  filters?: {
    actor?: string;
    action?: string;
    resource?: string;
    since?: string;
    until?: string;
  },
  options?: Omit<
    UseQueryOptions<AuditLogListResponse>,
    "queryKey" | "queryFn"
  >,
) {
  const filterKey: Record<string, string> = {};
  if (filters?.actor) filterKey.actor = filters.actor;
  if (filters?.action) filterKey.action = filters.action;
  if (filters?.resource) filterKey.resource = filters.resource;
  if (filters?.since) filterKey.since = filters.since;
  if (filters?.until) filterKey.until = filters.until;

  return useQuery({
    queryKey: queryKeys.audit.logs(filterKey),
    queryFn: () => auditApi.list(filters),
    ...options,
  });
}
