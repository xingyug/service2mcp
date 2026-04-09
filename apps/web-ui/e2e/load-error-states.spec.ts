import { expect, test, type Page, type Route } from "@playwright/test";

import { setupAuth } from "./helpers";

async function mockJson(page: Page, url: string | RegExp, payload: unknown) {
  await page.route(url, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

async function mockApiError(page: Page, url: string | RegExp) {
  await page.route(url, async (route: Route) => {
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "backend failed" }),
    });
  });
}

const scopedServiceResponse = {
  service_id: "svc-1",
  service_name: "Billing API",
  active_version: 7,
  version_count: 3,
  tool_count: 2,
  protocol: "openapi",
  tenant: "team-a",
  environment: "prod",
  created_at: "2026-03-29T00:00:00Z",
};

test.describe("Dashboard load error states", () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  test("PAT page shows the query error instead of an empty state", async ({
    page,
  }) => {
    await mockApiError(
      page,
      /http:\/\/localhost:8001\/api\/v1\/authn\/pats\?username=.*/,
    );

    await page.goto("/pats");

    await expect(page.getByText("Failed to load personal access tokens")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByText("No personal access tokens")).not.toBeVisible();
  });

  test("Policies page shows the query error instead of an empty state", async ({
    page,
  }) => {
    await mockApiError(
      page,
      /http:\/\/localhost:8001\/api\/v1\/authz\/policies(\?.*)?$/,
    );

    await page.goto("/policies");

    await expect(page.getByText("Failed to load policies")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByText("No policies found")).not.toBeVisible();
  });

  test("Audit page shows the query error instead of an empty state", async ({
    page,
  }) => {
    await mockApiError(
      page,
      /http:\/\/localhost:8001\/api\/v1\/audit\/logs(\?.*)?$/,
    );

    await page.goto("/audit");

    await expect(page.getByText("Failed to load audit log entries")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByText("No audit events found")).not.toBeVisible();
  });

  test("Compilations page shows the query error instead of an empty state", async ({
    page,
  }) => {
    await mockApiError(page, "http://localhost:8000/api/v1/compilations");

    await page.goto("/compilations");

    await expect(page.getByText("Failed to load compilation jobs")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByText("No compilation jobs found")).not.toBeVisible();
  });

  test("Gateway page shows the service query error instead of empty gateway data", async ({
    page,
  }) => {
    await mockApiError(page, "http://localhost:8000/api/v1/services");
    await mockJson(
      page,
      "http://localhost:8001/api/v1/gateway-binding/service-routes",
      { items: [] },
    );

    await page.goto("/gateway");

    await expect(page.getByText("Failed to load services")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByText("No service routes found.")).not.toBeVisible();
  });

  test("Service detail shows version load errors instead of version-dependent tabs", async ({
    page,
  }) => {
    await mockJson(
      page,
      "http://localhost:8000/api/v1/services/svc-1?tenant=team-a&environment=prod",
      scopedServiceResponse,
    );
    await mockApiError(
      page,
      "http://localhost:8000/api/v1/artifacts/svc-1/versions?tenant=team-a&environment=prod",
    );

    await page.goto("/services/svc-1?tenant=team-a&environment=prod");

    await expect(page.getByText("Failed to load artifact versions")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByRole("tab", { name: "Versions" })).not.toBeVisible();
  });

  test("Versions page shows version load errors instead of an empty state", async ({
    page,
  }) => {
    await mockJson(
      page,
      "http://localhost:8000/api/v1/services/svc-1?tenant=team-a&environment=prod",
      scopedServiceResponse,
    );
    await mockApiError(
      page,
      "http://localhost:8000/api/v1/artifacts/svc-1/versions?tenant=team-a&environment=prod",
    );

    await page.goto("/services/svc-1/versions?tenant=team-a&environment=prod");

    await expect(page.getByText("Failed to load artifact versions")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByText("No versions found.")).not.toBeVisible();
  });

  test("Review page shows version load errors without treating them as missing IR", async ({
    page,
  }) => {
    await mockJson(
      page,
      "http://localhost:8000/api/v1/services/svc-1?tenant=team-a&environment=prod",
      scopedServiceResponse,
    );
    await mockApiError(
      page,
      "http://localhost:8000/api/v1/artifacts/svc-1/versions?tenant=team-a&environment=prod",
    );

    await page.goto("/services/svc-1/review?tenant=team-a&environment=prod&version=7");

    await expect(page.getByText("Failed to load artifact versions")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByText("Review & Approval")).not.toBeVisible();
  });

  test("Compilation detail shows load errors instead of a not-found state", async ({
    page,
  }) => {
    await mockApiError(page, "http://localhost:8000/api/v1/compilations/job-1");

    await page.goto("/compilations/job-1");

    await expect(page.getByText("Failed to load compilation job")).toBeVisible();
    await expect(page.getByText(/API error 500/)).toBeVisible();
    await expect(page.getByText("Compilation job not found.")).not.toBeVisible();
  });
});
