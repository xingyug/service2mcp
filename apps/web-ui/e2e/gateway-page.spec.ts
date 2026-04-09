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

const versionOneRouteConfig = {
  service_id: "svc-1",
  service_name: "Billing API",
  namespace: "runtime-system",
  version_number: 1,
  default_route: {
    route_id: "svc-1-active",
    target_service: {
      name: "billing-runtime-v1",
      namespace: "runtime-system",
      port: 8003,
    },
  },
  version_route: {
    route_id: "svc-1-v1",
    target_service: {
      name: "billing-runtime-v1",
      namespace: "runtime-system",
      port: 8003,
    },
    match: {
      headers: {
        "x-tool-compiler-version": "1",
      },
    },
  },
};

const versionThreeRouteConfig = {
  ...versionOneRouteConfig,
  version_number: 3,
  default_route: {
    ...versionOneRouteConfig.default_route,
    target_service: {
      name: "billing-runtime-v3",
      namespace: "runtime-system",
      port: 8003,
    },
  },
  version_route: {
    ...versionOneRouteConfig.version_route,
    route_id: "svc-1-v3",
    target_service: {
      name: "billing-runtime-v3",
      namespace: "runtime-system",
      port: 8003,
    },
    match: {
      headers: {
        "x-tool-compiler-version": "3",
      },
    },
  },
};

const versionSevenRouteConfig = {
  ...versionOneRouteConfig,
  version_number: 7,
  default_route: {
    ...versionOneRouteConfig.default_route,
    target_service: {
      name: "billing-runtime-v7",
      namespace: "runtime-system",
      port: 8003,
    },
  },
  version_route: {
    ...versionOneRouteConfig.version_route,
    route_id: "svc-1-v7",
    target_service: {
      name: "billing-runtime-v7",
      namespace: "runtime-system",
      port: 8003,
    },
    match: {
      headers: {
        "x-tool-compiler-version": "7",
      },
    },
  },
};

test.describe("Gateway page", () => {
  test("sync routes sends current gateway routes as previous_routes", async ({
    page,
  }) => {
    await setupAuth(page);

    let syncPayload: Record<string, unknown> | undefined;

    await mockJson(page, "http://localhost:8000/api/v1/services", {
      services: [
        {
          service_id: "svc-1",
          active_version: 7,
          version_count: 3,
          service_name: "Billing API",
          tool_count: 2,
          created_at: "2026-03-29T02:00:00Z",
          protocol: "openapi",
        },
      ],
    });
    await mockJson(page, "http://localhost:8000/api/v1/artifacts/svc-1/versions", {
      versions: [
        {
          service_id: "svc-1",
          version_number: 1,
          is_active: false,
          created_at: "2026-03-29T00:00:00Z",
          ir: {},
          route_config: versionOneRouteConfig,
        },
        {
          service_id: "svc-1",
          version_number: 3,
          is_active: false,
          created_at: "2026-03-29T00:30:00Z",
          ir: {},
          route_config: versionThreeRouteConfig,
        },
        {
          service_id: "svc-1",
          version_number: 7,
          is_active: true,
          created_at: "2026-03-29T01:00:00Z",
          ir: {},
          route_config: versionSevenRouteConfig,
        },
      ],
    });
    await mockJson(
      page,
      "http://localhost:8001/api/v1/gateway-binding/service-routes",
      {
        items: [
          {
            route_id: "svc-1-active",
            route_type: "default",
            service_id: "svc-1",
            service_name: "Billing API",
            namespace: "runtime-system",
            target_service: {
              name: "billing-runtime-v7",
              namespace: "runtime-system",
              port: 8003,
            },
            version_number: 7,
          },
          {
            route_id: "svc-1-v7",
            route_type: "version",
            service_id: "svc-1",
            service_name: "Billing API",
            namespace: "runtime-system",
            target_service: {
              name: "billing-runtime-v7",
              namespace: "runtime-system",
              port: 8003,
            },
            version_number: 7,
            match: {
              headers: {
                "x-tool-compiler-version": "7",
              },
            },
          },
        ],
      },
    );
    await page.route(
      "http://localhost:8001/api/v1/gateway-binding/service-routes/sync",
      async (route) => {
        syncPayload = route.request().postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            route_ids: ["svc-1-active", "svc-1-v3"],
            service_routes_synced: 2,
            service_routes_deleted: 1,
            previous_routes: {},
          }),
        });
      },
    );

    await page.goto("/gateway");
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("Artifact Timestamp")).toBeVisible();
    await expect(page.getByText("Gateway Deployment History")).toBeVisible();
    await expect(
      page.getByText(/does not currently persist gateway sync\/rollback\/delete events/i),
    ).toBeVisible();

    await page.getByRole("button", { name: "Sync Routes" }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByPlaceholder("Enter service ID").fill("svc-1");
    await dialog.getByPlaceholder("Version number").fill("3");
    await dialog.getByRole("button", { name: "Sync Routes" }).click();

    await expect.poll(() => syncPayload).toBeTruthy();
    const previousRoutes = syncPayload?.previous_routes as Record<
      string,
      Record<string, unknown>
    >;
    expect(Object.keys(previousRoutes).sort()).toEqual([
      "svc-1-active",
      "svc-1-v7",
    ]);
  });

  test("rollback defaults to the latest real prior version", async ({ page }) => {
    await setupAuth(page);

    await mockJson(page, "http://localhost:8000/api/v1/services", {
      services: [
        {
          service_id: "svc-1",
          active_version: 7,
          version_count: 3,
          service_name: "Billing API",
          tool_count: 2,
          created_at: "2026-03-29T02:00:00Z",
          protocol: "openapi",
        },
      ],
    });
    await mockJson(page, "http://localhost:8000/api/v1/artifacts/svc-1/versions", {
      versions: [
        {
          service_id: "svc-1",
          version_number: 1,
          is_active: false,
          created_at: "2026-03-29T00:00:00Z",
          ir: {},
          route_config: versionOneRouteConfig,
        },
        {
          service_id: "svc-1",
          version_number: 3,
          is_active: false,
          created_at: "2026-03-29T00:30:00Z",
          ir: {},
          route_config: versionThreeRouteConfig,
        },
        {
          service_id: "svc-1",
          version_number: 7,
          is_active: true,
          created_at: "2026-03-29T01:00:00Z",
          ir: {},
          route_config: versionSevenRouteConfig,
        },
      ],
    });
    await mockJson(
      page,
      "http://localhost:8001/api/v1/gateway-binding/service-routes",
      {
        items: [],
      },
    );

    await page.goto("/gateway");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: "Rollback" }).click();
    await page.getByPlaceholder("Enter service ID").fill("svc-1");

    await expect(
      page.getByPlaceholder("Previous version number"),
    ).toHaveValue("3");
  });
});
