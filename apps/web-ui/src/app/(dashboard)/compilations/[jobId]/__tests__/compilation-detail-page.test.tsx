import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CompilationDetailPage from "../page";

const {
  mockUseCompilation,
  mockUseCompilationEvents,
  mockRetryMutate,
  mockRollbackMutate,
} = vi.hoisted(() => ({
  mockUseCompilation: vi.fn(),
  mockUseCompilationEvents: vi.fn(),
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
});
