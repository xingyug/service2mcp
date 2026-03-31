import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/__tests__/test-utils";
import type { PATListResponse, PATResponse } from "@/types/api";

const authState = vi.hoisted(() => ({
  user: {
    username: "alice",
    subject: "alice@example.com",
    email: "alice@example.com",
    roles: ["admin"],
  } as Record<string, unknown> | null,
}));

const authApiMock = vi.hoisted(() => ({
  listPATs: vi.fn(),
  createPAT: vi.fn(),
  revokePAT: vi.fn(),
}));

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

const clipboardWriteMock = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (selector: (state: { user: Record<string, unknown> | null }) => unknown) =>
    selector({ user: authState.user }),
}));

vi.mock("@/lib/api-client", () => ({
  authApi: authApiMock,
}));

vi.mock("sonner", () => ({
  toast: toastMock,
}));

vi.mock("@/components/ui/skeleton", () => ({
  Skeleton: (props: any) => <div data-testid="skeleton" {...props} />,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    type = "button",
    ...props
  }: {
    children: ReactNode;
    type?: "button" | "submit" | "reset";
    [key: string]: unknown;
  }) => (
    <button type={type} {...props}>
      {children}
    </button>
  ),
}));

vi.mock("@/components/ui/dialog", async () => {
  const React = await import("react");
  const DialogContext = React.createContext<{
    open: boolean;
    onOpenChange: (open: boolean) => void;
  } | null>(null);

  function Dialog({
    open,
    onOpenChange,
    children,
  }: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    children: ReactNode;
  }) {
    return (
      <DialogContext.Provider value={{ open, onOpenChange }}>
        {children}
      </DialogContext.Provider>
    );
  }

  function DialogTrigger({
    render,
    children,
  }: {
    render: React.ReactElement;
    children: ReactNode;
  }) {
    const context = React.useContext(DialogContext);
    if (!context) return null;

    return React.cloneElement(render, {
      onClick: () => context.onOpenChange(true),
      children,
    });
  }

  function DialogClose({
    render,
    children,
  }: {
    render: React.ReactElement;
    children: ReactNode;
  }) {
    const context = React.useContext(DialogContext);
    if (!context) return null;

    return React.cloneElement(render, {
      onClick: () => context.onOpenChange(false),
      children,
    });
  }

  function DialogContent({
    children,
    showCloseButton: _showCloseButton,
    ...props
  }: {
    children: ReactNode;
    [key: string]: unknown;
  }) {
    const context = React.useContext(DialogContext);
    return context?.open ? (
      <div role="dialog" {...props}>
        <button
          type="button"
          data-testid="dialog-dismiss"
          onClick={() => context.onOpenChange(false)}
        />
        {children}
      </div>
    ) : null;
  }

  return {
    Dialog,
    DialogClose,
    DialogContent,
    DialogDescription: ({ children, ...props }: any) => <p {...props}>{children}</p>,
    DialogFooter: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    DialogHeader: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    DialogTitle: ({ children, ...props }: any) => <h2 {...props}>{children}</h2>,
    DialogTrigger,
  };
});

vi.mock("@/components/ui/alert-dialog", async () => {
  const React = await import("react");
  const AlertContext = React.createContext<{
    open: boolean;
    onOpenChange: (open: boolean) => void;
  } | null>(null);

  function AlertDialog({
    open,
    onOpenChange,
    children,
  }: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    children: ReactNode;
  }) {
    return (
      <AlertContext.Provider value={{ open, onOpenChange }}>
        {children}
      </AlertContext.Provider>
    );
  }

  function AlertDialogContent({
    children,
    ...props
  }: {
    children: ReactNode;
    [key: string]: unknown;
  }) {
    const context = React.useContext(AlertContext);
    return context?.open ? (
      <div role="alertdialog" {...props}>
        {children}
      </div>
    ) : null;
  }

  function AlertDialogCancel({ children, ...props }: any) {
    const context = React.useContext(AlertContext);
    return (
      <button type="button" onClick={() => context?.onOpenChange(false)} {...props}>
        {children}
      </button>
    );
  }

  function AlertDialogAction({ children, ...props }: any) {
    return (
      <button type="button" {...props}>
        {children}
      </button>
    );
  }

  return {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription: ({ children, ...props }: any) => (
      <p {...props}>{children}</p>
    ),
    AlertDialogFooter: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    AlertDialogHeader: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    AlertDialogTitle: ({ children, ...props }: any) => <h2 {...props}>{children}</h2>,
  };
});

vi.mock("@/components/ui/tooltip", async () => {
  const React = await import("react");

  return {
    TooltipProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
    Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
    TooltipTrigger: ({
      render,
      children,
      ...props
    }: {
      render?: React.ReactElement;
      children: ReactNode;
      [key: string]: unknown;
    }) =>
      render
        ? React.cloneElement(render, props, children)
        : React.createElement("span", props, children),
    TooltipContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  };
});

import PATTokensPage from "../page";

function makePAT(overrides: Partial<PATResponse> = {}): PATResponse {
  return {
    pat_id: "pat-1",
    username: "alice",
    name: "CLI token",
    created_at: new Date().toISOString(),
    revoked_at: undefined,
    ...overrides,
  };
}

function makePATList(
  overrides: Partial<PATListResponse> = {},
): PATListResponse {
  return {
    pats: [],
    total: 0,
    page: 1,
    pageSize: 100,
    ...overrides,
  };
}

describe("PATTokensPage", () => {
  beforeEach(() => {
    authState.user = {
      username: "alice",
      subject: "alice@example.com",
      email: "alice@example.com",
      roles: ["admin"],
    };
    authApiMock.listPATs.mockReset();
    authApiMock.createPAT.mockReset();
    authApiMock.revokePAT.mockReset();
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    clipboardWriteMock.mockReset();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboardWriteMock },
    });
  });

  it("disables PAT management when the stored identity has no platform username", () => {
    authState.user = { subject: "alice@example.com" };

    renderWithProviders(<PATTokensPage />);

    expect(
      screen.getByText(/PAT management is unavailable/i),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Create Token" })[0],
    ).toBeDisabled();
    expect(authApiMock.listPATs).not.toHaveBeenCalled();
  });

  it("shows loading skeletons while the PAT list is being fetched", () => {
    authApiMock.listPATs.mockImplementation(() => new Promise(() => {}));

    renderWithProviders(<PATTokensPage />);

    expect(screen.getAllByTestId("skeleton")).toHaveLength(5);
  });

  it("renders the error state fallback and retries loading", async () => {
    const user = userEvent.setup();
    let shouldFail = true;
    authApiMock.listPATs.mockImplementation(() =>
      shouldFail
        ? Promise.reject("boom")
        : Promise.resolve(makePATList()),
    );

    renderWithProviders(<PATTokensPage />);

    expect(
      await screen.findByText("Failed to load personal access tokens"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The PAT list request did not succeed."),
    ).toBeInTheDocument();

    shouldFail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));

    await screen.findByText("No personal access tokens");
    expect(authApiMock.listPATs).toHaveBeenCalledTimes(2);
  });

  it("surfaces a create error when PAT creation is attempted without a username", async () => {
    const user = userEvent.setup();
    authState.user = { subject: "alice@example.com" };

    renderWithProviders(<PATTokensPage />);

    await screen.findByText("No personal access tokens");
    const createButtons = screen.getAllByRole("button", { name: "Create Token" });
    await user.click(createButtons[createButtons.length - 1]);

    const createDialog = await screen.findByRole("dialog");
    await user.type(within(createDialog).getByLabelText("Token Name"), "no username");
    await user.click(
      within(createDialog).getByRole("button", { name: "Create Token" }),
    );

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith("Failed to create token"),
    );
  });

  it("creates a token, copies it once, and clears the token dialog", async () => {
    const user = userEvent.setup();
    authApiMock.listPATs.mockResolvedValue(makePATList());
    authApiMock.createPAT.mockResolvedValue(
      makePAT({
        pat_id: "pat-new",
        name: "CI token",
        token: "secret-token",
      }),
    );

    renderWithProviders(<PATTokensPage />);

    await screen.findByText("No personal access tokens");
    const createButtons = screen.getAllByRole("button", { name: "Create Token" });
    await user.click(createButtons[createButtons.length - 1]);

    const createDialog = await screen.findByRole("dialog");
    await user.type(within(createDialog).getByLabelText("Token Name"), "  CI token  ");
    await user.click(
      within(createDialog).getByRole("button", { name: "Create Token" }),
    );

    await waitFor(() =>
      expect(authApiMock.createPAT).toHaveBeenCalledWith({
        username: "alice",
        name: "CI token",
      }),
    );
    expect(toastMock.success).toHaveBeenCalledWith("Token created");

    const tokenDialog = await screen.findByRole("dialog");
    expect(within(tokenDialog).getByText("Token Created")).toBeInTheDocument();
    expect(within(tokenDialog).getByText("secret-token")).toBeInTheDocument();

    const copyButton = tokenDialog.querySelector('button[variant="ghost"]');
    expect(copyButton).toBeTruthy();
    const clipboardSpy = vi.spyOn(navigator.clipboard, "writeText");
    await user.click(copyButton as HTMLButtonElement);
    expect(clipboardSpy).toHaveBeenCalledWith("secret-token");

    await user.click(within(tokenDialog).getByRole("button", { name: "Done" }));
    await waitFor(() =>
      expect(screen.queryByText("secret-token")).not.toBeInTheDocument(),
    );
  });

  it("clears the shown token when the token dialog closes", async () => {
    const user = userEvent.setup();
    authApiMock.listPATs.mockResolvedValue(makePATList());
    authApiMock.createPAT.mockResolvedValue(
      makePAT({
        pat_id: "pat-dismiss",
        name: "Dismiss token",
        token: "dismiss-secret",
      }),
    );

    renderWithProviders(<PATTokensPage />);

    await screen.findByText("No personal access tokens");
    await user.click(screen.getAllByRole("button", { name: "Create Token" })[0]);

    const createDialog = await screen.findByRole("dialog");
    await user.type(within(createDialog).getByLabelText("Token Name"), "dismiss token");
    await user.click(
      within(createDialog).getByRole("button", { name: "Create Token" }),
    );

    const tokenDialog = await screen.findByRole("dialog");
    expect(within(tokenDialog).getByText("dismiss-secret")).toBeInTheDocument();
    await user.click(within(tokenDialog).getByTestId("dialog-dismiss"));

    await waitFor(() =>
      expect(screen.queryByText("dismiss-secret")).not.toBeInTheDocument(),
    );
  });

  it("handles token creation failures without closing the create dialog", async () => {
    const user = userEvent.setup();
    authApiMock.listPATs.mockResolvedValue(makePATList());
    authApiMock.createPAT.mockRejectedValue(new Error("create failed"));

    renderWithProviders(<PATTokensPage />);

    await screen.findByText("No personal access tokens");
    await user.click(screen.getAllByRole("button", { name: "Create Token" })[0]);

    const createDialog = await screen.findByRole("dialog");
    await user.type(within(createDialog).getByLabelText("Token Name"), "broken token");
    await user.click(
      within(createDialog).getByRole("button", { name: "Create Token" }),
    );

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith("Failed to create token"),
    );
    expect(screen.getByText("Create Personal Access Token")).toBeInTheDocument();
  });

  it("renders PAT rows, paginates, and revokes active tokens", async () => {
    const user = userEvent.setup();
    const now = Date.now();
    authApiMock.listPATs.mockImplementation((_: string, page = 1) =>
      Promise.resolve(
        page === 1
          ? makePATList({
              pats: [
                makePAT({
                  pat_id: "pat-active",
                  name: "Active token",
                  created_at: new Date(now - 10_000).toISOString(),
                }),
                makePAT({
                  pat_id: "pat-revoked",
                  name: "Revoked token",
                  created_at: new Date(now - 3 * 60_000).toISOString(),
                  revoked_at: new Date(now - 1_000).toISOString(),
                }),
              ],
              total: 101,
              page: 1,
            })
          : makePATList({
              pats: [
                makePAT({
                  pat_id: "pat-last",
                  name: "Last token",
                  created_at: new Date(now - 2 * 24 * 60 * 60 * 1000).toISOString(),
                }),
              ],
              total: 101,
              page: 2,
            }),
      ),
    );
    authApiMock.revokePAT.mockResolvedValue(undefined);

    renderWithProviders(<PATTokensPage />);

    await screen.findByText("Active token");
    expect(screen.getByText(/\d+s ago/)).toBeInTheDocument();
    expect(screen.getByText("3 min ago")).toBeInTheDocument();
    expect(screen.getByText("Revoked")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–100 of 101")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Last token");
    expect(screen.getByText("2d ago")).toBeInTheDocument();
    expect(screen.getByText("Showing 101–101 of 101")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByText("Active token");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Last token");

    await user.click(screen.getByRole("button", { name: "Revoke" }));
    const revokeDialog = await screen.findByRole("alertdialog");
    expect(within(revokeDialog).getByText("Revoke Token")).toBeInTheDocument();
    await user.click(within(revokeDialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Revoke" }));
    const confirmDialog = await screen.findByRole("alertdialog");
    await user.click(within(confirmDialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() =>
      expect(authApiMock.revokePAT).toHaveBeenCalledWith("pat-last"),
    );
    expect(toastMock.success).toHaveBeenCalledWith("Token revoked");
  });

  it("surfaces revoke failures", async () => {
    const user = userEvent.setup();
    authApiMock.listPATs.mockResolvedValue(
      makePATList({
        pats: [makePAT({ pat_id: "pat-revoke", name: "Revoke me" })],
        total: 1,
      }),
    );
    authApiMock.revokePAT.mockRejectedValue(new Error("revoke failed"));

    renderWithProviders(<PATTokensPage />);

    await screen.findByText("Revoke me");
    await user.click(screen.getByRole("button", { name: "Revoke" }));

    const revokeDialog = await screen.findByRole("alertdialog");
    await user.click(within(revokeDialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith("Failed to revoke token"),
    );
  });
});
