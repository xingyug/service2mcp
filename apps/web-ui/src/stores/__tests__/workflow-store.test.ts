import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  useWorkflowStore,
  validTransitions,
  type WorkflowState,
} from "../workflow-store";

// ---------------------------------------------------------------------------
// Mock the API client
// ---------------------------------------------------------------------------

const mockGet = vi.fn();
const mockTransition = vi.fn();
const mockSaveNotes = vi.fn();
const mockHistory = vi.fn();

vi.mock("@/lib/api-client", () => ({
  workflowApi: {
    get: (...args: unknown[]) => mockGet(...args),
    transition: (...args: unknown[]) => mockTransition(...args),
    saveNotes: (...args: unknown[]) => mockSaveNotes(...args),
    history: (...args: unknown[]) => mockHistory(...args),
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function resetStore() {
  useWorkflowStore.setState({ workflows: {}, loading: {} });
}

function apiResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    service_id: "s",
    version_number: 1,
    state: "draft",
    review_notes: null,
    history: [],
    created_at: "2025-01-01T00:00:00+00:00",
    updated_at: "2025-01-01T00:00:00+00:00",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("workflow-store", () => {
  beforeEach(() => {
    resetStore();
    vi.clearAllMocks();
  });

  // -----------------------------------------------------------------------
  // loadWorkflow
  // -----------------------------------------------------------------------

  it("loadWorkflow fetches from backend and caches", async () => {
    mockGet.mockResolvedValue(apiResponse({ state: "in_review" }));

    const wf = await useWorkflowStore.getState().loadWorkflow("s", 1);

    expect(wf.serviceId).toBe("s");
    expect(wf.versionNumber).toBe(1);
    expect(wf.state).toBe("in_review");
    expect(mockGet).toHaveBeenCalledWith("s", 1);
  });

  it("loadWorkflow returns cached workflow without re-fetching", async () => {
    mockGet.mockResolvedValue(apiResponse());

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    const wf2 = await useWorkflowStore.getState().loadWorkflow("s", 1);

    expect(wf2.state).toBe("draft");
    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it("loadWorkflow falls back to draft on API error", async () => {
    mockGet.mockRejectedValue(new Error("network"));

    const wf = await useWorkflowStore.getState().loadWorkflow("s", 1);

    expect(wf.state).toBe("draft");
    expect(wf.history).toEqual([]);
  });

  // -----------------------------------------------------------------------
  // getWorkflow
  // -----------------------------------------------------------------------

  it("getWorkflow returns undefined for non-existent workflow", () => {
    const wf = useWorkflowStore.getState().getWorkflow("nope", 99);
    expect(wf).toBeUndefined();
  });

  it("getWorkflow returns the workflow when it exists", async () => {
    mockGet.mockResolvedValue(apiResponse({ service_id: "svc-2", version_number: 3 }));

    await useWorkflowStore.getState().loadWorkflow("svc-2", 3);
    const wf = useWorkflowStore.getState().getWorkflow("svc-2", 3);
    expect(wf).toBeDefined();
    expect(wf!.serviceId).toBe("svc-2");
  });

  // -----------------------------------------------------------------------
  // Valid transitions – happy path
  // -----------------------------------------------------------------------

  it("transitions draft → submitted via backend", async () => {
    mockGet.mockResolvedValue(apiResponse());
    mockTransition.mockResolvedValue(apiResponse({ state: "submitted", history: [{ from: "draft", to: "submitted", actor: "a", comment: null, timestamp: "2025-01-01T00:00:00Z" }] }));

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    await useWorkflowStore.getState().transition("s", 1, "submitted", "a");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("submitted");
    expect(mockTransition).toHaveBeenCalledWith("s", 1, "submitted", "a", undefined);
  });

  it("transitions submitted → in_review", async () => {
    mockGet.mockResolvedValue(apiResponse({ state: "submitted" }));
    mockTransition.mockResolvedValue(apiResponse({ state: "in_review" }));

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    await useWorkflowStore.getState().transition("s", 1, "in_review", "b");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("in_review");
  });

  it("transitions in_review → approved", async () => {
    mockGet.mockResolvedValue(apiResponse({ state: "in_review" }));
    mockTransition.mockResolvedValue(apiResponse({ state: "approved" }));

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    await useWorkflowStore.getState().transition("s", 1, "approved", "reviewer");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("approved");
  });

  it("transitions approved → published", async () => {
    mockGet.mockResolvedValue(apiResponse({ state: "approved" }));
    mockTransition.mockResolvedValue(apiResponse({ state: "published" }));

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    await useWorkflowStore.getState().transition("s", 1, "published", "publisher");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("published");
  });

  it("transitions published → deployed", async () => {
    mockGet.mockResolvedValue(apiResponse({ state: "published" }));
    mockTransition.mockResolvedValue(apiResponse({ state: "deployed" }));

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    await useWorkflowStore.getState().transition("s", 1, "deployed", "deployer");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("deployed");
  });

  // -----------------------------------------------------------------------
  // Rejection path
  // -----------------------------------------------------------------------

  it("transitions in_review → rejected", async () => {
    mockGet.mockResolvedValue(apiResponse({ state: "in_review" }));
    mockTransition.mockResolvedValue(apiResponse({ state: "rejected" }));

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    await useWorkflowStore.getState().transition("s", 1, "rejected", "reviewer");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("rejected");
  });

  it("transitions rejected → draft", async () => {
    mockGet.mockResolvedValue(apiResponse({ state: "rejected" }));
    mockTransition.mockResolvedValue(apiResponse({ state: "draft" }));

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    await useWorkflowStore.getState().transition("s", 1, "draft", "author");

    expect(useWorkflowStore.getState().getWorkflow("s", 1)!.state).toBe("draft");
  });

  // -----------------------------------------------------------------------
  // History entries
  // -----------------------------------------------------------------------

  it("stores history from backend response", async () => {
    const historyEntries = [
      { from: "submitted", to: "in_review", actor: "b", comment: null, timestamp: "2025-01-02T00:00:00Z" },
      { from: "draft", to: "submitted", actor: "a", comment: "ready", timestamp: "2025-01-01T00:00:00Z" },
    ];
    mockGet.mockResolvedValue(apiResponse({ state: "in_review", history: historyEntries }));

    const wf = await useWorkflowStore.getState().loadWorkflow("s", 1);

    expect(wf.history).toHaveLength(2);
    expect(wf.history[0].from).toBe("submitted");
    expect(wf.history[0].to).toBe("in_review");
    expect(wf.history[1].comment).toBe("ready");
  });

  // -----------------------------------------------------------------------
  // saveNotes
  // -----------------------------------------------------------------------

  it("saveNotes persists notes via backend", async () => {
    const notesPayload = { "op-1": "looks good", "op-2": "needs fix" };
    mockGet.mockResolvedValue(apiResponse());
    mockSaveNotes.mockResolvedValue(
      apiResponse({ review_notes: { operation_notes: notesPayload, overall_note: "Ship it" } }),
    );

    await useWorkflowStore.getState().loadWorkflow("s", 1);
    const wf = await useWorkflowStore.getState().saveNotes("s", 1, notesPayload, "Ship it");

    expect(wf.reviewNotes).toEqual({ operation_notes: notesPayload, overall_note: "Ship it" });
    expect(mockSaveNotes).toHaveBeenCalledWith("s", 1, notesPayload, "Ship it");
  });

  // -----------------------------------------------------------------------
  // Multiple workflows
  // -----------------------------------------------------------------------

  it("supports multiple independent workflows", async () => {
    mockGet
      .mockResolvedValueOnce(apiResponse({ service_id: "svc-a" }))
      .mockResolvedValueOnce(apiResponse({ service_id: "svc-b" }));
    mockTransition.mockResolvedValue(apiResponse({ service_id: "svc-a", state: "submitted" }));

    await useWorkflowStore.getState().loadWorkflow("svc-a", 1);
    await useWorkflowStore.getState().loadWorkflow("svc-b", 1);
    await useWorkflowStore.getState().transition("svc-a", 1, "submitted", "a");

    expect(useWorkflowStore.getState().getWorkflow("svc-a", 1)!.state).toBe("submitted");
    expect(useWorkflowStore.getState().getWorkflow("svc-b", 1)!.state).toBe("draft");
  });

  it("supports multiple versions of the same service", async () => {
    mockGet
      .mockResolvedValueOnce(apiResponse({ version_number: 1 }))
      .mockResolvedValueOnce(apiResponse({ version_number: 2 }));
    mockTransition.mockResolvedValue(apiResponse({ version_number: 1, state: "submitted" }));

    await useWorkflowStore.getState().loadWorkflow("svc", 1);
    await useWorkflowStore.getState().loadWorkflow("svc", 2);
    await useWorkflowStore.getState().transition("svc", 1, "submitted", "a");

    expect(useWorkflowStore.getState().getWorkflow("svc", 1)!.state).toBe("submitted");
    expect(useWorkflowStore.getState().getWorkflow("svc", 2)!.state).toBe("draft");
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
