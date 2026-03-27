import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ToolIntentFilter } from "../tool-intent-filter";
import type { Operation } from "@/types/api";

function makeOp(
  id: string,
  intent?: "discovery" | "action",
): Operation {
  return {
    id,
    name: `op-${id}`,
    description: `Desc ${id}`,
    params: [],
    risk: { risk_level: "safe", confidence: 0.9, source: "extractor" },
    tags: [],
    source: "extractor",
    confidence: 0.9,
    enabled: true,
    tool_intent: intent,
  };
}

const operations: Operation[] = [
  makeOp("1", "discovery"),
  makeOp("2", "discovery"),
  makeOp("3", "action"),
  makeOp("4", undefined),
];

describe("ToolIntentFilter", () => {
  it("renders All, Discovery, and Action buttons", () => {
    render(
      <ToolIntentFilter operations={operations} onFilterChange={vi.fn()} />,
    );
    expect(screen.getByText("All")).toBeInTheDocument();
    expect(screen.getByText("Discovery")).toBeInTheDocument();
    expect(screen.getByText("Action")).toBeInTheDocument();
  });

  it("shows correct counts in badges", () => {
    render(
      <ToolIntentFilter operations={operations} onFilterChange={vi.fn()} />,
    );
    // Count badges: All=4, Discovery=2, Action=1
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("calls onFilterChange with all operations on mount", () => {
    const onFilterChange = vi.fn();
    render(
      <ToolIntentFilter operations={operations} onFilterChange={onFilterChange} />,
    );
    expect(onFilterChange).toHaveBeenCalledWith(operations);
  });

  it("filters to discovery operations when Discovery clicked", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(
      <ToolIntentFilter operations={operations} onFilterChange={onFilterChange} />,
    );

    await user.click(screen.getByRole("button", { name: /Discovery/ }));

    const lastCall = onFilterChange.mock.calls[onFilterChange.mock.calls.length - 1][0];
    expect(lastCall).toHaveLength(2);
    expect(lastCall.every((op: Operation) => op.tool_intent === "discovery")).toBe(true);
  });

  it("filters to action operations when Action clicked", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(
      <ToolIntentFilter operations={operations} onFilterChange={onFilterChange} />,
    );

    await user.click(screen.getByRole("button", { name: /Action/ }));

    const lastCall = onFilterChange.mock.calls[onFilterChange.mock.calls.length - 1][0];
    expect(lastCall).toHaveLength(1);
    expect(lastCall[0].tool_intent).toBe("action");
  });

  it("returns all operations when All clicked after filtering", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(
      <ToolIntentFilter operations={operations} onFilterChange={onFilterChange} />,
    );

    // First filter to discovery
    await user.click(screen.getByRole("button", { name: /Discovery/ }));
    // Then back to all
    await user.click(screen.getByRole("button", { name: /All/ }));

    const lastCall = onFilterChange.mock.calls[onFilterChange.mock.calls.length - 1][0];
    expect(lastCall).toHaveLength(4);
  });

  it("renders icons for each toggle", () => {
    const { container } = render(
      <ToolIntentFilter operations={operations} onFilterChange={vi.fn()} />,
    );
    const svgs = container.querySelectorAll("svg");
    expect(svgs.length).toBeGreaterThanOrEqual(3);
  });
});
