import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const themeState = vi.hoisted(() => ({
  resolvedTheme: "light" as string | null | undefined,
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: themeState.resolvedTheme }),
}));

vi.mock("@/components/ui/button", async () => {
  const React = await import("react");

  return {
    Button: ({
      children,
      render,
      nativeButton: _nativeButton,
      size: _size,
      variant: _variant,
      ...props
    }: any) =>
      render
        ? React.cloneElement(render, props, children)
        : React.createElement("button", { type: "button", ...props }, children),
  };
});

vi.mock("@/components/ui/card", () => ({
  Card: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  CardContent: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  CardDescription: ({ children, ...props }: any) => (
    <div {...props}>{children}</div>
  ),
  CardHeader: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  CardTitle: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));

vi.mock("@/components/ui/skeleton", () => ({
  Skeleton: (props: any) => <div data-testid="skeleton" {...props} />,
}));

vi.mock("@/components/ui/tabs", async () => {
  const React = await import("react");

  const TabsContext = React.createContext<{
    value: string;
    setValue: (value: string) => void;
  } | null>(null);

  function Tabs({ defaultValue, children }: any) {
    const [value, setValue] = React.useState(defaultValue);
    return (
      <TabsContext.Provider value={{ value, setValue }}>
        {children}
      </TabsContext.Provider>
    );
  }

  function TabsList({ children, ...props }: any) {
    return <div {...props}>{children}</div>;
  }

  function TabsTrigger({ value, children, ...props }: any) {
    const context = React.useContext(TabsContext);
    if (!context) return null;

    return (
      <button
        type="button"
        data-active={context.value === value || undefined}
        onClick={() => context.setValue(value)}
        {...props}
      >
        {children}
      </button>
    );
  }

  function TabsContent({ value, children, ...props }: any) {
    const context = React.useContext(TabsContext);
    if (!context || context.value !== value) return null;
    return <div {...props}>{children}</div>;
  }

  return { Tabs, TabsList, TabsTrigger, TabsContent };
});

import ObservePage from "../page";

describe("ObservePage", () => {
  beforeEach(() => {
    themeState.resolvedTheme = "light";
  });

  it("renders the default dashboard with a light-theme fallback and switches tabs", async () => {
    themeState.resolvedTheme = null;
    const user = userEvent.setup();

    render(<ObservePage />);

    expect(screen.getByText("Observability Dashboards")).toBeInTheDocument();
    expect(screen.getByTitle("Grafana Dashboard")).toHaveAttribute(
      "src",
      expect.stringContaining(
        "/d/compilation/compilation-dashboard?orgId=1&theme=light&kiosk",
      ),
    );
    expect(
      screen.getByRole("link", { name: /open in grafana/i }),
    ).toHaveAttribute(
      "href",
      "http://localhost:3000/d/compilation/compilation-dashboard",
    );

    await user.click(screen.getByRole("button", { name: "Runtime" }));
    expect(screen.getByTitle("Grafana Dashboard")).toHaveAttribute(
      "src",
      expect.stringContaining(
        "/d/runtime/runtime-dashboard?orgId=1&theme=light&kiosk",
      ),
    );
    expect(
      screen.getByRole("link", { name: /open in grafana/i }),
    ).toHaveAttribute("href", "http://localhost:3000/d/runtime/runtime-dashboard");

    await user.click(screen.getByRole("button", { name: "Access Control" }));
    expect(screen.getByTitle("Grafana Dashboard")).toHaveAttribute(
      "src",
      expect.stringContaining(
        "/d/access-control/access-control-dashboard?orgId=1&theme=light&kiosk",
      ),
    );
    expect(
      screen.getByRole("link", { name: /open in grafana/i }),
    ).toHaveAttribute(
      "href",
      "http://localhost:3000/d/access-control/access-control-dashboard",
    );
  });

  it("uses the resolved dark theme and hides the loading skeleton after iframe load", () => {
    themeState.resolvedTheme = "dark";

    render(<ObservePage />);

    expect(screen.getAllByTestId("skeleton")).toHaveLength(3);
    const iframe = screen.getByTitle("Grafana Dashboard");
    expect(iframe).toHaveAttribute("src", expect.stringContaining("theme=dark"));

    fireEvent.load(iframe);

    expect(screen.queryByTestId("skeleton")).not.toBeInTheDocument();
  });

  it("shows Grafana setup guidance when the iframe errors", () => {
    render(<ObservePage />);
    const iframe = screen.getByTitle("Grafana Dashboard") as HTMLElement & {
      [key: string]: unknown;
    };
    const reactPropsKey = Object.keys(iframe).find((key) =>
      key.startsWith("__reactProps"),
    );

    expect(reactPropsKey).toBeTruthy();

    const reactProps = iframe[reactPropsKey!] as { onError?: () => void };
    act(() => {
      reactProps.onError?.();
    });

    expect(screen.getByText("Grafana Not Configured")).toBeInTheDocument();
    expect(screen.getByText("http://localhost:3000")).toBeInTheDocument();
    expect(screen.getByText("NEXT_PUBLIC_GRAFANA_URL")).toBeInTheDocument();
  });
});
