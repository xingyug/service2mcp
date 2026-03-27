import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ReviewStatusBadge, ReviewStateBadge } from "../review-status-badge";
import type { WorkflowState } from "@/stores/workflow-store";

// Mock the workflow store
vi.mock("@/stores/workflow-store", () => ({
  useWorkflowStore: vi.fn(),
}));

import { useWorkflowStore } from "@/stores/workflow-store";

const mockedUseWorkflowStore = vi.mocked(useWorkflowStore);

const states: { state: WorkflowState; label: string; colorFragment: string }[] =
  [
    { state: "draft", label: "Draft", colorFragment: "gray" },
    { state: "submitted", label: "Submitted", colorFragment: "blue" },
    { state: "in_review", label: "In Review", colorFragment: "yellow" },
    { state: "approved", label: "Approved", colorFragment: "green" },
    { state: "rejected", label: "Rejected", colorFragment: "red" },
    { state: "published", label: "Published", colorFragment: "purple" },
    { state: "deployed", label: "Deployed", colorFragment: "emerald" },
  ];

describe("ReviewStatusBadge", () => {
  beforeEach(() => {
    mockedUseWorkflowStore.mockImplementation((selector: (s: { getWorkflow: () => { state: string } | undefined }) => unknown) =>
      selector({
        getWorkflow: () => undefined,
      }),
    );
  });

  it("defaults to Draft when no workflow exists", () => {
    render(<ReviewStatusBadge serviceId="svc-1" versionNumber={1} />);
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  it("renders link to review page", () => {
    render(<ReviewStatusBadge serviceId="svc-1" versionNumber={2} />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute(
      "href",
      "/services/svc-1/review?version=2",
    );
  });

  it.each(states)(
    "renders correct label for $state state via ReviewStatusBadge",
    ({ state, label }) => {
      mockedUseWorkflowStore.mockImplementation((selector: (s: { getWorkflow: () => { state: string } | undefined }) => unknown) =>
        selector({
          getWorkflow: () => ({ state }),
        }),
      );
      render(<ReviewStatusBadge serviceId="svc-1" versionNumber={1} />);
      expect(screen.getByText(label)).toBeInTheDocument();
    },
  );

  it("renders an icon alongside the label", () => {
    const { container } = render(
      <ReviewStatusBadge serviceId="svc-1" versionNumber={1} />,
    );
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("applies additional className", () => {
    render(
      <ReviewStatusBadge
        serviceId="svc-1"
        versionNumber={1}
        className="extra"
      />,
    );
    const link = screen.getByRole("link");
    expect(link.className).toContain("extra");
  });
});

describe("ReviewStateBadge", () => {
  it.each(states)(
    "renders correct label for $state",
    ({ state, label }) => {
      render(<ReviewStateBadge state={state} />);
      expect(screen.getByText(label)).toBeInTheDocument();
    },
  );

  it.each(states)(
    "applies $colorFragment color class for $state",
    ({ state, label, colorFragment }) => {
      render(<ReviewStateBadge state={state} />);
      const badge = screen.getByText(label).closest("span");
      expect(badge?.className).toContain(colorFragment);
    },
  );

  it("renders an icon alongside the label", () => {
    const { container } = render(<ReviewStateBadge state="approved" />);
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("applies additional className", () => {
    render(<ReviewStateBadge state="draft" className="my-custom" />);
    const badge = screen.getByText("Draft").closest("span");
    expect(badge?.className).toContain("my-custom");
  });
});
