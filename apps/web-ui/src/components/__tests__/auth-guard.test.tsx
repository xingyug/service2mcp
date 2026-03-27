import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AuthGuard } from "../auth-guard";

// Mock next/navigation (also in setup.ts, but we override useRouter here)
const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: mockReplace,
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

// Mock auth store
vi.mock("@/stores/auth-store", () => ({
  useAuthStore: vi.fn(),
}));

import { useAuthStore } from "@/stores/auth-store";

const mockedUseAuthStore = vi.mocked(useAuthStore);

describe("AuthGuard", () => {
  beforeEach(() => {
    mockReplace.mockClear();
  });

  it("renders children when authenticated", () => {
    mockedUseAuthStore.mockImplementation((selector: (s: { isAuthenticated: boolean }) => boolean) =>
      selector({ isAuthenticated: true }),
    );
    render(
      <AuthGuard>
        <div>Protected Content</div>
      </AuthGuard>,
    );
    expect(screen.getByText("Protected Content")).toBeInTheDocument();
  });

  it("does not render children when not authenticated", () => {
    mockedUseAuthStore.mockImplementation((selector: (s: { isAuthenticated: boolean }) => boolean) =>
      selector({ isAuthenticated: false }),
    );
    render(
      <AuthGuard>
        <div>Protected Content</div>
      </AuthGuard>,
    );
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
  });

  it("redirects to /login when not authenticated", () => {
    mockedUseAuthStore.mockImplementation((selector: (s: { isAuthenticated: boolean }) => boolean) =>
      selector({ isAuthenticated: false }),
    );
    render(
      <AuthGuard>
        <div>Secret</div>
      </AuthGuard>,
    );
    expect(mockReplace).toHaveBeenCalledWith("/login");
  });

  it("does not redirect when authenticated", () => {
    mockedUseAuthStore.mockImplementation((selector: (s: { isAuthenticated: boolean }) => boolean) =>
      selector({ isAuthenticated: true }),
    );
    render(
      <AuthGuard>
        <div>Dashboard</div>
      </AuthGuard>,
    );
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("renders children element directly (not wrapped)", () => {
    mockedUseAuthStore.mockImplementation((selector: (s: { isAuthenticated: boolean }) => boolean) =>
      selector({ isAuthenticated: true }),
    );
    render(
      <AuthGuard>
        <span data-testid="child">Hello</span>
      </AuthGuard>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("handles multiple children when authenticated", () => {
    mockedUseAuthStore.mockImplementation((selector: (s: { isAuthenticated: boolean }) => boolean) =>
      selector({ isAuthenticated: true }),
    );
    render(
      <AuthGuard>
        <div>Child 1</div>
        <div>Child 2</div>
      </AuthGuard>,
    );
    expect(screen.getByText("Child 1")).toBeInTheDocument();
    expect(screen.getByText("Child 2")).toBeInTheDocument();
  });
});
