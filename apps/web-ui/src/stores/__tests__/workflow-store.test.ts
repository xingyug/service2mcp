import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  useWorkflowStore,
  validTransitions,
  type WorkflowState,
} from "../workflow-store";

const STORAGE_KEY = "workflow-storage";

function resetStore() {
  useWorkflowStore.setState({ workflows: {} });
}

describe("workflow-store", () => {
  beforeEach(() => {
    resetStore();
  });

  // -----------------------------------------------------------------------
  // getOrCreateWorkflow
  // -----------------------------------------------------------------------

  it("getOrCreateWorkflow creates a new workflow in draft state", () => {
    const wf = useWorkflowStore.getState().getOrCreateWorkflow("svc-1", 1);

    expect(wf.serviceId).toBe("svc-1");
    expect(wf.versionNumber).toBe(1);
    expect(wf.state).toBe("draft");
    expect(wf.history).toEqual([]);
  });

  it("getOrCreateWorkflow returns existing workflow without recreating", () => {
    const store = useWorkflowStore.getState();
    store.getOrCreateWorkflow("svc-1", 1);
    // Transition to move away from draft
    useWorkflowStore.getState().transition("svc-1", 1, "submitted", "alice");

    const wf2 = useWorkflowStore.getState().getOrCreateWorkflow("svc-1", 1);
    expect(wf2.state).toBe("submitted");
  });

  // -----------------------------------------------------------------------
  // getWorkflow
  // -----------------------------------------------------------------------

  it("getWorkflow returns undefined for non-existent workflow", () => {
    const wf = useWorkflowStore.getState().getWorkflow("nope", 99);
    expect(wf).toBeUndefined();
  });

  it("getWorkflow returns the workflow when it exists", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("svc-2", 3);
    const wf = useWorkflowStore.getState().getWorkflow("svc-2", 3);
    expect(wf).toBeDefined();
    expect(wf!.serviceId).toBe("svc-2");
  });

  // -----------------------------------------------------------------------
  // Valid transitions – happy path
  // -----------------------------------------------------------------------

  it("transitions draft → submitted", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "actor");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("submitted");
  });

  it("transitions submitted → in_review", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "in_review", "b");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("in_review");
  });

  it("transitions in_review → approved", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "in_review", "a");
    useWorkflowStore.getState().transition("s", 1, "approved", "reviewer");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("approved");
  });

  it("transitions approved → published", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "in_review", "a");
    useWorkflowStore.getState().transition("s", 1, "approved", "a");
    useWorkflowStore.getState().transition("s", 1, "published", "publisher");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("published");
  });

  it("transitions published → deployed", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "in_review", "a");
    useWorkflowStore.getState().transition("s", 1, "approved", "a");
    useWorkflowStore.getState().transition("s", 1, "published", "a");
    useWorkflowStore.getState().transition("s", 1, "deployed", "deployer");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("deployed");
  });

  // -----------------------------------------------------------------------
  // Rejection path
  // -----------------------------------------------------------------------

  it("transitions in_review → rejected", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "in_review", "a");
    useWorkflowStore.getState().transition("s", 1, "rejected", "reviewer");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("rejected");
  });

  it("transitions rejected → draft", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "in_review", "a");
    useWorkflowStore.getState().transition("s", 1, "rejected", "a");
    useWorkflowStore.getState().transition("s", 1, "draft", "author");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("draft");
  });

  // -----------------------------------------------------------------------
  // Invalid transitions
  // -----------------------------------------------------------------------

  it("ignores invalid transition draft → approved", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "approved", "a");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("draft");
  });

  it("ignores invalid transition deployed → draft", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "in_review", "a");
    useWorkflowStore.getState().transition("s", 1, "approved", "a");
    useWorkflowStore.getState().transition("s", 1, "published", "a");
    useWorkflowStore.getState().transition("s", 1, "deployed", "a");
    useWorkflowStore.getState().transition("s", 1, "draft", "a");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("deployed");
  });

  it("ignores invalid transition submitted → approved (skip in_review)", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "approved", "a");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("submitted");
  });

  // -----------------------------------------------------------------------
  // History entries
  // -----------------------------------------------------------------------

  it("creates a history entry on valid transition", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-01-15T10:00:00.000Z"));

    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "alice", "ready for review");

    const wf = useWorkflowStore.getState().getWorkflow("s", 1)!;
    expect(wf.history).toHaveLength(1);
    expect(wf.history[0]).toEqual({
      from: "draft",
      to: "submitted",
      actor: "alice",
      comment: "ready for review",
      timestamp: "2024-01-15T10:00:00.000Z",
    });

    vi.useRealTimers();
  });

  it("does not create a history entry on invalid transition", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "deployed", "a");

    const wf = useWorkflowStore.getState().getWorkflow("s", 1)!;
    expect(wf.history).toHaveLength(0);
  });

  it("prepends new history entries (newest first)", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");
    useWorkflowStore.getState().transition("s", 1, "in_review", "b");

    const wf = useWorkflowStore.getState().getWorkflow("s", 1)!;
    expect(wf.history).toHaveLength(2);
    expect(wf.history[0].from).toBe("submitted");
    expect(wf.history[0].to).toBe("in_review");
    expect(wf.history[1].from).toBe("draft");
    expect(wf.history[1].to).toBe("submitted");
  });

  it("history entry comment is optional", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("s", 1);
    useWorkflowStore.getState().transition("s", 1, "submitted", "a");

    const wf = useWorkflowStore.getState().getWorkflow("s", 1)!;
    expect(wf.history[0].comment).toBeUndefined();
  });

  // -----------------------------------------------------------------------
  // Multiple workflows
  // -----------------------------------------------------------------------

  it("supports multiple independent workflows", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("svc-a", 1);
    useWorkflowStore.getState().getOrCreateWorkflow("svc-b", 1);
    useWorkflowStore.getState().transition("svc-a", 1, "submitted", "a");

    expect(useWorkflowStore.getState().getWorkflow("svc-a", 1)!.state).toBe("submitted");
    expect(useWorkflowStore.getState().getWorkflow("svc-b", 1)!.state).toBe("draft");
  });

  it("supports multiple versions of the same service", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("svc", 1);
    useWorkflowStore.getState().getOrCreateWorkflow("svc", 2);
    useWorkflowStore.getState().transition("svc", 1, "submitted", "a");

    expect(useWorkflowStore.getState().getWorkflow("svc", 1)!.state).toBe("submitted");
    expect(useWorkflowStore.getState().getWorkflow("svc", 2)!.state).toBe("draft");
  });

  // -----------------------------------------------------------------------
  // Transition on non-existent workflow (auto-creates from draft)
  // -----------------------------------------------------------------------

  it("transition auto-creates workflow from draft when key is missing", () => {
    useWorkflowStore.getState().transition("new", 1, "submitted", "a");
    const wf = useWorkflowStore.getState().getWorkflow("new", 1);
    expect(wf).toBeDefined();
    expect(wf!.state).toBe("submitted");
  });

  // -----------------------------------------------------------------------
  // Persistence
  // -----------------------------------------------------------------------

  it("persists workflows to localStorage", () => {
    useWorkflowStore.getState().getOrCreateWorkflow("p", 1);

    const raw = localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    const stored = JSON.parse(raw!);
    expect(stored.state.workflows).toBeDefined();
    expect(stored.state.workflows["p-v1"]).toBeDefined();
    expect(stored.state.workflows["p-v1"].state).toBe("draft");
  });

  it("restores workflows from localStorage on rehydration", () => {
    const payload = {
      state: {
        workflows: {
          "r-v1": {
            serviceId: "r",
            versionNumber: 1,
            state: "approved",
            history: [],
          },
        },
      },
      version: 0,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    useWorkflowStore.persist.rehydrate();

    const wf = useWorkflowStore.getState().getWorkflow("r", 1);
    expect(wf).toBeDefined();
    expect(wf!.state).toBe("approved");
  });

  // -----------------------------------------------------------------------
  // validTransitions export
  // -----------------------------------------------------------------------

  it("exported validTransitions has correct structure", () => {
    const states: WorkflowState[] = [
      "draft",
      "submitted",
      "in_review",
      "approved",
      "rejected",
      "published",
      "deployed",
    ];
    for (const s of states) {
      expect(validTransitions[s]).toBeDefined();
      expect(Array.isArray(validTransitions[s])).toBe(true);
    }
    expect(validTransitions.deployed).toEqual([]);
  });
});
