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
  mockListRoutes,
  mockSyncRoutes,
  mockReconcile,
  mockUseArtifactVersions,
} = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockBack: vi.fn(),
  mockActivateVersion: vi.fn(),
  mockDeleteVersion: vi.fn(),
  mockListRoutes: vi.fn(),
  mockSyncRoutes: vi.fn(),
  mockReconcile: vi.fn(),
  mockUseArtifactVersions: vi.fn(),
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
  useSearchParams: () => new URLSearchParams("tenant=team-a&environment=prod"),
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
  useArtifactVersions: mockUseArtifactVersions,
}));

vi.mock("@/lib/api-client", () => ({
  artifactApi: {
    activateVersion: mockActivateVersion,
    deleteVersion: mockDeleteVersion,
  },
  gatewayApi: {
    listRoutes: mockListRoutes,
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
    mockUseArtifactVersions.mockReturnValue({
      data: {
        versions: [inactiveVersion, activeVersion],
      },
      isLoading: false,
      error: null,
    });
    mockActivateVersion.mockResolvedValue(activeVersion);
    mockDeleteVersion.mockResolvedValue(undefined);
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
      ],
    });
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
      "/compilations/new?service_id=svc-1&service_name=Billing%20API",
    );
  });

  it("activates and deletes artifact versions from the Versions tab", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ServiceDetailPage />);

    await user.click(screen.getByRole("tab", { name: "Versions" }));
    await user.click(screen.getByRole("button", { name: "Activate" }));

    await waitFor(() => {
      expect(mockActivateVersion).toHaveBeenCalledWith("svc-1", 1, {
        tenant: "team-a",
        environment: "prod",
      });
    });

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mockDeleteVersion).toHaveBeenCalledWith("svc-1", 1, {
        tenant: "team-a",
        environment: "prod",
      });
    });
  });

  it("syncs and reconciles gateway routes from the Gateway tab using live routes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ServiceDetailPage />);

    await user.click(screen.getByRole("tab", { name: "Gateway" }));
    expect(await screen.findByText("Synced")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sync" }));

    await waitFor(() => {
      expect(mockSyncRoutes).toHaveBeenCalledWith({
        route_config: activeRouteConfig,
        previous_routes: {
          "svc-1-active": {
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
        },
      });
    });

    await user.click(screen.getByRole("button", { name: "Reconcile" }));
    await waitFor(() => {
      expect(mockReconcile).toHaveBeenCalled();
    });
  });

  it("shows drifted when live gateway routes are missing", async () => {
    const user = userEvent.setup();
    mockListRoutes.mockResolvedValueOnce({ routes: [] });

    renderWithProviders(<ServiceDetailPage />);

    await user.click(screen.getByRole("tab", { name: "Gateway" }));

    expect(await screen.findByText("Drifted")).toBeInTheDocument();
    expect(
      screen.getByText(/Live gateway routes do not match the stored route configuration/i),
    ).toBeInTheDocument();
  });

  it("shows artifact load errors instead of rendering version-dependent tabs", () => {
    mockUseArtifactVersions.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("versions unavailable"),
    });

    renderWithProviders(<ServiceDetailPage />);

    expect(screen.getByText("Failed to load artifact versions")).toBeInTheDocument();
    expect(screen.getByText("versions unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Versions" })).not.toBeInTheDocument();
  });
});
