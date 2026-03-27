import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import LoginPage from "../login/page";

// Capture the mock router so we can assert on push/replace
const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: mockReplace,
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

// Mock the auth store
const mockLogin = vi.fn();
vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      token: null,
      user: null,
      isAuthenticated: false,
      login: mockLogin,
      logout: vi.fn(),
      setToken: vi.fn(),
    }),
}));

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset global fetch mock
    vi.restoreAllMocks();
  });

  it("renders login form with username and password fields", () => {
    render(<LoginPage />);
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("renders page heading and description", () => {
    render(<LoginPage />);
    expect(screen.getByText("Tool Compiler v2")).toBeInTheDocument();
    expect(
      screen.getByText("Enterprise API-to-MCP Tool Compilation Platform"),
    ).toBeInTheDocument();
  });

  it("renders sign in card with title", () => {
    render(<LoginPage />);
    // "Sign in" appears in the card title and also in the submit button
    expect(screen.getAllByText(/Sign in/).length).toBeGreaterThanOrEqual(2);
    expect(
      screen.getByText("Choose your preferred authentication method."),
    ).toBeInTheDocument();
  });

  it("renders PAT tab", () => {
    render(<LoginPage />);
    expect(screen.getByText("PAT Token")).toBeInTheDocument();
  });

  it("renders Password Login tab", () => {
    render(<LoginPage />);
    expect(screen.getByText("Password Login")).toBeInTheDocument();
  });

  it("shows sign in button on password tab", () => {
    render(<LoginPage />);
    expect(
      screen.getByRole("button", { name: "Sign in" }),
    ).toBeInTheDocument();
  });

  it("shows error message on failed password login", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 401,
      text: () => Promise.resolve("Invalid credentials"),
    } as Response);

    render(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByText("Invalid credentials")).toBeInTheDocument();
    });
  });

  it("calls auth store login on successful password submission", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          token: "jwt-token-123",
          username: "admin",
          email: "admin@example.com",
          roles: ["admin"],
        }),
    } as Response);

    render(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "password123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("jwt-token-123", {
        username: "admin",
        email: "admin@example.com",
        roles: ["admin"],
      });
    });
  });

  it("redirects to dashboard on successful login", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          token: "jwt-token-123",
          username: "admin",
        }),
    } as Response);

    render(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "password123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/");
    });
  });

  it("shows loading state during password submission", async () => {
    const user = userEvent.setup();
    // Create a promise that we can control
    let resolveFetch!: (value: Response) => void;
    vi.spyOn(globalThis, "fetch").mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    render(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "password123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    // While loading, button should say "Signing in…" and be disabled
    expect(screen.getByText("Signing in…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Signing in…" })).toBeDisabled();

    // Resolve the fetch to clean up
    resolveFetch({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ token: "t", username: "u" }),
    } as Response);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign in" })).not.toBeDisabled();
    });
  });

  it("shows error on network failure", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
      new Error("Network error"),
    );

    render(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "password123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByText("Network error")).toBeInTheDocument();
    });
  });

  it("uses basic auth token when API response has no token field", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    } as Response);

    render(<LoginPage />);

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "pass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      const expectedToken = btoa("admin:pass");
      expect(mockLogin).toHaveBeenCalledWith(
        expectedToken,
        expect.objectContaining({ username: "admin" }),
      );
    });
  });

  it("requires username and password fields", () => {
    render(<LoginPage />);
    const usernameInput = screen.getByLabelText("Username");
    const passwordInput = screen.getByLabelText("Password");
    expect(usernameInput).toBeRequired();
    expect(passwordInput).toBeRequired();
  });
});
