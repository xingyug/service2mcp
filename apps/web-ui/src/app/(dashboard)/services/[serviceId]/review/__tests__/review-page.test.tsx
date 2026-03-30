import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/__tests__/test-utils";

import ReviewPage from "../page";

const {
  mockBack,
  mockLoadWorkflow,
  mockGetWorkflow,
  mockUseService,
  mockUseArtifactVersions,
  mockUpdateVersion,
  toastSuccess,
  toastError,
} = vi.hoisted(() => ({
  mockBack: vi.fn(),
  mockLoadWorkflow: vi.fn(),
  mockGetWorkflow: vi.fn(),
  mockUseService: vi.fn(),
  mockUseArtifactVersions: vi.fn(),
  mockUpdateVersion: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

let search = "tenant=team-a&environment=prod&version=7";

const service = {
  service_id: "svc-1",
  name: "Billing API",
  protocol: "openapi",
  active_version: 7,
  version_count: 3,
  last_compiled: "2026-03-29T00:00:00Z",
};

const versions = [
  {
    service_id: "svc-1",
    version_number: 1,
    is_active: false,
    created_at: "2026-03-27T00:00:00Z",
    ir: {
      service_name: "Billing API v1",
      operations: [],
      metadata: {},
      created_at: "2026-03-27T00:00:00Z",
    },
  },
  {
    service_id: "svc-1",
    version_number: 3,
    is_active: false,
    created_at: "2026-03-28T00:00:00Z",
    ir: {
      service_name: "Billing API v3",
      operations: [],
      metadata: {},
      created_at: "2026-03-28T00:00:00Z",
    },
  },
  {
    service_id: "svc-1",
    version_number: 7,
    is_active: true,
    created_at: "2026-03-29T00:00:00Z",
    ir: {
      service_name: "Billing API v7",
      operations: [],
      metadata: {},
      created_at: "2026-03-29T00:00:00Z",
    },
  },
];

vi.mock("next/navigation", () => ({
  useParams: () => ({ serviceId: "svc-1" }),
  useRouter: () => ({
    back: mockBack,
    push: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(search),
}));

vi.mock("@/hooks/use-api", () => ({
  useService: mockUseService,
  useArtifactVersions: mockUseArtifactVersions,
}));

vi.mock("@/stores/workflow-store", async () => {
  const actual = await vi.importActual<typeof import("@/stores/workflow-store")>(
    "@/stores/workflow-store",
  );
  return {
    ...actual,
    useWorkflowStore: (selector: (state: Record<string, unknown>) => unknown) =>
      selector({
        loadWorkflow: mockLoadWorkflow,
        getWorkflow: mockGetWorkflow,
      }),
  };
});

vi.mock("@/lib/api-client", () => ({
  artifactApi: {
    updateVersion: mockUpdateVersion,
  },
}));

vi.mock("@/components/review/approval-workflow", () => ({
  ApprovalWorkflow: () => <div>approval-workflow</div>,
}));

vi.mock("@/components/review/review-panel", () => ({
  ReviewPanel: () => <div>review-panel</div>,
}));

vi.mock("@/components/review/approval-history", () => ({
  ApprovalHistory: () => <div>approval-history</div>,
}));

vi.mock("@/components/review/review-status-badge", () => ({
  ReviewStateBadge: ({ state }: { state: string }) => <span>{state}</span>,
}));

vi.mock("@/components/services/protocol-badge", () => ({
  ProtocolBadge: ({ protocol }: { protocol: string }) => <span>{protocol}</span>,
}));

vi.mock("@/components/services/version-diff", () => ({
  VersionDiff: ({
    fromVersion,
    toVersion,
  }: {
    fromVersion: number;
    toVersion: number;
  }) => <div>{`diff-${fromVersion}-${toVersion}`}</div>,
}));

vi.mock("@/components/services/ir-editor", () => ({
  IREditor: ({
    ir,
    onSave,
  }: {
    ir: { service_name?: string };
    onSave?: (updatedIR: {
      service_name: string;
      operations: [];
      metadata: Record<string, unknown>;
      created_at: string;
    }) => void | Promise<void>;
  }) => (
    <div>
      <span>{ir.service_name}</span>
      {onSave && (
        <button
          type="button"
          onClick={() =>
            void onSave({
              service_name: "Updated Billing API",
              operations: [],
              metadata: {},
              created_at: "2026-03-29T01:00:00Z",
            })
          }
        >
          Save IR
        </button>
      )}
    </div>
  ),
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
  },
}));

describe("ReviewPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    search = "tenant=team-a&environment=prod&version=7";
    mockUseService.mockReturnValue({ data: service, isLoading: false });
    mockUseArtifactVersions.mockReturnValue({
      data: { versions },
      isLoading: false,
    });
    mockLoadWorkflow.mockResolvedValue(undefined);
    mockGetWorkflow.mockReturnValue({
      state: "draft",
      history: [],
      reviewNotes: null,
    });
    mockUpdateVersion.mockResolvedValue(versions[2]);
  });

  it("rejects invalid version query params before loading the workflow", () => {
    search = "tenant=team-a&environment=prod&version=foo";

    renderWithProviders(<ReviewPage />);

    expect(screen.getByText("Invalid review version")).toBeInTheDocument();
    expect(
      screen.getByText("Choose a positive integer version number."),
    ).toBeInTheDocument();
    expect(mockLoadWorkflow).not.toHaveBeenCalled();
  });

  it("shows artifact load errors and does not load workflow state", () => {
    mockUseArtifactVersions.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("versions unavailable"),
    });

    renderWithProviders(<ReviewPage />);

    expect(screen.getByText("Failed to load artifact versions")).toBeInTheDocument();
    expect(screen.getByText("versions unavailable")).toBeInTheDocument();
    expect(mockLoadWorkflow).not.toHaveBeenCalled();
    expect(screen.queryByText("approval-workflow")).not.toBeInTheDocument();
  });

  it("uses the nearest real previous version for sparse diff histories", async () => {
    const user = userEvent.setup();

    renderWithProviders(<ReviewPage />);

    await waitFor(() => {
      expect(mockLoadWorkflow).toHaveBeenCalledWith("svc-1", 7, {
        tenant: "team-a",
        environment: "prod",
      });
      expect(mockGetWorkflow).toHaveBeenCalledWith("svc-1", 7, {
        tenant: "team-a",
        environment: "prod",
      });
    });

    await user.click(screen.getByRole("tab", { name: "Diff" }));

    expect(screen.getByText("Version Diff — v3 → v7")).toBeInTheDocument();
    expect(screen.getByText("diff-3-7")).toBeInTheDocument();
  });

  it("persists IR edits for editable review states", async () => {
    const user = userEvent.setup();

    renderWithProviders(<ReviewPage />);

    await user.click(screen.getByRole("tab", { name: "IR" }));
    await user.click(screen.getByRole("button", { name: "Save IR" }));

    await waitFor(() => {
      expect(mockUpdateVersion).toHaveBeenCalledWith(
        "svc-1",
        7,
        {
          ir_json: {
            service_name: "Updated Billing API",
            operations: [],
            metadata: {},
            created_at: "2026-03-29T01:00:00Z",
          },
        },
        { tenant: "team-a", environment: "prod" },
      );
    });
    expect(toastSuccess).toHaveBeenCalledWith("IR updated successfully.");
  });
});
