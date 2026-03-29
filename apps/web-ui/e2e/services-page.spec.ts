import { expect, test, type Page, type Route } from "@playwright/test";

import { setupAuth } from "./helpers";

async function mockServices(page: Page) {
  await page.route(
    "http://localhost:8000/api/v1/services",
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: [
            {
              service_id: "svc-aria2",
              active_version: 1,
              service_name: "Aria2 RPC",
              tool_count: 3,
              protocol: "jsonrpc",
              created_at: "2026-03-29T00:00:00Z",
            },
            {
              service_id: "svc-north",
              active_version: 2,
              service_name: "NorthBreeze",
              tool_count: 5,
              protocol: "odata",
              created_at: "2026-03-29T00:00:00Z",
            },
          ],
        }),
      });
    },
  );
}

test.describe("Services page", () => {
  test("search filters normalized service names from the backend payload", async ({
    page,
  }) => {
    await setupAuth(page);
    await mockServices(page);

    await page.goto("/services");
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("Aria2 RPC")).toBeVisible();
    await expect(page.getByText("NorthBreeze")).toBeVisible();

    await page.getByPlaceholder("Search services…").fill("north");

    await expect(page.getByText("NorthBreeze")).toBeVisible();
    await expect(page.getByText("Aria2 RPC")).not.toBeVisible();
  });
});
