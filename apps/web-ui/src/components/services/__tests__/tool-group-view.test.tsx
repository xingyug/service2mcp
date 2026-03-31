import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ToolGroupView } from "../tool-group-view";
import type { Operation, ToolGroup } from "@/types/api";

vi.mock("@/components/services/tool-card", () => ({
  ToolCard: ({ operation }: { operation: { name: string } }) => (
    <div data-testid="tool-card">{operation.name}</div>
  ),
}));

function makeOperation(overrides: Partial<Operation> = {}): Operation {
  return {
    id: "op-1",
    name: "listUsers",
    description: "Retrieve list of users",
    method: "GET",
    path: "/users",
    params: [],
    risk: {
      risk_level: "safe",
      writes_state: false,
      destructive: false,
      external_side_effect: false,
      idempotent: true,
      confidence: 0.95,
      source: "extractor",
    },
    tags: ["users"],
    source: "extractor",
    confidence: 0.92,
    enabled: true,
    tool_intent: "discovery",
    ...overrides,
  };
}

const groupedOnly: ToolGroup[] = [
  {
    group_id: "users",
    label: "Users",
    description: "User management",
    operation_ids: ["op-users", "op-runtime"],
  },
  {
    group_id: "admin",
    label: "Admin",
    description: "Admin workflows",
    operation_ids: ["op-audit"],
  },
];

const groupedOps: Operation[] = [
  makeOperation({
    id: "op-users",
    name: "listUsers",
    description: "Retrieve list of users",
  }),
  makeOperation({
    id: "op-runtime",
    name: "runtimeStatus",
    description: "View runtime status",
  }),
  makeOperation({
    id: "op-audit",
    name: "auditLog",
    description: "Inspect audit history",
  }),
];

const withUngroupedOps: Operation[] = [
  ...groupedOps,
  makeOperation({
    id: "op-export",
    name: "exportData",
    description: "Download platform export",
  }),
];

describe("ToolGroupView", () => {
  it("starts with grouped operations expanded and can expand or collapse all groups", async () => {
    const user = userEvent.setup();

    render(<ToolGroupView groups={groupedOnly} operations={withUngroupedOps} />);

    expect(screen.getByText("listUsers")).toBeInTheDocument();
    expect(screen.getByText("runtimeStatus")).toBeInTheDocument();
    expect(screen.getByText("auditLog")).toBeInTheDocument();
    expect(screen.queryByText("exportData")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand all/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /expand all/i }));

    expect(screen.getByText("exportData")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /collapse all/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /collapse all/i }));

    expect(screen.queryByText("listUsers")).not.toBeInTheDocument();
    expect(screen.queryByText("exportData")).not.toBeInTheDocument();
  });

  it("toggles individual groups and treats fully grouped data as already expanded", async () => {
    const user = userEvent.setup();

    render(<ToolGroupView groups={groupedOnly} operations={groupedOps} />);

    expect(screen.getByRole("button", { name: /collapse all/i })).toBeInTheDocument();
    expect(screen.queryByText("Ungrouped")).not.toBeInTheDocument();

    const usersTrigger = screen.getByRole("button", { name: /users/i });
    await user.click(usersTrigger);
    expect(screen.queryByText("listUsers")).not.toBeInTheDocument();

    await user.click(usersTrigger);
    expect(screen.getByText("listUsers")).toBeInTheDocument();
  });

  it("toggles the ungrouped section independently", async () => {
    const user = userEvent.setup();

    render(<ToolGroupView groups={groupedOnly} operations={withUngroupedOps} />);

    const ungroupedTrigger = screen.getByRole("button", { name: /ungrouped/i });
    await user.click(ungroupedTrigger);
    expect(screen.getByText("exportData")).toBeInTheDocument();

    await user.click(ungroupedTrigger);
    expect(screen.queryByText("exportData")).not.toBeInTheDocument();
  });

  it("filters operations by name or description and shows the search empty state", async () => {
    const user = userEvent.setup();

    render(<ToolGroupView groups={groupedOnly} operations={groupedOps} />);

    const search = screen.getByPlaceholderText(/search across all groups/i);

    await user.type(search, "audit");
    expect(screen.getByText("auditLog")).toBeInTheDocument();
    expect(screen.queryByText("listUsers")).not.toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "runtime");
    expect(screen.getByText("runtimeStatus")).toBeInTheDocument();
    expect(screen.queryByText("auditLog")).not.toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "missing");
    expect(
      screen.getByText("No operations match your search."),
    ).toBeInTheDocument();
  });

  it("shows the empty-state copy when there are no groups or operations", () => {
    render(<ToolGroupView groups={[]} operations={[]} />);

    expect(screen.getByText("No tool groups available.")).toBeInTheDocument();
  });
});
