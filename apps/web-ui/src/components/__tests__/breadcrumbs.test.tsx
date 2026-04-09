import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { Breadcrumbs } from "../breadcrumbs";

// We need to override usePathname per test
const mockUsePathname = vi.fn(() => "/");

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => mockUsePathname(),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

describe("Breadcrumbs", () => {
  it('shows only "Dashboard" for root path', () => {
    mockUsePathname.mockReturnValue("/");
    render(<Breadcrumbs />);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  it("shows Dashboard and Services for /services", () => {
    mockUsePathname.mockReturnValue("/services");
    render(<Breadcrumbs />);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Services")).toBeInTheDocument();
  });

  it("shows full trail for nested path /services/123", () => {
    mockUsePathname.mockReturnValue("/services/some-id");
    render(<Breadcrumbs />);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Services")).toBeInTheDocument();
    expect(screen.getByText("Some-id")).toBeInTheDocument();
  });

  it("uses known segment labels for compilations", () => {
    mockUsePathname.mockReturnValue("/compilations");
    render(<Breadcrumbs />);
    expect(screen.getByText("Compilations")).toBeInTheDocument();
  });

  it("formats UUID segments as truncated", () => {
    mockUsePathname.mockReturnValue(
      "/services/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    );
    render(<Breadcrumbs />);
    expect(screen.getByText("a1b2c3d4…")).toBeInTheDocument();
  });

  it("capitalizes unknown segments", () => {
    mockUsePathname.mockReturnValue("/custom-page");
    render(<Breadcrumbs />);
    expect(screen.getByText("Custom-page")).toBeInTheDocument();
  });

  it("last crumb is not a link (BreadcrumbPage)", () => {
    mockUsePathname.mockReturnValue("/services");
    render(<Breadcrumbs />);
    // The last item should be rendered as a span (BreadcrumbPage), not a link
    const servicesEl = screen.getByText("Services");
    expect(servicesEl.closest("a")).toBeNull();
  });

  it("non-last crumbs are links", () => {
    mockUsePathname.mockReturnValue("/services/my-svc");
    render(<Breadcrumbs />);
    const dashboardLink = screen.getByText("Dashboard").closest("a");
    expect(dashboardLink).toHaveAttribute("href", "/");
    const servicesLink = screen.getByText("Services").closest("a");
    expect(servicesLink).toHaveAttribute("href", "/services");
  });

  it("renders breadcrumb navigation landmark", () => {
    mockUsePathname.mockReturnValue("/");
    render(<Breadcrumbs />);
    expect(screen.getByRole("navigation", { name: /breadcrumb/i })).toBeInTheDocument();
  });

  it("renders separators between crumbs", () => {
    mockUsePathname.mockReturnValue("/services/test");
    const { container } = render(<Breadcrumbs />);
    const separators = container.querySelectorAll('[data-slot="breadcrumb-separator"]');
    // Dashboard > Services > Test = 2 separators
    expect(separators.length).toBe(2);
  });

  it("uses known label for policies segment", () => {
    mockUsePathname.mockReturnValue("/policies");
    render(<Breadcrumbs />);
    expect(screen.getByText("Policies")).toBeInTheDocument();
  });
});
