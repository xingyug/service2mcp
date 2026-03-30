import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DashboardPage from "../page";

const {
  mockUseServices,
  mockUseCompilations,
  mockUseAuditLogs,
} = vi.hoisted(() => ({
  mockUseServices: vi.fn(),
  mockUseCompilations: vi.fn(),
  mockUseAuditLogs: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("@/hooks/use-api", () => ({
  useServices: mockUseServices,
  useCompilations: mockUseCompilations,
  useAuditLogs: mockUseAuditLogs,
}));

vi.mock("@/components/dashboard/compilation-metrics", () => ({
  CompilationMetrics: () => <div>compilation-metrics</div>,
}));

describe("DashboardPage", () => {
  it("computes success rate from succeeded/failed jobs and total tools from tool_count", () => {
    mockUseServices.mockReturnValue({
      data: {
        services: [
          {
            service_id: "svc-1",
            name: "Billing API",
            protocol: "openapi",
            tool_count: 3,
            version_count: 99,
            last_compiled: "2026-03-29T00:00:00Z",
          },
          {
            service_id: "svc-2",
            name: "Orders API",
            protocol: "graphql",
            tool_count: 4,
            version_count: 99,
            last_compiled: "2026-03-29T00:00:00Z",
          },
        ],
      },
      isLoading: false,
      isError: false,
    });
    mockUseCompilations.mockReturnValue({
      data: [
        {
          job_id: "job-1",
          status: "succeeded",
          created_at: "2026-03-29T00:00:00Z",
        },
        {
          job_id: "job-2",
          status: "failed",
          created_at: "2026-03-29T00:00:00Z",
        },
      ],
      isLoading: false,
      isError: false,
    });
    mockUseAuditLogs.mockReturnValue({
      data: { entries: [] },
      isLoading: false,
      isError: false,
    });

    render(<DashboardPage />);

    expect(screen.getByText("50% success rate")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("marks system status as degraded when audit logs fail", () => {
    mockUseServices.mockReturnValue({
      data: { services: [] },
      isLoading: false,
      isError: false,
    });
    mockUseCompilations.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
    });
    mockUseAuditLogs.mockReturnValue({
      data: { entries: [] },
      isLoading: false,
      isError: true,
    });

    render(<DashboardPage />);

    expect(screen.getByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText("Some APIs unreachable")).toBeInTheDocument();
    expect(screen.getByText("Failed to load audit logs.")).toBeInTheDocument();
  });
});
