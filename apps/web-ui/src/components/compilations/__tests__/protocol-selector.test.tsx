import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ProtocolSelector } from "../protocol-selector";

const ALL_PROTOCOL_LABELS = [
  "Auto-detect",
  "OpenAPI",
  "REST",
  "GraphQL",
  "SQL",
  "gRPC",
  "SOAP",
];

const ALL_PROTOCOL_DESCRIPTIONS = [
  "Automatically detect the API protocol",
  "OpenAPI / Swagger specification",
  "Generic REST API endpoint",
  "GraphQL schema or endpoint",
  "SQL database interface",
  "Protocol Buffers / gRPC service",
  "SOAP / WSDL web service",
];

describe("ProtocolSelector", () => {
  it("renders all protocol options", () => {
    render(<ProtocolSelector value="" onChange={vi.fn()} />);
    for (const label of ALL_PROTOCOL_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders descriptions for all protocols", () => {
    render(<ProtocolSelector value="" onChange={vi.fn()} />);
    for (const desc of ALL_PROTOCOL_DESCRIPTIONS) {
      expect(screen.getByText(desc)).toBeInTheDocument();
    }
  });

  it("calls onChange when a protocol option is clicked", async () => {
    const user = userEvent.setup();
    const handleChange = vi.fn();

    render(<ProtocolSelector value="" onChange={handleChange} />);
    await user.click(screen.getByText("OpenAPI"));
    expect(handleChange).toHaveBeenCalledWith("openapi");
  });

  it("calls onChange with empty string for Auto-detect", async () => {
    const user = userEvent.setup();
    const handleChange = vi.fn();

    render(<ProtocolSelector value="openapi" onChange={handleChange} />);
    await user.click(screen.getByText("Auto-detect"));
    expect(handleChange).toHaveBeenCalledWith("");
  });

  it("calls onChange with 'graphql' for GraphQL", async () => {
    const user = userEvent.setup();
    const handleChange = vi.fn();

    render(<ProtocolSelector value="" onChange={handleChange} />);
    await user.click(screen.getByText("GraphQL"));
    expect(handleChange).toHaveBeenCalledWith("graphql");
  });

  it("calls onChange with 'grpc' for gRPC", async () => {
    const user = userEvent.setup();
    const handleChange = vi.fn();

    render(<ProtocolSelector value="" onChange={handleChange} />);
    await user.click(screen.getByText("gRPC"));
    expect(handleChange).toHaveBeenCalledWith("grpc");
  });

  it("highlights the selected protocol with primary styling", () => {
    const { container } = render(
      <ProtocolSelector value="openapi" onChange={vi.fn()} />,
    );
    const buttons = container.querySelectorAll("button");
    const openapiButton = Array.from(buttons).find((b) =>
      b.textContent?.includes("OpenAPI"),
    );
    expect(openapiButton?.className).toMatch(/border-primary/);
  });

  it("does not highlight non-selected protocols with ring styling", () => {
    const { container } = render(
      <ProtocolSelector value="openapi" onChange={vi.fn()} />,
    );
    const buttons = container.querySelectorAll("button");
    const restButton = Array.from(buttons).find((b) =>
      b.textContent?.includes("REST"),
    );
    // Non-selected buttons should NOT have the ring-2 class
    expect(restButton?.className).not.toMatch(/ring-2/);
    expect(restButton?.className).toMatch(/border-border/);
  });

  it("highlights Auto-detect when value is empty string", () => {
    const { container } = render(
      <ProtocolSelector value="" onChange={vi.fn()} />,
    );
    const buttons = container.querySelectorAll("button");
    const autoDetectButton = Array.from(buttons).find((b) =>
      b.textContent?.includes("Auto-detect"),
    );
    expect(autoDetectButton?.className).toMatch(/border-primary/);
  });

  it("renders exactly 7 protocol buttons", () => {
    const { container } = render(
      <ProtocolSelector value="" onChange={vi.fn()} />,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(7);
  });

  it("renders within a grid layout", () => {
    const { container } = render(
      <ProtocolSelector value="" onChange={vi.fn()} />,
    );
    const grid = container.querySelector(".grid");
    expect(grid).toBeInTheDocument();
  });

  it("each protocol button has type='button'", () => {
    const { container } = render(
      <ProtocolSelector value="" onChange={vi.fn()} />,
    );
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      expect(btn.getAttribute("type")).toBe("button");
    }
  });
});
