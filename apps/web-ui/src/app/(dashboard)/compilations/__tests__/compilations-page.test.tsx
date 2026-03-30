import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CompilationsPage from "../page";

const {
  mockUseCompilations,
  mockRetryMutate,
  mockRollbackMutate,
  mockPush,
} = vi.hoisted(() => ({
  mockUseCompilations: vi.fn(),
  mockRetryMutate: vi.fn(),
  mockRollbackMutate: vi.fn(),
  mockPush: vi.fn(),
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

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
    prefetch: vi.fn(),
  }),
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
  beforeEach(() => {
    vi.clearAllMocks();
  });

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

  it("shows a load error instead of the empty state when the query fails", async () => {
    const refetch = vi.fn();
    const user = userEvent.setup();
    mockUseCompilations.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("jobs unavailable"),
      refetch,
    });

    render(<CompilationsPage />);

    expect(screen.getByText("Failed to load compilation jobs")).toBeInTheDocument();
    expect(screen.getByText("jobs unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No compilation jobs found")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("navigates to the new retry job after a successful action", async () => {
    const user = userEvent.setup();
    mockUseCompilations.mockReturnValue({
      data: [
        {
          job_id: "job-failed",
          status: "failed",
          current_stage: "deploy",
          failed_stage: "deploy",
          created_at: "2026-03-29T00:00:00Z",
        },
      ],
      isLoading: false,
      refetch: vi.fn(),
    });
    mockRetryMutate.mockImplementation(
      (
        _variables: { jobId: string; fromStage?: string },
        options?: { onSuccess?: (job: { job_id: string }) => void },
      ) => options?.onSuccess?.({ job_id: "job-retry-2" }),
    );

    render(<CompilationsPage />);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Retry" }));

    expect(mockPush).toHaveBeenCalledWith("/compilations/job-retry-2");
  });

  it("navigates to the new rollback job after a successful action", async () => {
    const user = userEvent.setup();
    mockUseCompilations.mockReturnValue({
      data: [
        {
          job_id: "job-succeeded",
          status: "succeeded",
          current_stage: "register",
          created_at: "2026-03-29T00:00:00Z",
          completed_at: "2026-03-29T00:05:00Z",
        },
      ],
      isLoading: false,
      refetch: vi.fn(),
    });
    mockRollbackMutate.mockImplementation(
      (
        _jobId: string,
        options?: { onSuccess?: (job: { job_id: string }) => void },
      ) => options?.onSuccess?.({ job_id: "job-rollback-2" }),
    );

    render(<CompilationsPage />);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByText("Rollback"));

    expect(mockPush).toHaveBeenCalledWith("/compilations/job-rollback-2");
  });
});
