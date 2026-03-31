import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VersionDiff } from "../version-diff";
import type {
  ArtifactDiffResponse,
  Operation,
  ServiceScope,
} from "@/types/api";

const hookState = vi.hoisted(() => ({
  versions: [] as Array<{ version_number: number }>,
  returnVersionsData: true,
  diff: null as ArtifactDiffResponse | null,
  diffFactory: null as
    | ((
        args: {
          serviceId: string;
          from: number;
          to: number;
          scope?: ServiceScope;
        },
      ) => ArtifactDiffResponse | null)
    | null,
  isLoading: false,
  error: null as Error | null,
  lastVersionsArgs: null as [string, ServiceScope | undefined] | null,
  lastDiffArgs: null as
    | [string, number, number, ServiceScope | undefined]
    | null,
}));

vi.mock("@/hooks/use-api", () => ({
  useArtifactVersions: (serviceId: string, scope?: ServiceScope) => {
    hookState.lastVersionsArgs = [serviceId, scope];
    return hookState.returnVersionsData
      ? { data: { versions: hookState.versions } }
      : { data: undefined };
  },
  useArtifactDiff: (
    serviceId: string,
    from: number,
    to: number,
    scope?: ServiceScope,
  ) => {
    hookState.lastDiffArgs = [serviceId, from, to, scope];
    return {
      data: hookState.diffFactory
        ? hookState.diffFactory({ serviceId, from, to, scope })
        : hookState.diff,
      isLoading: hookState.isLoading,
      error: hookState.error,
    };
  },
}));

vi.mock("@/components/ui/badge", () => ({
  Badge: ({ children, ...props }: any) => <span {...props}>{children}</span>,
}));

vi.mock("@/components/ui/card", () => ({
  Card: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  CardContent: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  CardHeader: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  CardTitle: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));

vi.mock("@/components/ui/skeleton", () => ({
  Skeleton: ({ ...props }: any) => <div data-testid="skeleton" {...props} />,
}));

vi.mock("@/components/ui/separator", () => ({
  Separator: ({ ...props }: any) => <hr {...props} />,
}));

vi.mock("@/components/services/risk-badge", () => ({
  RiskBadge: ({ level }: { level: string }) => <span>{level}</span>,
}));

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

function makeOperation(overrides: Partial<Operation> = {}): Operation {
  return {
    id: "op-1",
    name: "listUsers",
    description: "Retrieve list of users",
    method: "GET",
    path: "/users",
    params: [
      {
        name: "page",
        type: "integer",
        required: false,
        description: "Page number",
        source: "extractor",
        confidence: 0.9,
      },
      {
        name: "limit",
        type: "integer",
        required: true,
        description: "Page size",
        source: "extractor",
        confidence: 0.95,
      },
    ],
    risk: {
      risk_level: "safe",
      writes_state: false,
      destructive: false,
      external_side_effect: false,
      idempotent: true,
      confidence: 0.91,
      source: "extractor",
    },
    tags: ["users"],
    source: "extractor",
    confidence: 0.92,
    enabled: true,
    tool_intent: "discovery",
    ...overrides,
  };
}

function makeDiff(overrides: Partial<ArtifactDiffResponse> = {}): ArtifactDiffResponse {
  return {
    from_version: 1,
    to_version: 2,
    added_operations: [],
    removed_operations: [],
    changed_operations: [],
    ...overrides,
  };
}

describe("VersionDiff", () => {
  beforeEach(() => {
    hookState.versions = [
      { version_number: 1 },
      { version_number: 2 },
      { version_number: 3 },
    ];
    hookState.returnVersionsData = true;
    hookState.diff = null;
    hookState.diffFactory = null;
    hookState.isLoading = false;
    hookState.error = null;
    hookState.lastVersionsArgs = null;
    hookState.lastDiffArgs = null;
  });

  it("shows a loading skeleton while the diff request is pending", () => {
    hookState.isLoading = true;

    render(<VersionDiff serviceId="svc-1" fromVersion={1} toVersion={2} />);

    expect(screen.getAllByTestId("skeleton")).toHaveLength(4);
    expect(hookState.lastDiffArgs).toEqual(["svc-1", 1, 2, undefined]);
  });

  it("shows an error message when the diff request fails", () => {
    hookState.error = new Error("boom");

    render(<VersionDiff serviceId="svc-1" fromVersion={1} toVersion={2} />);

    expect(screen.getByText("Failed to load diff: boom")).toBeInTheDocument();
  });

  it("shows the same-version prompt and updates hook args when selectors change", async () => {
    const user = userEvent.setup();
    hookState.diffFactory = ({ from, to }) =>
      from === to ? null : makeDiff({ from_version: from, to_version: to });

    render(<VersionDiff serviceId="svc-1" fromVersion={2} toVersion={2} />);

    expect(
      screen.getByText("Select two different versions to compare."),
    ).toBeInTheDocument();
    expect(hookState.lastDiffArgs).toEqual(["svc-1", 2, 2, undefined]);

    await user.click(screen.getAllByRole("button", { name: "v1" })[0]);
    expect(hookState.lastDiffArgs).toEqual(["svc-1", 1, 2, undefined]);

    await user.click(screen.getAllByRole("button", { name: "v3" })[1]);
    expect(hookState.lastDiffArgs).toEqual(["svc-1", 1, 3, undefined]);
  });

  it("renders empty-state copy when there are no operation differences", () => {
    hookState.diff = makeDiff();

    render(<VersionDiff serviceId="svc-1" fromVersion={1} toVersion={2} />);

    expect(
      screen.getByText("No differences between v1 and v2."),
    ).toBeInTheDocument();
  });

  it("handles missing version data and uses the singular change label", () => {
    hookState.returnVersionsData = false;
    hookState.diff = makeDiff({
      changed_operations: [
        {
          operation_id: "renameUser",
          diff_type: "changed",
          changes: [{ field: "name", new_value: "renamed" }],
        },
      ],
    });

    render(<VersionDiff serviceId="svc-1" fromVersion={1} toVersion={2} />);

    expect(screen.queryByRole("button", { name: "v1" })).not.toBeInTheDocument();
    expect(screen.getByText("1 change")).toBeInTheDocument();
  });

  it("renders added, removed, and changed operations and supports collapsing details", async () => {
    const user = userEvent.setup();
    hookState.diff = makeDiff({
      added_operations: [
        makeOperation({
          id: "op-added",
          name: "createUser",
          description: "Create a user",
        }),
      ],
      removed_operations: [
        makeOperation({
          id: "op-removed",
          name: "deleteUser",
          description: "Delete a user",
          params: [],
          risk: {
            risk_level: "high",
            writes_state: true,
            destructive: true,
            external_side_effect: true,
            idempotent: false,
            confidence: 0.88,
            source: "extractor",
          },
        }),
      ],
      changed_operations: [
        {
          operation_id: "patchUser",
          diff_type: "changed",
          changes: [
            { field: "auth", old_value: null, new_value: { type: "oauth2" } },
            { field: "timeout", old_value: 30 },
            { field: "mode", new_value: "safe" },
          ],
        },
      ],
    });

    render(
      <VersionDiff
        serviceId="svc-9"
        scope={{ tenant: "tenant-a", environment: "prod" }}
        fromVersion={1}
        toVersion={2}
      />,
    );

    expect(hookState.lastVersionsArgs).toEqual([
      "svc-9",
      { tenant: "tenant-a", environment: "prod" },
    ]);
    expect(hookState.lastDiffArgs).toEqual([
      "svc-9",
      1,
      2,
      { tenant: "tenant-a", environment: "prod" },
    ]);

    expect(screen.getByText("1 added")).toBeInTheDocument();
    expect(screen.getByText("1 removed")).toBeInTheDocument();
    expect(screen.getByText("1 changed")).toBeInTheDocument();
    expect(screen.getByText("Added Operations (1)")).toBeInTheDocument();
    expect(screen.getByText("Removed Operations (1)")).toBeInTheDocument();
    expect(screen.getByText("Changed Operations (1)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /createUser/i }));
    expect(screen.getByText("Create a user")).toBeInTheDocument();
    expect(screen.getByText("Parameters:")).toBeInTheDocument();
    expect(screen.getByText("limit")).toBeInTheDocument();
    expect(screen.getByText("required")).toBeInTheDocument();

    expect(screen.getByText("auth")).toBeInTheDocument();
    expect(screen.getByText("null")).toBeInTheDocument();
    expect(screen.getByText(/"type": "oauth2"/)).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getAllByText("safe").length).toBeGreaterThan(0);
    expect(screen.getByText("3 changes")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /patchUser/i }));
    expect(screen.queryByText("auth")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /patchUser/i }));
    expect(screen.getByText("auth")).toBeInTheDocument();
  });

  it("syncs its internal versions when the parent props change", () => {
    hookState.diffFactory = ({ from, to }) =>
      makeDiff({ from_version: from, to_version: to });

    const { rerender } = render(
      <VersionDiff serviceId="svc-1" fromVersion={1} toVersion={2} />,
    );

    expect(hookState.lastDiffArgs).toEqual(["svc-1", 1, 2, undefined]);

    rerender(<VersionDiff serviceId="svc-1" fromVersion={2} toVersion={3} />);

    expect(hookState.lastDiffArgs).toEqual(["svc-1", 2, 3, undefined]);
  });
});
