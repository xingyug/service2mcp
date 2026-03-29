import { create } from "zustand";

import { workflowApi, type WorkflowResponse } from "@/lib/api-client";

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
  state: WorkflowState;
  reviewNotes: { operation_notes?: Record<string, string>; overall_note?: string } | null;
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

function workflowKey(serviceId: string, version: number): string {
  return `${serviceId}-v${version}`;
}

function toRecord(resp: WorkflowResponse): WorkflowRecord {
  return {
    serviceId: resp.service_id,
    versionNumber: resp.version_number,
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

  getWorkflow: (serviceId: string, version: number) => WorkflowRecord | undefined;

  /** Fetch (or create) the workflow from the backend and cache it. */
  loadWorkflow: (serviceId: string, version: number) => Promise<WorkflowRecord>;

  /** Persist a state transition via the backend. */
  transition: (
    serviceId: string,
    version: number,
    to: WorkflowState,
    actor: string,
    comment?: string,
  ) => Promise<WorkflowRecord>;

  /** Persist review notes via the backend. */
  saveNotes: (
    serviceId: string,
    version: number,
    notes: Record<string, string>,
    overallNote?: string,
  ) => Promise<WorkflowRecord>;
}

export const useWorkflowStore = create<WorkflowStore>()((set, get) => ({
  workflows: {},
  loading: {},

  getWorkflow: (serviceId, version) => {
    const key = workflowKey(serviceId, version);
    return get().workflows[key];
  },

  loadWorkflow: async (serviceId, version) => {
    const key = workflowKey(serviceId, version);
    const existing = get().workflows[key];
    if (existing) return existing;

    set((s) => ({ loading: { ...s.loading, [key]: true } }));
    try {
      const resp = await workflowApi.get(serviceId, version);
      const record = toRecord(resp);
      set((s) => ({
        workflows: { ...s.workflows, [key]: record },
        loading: { ...s.loading, [key]: false },
      }));
      return record;
    } catch {
      set((s) => ({ loading: { ...s.loading, [key]: false } }));
      // Return a default draft record on failure so the UI still works
      const fallback: WorkflowRecord = {
        serviceId,
        versionNumber: version,
        state: "draft",
        reviewNotes: null,
        history: [],
      };
      set((s) => ({ workflows: { ...s.workflows, [key]: fallback } }));
      return fallback;
    }
  },

  transition: async (serviceId, version, to, actor, comment) => {
    const key = workflowKey(serviceId, version);
    const resp = await workflowApi.transition(serviceId, version, to, actor, comment);
    const record = toRecord(resp);
    set((s) => ({ workflows: { ...s.workflows, [key]: record } }));
    return record;
  },

  saveNotes: async (serviceId, version, notes, overallNote) => {
    const key = workflowKey(serviceId, version);
    const resp = await workflowApi.saveNotes(serviceId, version, notes, overallNote);
    const record = toRecord(resp);
    set((s) => ({ workflows: { ...s.workflows, [key]: record } }));
    return record;
  },
}));
