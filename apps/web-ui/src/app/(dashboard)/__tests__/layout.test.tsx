import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/ui/sidebar", () => ({
  SidebarProvider: ({ children }: { children: ReactNode }) => (
    <div data-testid="sidebar-provider">{children}</div>
  ),
  SidebarInset: ({ children }: { children: ReactNode }) => (
    <div data-testid="sidebar-inset">{children}</div>
  ),
  SidebarTrigger: () => <button type="button">Toggle sidebar</button>,
}));

vi.mock("@/components/app-sidebar", () => ({
  AppSidebar: () => <aside>App sidebar</aside>,
}));

vi.mock("@/components/auth-guard", () => ({
  AuthGuard: ({ children }: { children: ReactNode }) => (
    <div data-testid="auth-guard">{children}</div>
  ),
}));

vi.mock("@/components/ui/separator", () => ({
  Separator: () => <div data-testid="separator" />,
}));

vi.mock("@/components/breadcrumbs", () => ({
  Breadcrumbs: () => <nav>Breadcrumb trail</nav>,
}));

import DashboardLayout from "../layout";

describe("DashboardLayout", () => {
  it("renders the guarded dashboard shell around page content", () => {
    render(
      <DashboardLayout>
        <div>Dashboard body</div>
      </DashboardLayout>,
    );

    expect(screen.getByTestId("auth-guard")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-provider")).toBeInTheDocument();
    expect(screen.getByText("App sidebar")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Toggle sidebar" })).toBeInTheDocument();
    expect(screen.getByText("Breadcrumb trail")).toBeInTheDocument();
    expect(screen.getByTestId("separator")).toBeInTheDocument();
    expect(screen.getByText("Dashboard body")).toBeInTheDocument();
  });
});
