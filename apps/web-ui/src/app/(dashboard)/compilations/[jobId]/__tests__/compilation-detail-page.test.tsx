import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CompilationDetailPage from "../page";

const {
  mockUseCompilation,
  mockUseCompilationEvents,
  mockRetryMutate,
  mockRollbackMutate,
  mockPush,
} = vi.hoisted(() => ({
  mockUseCompilation: vi.fn(),
  mockUseCompilationEvents: vi.fn(),
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
  useCompilation: mockUseCompilation,
  useRetryCompilation: () => ({
    mutate: mockRetryMutate,
    isPending: false,
  }),
  useRollbackCompilation: () => ({
    mutate: mockRollbackMutate,
    isPending: false,
  }),
}));

vi.mock("react", async () => {
  const actual = await vi.importActual<typeof import("react")>("react");
  return {
    ...actual,
    use: () => ({ jobId: "job-1" }),
  };
});

vi.mock("@/lib/hooks/use-sse", () => ({
  useCompilationEvents: mockUseCompilationEvents,
}));

vi.mock("@/components/compilations/event-log", () => ({
  EventLog: () => <div>event-log</div>,
}));

describe("CompilationDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseCompilationEvents.mockReturnValue({
      events: [],
      isConnected: true,
      error: null,
    });
  });

  function renderDetailPage() {
    return render(
      <CompilationDetailPage params={Promise.resolve({ jobId: "job-1" })} />,
    );
  }

  it("subscribes to SSE for running jobs", async () => {
    mockUseCompilation.mockReturnValue({
      data: {
        job_id: "job-1",
        status: "running",
        current_stage: "extract",
        created_at: "2026-03-29T00:00:00Z",
      },
      isLoading: false,
    });

    renderDetailPage();

    expect(await screen.findByText("event-log")).toBeInTheDocument();
    expect(mockUseCompilationEvents).toHaveBeenCalledWith("job-1");
  });

  it("shows rollback and artifacts for succeeded jobs", async () => {
    mockUseCompilation.mockReturnValue({
      data: {
        job_id: "job-1",
        status: "succeeded",
        current_stage: "register",
        created_at: "2026-03-29T00:00:00Z",
        completed_at: "2026-03-29T00:05:00Z",
        tenant: "team-a",
        environment: "prod",
        artifacts: {
          ir_id: "svc-1",
          image_digest: "sha256:abc",
          deployment_id: "deploy-1",
        },
      },
      isLoading: false,
    });

    renderDetailPage();

    expect(await screen.findByRole("button", { name: /Rollback/i })).toBeInTheDocument();
    expect(screen.getByText("Artifacts")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /svc-1/i })).toHaveAttribute(
      "href",
      "/services/svc-1?tenant=team-a&environment=prod",
    );
  });

  it("shows retry controls and error details for failed jobs", async () => {
    mockUseCompilation.mockReturnValue({
      data: {
        job_id: "job-1",
        status: "failed",
        current_stage: "deploy",
        failed_stage: "deploy",
        created_at: "2026-03-29T00:00:00Z",
        completed_at: "2026-03-29T00:05:00Z",
        error_message: "boom",
      },
      isLoading: false,
    });

    renderDetailPage();

    expect(
      await screen.findAllByRole("button", { name: /Retry from deploy/i }),
    ).toHaveLength(2);
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("shows a load error instead of treating the job as missing", async () => {
    mockUseCompilation.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("detail unavailable"),
    });

    renderDetailPage();

    expect(
      await screen.findByText("Failed to load compilation job"),
    ).toBeInTheDocument();
    expect(screen.getByText("detail unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Compilation job not found.")).not.toBeInTheDocument();
  });

  it("navigates to the new retry job after retry succeeds", async () => {
    const user = userEvent.setup();
    mockUseCompilation.mockReturnValue({
      data: {
        job_id: "job-1",
        status: "failed",
        current_stage: "deploy",
        failed_stage: "deploy",
        created_at: "2026-03-29T00:00:00Z",
        completed_at: "2026-03-29T00:05:00Z",
        error_message: "boom",
      },
      isLoading: false,
    });
    mockRetryMutate.mockImplementation(
      (
        _variables: { jobId: string; fromStage?: string },
        options?: { onSuccess?: (job: { job_id: string }) => void },
      ) => options?.onSuccess?.({ job_id: "job-2" }),
    );

    renderDetailPage();

    await user.click(
      (await screen.findAllByRole("button", { name: /Retry from deploy/i }))[0],
    );

    expect(mockPush).toHaveBeenCalledWith("/compilations/job-2");
  });

  it("navigates to the new rollback job after rollback succeeds", async () => {
    const user = userEvent.setup();
    mockUseCompilation.mockReturnValue({
      data: {
        job_id: "job-1",
        status: "succeeded",
        current_stage: "register",
        created_at: "2026-03-29T00:00:00Z",
        completed_at: "2026-03-29T00:05:00Z",
      },
      isLoading: false,
    });
    mockRollbackMutate.mockImplementation(
      (
        _jobId: string,
        options?: { onSuccess?: (job: { job_id: string }) => void },
      ) => options?.onSuccess?.({ job_id: "job-3" }),
    );

    renderDetailPage();

    await user.click(await screen.findByRole("button", { name: /Rollback/i }));

    expect(mockPush).toHaveBeenCalledWith("/compilations/job-3");
  });
});
