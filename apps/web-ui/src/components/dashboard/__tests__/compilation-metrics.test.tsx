import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CompilationMetrics } from "../compilation-metrics";

const { mockUseCompilations } = vi.hoisted(() => ({
  mockUseCompilations: vi.fn(),
}));

vi.mock("@/hooks/use-api", () => ({
  useCompilations: mockUseCompilations,
}));

describe("CompilationMetrics", () => {
  it("uses real lower-case statuses and protocol fields for distributions", () => {
    mockUseCompilations.mockReturnValue({
      data: [
        {
          job_id: "job-1",
          status: "running",
          protocol: "openapi",
          created_at: "2026-03-29T00:00:00Z",
        },
        {
          job_id: "job-2",
          status: "succeeded",
          protocol: "graphql",
          created_at: "2026-03-29T00:00:00Z",
        },
        {
          job_id: "job-3",
          status: "rolled_back",
          protocol: "openapi",
          created_at: "2026-03-29T00:00:00Z",
        },
      ],
      isLoading: false,
    });

    render(<CompilationMetrics />);

    expect(screen.getByText("In Progress (1)")).toBeInTheDocument();
    expect(screen.getByText("Succeeded (1)")).toBeInTheDocument();
    expect(screen.getByText("Rolled Back (1)")).toBeInTheDocument();
    expect(screen.getByText("openapi")).toBeInTheDocument();
    expect(screen.getByText("graphql")).toBeInTheDocument();
  });
});
