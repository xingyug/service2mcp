import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ToolCard } from "../tool-card";
import type { Operation } from "@/types/api";

function makeOperation(overrides: Partial<Operation> = {}): Operation {
  return {
    id: "op-1",
    name: "getUsers",
    description: "Retrieve list of users",
    method: "GET",
    path: "/users",
    params: [
      {
        name: "page",
        type: "integer",
        required: false,
        description: "Page number",
        source: "extractor",
        confidence: 0.9,
      },
      {
        name: "limit",
        type: "integer",
        required: true,
        description: "Results per page",
        source: "extractor",
        confidence: 0.95,
      },
    ],
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

describe("ToolCard", () => {
  it("renders operation name", () => {
    render(<ToolCard operation={makeOperation()} />);
    expect(screen.getByText("getUsers")).toBeInTheDocument();
  });

  it("renders operation description", () => {
    render(<ToolCard operation={makeOperation()} />);
    expect(screen.getByText("Retrieve list of users")).toBeInTheDocument();
  });

  it("renders risk badge", () => {
    render(<ToolCard operation={makeOperation()} />);
    expect(screen.getByText("Safe")).toBeInTheDocument();
  });

  it("renders method and path", () => {
    render(<ToolCard operation={makeOperation()} />);
    expect(screen.getByText("GET /users")).toBeInTheDocument();
  });

  it("shows parameter count", () => {
    render(<ToolCard operation={makeOperation()} />);
    expect(screen.getByText("2 params")).toBeInTheDocument();
  });

  it("shows singular param text when 1 param", () => {
    const op = makeOperation({
      params: [
        {
          name: "id",
          type: "string",
          required: true,
          description: "User ID",
          source: "extractor",
          confidence: 1,
        },
      ],
    });
    render(<ToolCard operation={op} />);
    expect(screen.getByText("1 param")).toBeInTheDocument();
  });

  it("shows parameter details when expanded", async () => {
    const user = userEvent.setup();
    render(<ToolCard operation={makeOperation()} />);

    // Click to expand
    const trigger = screen.getByRole("button");
    await user.click(trigger);

    // Check parameter names appear in the expanded content
    expect(screen.getByText("page")).toBeInTheDocument();
    expect(screen.getByText("limit")).toBeInTheDocument();
  });

  it("calls onToggle when switch is toggled", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<ToolCard operation={makeOperation()} onToggle={onToggle} />);

    const switchEl = screen.getByRole("switch");
    await user.click(switchEl);

    expect(onToggle).toHaveBeenCalledWith("op-1", false);
  });

  it("handles operation with no parameters", () => {
    const op = makeOperation({ params: [] });
    render(<ToolCard operation={op} />);
    expect(screen.getByText("0 params")).toBeInTheDocument();
  });

  it("shows confidence percentage", () => {
    render(<ToolCard operation={makeOperation({ confidence: 0.85 })} />);
    expect(screen.getByText("85%")).toBeInTheDocument();
  });

  it("renders intent badge when tool_intent is set", () => {
    render(<ToolCard operation={makeOperation({ tool_intent: "action" })} />);
    expect(screen.getByText("action")).toBeInTheDocument();
  });

  it("renders risk metadata when expanded", async () => {
    const user = userEvent.setup();
    render(<ToolCard operation={makeOperation()} />);

    await user.click(screen.getByRole("button"));

    expect(screen.getByText(/Writes state:/)).toBeInTheDocument();
    expect(screen.getByText(/Destructive:/)).toBeInTheDocument();
  });
});
