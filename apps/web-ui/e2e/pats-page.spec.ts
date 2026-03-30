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
  }, user);
}

test.describe("PAT page", () => {
  test("uses the stored platform username instead of the JWT subject", async ({
    page,
  }) => {
    let requestedUsername: string | null = null;

    await page.route(
      /http:\/\/localhost:8001\/api\/v1\/authn\/pats\?username=.*/,
      async (route: Route) => {
        requestedUsername = new URL(route.request().url()).searchParams.get("username");
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: [] }),
        });
      },
    );

    await setupAuth(page, {
      username: "alice",
      subject: "alice@example.com",
      email: "alice@example.com",
      roles: ["admin"],
    });

    await page.goto("/pats");
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("heading", { name: "Personal Access Tokens" })).toBeVisible();
    expect(requestedUsername).toBe("alice");
  });

  test("creates PATs without sending the stored email address", async ({ page }) => {
    let createRequestBody: Record<string, unknown> | null = null;

    await page.route(
      /http:\/\/localhost:8001\/api\/v1\/authn\/pats\?username=.*/,
      async (route: Route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: [] }),
        });
      },
    );
    await page.route("http://localhost:8001/api/v1/authn/pats", async (route: Route) => {
      createRequestBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "pat-1",
          username: "alice",
          name: "CLI token",
          token: "pat_secret",
          created_at: "2026-03-30T00:00:00Z",
          revoked_at: null,
        }),
      });
    });

    await setupAuth(page, {
      username: "alice",
      subject: "alice@example.com",
      email: "alice@example.com",
      roles: ["admin"],
    });

    await page.goto("/pats");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: "Create Token" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Create Personal Access Token" });
    await dialog.getByLabel("Token Name").fill("CLI token");
    await dialog.getByRole("button", { name: "Create Token" }).click();

    await expect(page.getByRole("dialog", { name: "Token Created" })).toBeVisible();
    expect(createRequestBody).toEqual({
      username: "alice",
      name: "CLI token",
    });
  });

  test("supports paginating beyond the first PAT page", async ({ page }) => {
    await page.route(
      /http:\/\/localhost:8001\/api\/v1\/authn\/pats\?username=.*/,
      async (route: Route) => {
        const requestUrl = new URL(route.request().url());
        const currentPage = Number(requestUrl.searchParams.get("page") ?? "1");
        const items =
          currentPage === 1
            ? Array.from({ length: 100 }, (_, index) => ({
                id: `pat-${101 - index}`,
                username: "alice",
                name: `Token ${101 - index}`,
                created_at: "2026-03-30T00:00:00Z",
                revoked_at: null,
              }))
            : [
                {
                  id: "pat-1",
                  username: "alice",
                  name: "Token 1",
                  created_at: "2026-03-29T00:00:00Z",
                  revoked_at: null,
                },
              ];
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items,
            total: 101,
            page: currentPage,
            page_size: 100,
          }),
        });
      },
    );

    await setupAuth(page, {
      username: "alice",
      subject: "alice@example.com",
      email: "alice@example.com",
      roles: ["admin"],
    });

    await page.goto("/pats");
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("Showing 1–100 of 101")).toBeVisible();
    await expect(page.getByText("Token 101", { exact: true })).toBeVisible();
    await expect(page.getByText("Token 1", { exact: true })).not.toBeVisible();

    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("Showing 101–101 of 101")).toBeVisible();
    await expect(page.getByText("Token 1", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  });
});
