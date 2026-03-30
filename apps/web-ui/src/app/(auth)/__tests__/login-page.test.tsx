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

  function mockApiErrorResponse(detail: string, status = 401): Response {
    return {
      ok: false,
      status,
      statusText: "Unauthorized",
      json: () => Promise.resolve({ detail }),
      headers: new Headers(),
    } as Response;
  }

  it("renders login form with JWT token field", () => {
    render(<LoginPage />);
    expect(screen.getByLabelText("JWT Token", { selector: "input" })).toBeInTheDocument();
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
      screen.getByText("Validate a JWT or PAT against the access-control service."),
    ).toBeInTheDocument();
  });

  it("renders PAT tab", () => {
    render(<LoginPage />);
    expect(screen.getByText("PAT Token")).toBeInTheDocument();
  });

  it("renders JWT Token tab", () => {
    render(<LoginPage />);
    expect(screen.getByRole("tab", { name: "JWT Token" })).toBeInTheDocument();
  });

  it("shows JWT sign in button on default tab", () => {
    render(<LoginPage />);
    expect(
      screen.getByRole("button", { name: "Sign in with JWT" }),
    ).toBeInTheDocument();
  });

  it("shows error message on failed JWT login", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      mockApiErrorResponse("JWT is invalid"),
    );

    render(<LoginPage />);

    await user.type(screen.getByLabelText("JWT Token", { selector: "input" }), "bad-token");
    await user.click(screen.getByRole("button", { name: "Sign in with JWT" }));

    await waitFor(() => {
      expect(screen.getByText("JWT is invalid")).toBeInTheDocument();
    });
  });

  it("calls auth store login on successful JWT submission", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          subject: "admin@example.com",
          token_type: "jwt",
          claims: {
            preferred_username: "admin",
            email: "admin@example.com",
            roles: ["admin"],
          },
        }),
    } as Response);

    render(<LoginPage />);

    await user.type(
      screen.getByLabelText("JWT Token", { selector: "input" }),
      "jwt-token-123",
    );
    await user.click(screen.getByRole("button", { name: "Sign in with JWT" }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("jwt-token-123", {
        username: "admin",
        subject: "admin@example.com",
        tokenType: "jwt",
        claims: {
          preferred_username: "admin",
          email: "admin@example.com",
          roles: ["admin"],
        },
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
          subject: "admin",
          token_type: "jwt",
          claims: {},
        }),
    } as Response);

    render(<LoginPage />);

    await user.type(
      screen.getByLabelText("JWT Token", { selector: "input" }),
      "jwt-token-123",
    );
    await user.click(screen.getByRole("button", { name: "Sign in with JWT" }));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/");
    });
  });

  it("shows loading state during JWT submission", async () => {
    const user = userEvent.setup();
    // Create a promise that we can control
    let resolveFetch!: (value: Response) => void;
    vi.spyOn(globalThis, "fetch").mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    render(<LoginPage />);

    await user.type(
      screen.getByLabelText("JWT Token", { selector: "input" }),
      "jwt-token-123",
    );
    await user.click(screen.getByRole("button", { name: "Sign in with JWT" }));

    // While loading, button should say "Validating…" and be disabled
    expect(screen.getByText("Validating…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validating…" })).toBeDisabled();

    // Resolve the fetch to clean up
    resolveFetch({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ subject: "u", token_type: "jwt", claims: {} }),
    } as Response);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign in with JWT" })).not.toBeDisabled();
    });
  });

  it("shows error on network failure", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
      new Error("Network error"),
    );

    render(<LoginPage />);

    await user.type(
      screen.getByLabelText("JWT Token", { selector: "input" }),
      "jwt-token-123",
    );
    await user.click(screen.getByRole("button", { name: "Sign in with JWT" }));

    await waitFor(() => {
      expect(screen.getByText("Network error")).toBeInTheDocument();
    });
  });

  it("stores the submitted JWT token after validation succeeds", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          subject: "opaque-subject",
          token_type: "jwt",
          claims: {},
        }),
    } as Response);

    render(<LoginPage />);

    await user.type(
      screen.getByLabelText("JWT Token", { selector: "input" }),
      "submitted-jwt",
    );
    await user.click(screen.getByRole("button", { name: "Sign in with JWT" }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("submitted-jwt", {
        username: undefined,
        subject: "opaque-subject",
        tokenType: "jwt",
        claims: {},
        email: undefined,
        roles: undefined,
      });
    });
  });

  it("stores PAT roles after successful PAT validation", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          subject: "admin",
          username: "admin",
          token_type: "pat",
          claims: {
            roles: ["admin"],
          },
        }),
    } as Response);

    render(<LoginPage />);

    await user.click(screen.getByRole("tab", { name: "PAT Token" }));
    await user.type(
      screen.getByLabelText("Personal Access Token", { selector: "input" }),
      "submitted-pat",
    );
    await user.click(screen.getByRole("button", { name: "Sign in with PAT" }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("submitted-pat", {
        username: "admin",
        subject: "admin",
        tokenType: "pat",
        claims: { roles: ["admin"] },
        email: undefined,
        roles: ["admin"],
      });
    });
  });

  it("requires JWT token field", () => {
    render(<LoginPage />);
    const jwtInput = screen.getByLabelText("JWT Token", { selector: "input" });
    expect(jwtInput).toBeRequired();
  });
});
