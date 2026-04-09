/* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-unused-vars */
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const authState = vi.hoisted(() => ({
  user: {
    username: "alice",
    subject: "alice@example.com",
  } as Record<string, string> | null,
}));

const createCompilationMock = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  isPending: false,
}));

const routerPushMock = vi.hoisted(() => vi.fn());

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: routerPushMock,
  }),
}));

vi.mock("sonner", () => ({
  toast: toastMock,
}));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (
    selector: (state: { user: Record<string, string> | null }) => unknown,
  ) => selector({ user: authState.user }),
}));

vi.mock("@/hooks/use-api", () => ({
  useCreateCompilation: () => createCompilationMock,
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

vi.mock("@/components/ui/card", () => ({
  Card: ({ children, ...props }: { children: ReactNode; [key: string]: unknown }) => (
    <div {...props}>{children}</div>
  ),
  CardHeader: ({
    children,
    ...props
  }: {
    children: ReactNode;
    [key: string]: unknown;
  }) => <div {...props}>{children}</div>,
  CardTitle: ({
    children,
    ...props
  }: {
    children: ReactNode;
    [key: string]: unknown;
  }) => <h2 {...props}>{children}</h2>,
  CardDescription: ({
    children,
    ...props
  }: {
    children: ReactNode;
    [key: string]: unknown;
  }) => <p {...props}>{children}</p>,
  CardContent: ({
    children,
    ...props
  }: {
    children: ReactNode;
    [key: string]: unknown;
  }) => <div {...props}>{children}</div>,
}));

vi.mock("@/components/ui/input", () => ({
  Input: (props: any) => <input {...props} />,
}));

vi.mock("@/components/ui/label", () => ({
  Label: ({ children, ...props }: { children: ReactNode; [key: string]: unknown }) => (
    <label {...props}>{children}</label>
  ),
}));

vi.mock("@/components/ui/textarea", () => ({
  Textarea: (props: any) => <textarea {...props} />,
}));

vi.mock("@/components/ui/switch", () => ({
  Switch: ({
    checked,
    onCheckedChange,
    ...props
  }: {
    checked: boolean;
    onCheckedChange: (checked: boolean) => void;
    [key: string]: unknown;
  }) => (
    <input
      type="checkbox"
      role="switch"
      checked={checked}
      onChange={(event) => onCheckedChange(event.target.checked)}
      {...props}
    />
  ),
}));

vi.mock("@/components/ui/separator", () => ({
  Separator: () => <hr />,
}));

vi.mock("@/components/ui/badge", () => ({
  Badge: ({ children, ...props }: { children: ReactNode; [key: string]: unknown }) => (
    <span {...props}>{children}</span>
  ),
}));

vi.mock("@/components/ui/radio-group", async () => {
  const React = await import("react");
  const RadioGroupContext = React.createContext<{
    value: string;
    onValueChange: (value: string) => void;
    name: string;
  } | null>(null);

  function RadioGroup({
    value,
    onValueChange,
    children,
    ...props
  }: {
    value: string;
    onValueChange: (value: string) => void;
    children: ReactNode;
    [key: string]: unknown;
  }) {
    const name = React.useId();
    return (
      <RadioGroupContext.Provider value={{ value, onValueChange, name }}>
        <div {...props}>{children}</div>
      </RadioGroupContext.Provider>
    );
  }

  function RadioGroupItem({
    value,
    id,
    ...props
  }: {
    value: string;
    id?: string;
    [key: string]: unknown;
  }) {
    const context = React.useContext(RadioGroupContext);
    if (!context) return null;

    return (
      <input
        type="radio"
        id={id}
        name={context.name}
        checked={context.value === value}
        onChange={() => context.onValueChange(value)}
        {...props}
      />
    );
  }

  return {
    RadioGroup,
    RadioGroupItem,
  };
});

vi.mock("@/components/ui/select", async () => {
  const React = await import("react");

  function SelectItem({
    children,
  }: {
    children: ReactNode;
    value: string;
  }) {
    return <>{children}</>;
  }

  function extractOptions(children: ReactNode): Array<{ value: string; label: string }> {
    const options: Array<{ value: string; label: string }> = [];

    React.Children.forEach(children, (child) => {
      if (!React.isValidElement(child)) return;
      const props = child.props as Record<string, unknown>;

      if (child.type === SelectItem) {
        options.push({
          value: String(props.value),
          label: String(props.children),
        });
      }

      if (props.children) {
        options.push(...extractOptions(props.children as ReactNode));
      }
    });

    return options;
  }

  function Select({
    value,
    onValueChange,
    children,
  }: {
    value: string;
    onValueChange: (value: string) => void;
    children: ReactNode;
  }) {
    const options = extractOptions(children);

    return (
      <select
        aria-label="Authentication Type"
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  return {
    Select,
    SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectItem,
    SelectTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectValue: () => null,
  };
});

import { CompilationWizard } from "../compilation-wizard";

class MockFileReader {
  onload: ((event: { target: { result: string } }) => void) | null = null;

  readAsText(file: File) {
    this.onload?.({
      target: {
        result: `contents:${file.name}`,
      },
    });
  }
}

function renderWizard(props: {
  initialServiceName?: string;
  initialServiceId?: string;
} = {}) {
  return render(<CompilationWizard {...props} />);
}

function getSourceUrlInput() {
  return screen.getByPlaceholderText(
    "https://api.example.com/openapi.yaml",
  ) as HTMLInputElement;
}

async function goToOptions(user: ReturnType<typeof userEvent.setup>, url = "https://example.com/petstore.yaml") {
  fireEvent.change(getSourceUrlInput(), {
    target: { value: url },
  });
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Protocol & Options" });
}

async function goToAuth(user: ReturnType<typeof userEvent.setup>, url = "https://example.com/petstore.yaml") {
  await goToOptions(user, url);
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Auth Configuration" });
}

async function goToReview(user: ReturnType<typeof userEvent.setup>, url = "https://example.com/petstore.yaml") {
  await goToAuth(user, url);
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("button", { name: "Create Compilation" });
}

describe("CompilationWizard integration", () => {
  beforeEach(() => {
    authState.user = {
      username: "alice",
      subject: "alice@example.com",
    };
    createCompilationMock.mutateAsync.mockReset();
    createCompilationMock.isPending = false;
    routerPushMock.mockReset();
    toastMock.success.mockReset();
    toastMock.error.mockReset();
    vi.stubGlobal("FileReader", MockFileReader);
  });

  it("validates the source step and auto-derives a service name from the URL", async () => {
    const user = userEvent.setup();
    renderWizard();

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Source URL is required.")).toBeInTheDocument();

    fireEvent.change(getSourceUrlInput(), {
      target: { value: "https://example.com/petstore.yaml" },
    });

    await waitFor(() =>
      expect(screen.queryByText("Source URL is required.")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText(/Service Name/i)).toHaveValue("petstore");

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(
      await screen.findByRole("heading", { name: "Protocol & Options" }),
    ).toBeInTheDocument();
  });

  it("keeps a custom service name when the source URL changes", async () => {
    const user = userEvent.setup();
    renderWizard();

    fireEvent.change(screen.getByLabelText(/Service Name/i), {
      target: { value: "Billing API" },
    });
    fireEvent.change(getSourceUrlInput(), {
      target: { value: "https://example.com/petstore.yaml" },
    });

    expect(screen.getByLabelText(/Service Name/i)).toHaveValue("Billing API");
  });

  it("supports paste mode and navigating back to the source step from review", async () => {
    const user = userEvent.setup();
    renderWizard();

    await user.click(screen.getByLabelText("Paste Content"));
    fireEvent.change(screen.getByLabelText("Specification Content (YAML / JSON)"), {
      target: { value: '{"openapi":"3.1.0"}' },
    });

    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("(pasted content)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Source Input/i }));
    expect(
      await screen.findByLabelText("Specification Content (YAML / JSON)"),
    ).toHaveValue('{"openapi":"3.1.0"}');
  });

  it("handles file uploads and drag-and-drop replacements", async () => {
    const user = userEvent.setup();
    const { container } = renderWizard();

    await user.click(screen.getByLabelText("Upload File"));

    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement | null;
    expect(fileInput).toBeTruthy();

    fireEvent.change(fileInput!, {
      target: {
        files: [new File(["openapi: 3.1.0"], "spec.yaml", { type: "text/yaml" })],
      },
    });

    expect(await screen.findByText("spec.yaml")).toBeInTheDocument();

    const dropZone = fileInput!.parentElement as HTMLDivElement;
    fireEvent.dragOver(dropZone);
    expect(dropZone.className).toMatch(/border-primary/);
    fireEvent.dragLeave(dropZone);
    expect(dropZone.className).toMatch(/border-border/);

    fireEvent.drop(dropZone, {
      dataTransfer: {
        files: [new File(['{"openapi":"3.1.0"}'], "replacement.json", { type: "application/json" })],
      },
    });

    expect(await screen.findByText("replacement.json")).toBeInTheDocument();
  });

  it("renders the auth-specific field groups for each auth mode", async () => {
    const user = userEvent.setup();
    renderWizard();

    await goToAuth(user);

    const authType = screen.getByLabelText("Authentication Type");

    await user.selectOptions(authType, "bearer");
    expect(screen.getByLabelText("Secret Reference")).toBeInTheDocument();

    await user.selectOptions(authType, "basic");
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password Secret Reference")).toBeInTheDocument();

    await user.selectOptions(authType, "api_key");
    expect(screen.getByLabelText("Header Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Secret Reference")).toBeInTheDocument();

    await user.selectOptions(authType, "custom_header");
    expect(screen.getByLabelText("Header Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Value Secret Reference")).toBeInTheDocument();

    await user.selectOptions(authType, "oauth2");
    expect(screen.getByLabelText("Token URL")).toBeInTheDocument();
    expect(screen.getByLabelText("Client ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Client Secret Reference")).toBeInTheDocument();
  });

  it("validates oauth2 configuration before allowing review", async () => {
    const user = userEvent.setup();
    renderWizard();

    await goToAuth(user);

    await user.selectOptions(screen.getByLabelText("Authentication Type"), "oauth2");

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Token URL is required.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Token URL"), "https://auth.example.com/token");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Client ID is required.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Client ID"), "client-id");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(
      screen.getByText("Client secret reference is required."),
    ).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("Client Secret Reference"),
      "vault://oauth2/client-secret",
    );
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("https://auth.example.com/token")).toBeInTheDocument();
    expect(screen.getByText("client-id")).toBeInTheDocument();
  });

  it("submits a compilation successfully and redirects to the new job", async () => {
    const user = userEvent.setup();
    createCompilationMock.mutateAsync.mockResolvedValue({ job_id: "job-123" });

    renderWizard({
      initialServiceId: "billing-api",
      initialServiceName: "Billing API",
    });

    fireEvent.change(screen.getByLabelText(/Service Name/i), {
      target: { value: "   " },
    });

    await goToOptions(user, "https://example.com/billing.yaml");

    await user.click(screen.getByText("OpenAPI").closest("button")!);
    await user.click(screen.getByLabelText("Codegen"));
    await user.click(screen.getByLabelText("Skip LLM Enhancement"));
    await user.type(screen.getByLabelText(/^Tenant/), "team-a");
    await user.type(screen.getByLabelText(/^Environment/), "prod");

    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.selectOptions(screen.getByLabelText("Authentication Type"), "bearer");
    await user.type(
      screen.getByLabelText("Secret Reference"),
      "vault://secrets/billing-token",
    );
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.click(screen.getByRole("button", { name: "Create Compilation" }));

    await waitFor(() =>
      expect(createCompilationMock.mutateAsync).toHaveBeenCalledWith({
        created_by: "alice",
        source_url: "https://example.com/billing.yaml",
        service_id: "billing-api",
        service_name: "Billing API",
        options: {
          runtime_mode: "codegen",
          force_protocol: "openapi",
          skip_enhancement: true,
          tenant: "team-a",
          environment: "prod",
          auth: {
            type: "bearer",
            runtime_secret_ref: "vault://secrets/billing-token",
          },
        },
      }),
    );

    expect(toastMock.success).toHaveBeenCalledWith(
      "Compilation job created successfully!",
    );
    expect(routerPushMock).toHaveBeenCalledWith("/compilations/job-123");
  });

  it("surfaces a fallback error when compilation creation fails", async () => {
    const user = userEvent.setup();
    createCompilationMock.mutateAsync.mockRejectedValue("boom");

    renderWizard();

    await goToReview(user);
    await user.click(screen.getByRole("button", { name: "Create Compilation" }));

    await waitFor(() =>
      expect(
        screen.getByText("Failed to create compilation."),
      ).toBeInTheDocument(),
    );
    expect(toastMock.error).toHaveBeenCalledWith("Failed to create compilation.");
    expect(routerPushMock).not.toHaveBeenCalled();
  });
});
