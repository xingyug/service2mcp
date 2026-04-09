import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { IntentBadge } from "../intent-badge";

describe("IntentBadge", () => {
  it("renders discovery badge with correct text", () => {
    render(<IntentBadge intent="discovery" />);
    expect(screen.getByText("discovery")).toBeInTheDocument();
  });

  it("renders action badge with correct text", () => {
    render(<IntentBadge intent="action" />);
    expect(screen.getByText("action")).toBeInTheDocument();
  });

  it("applies blue styling for discovery intent", () => {
    render(<IntentBadge intent="discovery" />);
    const badge = screen.getByText("discovery").closest("span");
    expect(badge?.className).toContain("blue");
  });

  it("applies orange styling for action intent", () => {
    render(<IntentBadge intent="action" />);
    const badge = screen.getByText("action").closest("span");
    expect(badge?.className).toContain("orange");
  });

  it("renders nothing when intent is undefined", () => {
    const { container } = render(<IntentBadge />);
    expect(container.innerHTML).toBe("");
  });

  it("renders an icon alongside the label", () => {
    const { container } = render(<IntentBadge intent="discovery" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();
  });

  it("has uppercase text styling", () => {
    render(<IntentBadge intent="action" />);
    const badge = screen.getByText("action").closest("span");
    expect(badge?.className).toContain("uppercase");
  });

  it("applies additional className prop", () => {
    render(<IntentBadge intent="discovery" className="my-class" />);
    const badge = screen.getByText("discovery").closest("span");
    expect(badge?.className).toContain("my-class");
  });
});
