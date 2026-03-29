import type { ReactNode } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/__tests__/test-utils";

import VersionsPage from "../page";

const {
  mockPush,
  mockActivateVersion,
  mockDeleteVersion,
  mockSuccessToast,
  mockErrorToast,
} = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockActivateVersion: vi.fn(),
  mockDeleteVersion: vi.fn(),
  mockSuccessToast: vi.fn(),
  mockErrorToast: vi.fn(),
}));

const service = {
  service_id: "svc-1",
  name: "Billing API",
  protocol: "openapi",
  active_version: 2,
  version_count: 2,
  last_compiled: "2026-03-29T00:00:00Z",
};

const versions = [
  {
    service_id: "svc-1",
    version_number: 1,
    is_active: false,
    created_at: "2026-03-28T00:00:00Z",
    ir: {
      service_name: "Billing API v1",
      operations: [],
    },
  },
  {
    service_id: "svc-1",
    version_number: 2,
    is_active: true,
    created_at: "2026-03-29T00:00:00Z",
    ir: {
      service_name: "Billing API v2",
      operations: [],
    },
  },
];

vi.mock("next/navigation", () => ({
  useParams: () => ({ serviceId: "svc-1" }),
  useRouter: () => ({
    push: mockPush,
    back: vi.fn(),
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
    data: { versions },
    isLoading: false,
  }),
}));

vi.mock("@/lib/api-client", () => ({
  artifactApi: {
    activateVersion: mockActivateVersion,
    deleteVersion: mockDeleteVersion,
  },
}));

vi.mock("@/components/services/version-diff-dialog", () => ({
  VersionDiffDialog: ({ trigger }: { trigger?: ReactNode }) =>
    trigger ?? <button type="button">Compare Versions</button>,
}));

vi.mock("@/components/services/ir-editor", () => ({
  IREditor: ({ ir }: { ir: { service_name?: string } }) => (
    <div data-testid="ir-editor">{ir.service_name}</div>
  ),
}));

vi.mock("sonner", () => ({
  toast: {
    success: mockSuccessToast,
    error: mockErrorToast,
  },
}));

describe("VersionsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockActivateVersion.mockResolvedValue(undefined);
    mockDeleteVersion.mockResolvedValue(undefined);
  });

  it("activates an inactive version from the versions table", async () => {
    const user = userEvent.setup();

    renderWithProviders(<VersionsPage />);

    const versionRow = screen.getByText("v1").closest("tr");
    expect(versionRow).not.toBeNull();

    await user.click(
      within(versionRow as HTMLElement).getByRole("button", {
        name: "Activate",
      }),
    );

    await waitFor(() => {
      expect(mockActivateVersion).toHaveBeenCalledWith("svc-1", 1);
    });
    expect(mockSuccessToast).toHaveBeenCalledWith("Activated version v1.");
  });

  it("deletes an inactive version through the confirmation dialog", async () => {
    const user = userEvent.setup();

    renderWithProviders(<VersionsPage />);

    const versionRow = screen.getByText("v1").closest("tr");
    expect(versionRow).not.toBeNull();

    await user.click(
      within(versionRow as HTMLElement).getByRole("button", { name: "Delete" }),
    );

    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mockDeleteVersion).toHaveBeenCalledWith("svc-1", 1);
    });
    expect(mockSuccessToast).toHaveBeenCalledWith("Deleted version v1.");
  });
});
