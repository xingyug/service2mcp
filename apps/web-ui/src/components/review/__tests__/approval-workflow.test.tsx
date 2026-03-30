import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalWorkflow } from "../approval-workflow";

const {
  mockTransition,
  mockListVersions,
  mockActivateVersion,
  mockGetVersion,
  mockListRoutes,
  mockSyncRoutes,
  mockRollbackRoutes,
  toastSuccess,
  toastError,
} = vi.hoisted(() => ({
  mockTransition: vi.fn(),
  mockListVersions: vi.fn(),
  mockActivateVersion: vi.fn(),
  mockGetVersion: vi.fn(),
  mockListRoutes: vi.fn(),
  mockSyncRoutes: vi.fn(),
  mockRollbackRoutes: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({
      user: {
        username: "alice",
        subject: "alice@example.com",
      },
    }),
}));

vi.mock("@/stores/workflow-store", async () => {
  const actual = await vi.importActual<typeof import("@/stores/workflow-store")>(
    "@/stores/workflow-store",
  );
  return {
    ...actual,
    useWorkflowStore: (selector: (state: Record<string, unknown>) => unknown) =>
      selector({
        transition: mockTransition,
      }),
  };
});

vi.mock("@/lib/api-client", () => ({
  artifactApi: {
    listVersions: mockListVersions,
    activateVersion: mockActivateVersion,
    getVersion: mockGetVersion,
  },
  gatewayApi: {
    listRoutes: mockListRoutes,
    syncRoutes: mockSyncRoutes,
    rollbackRoutes: mockRollbackRoutes,
  },
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
  },
}));

describe("ApprovalWorkflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("rolls back the previous active version if publish transition fails", async () => {
    const user = userEvent.setup();
    mockListVersions.mockResolvedValue({
      versions: [
        { version_number: 1, is_active: true },
        { version_number: 2, is_active: false },
      ],
    });
    mockActivateVersion.mockResolvedValue(undefined);
    mockTransition.mockRejectedValue(new Error("Transition failed"));

    render(
      <ApprovalWorkflow
        serviceId="svc-1"
        versionNumber={2}
        scope={{ tenant: "team-a", environment: "prod" }}
        currentState="approved"
      />,
    );

    await user.click(screen.getByRole("button", { name: "Publish" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Publish" }));

    await waitFor(() => {
      expect(mockActivateVersion).toHaveBeenNthCalledWith(1, "svc-1", 2, {
        tenant: "team-a",
        environment: "prod",
      });
      expect(mockActivateVersion).toHaveBeenNthCalledWith(2, "svc-1", 1, {
        tenant: "team-a",
        environment: "prod",
      });
      expect(mockTransition).toHaveBeenCalledWith(
        "svc-1",
        2,
        "published",
        "alice",
        undefined,
        { tenant: "team-a", environment: "prod" },
      );
    });
    expect(toastError).toHaveBeenCalledWith("Transition failed");
  });

  it("deploys with the full route config and rolls it back if the workflow transition fails", async () => {
    const user = userEvent.setup();
    const routeConfig = {
      service_id: "svc-1",
      version_number: 2,
      default_route: {
        route_id: "svc-1-active",
        target_service: { name: "billing-runtime-v2", namespace: "runtime-system", port: 8003 },
      },
    };
    const previousRoutes = {
      "svc-1-active": {
        route_id: "svc-1-active",
        service_id: "svc-1",
        route_type: "default",
        namespace: "runtime-system",
        target_service: { name: "billing-runtime-v1" },
      },
    };

    mockGetVersion.mockResolvedValue({
      service_id: "svc-1",
      version_number: 2,
      route_config: routeConfig,
    });
    mockListRoutes.mockResolvedValue({
      routes: Object.values(previousRoutes),
    });
    mockSyncRoutes.mockResolvedValue({
      route_ids: ["svc-1-active"],
      service_routes_synced: 1,
      service_routes_deleted: 0,
      previous_routes: previousRoutes,
    });
    mockTransition.mockRejectedValue(new Error("Transition failed"));

    render(
      <ApprovalWorkflow
        serviceId="svc-1"
        versionNumber={2}
        currentState="published"
      />,
    );

    await user.click(screen.getByRole("button", { name: "Deploy" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Deploy" }));

    await waitFor(() => {
      expect(mockSyncRoutes).toHaveBeenCalledWith({
        route_config: routeConfig,
        previous_routes: previousRoutes,
      });
      expect(mockRollbackRoutes).toHaveBeenCalledWith({
        route_config: routeConfig,
        previous_routes: previousRoutes,
      });
    });
    expect(toastError).toHaveBeenCalledWith("Transition failed");
  });
});
