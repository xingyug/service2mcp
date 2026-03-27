import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { RiskFilter } from "../risk-filter";
import type { Operation } from "@/types/api";

function makeOp(
  id: string,
  riskLevel: "safe" | "cautious" | "dangerous" | "unknown",
): Operation {
  return {
    id,
    name: `op-${id}`,
    description: `Description for ${id}`,
    params: [],
    risk: {
      risk_level: riskLevel,
      confidence: 0.9,
      source: "extractor",
    },
    tags: [],
    source: "extractor",
    confidence: 0.9,
    enabled: true,
  };
}

const operations: Operation[] = [
  makeOp("1", "safe"),
  makeOp("2", "safe"),
  makeOp("3", "cautious"),
  makeOp("4", "dangerous"),
  makeOp("5", "unknown"),
];

describe("RiskFilter", () => {
  it("renders buttons for all risk levels", () => {
    render(<RiskFilter operations={operations} onFilterChange={vi.fn()} />);
    expect(screen.getByText("Safe")).toBeInTheDocument();
    expect(screen.getByText("Cautious")).toBeInTheDocument();
    expect(screen.getByText("Dangerous")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });

  it("displays correct counts per risk level", () => {
    render(<RiskFilter operations={operations} onFilterChange={vi.fn()} />);
    // Summary text shows counts
    expect(screen.getByText(/2 safe/)).toBeInTheDocument();
    expect(screen.getByText(/1 cautious/)).toBeInTheDocument();
    expect(screen.getByText(/1 dangerous/)).toBeInTheDocument();
    expect(screen.getByText(/1 unknown/)).toBeInTheDocument();
  });

  it("calls onFilterChange on initial render with all operations", () => {
    const onFilterChange = vi.fn();
    render(<RiskFilter operations={operations} onFilterChange={onFilterChange} />);
    // The effect syncs filter on mount — should be called with all ops
    expect(onFilterChange).toHaveBeenCalledWith(operations);
  });

  it("calls onFilterChange with filtered operations when toggling", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(<RiskFilter operations={operations} onFilterChange={onFilterChange} />);

    // Click "Safe" button to deselect it
    const safeButton = screen.getByRole("button", { name: /Safe/ });
    await user.click(safeButton);

    // Should filter out safe operations (ids 1, 2)
    const lastCall = onFilterChange.mock.calls[onFilterChange.mock.calls.length - 1][0];
    expect(lastCall).toHaveLength(3);
    expect(lastCall.every((op: Operation) => op.risk.risk_level !== "safe")).toBe(true);
  });

  it("shows dangerous operations warning banner", () => {
    render(<RiskFilter operations={operations} onFilterChange={vi.fn()} />);
    expect(
      screen.getByText(/dangerous operation.*detected/i),
    ).toBeInTheDocument();
  });

  it("does not show warning banner when no dangerous operations", () => {
    const safeOps = [makeOp("1", "safe"), makeOp("2", "cautious")];
    render(<RiskFilter operations={safeOps} onFilterChange={vi.fn()} />);
    expect(
      screen.queryByText(/dangerous operation.*detected/i),
    ).not.toBeInTheDocument();
  });

  it("prevents deselecting the last active filter", async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    const singleOp = [makeOp("1", "safe")];
    render(<RiskFilter operations={singleOp} onFilterChange={onFilterChange} />);

    // Deselect cautious, dangerous, unknown first
    await user.click(screen.getByRole("button", { name: /Cautious/ }));
    await user.click(screen.getByRole("button", { name: /Dangerous/ }));
    await user.click(screen.getByRole("button", { name: /Unknown/ }));

    // Now try to deselect "Safe" — it should still be active (can't deselect last)
    await user.click(screen.getByRole("button", { name: /Safe/ }));

    const lastCall = onFilterChange.mock.calls[onFilterChange.mock.calls.length - 1][0];
    expect(lastCall.length).toBeGreaterThan(0);
  });

  it("renders summary text with counts", () => {
    render(<RiskFilter operations={operations} onFilterChange={vi.fn()} />);
    expect(
      screen.getByText("2 safe, 1 cautious, 1 dangerous, 1 unknown"),
    ).toBeInTheDocument();
  });
});
