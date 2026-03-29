import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CompilationsPage from "../page";

const {
  mockUseCompilations,
  mockRetryMutate,
  mockRollbackMutate,
} = vi.hoisted(() => ({
  mockUseCompilations: vi.fn(),
  mockRetryMutate: vi.fn(),
  mockRollbackMutate: vi.fn(),
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
  useCompilations: mockUseCompilations,
  useRetryCompilation: () => ({
    mutate: mockRetryMutate,
    isPending: false,
  }),
  useRollbackCompilation: () => ({
    mutate: mockRollbackMutate,
    isPending: false,
  }),
}));

describe("CompilationsPage", () => {
  it("treats running jobs as in-progress for automatic polling", () => {
    mockUseCompilations.mockReturnValue({
      data: [
        {
          job_id: "job-running",
          status: "running",
          current_stage: "extract",
          created_at: "2026-03-29T00:00:00Z",
        },
      ],
      isLoading: false,
      refetch: vi.fn(),
    });

    render(<CompilationsPage />);

    const options = mockUseCompilations.mock.calls[0][0];
    expect(
      options.refetchInterval({
        state: {
          data: [
            {
              job_id: "job-running",
              status: "running",
              created_at: "2026-03-29T00:00:00Z",
            },
          ],
        },
      }),
    ).toBe(10_000);
  });

  it("does not poll for terminal succeeded jobs", () => {
    mockUseCompilations.mockReturnValue({
      data: [
        {
          job_id: "job-success",
          status: "succeeded",
          current_stage: "register",
          completed_at: "2026-03-29T00:10:00Z",
          created_at: "2026-03-29T00:00:00Z",
        },
      ],
      isLoading: false,
      refetch: vi.fn(),
    });

    render(<CompilationsPage />);

    const options = mockUseCompilations.mock.calls[0][0];
    expect(
      options.refetchInterval({
        state: {
          data: [
            {
              job_id: "job-success",
              status: "succeeded",
              created_at: "2026-03-29T00:00:00Z",
            },
          ],
        },
      }),
    ).toBe(false);

    expect(screen.getByText("Succeeded")).toBeInTheDocument();
  });
});
