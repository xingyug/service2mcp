import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NewCompilationPage from "../page";

const { mockWizard } = vi.hoisted(() => ({
  mockWizard: vi.fn(),
}));

let search = "service_id=svc-1&service_name=Billing%20API";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search),
}));

vi.mock("@/components/compilations/compilation-wizard", () => ({
  CompilationWizard: (props: Record<string, unknown>) => {
    mockWizard(props);
    return <div>compilation-wizard</div>;
  },
}));

describe("NewCompilationPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    search = "service_id=svc-1&service_name=Billing%20API";
  });

  it("passes both the stable service id and display name into the wizard", () => {
    render(<NewCompilationPage />);

    expect(screen.getByText("compilation-wizard")).toBeInTheDocument();
    expect(mockWizard).toHaveBeenCalledWith({
      initialServiceId: "svc-1",
      initialServiceName: "Billing API",
    });
  });
});
