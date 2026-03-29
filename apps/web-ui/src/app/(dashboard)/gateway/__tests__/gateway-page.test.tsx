import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/__tests__/test-utils";

import GatewayPage from "../page";

const {
  mockListVersions,
  mockListRoutes,
  mockSyncRoutes,
  mockRollbackRoutes,
  mockDeleteRoutes,
  mockReconcile,
  mockSuccessToast,
  mockErrorToast,
} = vi.hoisted(() => ({
  mockListVersions: vi.fn(),
  mockListRoutes: vi.fn(),
  mockSyncRoutes: vi.fn(),
  mockRollbackRoutes: vi.fn(),
  mockDeleteRoutes: vi.fn(),
  mockReconcile: vi.fn(),
  mockSuccessToast: vi.fn(),
  mockErrorToast: vi.fn(),
}));

const service = {
  service_id: "svc-1",
  name: "Billing API",
  protocol: "openapi",
  active_version: 2,
  version_count: 2,
  last_compiled: "2026-03-29T02:00:00Z",
};

const versionOneRouteConfig = {
  service_id: "svc-1",
  service_name: "Billing API",
  namespace: "runtime-system",
  version_number: 1,
  default_route: {
    route_id: "svc-1-active",
    target_service: {
      name: "billing-runtime-v1",
      namespace: "runtime-system",
      port: 8003,
    },
  },
  version_route: {
    route_id: "svc-1-v1",
    target_service: {
      name: "billing-runtime-v1",
      namespace: "runtime-system",
      port: 8003,
    },
    match: {
      headers: {
        "x-tool-compiler-version": "1",
      },
    },
  },
};

const versionTwoRouteConfig = {
  service_id: "svc-1",
  service_name: "Billing API",
  namespace: "runtime-system",
  version_number: 2,
  default_route: {
    route_id: "svc-1-active",
    target_service: {
      name: "billing-runtime-v2",
      namespace: "runtime-system",
      port: 8003,
    },
  },
  version_route: {
    route_id: "svc-1-v2",
    target_service: {
      name: "billing-runtime-v2",
      namespace: "runtime-system",
      port: 8003,
    },
    match: {
      headers: {
        "x-tool-compiler-version": "2",
      },
    },
  },
};

const versions = [
  {
    service_id: "svc-1",
    version_number: 1,
    is_active: false,
    created_at: "2026-03-29T00:00:00Z",
    ir: {} as never,
    route_config: versionOneRouteConfig,
  },
  {
    service_id: "svc-1",
    version_number: 2,
    is_active: true,
    created_at: "2026-03-29T01:00:00Z",
    ir: {} as never,
    route_config: versionTwoRouteConfig,
  },
];

vi.mock("@/hooks/use-api", () => ({
  useServices: () => ({
    data: { services: [service] },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/lib/api-client", () => ({
  artifactApi: {
    listVersions: mockListVersions,
  },
  gatewayApi: {
    listRoutes: mockListRoutes,
    syncRoutes: mockSyncRoutes,
    rollbackRoutes: mockRollbackRoutes,
    deleteRoutes: mockDeleteRoutes,
    reconcile: mockReconcile,
  },
}));

vi.mock("sonner", () => ({
  toast: {
    success: mockSuccessToast,
    error: mockErrorToast,
  },
}));

describe("GatewayPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListVersions.mockResolvedValue({ versions });
    mockListRoutes.mockResolvedValue({
      routes: [
        {
          route_id: "svc-1-active",
          route_type: "default",
          service_id: "svc-1",
          service_name: "Billing API",
          namespace: "runtime-system",
          target_service: {
            name: "billing-runtime-v2",
            namespace: "runtime-system",
            port: 8003,
          },
          version_number: 2,
        },
        {
          route_id: "svc-1-v2",
          route_type: "version",
          service_id: "svc-1",
          service_name: "Billing API",
          namespace: "runtime-system",
          target_service: {
            name: "billing-runtime-v2",
            namespace: "runtime-system",
            port: 8003,
          },
          version_number: 2,
          match: {
            headers: {
              "x-tool-compiler-version": "2",
            },
          },
        },
      ],
    });
    mockSyncRoutes.mockResolvedValue({
      route_ids: ["svc-1-active", "svc-1-v2"],
      service_routes_synced: 2,
      service_routes_deleted: 0,
      previous_routes: {},
    });
    mockRollbackRoutes.mockResolvedValue({
      route_ids: ["svc-1-active", "svc-1-v2"],
      service_routes_synced: 2,
      service_routes_deleted: 0,
      previous_routes: {},
    });
    mockDeleteRoutes.mockResolvedValue({
      route_ids: ["svc-1-active", "svc-1-v2"],
      service_routes_synced: 0,
      service_routes_deleted: 2,
      previous_routes: {},
    });
    mockReconcile.mockResolvedValue({
      consumers_synced: 0,
      consumers_deleted: 0,
      policy_bindings_synced: 0,
      policy_bindings_deleted: 0,
      service_routes_synced: 0,
      service_routes_deleted: 0,
    });
  });

  it("derives route status from real gateway routes and shows real deployment history", async () => {
    const user = userEvent.setup();

    renderWithProviders(<GatewayPage />);

    await waitFor(() => {
      expect(mockListVersions).toHaveBeenCalledWith("svc-1");
    });
    await waitFor(() => {
      expect(mockListRoutes).toHaveBeenCalled();
    });

    expect(screen.getByText("Billing API")).toBeInTheDocument();
    expect(screen.getAllByText("Synced").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /Deployment History/i }));
    expect(screen.getByText("v1 → v2")).toBeInTheDocument();
  });

  it(
    "syncs and rolls back using the selected version route configs",
    async () => {
      const user = userEvent.setup();

      renderWithProviders(<GatewayPage />);

    await waitFor(() => {
      expect(mockListVersions).toHaveBeenCalledWith("svc-1");
    });

    await user.click(screen.getAllByRole("button", { name: "Sync Routes" })[0]);
    let dialog = screen.getByRole("dialog");
    const serviceIdInput = within(dialog).getByPlaceholderText("Enter service ID");
    const versionInput = within(dialog).getByPlaceholderText("Version number");
    await user.type(serviceIdInput, "svc-1");
    await user.clear(versionInput);
    await user.type(versionInput, "2");
    await user.click(within(dialog).getByRole("button", { name: "Sync Routes" }));

    await waitFor(() => {
      expect(mockSyncRoutes).toHaveBeenCalledWith({
        route_config: versionTwoRouteConfig,
        previous_routes: {},
      });
    });

    await user.click(screen.getByRole("button", { name: "Rollback" }));
    dialog = screen.getByRole("dialog");
    const rollbackServiceIdInput =
      within(dialog).getByPlaceholderText("Enter service ID");
    const targetVersionInput =
      within(dialog).getByPlaceholderText("Previous version number");
    await user.type(rollbackServiceIdInput, "svc-1");
    await user.clear(targetVersionInput);
    await user.type(targetVersionInput, "1");
    await user.click(within(dialog).getByRole("button", { name: "Rollback" }));

      await waitFor(() => {
        expect(mockRollbackRoutes).toHaveBeenCalledWith({
          route_config: versionTwoRouteConfig,
          previous_routes: {
            "svc-1-active": {
              route_id: "svc-1-active",
              route_type: "default",
              service_id: "svc-1",
              service_name: "Billing API",
              namespace: "runtime-system",
              target_service: {
                name: "billing-runtime-v1",
                namespace: "runtime-system",
                port: 8003,
              },
              version_number: 1,
            },
            "svc-1-v1": {
              route_id: "svc-1-v1",
              route_type: "version",
              service_id: "svc-1",
              service_name: "Billing API",
              namespace: "runtime-system",
              target_service: {
                name: "billing-runtime-v1",
                namespace: "runtime-system",
                port: 8003,
              },
              version_number: 1,
              match: {
                headers: {
                  "x-tool-compiler-version": "1",
                },
              },
            },
          },
        });
      });
    },
    15000,
  );
});
