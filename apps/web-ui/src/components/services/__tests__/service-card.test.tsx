import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ServiceCard } from "../service-card";
import type { ServiceSummary } from "@/types/api";

function makeService(overrides: Partial<ServiceSummary> = {}): ServiceSummary {
  return {
    service_id: "svc-123",
    name: "My Test Service",
    protocol: "openapi",
    version_count: 3,
    ...overrides,
  };
}

describe("ServiceCard", () => {
  it("renders the service name", () => {
    render(<ServiceCard service={makeService()} />);
    expect(screen.getByText("My Test Service")).toBeInTheDocument();
  });

  it("renders a protocol badge", () => {
    render(<ServiceCard service={makeService({ protocol: "graphql" })} />);
    expect(screen.getByText("GraphQL")).toBeInTheDocument();
  });

  it("renders version count with plural", () => {
    render(<ServiceCard service={makeService({ version_count: 5 })} />);
    expect(screen.getByText("5 versions")).toBeInTheDocument();
  });

  it("renders version count singular when 1", () => {
    render(<ServiceCard service={makeService({ version_count: 1 })} />);
    expect(screen.getByText("1 version")).toBeInTheDocument();
  });

  it("renders link to service detail page", () => {
    render(<ServiceCard service={makeService({ service_id: "svc-abc" })} />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/services/svc-abc");
  });

  it("preserves tenant/environment in the detail link", () => {
    render(
      <ServiceCard
        service={makeService({
          service_id: "svc-abc",
          tenant: "acme",
          environment: "prod",
        })}
      />,
    );
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute(
      "href",
      "/services/svc-abc?tenant=acme&environment=prod",
    );
  });

  it("shows active version badge when present", () => {
    render(<ServiceCard service={makeService({ active_version: 2 })} />);
    expect(screen.getByText("v2")).toBeInTheDocument();
  });

  it("does not show active version badge when absent", () => {
    render(<ServiceCard service={makeService()} />);
    expect(screen.queryByText(/^v\d/)).not.toBeInTheDocument();
  });

  it("shows tenant badge when present", () => {
    render(<ServiceCard service={makeService({ tenant: "acme-corp" })} />);
    expect(screen.getByText("acme-corp")).toBeInTheDocument();
  });

  it("shows environment badge when present", () => {
    render(<ServiceCard service={makeService({ environment: "production" })} />);
    expect(screen.getByText("production")).toBeInTheDocument();
  });

  it("does not show tenant/environment section when both absent", () => {
    render(<ServiceCard service={makeService()} />);
    expect(screen.queryByText("acme-corp")).not.toBeInTheDocument();
    expect(screen.queryByText("production")).not.toBeInTheDocument();
  });

  it("handles minimal service data (only required fields)", () => {
    const minimal: ServiceSummary = {
      service_id: "min-svc",
      name: "Minimal",
      protocol: "rest",
      version_count: 0,
    };
    render(<ServiceCard service={minimal} />);
    expect(screen.getByText("Minimal")).toBeInTheDocument();
    expect(screen.getByText("REST")).toBeInTheDocument();
    expect(screen.getByText("0 versions")).toBeInTheDocument();
  });

  it("shows 'Never' when last_compiled is absent", () => {
    render(<ServiceCard service={makeService()} />);
    expect(screen.getByText("Never")).toBeInTheDocument();
  });
});
