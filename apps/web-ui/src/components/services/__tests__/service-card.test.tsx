import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-30T15:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

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

  it("shows 'Just now' for sub-minute compile times", () => {
    render(
      <ServiceCard
        service={makeService({
          last_compiled: "2026-03-30T14:59:45Z",
        })}
      />,
    );
    expect(screen.getByText("Just now")).toBeInTheDocument();
  });

  it("shows minute and hour relative times", () => {
    render(
      <>
        <ServiceCard
          service={makeService({
            service_id: "minute-svc",
            last_compiled: "2026-03-30T14:45:00Z",
          })}
        />
        <ServiceCard
          service={makeService({
            service_id: "hour-svc",
            last_compiled: "2026-03-30T11:00:00Z",
          })}
        />
      </>,
    );

    expect(screen.getByText("15m ago")).toBeInTheDocument();
    expect(screen.getByText("4h ago")).toBeInTheDocument();
  });

  it("shows day relative times", () => {
    render(
      <ServiceCard
        service={makeService({
          last_compiled: "2026-03-27T15:00:00Z",
        })}
      />,
    );
    expect(screen.getByText("3d ago")).toBeInTheDocument();
  });
});
