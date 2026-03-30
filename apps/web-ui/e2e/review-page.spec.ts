import { expect, test, type Page, type Route } from "@playwright/test";

import { setupAuth } from "./helpers";

const serviceResponse = {
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

const versionOne = {
  service_id: "svc-1",
  version_number: 1,
  is_active: false,
  created_at: "2026-03-27T00:00:00Z",
  ir_json: {
    service_name: "Billing API v1",
    operations: [],
    metadata: {},
    created_at: "2026-03-27T00:00:00Z",
  },
};

const versionThree = {
  service_id: "svc-1",
  version_number: 3,
  is_active: false,
  created_at: "2026-03-28T00:00:00Z",
  ir_json: {
    service_name: "Billing API v3",
    operations: [],
    metadata: {},
    created_at: "2026-03-28T00:00:00Z",
  },
};

const versionSeven = {
  service_id: "svc-1",
  version_number: 7,
  is_active: true,
  created_at: "2026-03-29T00:00:00Z",
  ir_json: {
    service_name: "Billing API v7",
    operations: [],
    metadata: {},
    created_at: "2026-03-29T00:00:00Z",
  },
};

async function mockJson(page: Page, url: string | RegExp, payload: unknown) {
  await page.route(url, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

async function mockReviewPageBase(page: Page) {
  await mockJson(
    page,
    "http://localhost:8000/api/v1/services/svc-1?tenant=team-a&environment=prod",
    serviceResponse,
  );
  await mockJson(
    page,
    "http://localhost:8000/api/v1/artifacts/svc-1/versions?tenant=team-a&environment=prod",
    {
      service_id: "svc-1",
      versions: [versionOne, versionThree, versionSeven],
    },
  );
}

test.describe("Review page", () => {
  test("invalid version queries show a validation error without loading workflow state", async ({
    page,
  }) => {
    await setupAuth(page);
    await mockReviewPageBase(page);

    let workflowRequests = 0;
    await page.route(
      /http:\/\/localhost:8000\/api\/v1\/workflows\/svc-1\/v\/.*/,
      async (route: Route) => {
        workflowRequests += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "00000000-0000-0000-0000-000000000001",
            service_id: "svc-1",
            version_number: 7,
            state: "draft",
            review_notes: null,
            history: [],
            created_at: "2026-03-29T00:00:00Z",
            updated_at: "2026-03-29T00:00:00Z",
          }),
        });
      },
    );

    await page.goto("/services/svc-1/review?tenant=team-a&environment=prod&version=foo");
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("Invalid review version")).toBeVisible();
    await expect(
      page.getByText("Choose a positive integer version number."),
    ).toBeVisible();
    expect(workflowRequests).toBe(0);
  });

  test("sparse review histories diff against the nearest real previous version", async ({
    page,
  }) => {
    await setupAuth(page);
    await mockReviewPageBase(page);
    await mockJson(
      page,
      "http://localhost:8000/api/v1/workflows/svc-1/v/7?tenant=team-a&environment=prod",
      {
        id: "00000000-0000-0000-0000-000000000001",
        service_id: "svc-1",
        version_number: 7,
        state: "approved",
        review_notes: null,
        history: [],
        created_at: "2026-03-29T00:00:00Z",
        updated_at: "2026-03-29T00:00:00Z",
      },
    );
    await mockJson(
      page,
      /http:\/\/localhost:8000\/api\/v1\/artifacts\/svc-1\/diff\?tenant=team-a&environment=prod&from=3&to=7/,
      {
        service_id: "svc-1",
        from_version: 3,
        to_version: 7,
        added_operations: [],
        removed_operations: [],
        changed_operations: [],
      },
    );
    await mockJson(
      page,
      "http://localhost:8000/api/v1/artifacts/svc-1/versions/3?tenant=team-a&environment=prod",
      versionThree,
    );
    await mockJson(
      page,
      "http://localhost:8000/api/v1/artifacts/svc-1/versions/7?tenant=team-a&environment=prod",
      versionSeven,
    );

    await page.goto("/services/svc-1/review?tenant=team-a&environment=prod&version=7");
    await page.waitForLoadState("networkidle");

    await page.getByRole("tab", { name: "Diff" }).click();

    await expect(page.getByText("Version Diff — v3 → v7")).toBeVisible();
  });
});
