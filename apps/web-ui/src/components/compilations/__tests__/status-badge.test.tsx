import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBadge } from "../status-badge";
import type { CompilationStatus } from "@/types/api";

const statusLabelMap: Record<CompilationStatus, string> = {
  pending: "Pending",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  rolled_back: "Rolled Back",
};

describe("StatusBadge", () => {
  it.each(Object.entries(statusLabelMap))(
    "renders label '%s' → '%s'",
    (status, expectedLabel) => {
      render(<StatusBadge status={status as CompilationStatus} />);
      expect(screen.getByText(expectedLabel)).toBeInTheDocument();
    },
  );

  it("applies green classes for succeeded status", () => {
    render(<StatusBadge status="succeeded" />);
    const badge = screen.getByText("Succeeded");
    expect(badge.className).toMatch(/bg-green/);
  });

  it("applies red classes for failed status", () => {
    render(<StatusBadge status="failed" />);
    const badge = screen.getByText("Failed");
    expect(badge.className).toMatch(/bg-red/);
  });

  it("applies muted classes for pending status", () => {
    render(<StatusBadge status="pending" />);
    const badge = screen.getByText("Pending");
    expect(badge.className).toMatch(/bg-muted/);
  });

  it("applies blue classes for running status", () => {
    render(<StatusBadge status="running" />);
    const badge = screen.getByText("Running");
    expect(badge.className).toMatch(/bg-blue/);
  });

  it("applies yellow classes for rolled_back", () => {
    render(<StatusBadge status="rolled_back" />);
    const badge = screen.getByText("Rolled Back");
    expect(badge.className).toMatch(/bg-yellow/);
  });

  it("renders as an inline span with rounded-full styling", () => {
    render(<StatusBadge status="succeeded" />);
    const badge = screen.getByText("Succeeded");
    expect(badge.tagName).toBe("SPAN");
    expect(badge.className).toMatch(/rounded-full/);
  });

  it("merges custom className prop", () => {
    render(<StatusBadge status="pending" className="my-custom-class" />);
    const badge = screen.getByText("Pending");
    expect(badge.className).toContain("my-custom-class");
  });

  it("applies blue classes for all in-progress statuses", () => {
    const blueStatuses: CompilationStatus[] = ["running"];
    for (const status of blueStatuses) {
      const { unmount } = render(<StatusBadge status={status} />);
      const badge = screen.getByText(statusLabelMap[status]);
      expect(badge.className).toMatch(/bg-blue/);
      unmount();
    }
  });

  it("renders with whitespace-nowrap to prevent label wrapping", () => {
    render(<StatusBadge status="rolled_back" />);
    const badge = screen.getByText("Rolled Back");
    expect(badge.className).toMatch(/whitespace-nowrap/);
  });
});
