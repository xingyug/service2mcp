import { expect, test, type Page, type Route } from "@playwright/test";

import { setupAuth } from "./helpers";

async function mockJson(
  page: Page,
  url: string | RegExp,
  payload: unknown,
) {
  await page.route(url, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

test.describe("Compilation status and dashboard flows", () => {
  test("dashboard metrics reflect live lower-case compilation statuses", async ({
    page,
  }) => {
    await setupAuth(page);

    await mockJson(page, "http://localhost:8000/api/v1/services", {
      services: [
        {
          service_id: "svc-openapi",
          active_version: 3,
          service_name: "Directus OpenAPI",
          tool_count: 4,
          created_at: "2026-03-29T00:00:00Z",
          protocol: "openapi",
        },
        {
          service_id: "svc-rest",
          active_version: 1,
          service_name: "PocketBase REST",
          tool_count: 3,
          created_at: "2026-03-29T01:00:00Z",
          protocol: "rest",
        },
      ],
    });

    await mockJson(page, "http://localhost:8000/api/v1/compilations", [
      {
        id: "job-succeeded",
        status: "succeeded",
        protocol: "openapi",
        current_stage: "register",
        created_at: "2026-03-29T02:00:00Z",
        updated_at: "2026-03-29T02:05:00Z",
        service_name: "Directus OpenAPI",
      },
      {
        id: "job-failed",
        status: "failed",
        protocol: "rest",
        current_stage: "deploy",
        error_detail: "boom",
        created_at: "2026-03-29T03:00:00Z",
        updated_at: "2026-03-29T03:02:00Z",
        service_name: "PocketBase REST",
      },
      {
        id: "job-running",
        status: "running",
        protocol: "grpc",
        current_stage: "extract",
        created_at: "2026-03-29T04:00:00Z",
        updated_at: "2026-03-29T04:01:00Z",
        service_name: "OpenFGA",
      },
    ]);

    await mockJson(page, /http:\/\/localhost:8001\/api\/v1\/audit\/logs.*/, {
      items: [
        {
          id: "audit-1",
          actor: "admin",
          action: "create-policy",
          resource: "policy-1",
          detail: { outcome: "ok" },
          timestamp: "2026-03-29T04:30:00Z",
        },
      ],
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await expect(
      page.getByRole("heading", { name: "Dashboard" }),
    ).toBeVisible();
    await expect(page.getByText("50% success rate")).toBeVisible();
    await expect(page.getByText("Active Tools")).toBeVisible();
    await expect(page.getByText("Across 2 services")).toBeVisible();
    await expect(page.getByText("Succeeded (1)")).toBeVisible();
    await expect(page.getByText("Failed (1)")).toBeVisible();
    await expect(page.getByText("In Progress (1)")).toBeVisible();
    await expect(page.getByText("openapi")).toBeVisible();
    await expect(page.getByText("rest")).toBeVisible();
  });

  test("dashboard degrades overall health when audit loading fails", async ({
    page,
  }) => {
    await setupAuth(page);

    await mockJson(page, "http://localhost:8000/api/v1/services", {
      services: [],
    });
    await mockJson(page, "http://localhost:8000/api/v1/compilations", []);
    await page.route(/http:\/\/localhost:8001\/api\/v1\/audit\/logs.*/, async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "audit unavailable" }),
      });
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("Degraded")).toBeVisible();
    await expect(page.getByText("Some APIs unreachable")).toBeVisible();
    await expect(page.getByText("Failed to load audit logs.")).toBeVisible();
  });

  test("failed compilation detail shows retry controls and error details", async ({
    page,
  }) => {
    await setupAuth(page);

    await mockJson(
      page,
      "http://localhost:8000/api/v1/compilations/job-failed",
      {
        id: "job-failed",
        status: "failed",
        current_stage: "deploy",
        error_detail: "boom",
        created_at: "2026-03-29T03:00:00Z",
        updated_at: "2026-03-29T03:02:00Z",
        service_name: "PocketBase REST",
      },
    );

    await page.goto("/compilations/job-failed");
    await page.waitForLoadState("networkidle");

    await expect(
      page.locator("[data-slot='card-title']").filter({ hasText: "Error" }),
    ).toBeVisible();
    await expect(page.getByText("boom")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Retry from deploy/i }).first(),
    ).toBeVisible();
  });

  test("retry redirects to the new compilation job", async ({ page }) => {
    await setupAuth(page);

    let retryRequests = 0;

    await mockJson(
      page,
      "http://localhost:8000/api/v1/compilations/job-failed",
      {
        id: "job-failed",
        status: "failed",
        current_stage: "deploy",
        failed_stage: "deploy",
        error_detail: "boom",
        created_at: "2026-03-29T03:00:00Z",
        updated_at: "2026-03-29T03:02:00Z",
        service_name: "PocketBase REST",
      },
    );
    await page.route(
      "http://localhost:8000/api/v1/compilations/job-failed/retry?from_stage=deploy",
      async (route) => {
        retryRequests += 1;
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            id: "job-retry-2",
            status: "pending",
            current_stage: "queued",
            created_at: "2026-03-29T03:03:00Z",
            updated_at: "2026-03-29T03:03:00Z",
            service_name: "PocketBase REST",
          }),
        });
      },
    );
    await mockJson(
      page,
      "http://localhost:8000/api/v1/compilations/job-retry-2",
      {
        id: "job-retry-2",
        status: "pending",
        current_stage: "queued",
        created_at: "2026-03-29T03:03:00Z",
        updated_at: "2026-03-29T03:03:00Z",
        service_name: "PocketBase REST",
      },
    );

    await page.goto("/compilations/job-failed");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /Retry from deploy/i }).first().click();

    await expect.poll(() => retryRequests).toBe(1);
    await expect(page).toHaveURL(/\/compilations\/job-retry-2$/);
  });

  test("succeeded compilation detail shows rollback and artifacts", async ({
    page,
  }) => {
    await setupAuth(page);

    await mockJson(
      page,
      "http://localhost:8000/api/v1/compilations/job-succeeded",
      {
        id: "job-succeeded",
        status: "succeeded",
        current_stage: "register",
        created_at: "2026-03-29T02:00:00Z",
        updated_at: "2026-03-29T02:05:00Z",
        service_name: "Directus OpenAPI",
      },
    );

    await page.goto("/compilations/job-succeeded");
    await page.waitForLoadState("networkidle");

    await expect(
      page.getByRole("button", { name: /Rollback/i }),
    ).toBeVisible();
    await expect(page.getByText("Artifacts")).toBeVisible();
    await expect(page.getByText("IR ID")).toBeVisible();
  });

  test("rollback redirects to the new compilation job", async ({ page }) => {
    await setupAuth(page);

    let rollbackRequests = 0;

    await mockJson(
      page,
      "http://localhost:8000/api/v1/compilations/job-succeeded",
      {
        id: "job-succeeded",
        status: "succeeded",
        current_stage: "register",
        created_at: "2026-03-29T02:00:00Z",
        updated_at: "2026-03-29T02:05:00Z",
        service_name: "Directus OpenAPI",
      },
    );
    await page.route(
      "http://localhost:8000/api/v1/compilations/job-succeeded/rollback",
      async (route) => {
        rollbackRequests += 1;
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            id: "job-rollback-2",
            status: "pending",
            current_stage: "queued",
            created_at: "2026-03-29T02:06:00Z",
            updated_at: "2026-03-29T02:06:00Z",
            service_name: "Directus OpenAPI",
          }),
        });
      },
    );
    await mockJson(
      page,
      "http://localhost:8000/api/v1/compilations/job-rollback-2",
      {
        id: "job-rollback-2",
        status: "pending",
        current_stage: "queued",
        created_at: "2026-03-29T02:06:00Z",
        updated_at: "2026-03-29T02:06:00Z",
        service_name: "Directus OpenAPI",
      },
    );

    await page.goto("/compilations/job-succeeded");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /Rollback/i }).click();

    await expect.poll(() => rollbackRequests).toBe(1);
    await expect(page).toHaveURL(/\/compilations\/job-rollback-2$/);
  });

  test("running compilation detail opens the SSE stream", async ({ page }) => {
    await setupAuth(page);

    let eventsRequested = 0;

    await mockJson(
      page,
      "http://localhost:8000/api/v1/compilations/job-running",
      {
        id: "job-running",
        status: "running",
        current_stage: "extract",
        created_at: "2026-03-29T04:00:00Z",
        updated_at: "2026-03-29T04:01:00Z",
        service_name: "OpenFGA",
      },
    );

    await page.route(
      "http://localhost:8000/api/v1/compilations/job-running/events",
      async (route) => {
        eventsRequested += 1;
        await route.fulfill({
          status: 200,
          headers: {
            "content-type": "text/event-stream",
          },
          body: [
            "event: stage.started",
            'data: {"event_type":"stage.started","stage":"extract","detail":{"status":"running"},"created_at":"2026-03-29T04:00:30Z"}',
            "",
          ].join("\n"),
        });
      },
    );

    await page.goto("/compilations/job-running");
    await page.waitForLoadState("domcontentloaded");

    await expect(page.getByText("Running", { exact: true })).toBeVisible();
    await expect.poll(() => eventsRequested).toBeGreaterThan(0);
  });
});
