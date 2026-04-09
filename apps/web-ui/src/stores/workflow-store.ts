import { create } from "zustand";

import { workflowApi, type WorkflowResponse } from "@/lib/api-client";
import { normalizeServiceScope } from "@/lib/service-scope";
import type { ServiceScope } from "@/types/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type WorkflowState =
  | "draft"
  | "submitted"
  | "in_review"
  | "approved"
  | "rejected"
  | "published"
  | "deployed";

export interface WorkflowHistoryEntry {
  from: WorkflowState;
  to: WorkflowState;
  actor: string;
  comment?: string;
  timestamp: string;
}

export interface WorkflowRecord {
  serviceId: string;
  versionNumber: number;
  tenant?: string;
  environment?: string;
  state: WorkflowState;
  reviewNotes:
    | {
        operation_notes?: Record<string, string>;
        overall_note?: string;
        reviewed_operations?: string[];
      }
    | null;
  history: WorkflowHistoryEntry[];
}

// ---------------------------------------------------------------------------
// Valid transitions (kept for UI-side gating)
// ---------------------------------------------------------------------------

export const validTransitions: Record<WorkflowState, WorkflowState[]> = {
  draft: ["submitted"],
  submitted: ["in_review"],
  in_review: ["approved", "rejected"],
  approved: ["published"],
  rejected: ["draft"],
  published: ["deployed"],
  deployed: [],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function workflowKey(serviceId: string, version: number, scope?: ServiceScope): string {
  const normalized = normalizeServiceScope(scope);
  return [
    serviceId,
    version,
    normalized?.tenant ?? "",
    normalized?.environment ?? "",
  ].join("::");
}

function toRecord(resp: WorkflowResponse): WorkflowRecord {
  const scope = normalizeServiceScope({
    tenant: resp.tenant ?? undefined,
    environment: resp.environment ?? undefined,
  });
  return {
    serviceId: resp.service_id,
    versionNumber: resp.version_number,
    tenant: scope?.tenant,
    environment: scope?.environment,
    state: resp.state as WorkflowState,
    reviewNotes: resp.review_notes,
    history: (resp.history ?? []).map((h) => ({
      from: h.from as WorkflowState,
      to: h.to as WorkflowState,
      actor: h.actor,
      comment: h.comment,
      timestamp: h.timestamp,
    })),
  };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface WorkflowStore {
  workflows: Record<string, WorkflowRecord>;
  loading: Record<string, boolean>;

  getWorkflow: (
    serviceId: string,
    version: number,
    scope?: ServiceScope,
  ) => WorkflowRecord | undefined;

  /** Fetch (or create) the workflow from the backend and cache it. */
  loadWorkflow: (
    serviceId: string,
    version: number,
    scope?: ServiceScope,
  ) => Promise<WorkflowRecord>;

  /** Persist a state transition via the backend. */
  transition: (
    serviceId: string,
    version: number,
    to: WorkflowState,
    actor: string,
    comment?: string,
    scope?: ServiceScope,
  ) => Promise<WorkflowRecord>;

  /** Persist review notes via the backend. */
  saveNotes: (
    serviceId: string,
    version: number,
    notes: Record<string, string>,
    overallNote?: string,
    reviewedOperations?: string[],
    scope?: ServiceScope,
  ) => Promise<WorkflowRecord>;
}

export const useWorkflowStore = create<WorkflowStore>()((set, get) => ({
  workflows: {},
  loading: {},

  getWorkflow: (serviceId, version, scope) => {
    const key = workflowKey(serviceId, version, scope);
    return get().workflows[key];
  },

  loadWorkflow: async (serviceId, version, scope) => {
    const normalizedScope = normalizeServiceScope(scope);
    const key = workflowKey(serviceId, version, normalizedScope);
    const existing = get().workflows[key];
    if (existing) return existing;

    set((s) => ({ loading: { ...s.loading, [key]: true } }));
    try {
      const resp = await workflowApi.get(serviceId, version, normalizedScope);
      const record = toRecord(resp);
      set((s) => ({
        workflows: { ...s.workflows, [key]: record },
        loading: { ...s.loading, [key]: false },
      }));
      return record;
    } catch {
      set((s) => ({ loading: { ...s.loading, [key]: false } }));
      // Return a default draft record on failure so the UI still works.
      // Do NOT cache — next call should retry the API.
      return {
        serviceId,
        versionNumber: version,
        tenant: normalizedScope?.tenant,
        environment: normalizedScope?.environment,
        state: "draft",
        reviewNotes: null,
        history: [],
      } satisfies WorkflowRecord;
    }
  },

  transition: async (serviceId, version, to, actor, comment, scope) => {
    const normalizedScope = normalizeServiceScope(scope);
    const key = workflowKey(serviceId, version, normalizedScope);
    const resp = await workflowApi.transition(
      serviceId,
      version,
      to,
      actor,
      comment,
      normalizedScope,
    );
    const record = toRecord(resp);
    set((s) => ({ workflows: { ...s.workflows, [key]: record } }));
    return record;
  },

  saveNotes: async (serviceId, version, notes, overallNote, reviewedOperations, scope) => {
    const normalizedScope = normalizeServiceScope(scope);
    const key = workflowKey(serviceId, version, normalizedScope);
    const resp = await workflowApi.saveNotes(
      serviceId,
      version,
      notes,
      overallNote,
      reviewedOperations,
      normalizedScope,
    );
    const record = toRecord(resp);
    set((s) => ({ workflows: { ...s.workflows, [key]: record } }));
    return record;
  },
}));
