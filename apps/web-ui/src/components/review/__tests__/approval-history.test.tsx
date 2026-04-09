import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ApprovalHistory } from "../approval-history";
import type { WorkflowHistoryEntry } from "@/stores/workflow-store";

function makeEntry(
  overrides: Partial<WorkflowHistoryEntry> = {},
): WorkflowHistoryEntry {
  return {
    from: "draft",
    to: "submitted",
    actor: "alice",
    timestamp: "2024-06-15T10:30:00Z",
    ...overrides,
  };
}

describe("ApprovalHistory", () => {
  it("renders empty state when no history", () => {
    render(<ApprovalHistory history={[]} />);
    expect(
      screen.getByText(/no workflow history yet/i),
    ).toBeInTheDocument();
  });

  it("renders a timeline entry with actor name", () => {
    render(<ApprovalHistory history={[makeEntry()]} />);
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it('shows "transitioned" label text', () => {
    render(<ApprovalHistory history={[makeEntry()]} />);
    expect(screen.getByText("transitioned")).toBeInTheDocument();
  });

  it("shows from state badge", () => {
    render(<ApprovalHistory history={[makeEntry({ from: "draft" })]} />);
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  it("shows to state badge", () => {
    render(<ApprovalHistory history={[makeEntry({ to: "submitted" })]} />);
    expect(screen.getByText("Submitted")).toBeInTheDocument();
  });

  it("shows arrow separator between states", () => {
    render(<ApprovalHistory history={[makeEntry()]} />);
    expect(screen.getByText("→")).toBeInTheDocument();
  });

  it("renders timestamp", () => {
    render(
      <ApprovalHistory
        history={[makeEntry({ timestamp: "2024-06-15T10:30:00Z" })]}
      />,
    );
    // formatTimestamp produces locale-formatted date — check that it renders something
    const timeText = screen.getByText(/Jun/i);
    expect(timeText).toBeInTheDocument();
  });

  it("shows comment when present", () => {
    render(
      <ApprovalHistory
        history={[makeEntry({ comment: "Looks good to me!" })]}
      />,
    );
    expect(screen.getByText(/Looks good to me!/)).toBeInTheDocument();
  });

  it("does not show comment when absent", () => {
    render(<ApprovalHistory history={[makeEntry({ comment: undefined })]} />);
    expect(screen.queryByText(/"/)).not.toBeInTheDocument();
  });

  it("renders multiple entries", () => {
    const history = [
      makeEntry({ actor: "alice", from: "draft", to: "submitted" }),
      makeEntry({
        actor: "bob",
        from: "submitted",
        to: "in_review",
        timestamp: "2024-06-16T12:00:00Z",
      }),
    ];
    render(<ApprovalHistory history={history} />);
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
  });

  it("renders correct from→to labels for various transitions", () => {
    const history = [
      makeEntry({ from: "in_review", to: "approved", actor: "carol" }),
    ];
    render(<ApprovalHistory history={history} />);
    expect(screen.getByText("In Review")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
  });
});
