import { create } from "zustand";
import { persist } from "zustand/middleware";

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
  history: WorkflowHistoryEntry[];
}

// ---------------------------------------------------------------------------
// Valid transitions
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
// Store
// ---------------------------------------------------------------------------

function workflowKey(serviceId: string, version: number): string {
  return `${serviceId}-v${version}`;
}

interface WorkflowStore {
  workflows: Record<string, WorkflowRecord>;
  getWorkflow: (serviceId: string, version: number) => WorkflowRecord | undefined;
  getOrCreateWorkflow: (serviceId: string, version: number) => WorkflowRecord;
  transition: (
    serviceId: string,
    version: number,
    to: WorkflowState,
    actor: string,
    comment?: string,
  ) => void;
}

export const useWorkflowStore = create<WorkflowStore>()(
  persist(
    (set, get) => ({
      workflows: {},

      getWorkflow: (serviceId, version) => {
        const key = workflowKey(serviceId, version);
        return get().workflows[key];
      },

      getOrCreateWorkflow: (serviceId, version) => {
        const key = workflowKey(serviceId, version);
        const existing = get().workflows[key];
        if (existing) return existing;

        const record: WorkflowRecord = {
          serviceId,
          versionNumber: version,
          state: "draft",
          history: [],
        };
        set((s) => ({
          workflows: { ...s.workflows, [key]: record },
        }));
        return record;
      },

      transition: (serviceId, version, to, actor, comment) => {
        const key = workflowKey(serviceId, version);
        set((s) => {
          const current = s.workflows[key] ?? {
            serviceId,
            versionNumber: version,
            state: "draft" as WorkflowState,
            history: [] as WorkflowHistoryEntry[],
          };

          const allowed = validTransitions[current.state];
          if (!allowed.includes(to)) return s;

          const entry: WorkflowHistoryEntry = {
            from: current.state,
            to,
            actor,
            comment,
            timestamp: new Date().toISOString(),
          };

          return {
            workflows: {
              ...s.workflows,
              [key]: {
                ...current,
                state: to,
                history: [entry, ...current.history],
              },
            },
          };
        });
      },
    }),
    { name: "workflow-storage" },
  ),
);
