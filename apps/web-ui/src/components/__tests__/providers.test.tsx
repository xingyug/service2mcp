import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { Providers } from "../providers";

const seenClients: QueryClient[] = [];

function QueryClientProbe({ label }: { label: string }) {
  const queryClient = useQueryClient();
  seenClients.push(queryClient);

  const queryDefaults = queryClient.getDefaultOptions().queries ?? {};

  return (
    <div>
      <span>{label}</span>
      <output data-testid="query-defaults">
        {JSON.stringify({
          staleTime: queryDefaults.staleTime,
          retry: queryDefaults.retry,
          refetchOnWindowFocus: queryDefaults.refetchOnWindowFocus,
        })}
      </output>
    </div>
  );
}

describe("Providers", () => {
  beforeEach(() => {
    seenClients.length = 0;
  });

  it("wraps children with the configured query client defaults", () => {
    render(
      <Providers>
        <QueryClientProbe label="inside providers" />
      </Providers>,
    );

    expect(screen.getByText("inside providers")).toBeInTheDocument();
    expect(screen.getByTestId("query-defaults")).toHaveTextContent(
      JSON.stringify({
        staleTime: 30_000,
        retry: 1,
        refetchOnWindowFocus: false,
      }),
    );
  });

  it("reuses the same query client across rerenders", () => {
    const { rerender } = render(
      <Providers>
        <QueryClientProbe label="first render" />
      </Providers>,
    );

    const firstClient = seenClients.at(-1);

    rerender(
      <Providers>
        <QueryClientProbe label="second render" />
      </Providers>,
    );

    const secondClient = seenClients.at(-1);

    expect(firstClient).toBeDefined();
    expect(secondClient).toBeDefined();
    expect(secondClient).toBe(firstClient);
  });
});
