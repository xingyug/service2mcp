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

async function mockScopedServices(page: Page) {
  await page.route(
    "http://localhost:8000/api/v1/services",
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          services: [
            {
              service_id: "billing-api",
              active_version: 1,
              service_name: "Billing API Prod",
              tool_count: 3,
              protocol: "openapi",
              tenant: "team-a",
              environment: "prod",
              created_at: "2026-03-29T00:00:00Z",
            },
            {
              service_id: "billing-api",
              active_version: 2,
              service_name: "Billing API Staging",
              tool_count: 4,
              protocol: "openapi",
              tenant: "team-b",
              environment: "staging",
              created_at: "2026-03-29T00:00:00Z",
            },
          ],
        }),
      });
    },
  );
}

async function mockLargeServiceCatalog(page: Page) {
  await page.route(
    "http://localhost:8000/api/v1/services",
    async (route: Route) => {
      const services = Array.from({ length: 1001 }, (_, index) => ({
        service_id: `svc-${index + 1}`,
        active_version: 1,
        service_name:
          index === 1000 ? "Long Tail Service" : `Service ${index + 1}`,
        tool_count: 1,
        protocol: "openapi",
        created_at: "2026-03-29T00:00:00Z",
      }));

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ services }),
      });
    },
  );
}

async function mockCompilationJob(page: Page) {
  await page.route(
    "http://localhost:8000/api/v1/compilations/job-1",
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "job-1",
          status: "succeeded",
          protocol: "openapi",
          current_stage: "register",
          created_at: "2026-03-29T00:00:00Z",
          updated_at: "2026-03-29T00:05:00Z",
          service_id: "billing-api",
          service_name: "Billing API",
          tenant: "team-a",
          environment: "prod",
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

  test("search also matches the stable service_id", async ({ page }) => {
    await setupAuth(page);
    await mockServices(page);

    await page.goto("/services");
    await page.waitForLoadState("networkidle");

    await page.getByPlaceholder("Search services…").fill("svc-north");

    await expect(page.getByText("NorthBreeze")).toBeVisible();
    await expect(page.getByText("Aria2 RPC")).not.toBeVisible();
  });

  test("service cards preserve tenant/environment in scoped detail links", async ({
    page,
  }) => {
    await setupAuth(page);
    await mockScopedServices(page);

    await page.goto("/services");
    await page.waitForLoadState("networkidle");

    await expect(
      page.locator('a[href="/services/billing-api?tenant=team-a&environment=prod"]'),
    ).toHaveCount(1);
    await expect(
      page.locator(
        'a[href="/services/billing-api?tenant=team-b&environment=staging"]',
      ),
    ).toHaveCount(1);
  });

  test("search can still find services beyond the old 1000-row backend cap", async ({
    page,
  }) => {
    await setupAuth(page);
    await mockLargeServiceCatalog(page);

    await page.goto("/services");
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("1001")).toBeVisible();

    await page.getByPlaceholder("Search services…").fill("long tail");

    await expect(page.getByText("Long Tail Service")).toBeVisible();
    await expect(page.getByText("Service 1")).not.toBeVisible();
  });

  test("compilation detail links to the scoped service using service_id", async ({
    page,
  }) => {
    await setupAuth(page);
    await mockCompilationJob(page);

    await page.goto("/compilations/job-1");
    await page.waitForLoadState("networkidle");

    await expect(
      page.locator(
        'a[href="/services/billing-api?tenant=team-a&environment=prod"]',
      ),
    ).toHaveCount(1);
    await expect(
      page.locator(
        'a[href="/services/Billing%20API?tenant=team-a&environment=prod"]',
      ),
    ).toHaveCount(0);
  });
});
