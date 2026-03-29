import type { ReactNode } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/__tests__/test-utils";

import ServiceDetailPage from "../page";

const {
  mockPush,
  mockBack,
  mockActivateVersion,
  mockDeleteVersion,
  mockSyncRoutes,
  mockReconcile,
} = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockBack: vi.fn(),
  mockActivateVersion: vi.fn(),
  mockDeleteVersion: vi.fn(),
  mockSyncRoutes: vi.fn(),
  mockReconcile: vi.fn(),
}));

const service = {
  service_id: "svc-1",
  name: "Billing API",
  protocol: "openapi",
  active_version: 2,
  version_count: 2,
  last_compiled: "2026-03-29T00:00:00Z",
};

const inactiveVersion = {
  service_id: "svc-1",
  version_number: 1,
  is_active: false,
  created_at: "2026-03-28T00:00:00Z",
  ir: {
    service_name: "Billing API v1",
    operations: [],
  },
};

const activeRouteConfig = {
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
};

const activeVersion = {
  service_id: "svc-1",
  version_number: 2,
  is_active: true,
  created_at: "2026-03-29T00:00:00Z",
  ir: {
    service_name: "Billing API IR",
    operations: [
      {
        id: "listInvoices",
        name: "List Invoices",
        description: "",
        params: [],
        risk: { risk_level: "safe", confidence: 1, source: "extractor" },
        tags: [],
        source: "extractor",
        confidence: 1,
        enabled: true,
      },
    ],
  },
  route_config: activeRouteConfig,
};

vi.mock("next/navigation", () => ({
  useParams: () => ({ serviceId: "svc-1" }),
  useRouter: () => ({
    push: mockPush,
    back: mockBack,
    replace: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

vi.mock("@/hooks/use-api", () => ({
  useService: () => ({
    data: service,
    isLoading: false,
    error: null,
  }),
  useArtifactVersions: () => ({
    data: {
      versions: [inactiveVersion, activeVersion],
    },
    isLoading: false,
  }),
}));

vi.mock("@/lib/api-client", () => ({
  artifactApi: {
    activateVersion: mockActivateVersion,
    deleteVersion: mockDeleteVersion,
  },
  gatewayApi: {
    syncRoutes: mockSyncRoutes,
    reconcile: mockReconcile,
  },
}));

vi.mock("@/components/services/protocol-badge", () => ({
  ProtocolBadge: ({ protocol }: { protocol: string }) => <span>{protocol}</span>,
}));

vi.mock("@/components/services/tool-card", () => ({
  ToolCard: ({ operation }: { operation: { name: string } }) => (
    <div>{operation.name}</div>
  ),
}));

vi.mock("@/components/services/ir-editor", () => ({
  IREditor: ({ ir }: { ir: { service_name?: string } }) => (
    <div data-testid="ir-editor">{ir.service_name}</div>
  ),
}));

vi.mock("@/components/services/version-diff-dialog", () => ({
  VersionDiffDialog: ({ trigger }: { trigger?: ReactNode }) =>
    trigger ?? <button type="button">Compare Versions</button>,
}));

vi.mock("@/components/review/review-status-badge", () => ({
  ReviewStatusBadge: () => <span>review-status</span>,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

describe("ServiceDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockActivateVersion.mockResolvedValue(activeVersion);
    mockDeleteVersion.mockResolvedValue(undefined);
    mockSyncRoutes.mockResolvedValue({
      route_ids: ["svc-1-active"],
      service_routes_synced: 1,
      service_routes_deleted: 0,
      previous_routes: {},
    });
    mockReconcile.mockResolvedValue({
      consumers_synced: 0,
      consumers_deleted: 0,
      policy_bindings_synced: 0,
      policy_bindings_deleted: 0,
      service_routes_synced: 1,
      service_routes_deleted: 0,
    });
  });

  it("wires header actions to navigation and tab selection", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ServiceDetailPage />);

    await user.click(screen.getByRole("button", { name: "View IR" }));
    expect(screen.getByTestId("ir-editor")).toHaveTextContent("Billing API IR");

    await user.click(screen.getByRole("button", { name: "Manage Access" }));
    expect(mockPush).toHaveBeenCalledWith("/policies?resource_id=svc-1");

    await user.click(screen.getByRole("button", { name: "Recompile" }));
    expect(mockPush).toHaveBeenCalledWith(
      "/compilations/new?service_name=Billing%20API",
    );
  });

  it("activates and deletes artifact versions from the Versions tab", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ServiceDetailPage />);

    await user.click(screen.getByRole("tab", { name: "Versions" }));
    await user.click(screen.getByRole("button", { name: "Activate" }));

    await waitFor(() => {
      expect(mockActivateVersion).toHaveBeenCalledWith("svc-1", 1);
    });

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mockDeleteVersion).toHaveBeenCalledWith("svc-1", 1);
    });
  });

  it("syncs and reconciles gateway routes from the Gateway tab", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ServiceDetailPage />);

    await user.click(screen.getByRole("tab", { name: "Gateway" }));
    await user.click(screen.getByRole("button", { name: "Sync" }));

    await waitFor(() => {
      expect(mockSyncRoutes).toHaveBeenCalledWith({
        route_config: activeRouteConfig,
        previous_routes: {},
      });
    });

    await user.click(screen.getByRole("button", { name: "Reconcile" }));
    await waitFor(() => {
      expect(mockReconcile).toHaveBeenCalled();
    });
  });
});
