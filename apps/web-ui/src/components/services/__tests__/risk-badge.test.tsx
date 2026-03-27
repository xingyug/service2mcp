import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { RiskBadge } from "../risk-badge";
import type { RiskLevel } from "@/types/api";

describe("RiskBadge", () => {
  const levels: { level: RiskLevel; label: string; colorFragment: string }[] = [
    { level: "safe", label: "Safe", colorFragment: "green" },
    { level: "cautious", label: "Cautious", colorFragment: "yellow" },
    { level: "dangerous", label: "Dangerous", colorFragment: "red" },
    { level: "unknown", label: "Unknown", colorFragment: "muted" },
  ];

  it.each(levels)(
    "renders correct label for $level risk level",
    ({ level, label }) => {
      render(<RiskBadge level={level} />);
      expect(screen.getByText(label)).toBeInTheDocument();
    },
  );

  it.each(levels)(
    "applies $colorFragment color class for $level risk level",
    ({ level, label, colorFragment }) => {
      render(<RiskBadge level={level} />);
      const badge = screen.getByText(label).closest("span");
      expect(badge?.className).toContain(colorFragment);
    },
  );

  it.each(levels)(
    "renders an icon for $level risk level",
    ({ level }) => {
      const { container } = render(<RiskBadge level={level} />);
      const svg = container.querySelector("svg");
      expect(svg).toBeInTheDocument();
    },
  );

  it("applies additional className prop", () => {
    render(<RiskBadge level="safe" className="extra-class" />);
    const badge = screen.getByText("Safe").closest("span");
    expect(badge?.className).toContain("extra-class");
  });

  it("has rounded-full styling", () => {
    render(<RiskBadge level="cautious" />);
    const badge = screen.getByText("Cautious").closest("span");
    expect(badge?.className).toContain("rounded-full");
  });

  it("renders inline-flex layout", () => {
    render(<RiskBadge level="dangerous" />);
    const badge = screen.getByText("Dangerous").closest("span");
    expect(badge?.className).toContain("inline-flex");
  });
});
