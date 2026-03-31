import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VersionDiffDialog } from "../version-diff-dialog";

const hookState = vi.hoisted(() => ({
  versions: [] as Array<{ version_number: number }>,
}));

vi.mock("@/hooks/use-api", () => ({
  useArtifactVersions: () => ({
    data: { versions: hookState.versions },
  }),
}));

vi.mock("@/components/services/version-diff", () => ({
  VersionDiff: ({ serviceId, scope, fromVersion, toVersion }: any) => (
    <div data-testid="version-diff">
      {serviceId}:{scope?.tenant ?? "no-scope"}:{fromVersion}:{toVersion}
    </div>
  ),
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, ...props }: any) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
}));

vi.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));

vi.mock("@/components/ui/dialog", async () => {
  const React = await import("react");

  const DialogContext = React.createContext<{
    open: boolean;
    onOpenChange: (open: boolean) => void;
  } | null>(null);

  function Dialog({ open, onOpenChange, children }: any) {
    return (
      <DialogContext.Provider value={{ open, onOpenChange }}>
        {children}
      </DialogContext.Provider>
    );
  }

  function DialogTrigger({ render }: any) {
    const context = React.useContext(DialogContext);
    if (!context) return null;

    return React.cloneElement(render, {
      onClick: () => context.onOpenChange(true),
    });
  }

  function DialogContent({ children, ...props }: any) {
    const context = React.useContext(DialogContext);
    return context?.open ? <div {...props}>{children}</div> : null;
  }

  return {
    Dialog,
    DialogContent,
    DialogDescription: ({ children, ...props }: any) => (
      <p {...props}>{children}</p>
    ),
    DialogHeader: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    DialogTitle: ({ children, ...props }: any) => <h2 {...props}>{children}</h2>,
    DialogTrigger,
  };
});

vi.mock("@/components/ui/select", async () => {
  const React = await import("react");

  const SelectContext = React.createContext<{
    value: string;
    onValueChange: (value: string) => void;
  } | null>(null);

  function Select({ value, onValueChange, children }: any) {
    return (
      <SelectContext.Provider value={{ value, onValueChange }}>
        {children}
      </SelectContext.Provider>
    );
  }

  function SelectTrigger({ children, ...props }: any) {
    return <div {...props}>{children}</div>;
  }

  function SelectValue({ placeholder }: any) {
    const context = React.useContext(SelectContext);
    return <span>{context?.value || placeholder}</span>;
  }

  function SelectContent({ children, ...props }: any) {
    return <div {...props}>{children}</div>;
  }

  function SelectItem({ value, children, ...props }: any) {
    const context = React.useContext(SelectContext);
    if (!context) return null;

    return (
      <button
        type="button"
        onClick={() => context.onValueChange(value)}
        {...props}
      >
        {children}
      </button>
    );
  }

  return {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
  };
});

describe("VersionDiffDialog", () => {
  beforeEach(() => {
    hookState.versions = [
      { version_number: 1 },
      { version_number: 2 },
      { version_number: 3 },
    ];
  });

  it("defaults to the newest two versions when opened", async () => {
    const user = userEvent.setup();

    render(
      <VersionDiffDialog
        serviceId="svc-1"
        scope={{ tenant: "tenant-a", environment: "dev" }}
      />,
    );

    await user.click(screen.getByRole("button", { name: /compare versions/i }));

    expect(await screen.findByTestId("version-diff")).toHaveTextContent(
      "svc-1:tenant-a:2:3",
    );
  });

  it("shows selectors when the same version is chosen twice and switches to a diff after selection", async () => {
    const user = userEvent.setup();

    render(
      <VersionDiffDialog serviceId="svc-1" initialFrom={2} initialTo={2} />,
    );

    await user.click(screen.getByRole("button", { name: /compare versions/i }));

    expect(
      screen.getByText("Select two different versions to compare."),
    ).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "v1" })[0]);

    expect(await screen.findByTestId("version-diff")).toHaveTextContent(
      "svc-1:no-scope:1:2",
    );
  });

  it("updates the target version when the second selector changes", async () => {
    const user = userEvent.setup();

    render(
      <VersionDiffDialog serviceId="svc-1" initialFrom={1} initialTo={1} />,
    );

    await user.click(screen.getByRole("button", { name: /compare versions/i }));
    await user.click(screen.getAllByRole("button", { name: "v3" })[1]);

    expect(await screen.findByTestId("version-diff")).toHaveTextContent(
      "svc-1:no-scope:1:3",
    );
  });

  it("supports a custom trigger and syncs updated initial versions from props", async () => {
    const user = userEvent.setup();

    const { rerender } = render(
      <VersionDiffDialog
        serviceId="svc-2"
        scope={{ tenant: "tenant-b" }}
        initialFrom={1}
        initialTo={3}
        trigger={<button type="button">Open diff</button>}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Open diff" }));

    expect(screen.queryByRole("button", { name: /compare versions/i })).not.toBeInTheDocument();
    expect(await screen.findByTestId("version-diff")).toHaveTextContent(
      "svc-2:tenant-b:1:3",
    );

    rerender(
      <VersionDiffDialog
        serviceId="svc-2"
        scope={{ tenant: "tenant-b" }}
        initialFrom={2}
        initialTo={3}
        trigger={<button type="button">Open diff</button>}
      />,
    );

    expect(await screen.findByTestId("version-diff")).toHaveTextContent(
      "svc-2:tenant-b:2:3",
    );
  });
});
