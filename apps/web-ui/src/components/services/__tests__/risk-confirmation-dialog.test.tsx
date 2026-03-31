import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RiskConfirmationDialog } from "../risk-confirmation-dialog";
import type { Operation } from "@/types/api";

vi.mock("@/components/ui/alert-dialog", () => ({
  AlertDialog: ({ open, onOpenChange, children }: any) =>
    open ? (
      <div>
        <button type="button" onClick={() => onOpenChange(false)}>
          Dismiss dialog
        </button>
        {children}
      </div>
    ) : null,
  AlertDialogContent: ({ children, ...props }: any) => (
    <div {...props}>{children}</div>
  ),
  AlertDialogHeader: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  AlertDialogTitle: ({ children, ...props }: any) => <h2 {...props}>{children}</h2>,
  AlertDialogDescription: ({ children, ...props }: any) => (
    <p {...props}>{children}</p>
  ),
  AlertDialogFooter: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  AlertDialogAction: ({ children, ...props }: any) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
  AlertDialogCancel: ({ children, ...props }: any) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
  AlertDialogMedia: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));

vi.mock("@/components/ui/checkbox", () => ({
  Checkbox: ({ checked, onCheckedChange, ...props }: any) => (
    <input
      type="checkbox"
      checked={checked}
      onChange={(event) => onCheckedChange(event.target.checked)}
      {...props}
    />
  ),
}));

vi.mock("@/components/ui/badge", () => ({
  Badge: ({ children, ...props }: any) => <span {...props}>{children}</span>,
}));

vi.mock("@/components/ui/separator", () => ({
  Separator: (props: any) => <hr {...props} />,
}));

function makeOperation(overrides: Partial<Operation> = {}): Operation {
  return {
    id: "op-danger",
    name: "deleteEverything",
    description: "Delete all records",
    method: "DELETE",
    path: "/records",
    params: [],
    risk: {
      risk_level: "high",
      writes_state: true,
      destructive: false,
      external_side_effect: true,
      idempotent: true,
      confidence: 0.98,
      source: "extractor",
    },
    tags: ["admin"],
    source: "extractor",
    confidence: 0.91,
    enabled: true,
    tool_intent: "action",
    ...overrides,
  };
}

describe("RiskConfirmationDialog", () => {
  it("renders nothing when no operation is selected", () => {
    render(
      <RiskConfirmationDialog
        operation={null}
        open
        onOpenChange={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.queryByText("Dangerous Operation")).not.toBeInTheDocument();
  });

  it("shows risk details, forwards open changes, and requires confirmation before proceeding", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const onConfirm = vi.fn();

    render(
      <RiskConfirmationDialog
        operation={makeOperation()}
        open
        onOpenChange={onOpenChange}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByText("Dangerous Operation")).toBeInTheDocument();
    expect(screen.getByText("deleteEverything")).toBeInTheDocument();
    expect(screen.getByText("Writes State")).toBeInTheDocument();
    expect(screen.getByText("Destructive")).toBeInTheDocument();
    expect(screen.getByText("External Side Effect")).toBeInTheDocument();
    expect(screen.getByText("Idempotent")).toBeInTheDocument();
    expect(screen.getAllByText("Yes")).toHaveLength(3);
    expect(screen.getAllByText("No")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Dismiss dialog" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);

    const proceed = screen.getByRole("button", { name: "Proceed" });
    expect(proceed).toBeDisabled();

    await user.click(screen.getByRole("checkbox"));
    expect(proceed).toBeEnabled();

    await user.click(proceed);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("resets the confirmation checkbox whenever the dialog is reopened", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    const { rerender } = render(
      <RiskConfirmationDialog
        operation={makeOperation()}
        open
        onOpenChange={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    await user.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Proceed" })).toBeEnabled();

    rerender(
      <RiskConfirmationDialog
        operation={makeOperation()}
        open={false}
        onOpenChange={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    rerender(
      <RiskConfirmationDialog
        operation={makeOperation()}
        open
        onOpenChange={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Proceed" })).toBeDisabled();
  });
});
