import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ProtocolBadge } from "../protocol-badge";

describe("ProtocolBadge", () => {
  const protocols = [
    { key: "openapi", label: "OpenAPI", colorFragment: "blue" },
    { key: "rest", label: "REST", colorFragment: "green" },
    { key: "graphql", label: "GraphQL", colorFragment: "pink" },
    { key: "grpc", label: "gRPC", colorFragment: "purple" },
    { key: "jsonrpc", label: "JSON-RPC", colorFragment: "amber" },
    { key: "odata", label: "OData", colorFragment: "indigo" },
    { key: "scim", label: "SCIM", colorFragment: "teal" },
    { key: "soap", label: "SOAP", colorFragment: "orange" },
    { key: "sql", label: "SQL", colorFragment: "cyan" },
  ];

  it.each(protocols)(
    "renders correct label for $key protocol",
    ({ key, label }) => {
      render(<ProtocolBadge protocol={key} />);
      expect(screen.getByText(label)).toBeInTheDocument();
    },
  );

  it.each(protocols)(
    "applies $colorFragment color class for $key protocol",
    ({ key, colorFragment }) => {
      render(<ProtocolBadge protocol={key} />);
      const badge = screen.getByText(protocols.find((p) => p.key === key)!.label).closest("span");
      expect(badge?.className).toContain(colorFragment);
    },
  );

  it("renders fallback for unknown protocol", () => {
    render(<ProtocolBadge protocol="ftp" />);
    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });

  it("handles case-insensitive protocol input", () => {
    render(<ProtocolBadge protocol="OpenAPI" />);
    expect(screen.getByText("OpenAPI")).toBeInTheDocument();
  });

  it('uses smaller sizing when size="sm"', () => {
    render(<ProtocolBadge protocol="rest" size="sm" />);
    const badge = screen.getByText("REST").closest("span");
    expect(badge?.className).toContain("text-[10px]");
  });

  it('uses medium sizing by default (size="md")', () => {
    render(<ProtocolBadge protocol="rest" />);
    const badge = screen.getByText("REST").closest("span");
    expect(badge?.className).toContain("text-xs");
  });

  it("applies additional className prop", () => {
    render(<ProtocolBadge protocol="openapi" className="custom-class" />);
    const badge = screen.getByText("OpenAPI").closest("span");
    expect(badge?.className).toContain("custom-class");
  });

  it("renders an icon alongside the label", () => {
    const { container } = render(<ProtocolBadge protocol="openapi" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();
  });
});
