import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/__tests__/test-utils";

import ServicesPage from "../page";

const services = [
  {
    service_id: "svc-jsonrpc",
    name: "Aria2 RPC",
    protocol: "jsonrpc",
    active_version: 1,
    version_count: 1,
    last_compiled: "2026-03-29T00:00:00Z",
  },
  {
    service_id: "svc-odata",
    name: "NorthBreeze",
    protocol: "odata",
    active_version: 2,
    version_count: 2,
    last_compiled: "2026-03-29T00:00:00Z",
  },
  {
    service_id: "svc-scim",
    name: "Jackson SCIM",
    protocol: "scim",
    active_version: 3,
    version_count: 3,
    last_compiled: "2026-03-29T00:00:00Z",
  },
  {
    service_id: "svc-rest",
    name: "Directus REST",
    protocol: "rest",
    active_version: 4,
    version_count: 4,
    last_compiled: "2026-03-29T00:00:00Z",
  },
];

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("@/hooks/use-api", () => ({
  useServices: () => ({
    data: { services },
    isLoading: false,
    error: null,
  }),
}));

vi.mock("@/components/services/service-card", () => ({
  ServiceCard: ({
    service,
  }: {
    service: { name: string; protocol: string };
  }) => <div>{`${service.name} (${service.protocol})`}</div>,
}));

vi.mock("@/components/services/protocol-badge", () => ({
  ProtocolBadge: ({ protocol }: { protocol: string }) => <span>{protocol}</span>,
}));

describe("ServicesPage", () => {
  it("renders protocol filters for jsonrpc, odata, and scim", () => {
    renderWithProviders(<ServicesPage />);

    expect(screen.getByRole("button", { name: "jsonrpc" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "odata" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "scim" })).toBeInTheDocument();
  });

  it("filters the service list by a newly supported protocol", async () => {
    const user = userEvent.setup();

    renderWithProviders(<ServicesPage />);

    expect(screen.getByText("Aria2 RPC (jsonrpc)")).toBeInTheDocument();
    expect(screen.getByText("NorthBreeze (odata)")).toBeInTheDocument();
    expect(screen.getByText("Jackson SCIM (scim)")).toBeInTheDocument();
    expect(screen.getByText("Directus REST (rest)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "jsonrpc" }));

    expect(screen.getByText("Aria2 RPC (jsonrpc)")).toBeInTheDocument();
    expect(screen.queryByText("NorthBreeze (odata)")).not.toBeInTheDocument();
    expect(screen.queryByText("Jackson SCIM (scim)")).not.toBeInTheDocument();
    expect(screen.queryByText("Directus REST (rest)")).not.toBeInTheDocument();
  });

  it("filters the service list by search term without crashing", async () => {
    const user = userEvent.setup();

    renderWithProviders(<ServicesPage />);

    await user.type(screen.getByPlaceholderText("Search services…"), "north");

    expect(screen.queryByText("Aria2 RPC (jsonrpc)")).not.toBeInTheDocument();
    expect(screen.getByText("NorthBreeze (odata)")).toBeInTheDocument();
    expect(screen.queryByText("Jackson SCIM (scim)")).not.toBeInTheDocument();
    expect(screen.queryByText("Directus REST (rest)")).not.toBeInTheDocument();
  });

  it("matches the stable service_id in search results", async () => {
    const user = userEvent.setup();

    renderWithProviders(<ServicesPage />);

    await user.type(screen.getByPlaceholderText("Search services…"), "svc-odata");

    expect(screen.queryByText("Aria2 RPC (jsonrpc)")).not.toBeInTheDocument();
    expect(screen.getByText("NorthBreeze (odata)")).toBeInTheDocument();
    expect(screen.queryByText("Jackson SCIM (scim)")).not.toBeInTheDocument();
    expect(screen.queryByText("Directus REST (rest)")).not.toBeInTheDocument();
  });
});
