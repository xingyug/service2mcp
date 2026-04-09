import { expect, test, type Page, type Route } from "@playwright/test";

async function setupAuth(page: Page, user: Record<string, unknown>) {
  await page.goto("/login");
  await page.evaluate((storedUser) => {
    localStorage.setItem(
      "auth-storage",
      JSON.stringify({
        state: {
          token: "test-token",
          user: storedUser,
          isAuthenticated: true,
        },
        version: 0,
      }),
    );
    localStorage.setItem("auth_token", "test-token");
  }, user);
}

test.describe("Audit page", () => {
  test("exports the full filtered dataset with include_all enabled", async ({ page }) => {
    let sawFullExportRequest = false;

    await page.route(
      /http:\/\/localhost:8001\/api\/v1\/audit\/logs.*/,
      async (route: Route) => {
        const url = new URL(route.request().url());
        const includeAll = url.searchParams.get("include_all") === "true";
        if (includeAll) {
          sawFullExportRequest = true;
        }

        const items = includeAll
          ? Array.from({ length: 1002 }, (_, index) => ({
              id: `entry-${index + 1}`,
              actor: "alice",
              action: "policy.created",
              resource: "svc-1",
              detail: { batch: "full" },
              timestamp: `2026-03-30T00:00:${String(index % 60).padStart(2, "0")}Z`,
            }))
          : [
              {
                id: "entry-1",
                actor: "alice",
                action: "policy.created",
                resource: "svc-1",
                detail: { batch: "paged" },
                timestamp: "2026-03-30T00:00:00Z",
              },
            ];

        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items }),
        });
      },
    );

    await setupAuth(page, {
      username: "alice",
      subject: "alice",
      roles: ["admin"],
    });

    await page.goto("/audit");
    await expect(page.getByRole("heading", { name: "Audit Log" })).toBeVisible();

    await page.getByRole("button", { name: "Export CSV" }).click();

    await expect.poll(() => sawFullExportRequest).toBe(true);
    await expect(page.getByText("Exported 1002 matching entries")).toBeVisible();
  });
});
