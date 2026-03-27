import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBadge } from "../status-badge";
import type { CompilationStatus } from "@/types/api";

const statusLabelMap: Record<CompilationStatus, string> = {
  PENDING: "Pending",
  DETECTING: "Detecting",
  EXTRACTING: "Extracting",
  ENHANCING: "Enhancing",
  VALIDATING_IR: "Validating IR",
  GENERATING: "Generating",
  BUILDING: "Building",
  DEPLOYING: "Deploying",
  VALIDATING_RUNTIME: "Validating Runtime",
  ROUTING: "Routing",
  REGISTERING: "Registering",
  PUBLISHED: "Published",
  FAILED: "Failed",
  ROLLING_BACK: "Rolling Back",
  ROLLED_BACK: "Rolled Back",
};

describe("StatusBadge", () => {
  it.each(Object.entries(statusLabelMap))(
    "renders label '%s' → '%s'",
    (status, expectedLabel) => {
      render(<StatusBadge status={status as CompilationStatus} />);
      expect(screen.getByText(expectedLabel)).toBeInTheDocument();
    },
  );

  it("applies green classes for PUBLISHED status", () => {
    render(<StatusBadge status="PUBLISHED" />);
    const badge = screen.getByText("Published");
    expect(badge.className).toMatch(/bg-green/);
  });

  it("applies red classes for FAILED status", () => {
    render(<StatusBadge status="FAILED" />);
    const badge = screen.getByText("Failed");
    expect(badge.className).toMatch(/bg-red/);
  });

  it("applies muted classes for PENDING status", () => {
    render(<StatusBadge status="PENDING" />);
    const badge = screen.getByText("Pending");
    expect(badge.className).toMatch(/bg-muted/);
  });

  it("applies blue classes for active stages like EXTRACTING", () => {
    render(<StatusBadge status="EXTRACTING" />);
    const badge = screen.getByText("Extracting");
    expect(badge.className).toMatch(/bg-blue/);
  });

  it("applies yellow classes for ROLLING_BACK", () => {
    render(<StatusBadge status="ROLLING_BACK" />);
    const badge = screen.getByText("Rolling Back");
    expect(badge.className).toMatch(/bg-yellow/);
  });

  it("applies yellow classes for ROLLED_BACK", () => {
    render(<StatusBadge status="ROLLED_BACK" />);
    const badge = screen.getByText("Rolled Back");
    expect(badge.className).toMatch(/bg-yellow/);
  });

  it("renders as an inline span with rounded-full styling", () => {
    render(<StatusBadge status="PUBLISHED" />);
    const badge = screen.getByText("Published");
    expect(badge.tagName).toBe("SPAN");
    expect(badge.className).toMatch(/rounded-full/);
  });

  it("merges custom className prop", () => {
    render(<StatusBadge status="PENDING" className="my-custom-class" />);
    const badge = screen.getByText("Pending");
    expect(badge.className).toContain("my-custom-class");
  });

  it("applies blue classes for all in-progress stages", () => {
    const blueStatuses: CompilationStatus[] = [
      "DETECTING",
      "ENHANCING",
      "VALIDATING_IR",
      "GENERATING",
      "BUILDING",
      "DEPLOYING",
      "VALIDATING_RUNTIME",
      "ROUTING",
      "REGISTERING",
    ];
    for (const status of blueStatuses) {
      const { unmount } = render(<StatusBadge status={status} />);
      const badge = screen.getByText(statusLabelMap[status]);
      expect(badge.className).toMatch(/bg-blue/);
      unmount();
    }
  });

  it("renders with whitespace-nowrap to prevent label wrapping", () => {
    render(<StatusBadge status="VALIDATING_RUNTIME" />);
    const badge = screen.getByText("Validating Runtime");
    expect(badge.className).toMatch(/whitespace-nowrap/);
  });
});
