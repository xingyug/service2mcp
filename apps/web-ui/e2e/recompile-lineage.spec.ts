import { expect, test, type Route } from "@playwright/test";

import { setupAuth } from "./helpers";

test.describe("Recompile lineage", () => {
  test("preserves the stable service_id when recompiling from a service detail entry point", async ({
    page,
  }) => {
    await setupAuth(page);

    let createPayload: Record<string, unknown> | undefined;
    await page.route(
      "http://localhost:8000/api/v1/compilations/job-1",
      async (route: Route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "job-1",
            status: "pending",
            current_stage: "extract",
            created_at: "2026-03-29T00:00:00Z",
            updated_at: "2026-03-29T00:00:00Z",
            service_id: "svc-1",
            service_name: "svc-1",
          }),
        });
      },
    );
    await page.route(
      "http://localhost:8000/api/v1/compilations",
      async (route: Route) => {
        createPayload = route.request().postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            id: "job-1",
            status: "pending",
            current_stage: "extract",
            created_at: "2026-03-29T00:00:00Z",
            updated_at: "2026-03-29T00:00:00Z",
            service_id: "svc-1",
            service_name: "svc-1",
          }),
        });
      },
    );

    await page.goto("/compilations/new?service_id=svc-1&service_name=Billing%20API");

    await expect(page.getByLabel("Service Name")).toHaveValue("Billing API");
    await page
      .getByPlaceholder("https://api.example.com/openapi.yaml")
      .fill("https://example.com/openapi.yaml");

    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Create Compilation" }).click();

    await expect.poll(() => createPayload?.service_id).toBe("svc-1");
    expect(createPayload?.service_name).toBe("Billing API");
  });
});
