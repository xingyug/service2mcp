import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AuditLogPage from "../page";

const {
  mockUseAuditLogs,
  mockAuditList,
  mockToastSuccess,
  mockToastError,
  mockAnchorClick,
} = vi.hoisted(() => ({
  mockUseAuditLogs: vi.fn(),
  mockAuditList: vi.fn(),
  mockToastSuccess: vi.fn(),
  mockToastError: vi.fn(),
  mockAnchorClick: vi.fn(),
}));

vi.mock("@/hooks/use-api", () => ({
  useAuditLogs: mockUseAuditLogs,
}));

vi.mock("@/lib/api-client", () => ({
  auditApi: {
    list: mockAuditList,
  },
}));

vi.mock("sonner", () => ({
  toast: {
    success: mockToastSuccess,
    error: mockToastError,
  },
}));

describe("AuditLogPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAuditLogs.mockReturnValue({
      data: {
        entries: [
          {
            id: "entry-1",
            actor: "alice",
            action: "policy.created",
            resource: "svc-1",
            detail: '{"ok":true}',
            timestamp: "2026-03-30T00:00:00Z",
          },
        ],
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockAuditList.mockResolvedValue({
      entries: Array.from({ length: 1002 }, (_, index) => ({
        id: `entry-${index + 1}`,
        actor: "alice",
        action: "policy.created",
        resource: "svc-1",
        detail: '{"ok":true}',
        timestamp: `2026-03-30T00:00:${String(index % 60).padStart(2, "0")}Z`,
      })),
    });

    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:export");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tagName: string) => {
      const element = originalCreateElement(tagName);
      if (tagName === "a") {
        Object.defineProperty(element, "click", {
          value: mockAnchorClick,
          configurable: true,
        });
      }
      return element;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("exports the full filtered audit dataset instead of the loaded subset", async () => {
    const user = userEvent.setup();

    render(<AuditLogPage />);

    await user.click(screen.getByRole("button", { name: "Export CSV" }));

    await waitFor(() =>
      expect(mockAuditList).toHaveBeenCalledWith(expect.anything(), {
        include_all: true,
      }),
    );
    expect(mockAnchorClick).toHaveBeenCalled();
    expect(mockToastSuccess).toHaveBeenCalledWith("Exported 1002 matching entries");
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
    expect(mockToastError).not.toHaveBeenCalled();
  });
});
