import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PoliciesPage from "../page";

const {
  mockUsePolicies,
  mockCreatePolicy,
  mockUpdatePolicy,
  mockDeletePolicy,
  mockEvaluatePolicy,
  mockToastSuccess,
  mockToastError,
} = vi.hoisted(() => ({
  mockUsePolicies: vi.fn(),
  mockCreatePolicy: vi.fn(),
  mockUpdatePolicy: vi.fn(),
  mockDeletePolicy: vi.fn(),
  mockEvaluatePolicy: vi.fn(),
  mockToastSuccess: vi.fn(),
  mockToastError: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/hooks/use-api", () => ({
  usePolicies: mockUsePolicies,
}));

vi.mock("@/lib/api-client", () => ({
  policyApi: {
    create: mockCreatePolicy,
    update: mockUpdatePolicy,
    delete: mockDeletePolicy,
    evaluate: mockEvaluatePolicy,
  },
}));

vi.mock("sonner", () => ({
  toast: {
    success: mockToastSuccess,
    error: mockToastError,
  },
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <PoliciesPage />
    </QueryClientProvider>,
  );
}

describe("PoliciesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUsePolicies.mockReturnValue({
      data: { policies: [] },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockCreatePolicy.mockResolvedValue({
      policy_id: "pol-1",
      subject_type: "role",
      subject_id: "editor",
      resource_id: "svc-1",
      action_pattern: "read",
      risk_threshold: "safe",
      decision: "allow",
      created_at: "2026-03-30T00:00:00Z",
    });
    mockUpdatePolicy.mockResolvedValue(undefined);
    mockDeletePolicy.mockResolvedValue(undefined);
    mockEvaluatePolicy.mockResolvedValue({
      decision: "allow",
      matched_policy_id: "pol-1",
      reason: "Matched",
    });
  });

  it("forwards subject_type filters to the backend query", async () => {
    const user = userEvent.setup();

    renderPage();

    await user.type(screen.getByPlaceholderText("Any subject type"), "role");

    await waitFor(() =>
      expect(mockUsePolicies.mock.lastCall?.[0]).toEqual({ subject_type: "role" }),
    );
  });

  it("accepts arbitrary subject types when creating policies", async () => {
    const user = userEvent.setup();

    renderPage();

    await user.click(screen.getAllByRole("button", { name: "Create Policy" })[0]);
    const dialog = await screen.findByRole("dialog");

    await user.clear(within(dialog).getByPlaceholderText("e.g. user, group, role"));
    await user.type(
      within(dialog).getByPlaceholderText("e.g. user, group, role"),
      "role",
    );
    await user.type(within(dialog).getByPlaceholderText("e.g. alice"), "editor");
    await user.type(
      within(dialog).getByPlaceholderText('e.g. svc-123 or "*"'),
      "svc-1",
    );
    await user.type(within(dialog).getByPlaceholderText('e.g. read or "*"'), "read");

    await user.click(within(dialog).getByRole("button", { name: "Create Policy" }));

    await waitFor(() =>
      expect(mockCreatePolicy).toHaveBeenCalledWith({
        subject_type: "role",
        subject_id: "editor",
        resource_id: "svc-1",
        action_pattern: "read",
        risk_threshold: "safe",
        decision: "allow",
      }),
    );
  });

  it("clears stale evaluation results when inputs change", async () => {
    const user = userEvent.setup();

    renderPage();

    await user.click(screen.getByText("Test Policy Evaluation"));
    await user.type(screen.getByPlaceholderText("e.g. alice"), "alice");
    await user.type(screen.getByPlaceholderText("e.g. read"), "read");
    await user.type(screen.getByPlaceholderText("e.g. service-123"), "svc-1");

    await user.click(screen.getByRole("button", { name: "Evaluate" }));

    expect(await screen.findByText("Allow")).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("e.g. read"), "_all");

    await waitFor(() =>
      expect(screen.queryByText("Allow")).not.toBeInTheDocument(),
    );
  });

  it("clears the previous decision when a later evaluation fails", async () => {
    const user = userEvent.setup();
    mockEvaluatePolicy
      .mockResolvedValueOnce({
        decision: "allow",
        matched_policy_id: "pol-1",
        reason: "Matched",
      })
      .mockRejectedValueOnce(new Error("evaluation failed"));

    renderPage();

    await user.click(screen.getByText("Test Policy Evaluation"));
    await user.type(screen.getByPlaceholderText("e.g. alice"), "alice");
    await user.type(screen.getByPlaceholderText("e.g. read"), "read");
    await user.type(screen.getByPlaceholderText("e.g. service-123"), "svc-1");

    await user.click(screen.getByRole("button", { name: "Evaluate" }));
    expect(await screen.findByText("Allow")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Evaluate" }));

    await waitFor(() =>
      expect(screen.queryByText("Allow")).not.toBeInTheDocument(),
    );
    expect(mockToastError).toHaveBeenCalledWith("Policy evaluation failed");
  });
});
