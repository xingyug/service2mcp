import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ServiceIR } from "@/types/api";

import { ReviewPanel } from "../review-panel";

const { mockSaveNotes, toastError } = vi.hoisted(() => ({
  mockSaveNotes: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/stores/workflow-store", async () => {
  const actual = await vi.importActual<typeof import("@/stores/workflow-store")>(
    "@/stores/workflow-store",
  );
  return {
    ...actual,
    useWorkflowStore: (selector: (state: Record<string, unknown>) => unknown) =>
      selector({
        saveNotes: mockSaveNotes,
      }),
  };
});

vi.mock("sonner", () => ({
  toast: {
    error: toastError,
  },
}));

const ir: ServiceIR = {
  ir_version: "1.0.0",
  compiler_version: "0.1.0",
  source_hash: "a".repeat(64),
  protocol: "openapi",
  service_name: "Billing API",
  service_description: "Billing service",
  base_url: "https://billing.example.com",
  auth: { type: "none" },
  operations: [
    {
      id: "listUsers",
      name: "List Users",
      description: "Returns users",
      method: "GET",
      path: "/users",
      params: [],
      risk: {
        risk_level: "safe",
        confidence: 1,
        source: "extractor",
        writes_state: false,
        destructive: false,
        external_side_effect: false,
        idempotent: true,
      },
      tags: [],
      source: "extractor",
      confidence: 1,
      enabled: true,
    },
    {
      id: "deleteUser",
      name: "Delete User",
      description: "Deletes a user",
      method: "DELETE",
      path: "/users/{id}",
      params: [],
      risk: {
        risk_level: "dangerous",
        confidence: 1,
        source: "extractor",
        writes_state: true,
        destructive: true,
        external_side_effect: true,
        idempotent: false,
      },
      tags: [],
      source: "extractor",
      confidence: 1,
      enabled: true,
    },
  ],
  metadata: {},
  created_at: "2026-03-29T00:00:00Z",
};

describe("ReviewPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSaveNotes.mockResolvedValue(undefined);
  });

  it("hydrates saved review notes and reviewed operations from workflow state", async () => {
    render(
      <ReviewPanel
        ir={ir}
        serviceId="svc-1"
        versionNumber={7}
        workflow={{
          serviceId: "svc-1",
          versionNumber: 7,
          state: "in_review",
          history: [],
          reviewNotes: {
            operation_notes: { listUsers: "Looks safe" },
            overall_note: "Ready to approve",
            reviewed_operations: ["listUsers"],
          },
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Mark List Users as reviewed")).toBeChecked();
    });
    expect(screen.getByLabelText("Overall Review Notes")).toHaveValue(
      "Ready to approve",
    );
    expect(screen.getByText("1 of 2 operations reviewed")).toBeInTheDocument();
  });

  it("does not complete the review when saving notes fails", async () => {
    const user = userEvent.setup();
    const onCompleteReview = vi.fn();
    mockSaveNotes.mockRejectedValue(new Error("save failed"));

    render(
      <ReviewPanel
        ir={ir}
        serviceId="svc-1"
        versionNumber={7}
        workflow={{
          serviceId: "svc-1",
          versionNumber: 7,
          state: "in_review",
          history: [],
          reviewNotes: {
            operation_notes: {},
            overall_note: "",
            reviewed_operations: ["listUsers", "deleteUser"],
          },
        }}
        onCompleteReview={onCompleteReview}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Complete Review" })).toBeEnabled();
    });

    await user.click(screen.getByRole("button", { name: "Complete Review" }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith("save failed");
    });
    expect(onCompleteReview).not.toHaveBeenCalled();
  });

  it("passes scope through when saving review notes", async () => {
    const user = userEvent.setup();

    render(
      <ReviewPanel
        ir={ir}
        serviceId="svc-1"
        versionNumber={7}
        scope={{ tenant: "team-a", environment: "prod" }}
        workflow={{
          serviceId: "svc-1",
          versionNumber: 7,
          tenant: "team-a",
          environment: "prod",
          state: "in_review",
          history: [],
          reviewNotes: {
            operation_notes: { listUsers: "Looks safe" },
            overall_note: "Ready to approve",
            reviewed_operations: ["listUsers", "deleteUser"],
          },
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Complete Review" })).toBeEnabled();
    });

    await user.click(screen.getByRole("button", { name: "Complete Review" }));

    await waitFor(() => {
      expect(mockSaveNotes).toHaveBeenCalledWith(
        "svc-1",
        7,
        { listUsers: "Looks safe" },
        "Ready to approve",
        ["listUsers", "deleteUser"],
        { tenant: "team-a", environment: "prod" },
      );
    });
  });
});
