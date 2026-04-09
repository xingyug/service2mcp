import { test, expect } from "@playwright/test";

test.describe("Login Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
  });

  test("renders login page with title and description", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "service2mcp" })).toBeVisible();
    await expect(page.getByText("Enterprise API-to-MCP Tool Compilation Platform")).toBeVisible();
    // CardTitle renders as <div>, not a heading element
    await expect(page.locator("[data-slot='card-title']").getByText("Sign in")).toBeVisible();
  });

  test("renders JWT token field on the default tab", async ({ page }) => {
    await expect(page.locator("#jwt-token")).toBeVisible();
    await expect(page.getByLabel("JWT Token")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in with JWT" })).toBeVisible();
  });

  test("can switch between JWT and PAT tabs", async ({ page }) => {
    await expect(page.locator("#jwt-token")).toBeVisible();

    await page.getByRole("tab", { name: "PAT Token" }).click();
    await expect(page.locator("#pat")).toBeVisible();
    await expect(page.getByLabel("Personal Access Token")).toBeVisible();
    await expect(page.locator("#jwt-token")).not.toBeVisible();

    await page.getByRole("tab", { name: "JWT Token" }).click();
    await expect(page.locator("#jwt-token")).toBeVisible();
    await expect(page.locator("#pat")).not.toBeVisible();
  });

  test("shows validation on submitting empty JWT form", async ({ page }) => {
    const jwtInput = page.locator("#jwt-token");
    await expect(jwtInput).toHaveAttribute("required", "");
  });

  test("shows validation on submitting empty PAT form", async ({ page }) => {
    await page.getByRole("tab", { name: "PAT Token" }).click();
    const patInput = page.locator("#pat");
    await expect(patInput).toHaveAttribute("required", "");
  });

  test("can fill in and submit JWT form", async ({ page }) => {
    await page.fill("#jwt-token", "test-jwt-token");
    await expect(page.locator("#jwt-token")).toHaveValue("test-jwt-token");

    await page.getByRole("button", { name: "Sign in with JWT" }).click();
    await expect(page.locator(".text-destructive").first()).toBeVisible({ timeout: 10000 });
  });

  test("can fill in and submit PAT form", async ({ page }) => {
    await page.getByRole("tab", { name: "PAT Token" }).click();
    await page.fill("#pat", "my-test-pat-token");
    await expect(page.locator("#pat")).toHaveValue("my-test-pat-token");

    await page.getByRole("button", { name: "Sign in with PAT" }).click();
    await expect(page.locator(".text-destructive").first()).toBeVisible({ timeout: 10000 });
  });

  test("successful login stores auth_token so dashboard requests stay authenticated", async ({
    page,
  }) => {
    let servicesAuthorization: string | undefined;

    await page.route("http://localhost:8001/api/v1/authn/validate", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          subject: "admin@example.com",
          username: "admin",
          token_type: "jwt",
          claims: {
            preferred_username: "admin",
            email: "admin@example.com",
            roles: ["admin"],
          },
        }),
      });
    });
    await page.route("http://localhost:8000/api/v1/services", async (route) => {
      servicesAuthorization = route.request().headers()["authorization"];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ services: [] }),
      });
    });
    await page.route("http://localhost:8000/api/v1/compilations", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    });
    await page.route("http://localhost:8001/api/v1/audit/logs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    });

    await page.fill("#jwt-token", "jwt-session-token");
    await page.getByRole("button", { name: "Sign in with JWT" }).click();

    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect.poll(() => servicesAuthorization).toBe(
      "Bearer jwt-session-token",
    );
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem("auth_token")))
      .toBe("jwt-session-token");
  });

  test("successful PAT login preserves admin roles in stored auth state", async ({
    page,
  }) => {
    await page.route("http://localhost:8001/api/v1/authn/validate", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          subject: "admin",
          username: "admin",
          token_type: "pat",
          claims: {
            roles: ["admin"],
          },
        }),
      });
    });
    await page.route("http://localhost:8000/api/v1/services", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ services: [] }),
      });
    });
    await page.route("http://localhost:8000/api/v1/compilations", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    });
    await page.route("http://localhost:8001/api/v1/audit/logs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    });

    await page.getByRole("tab", { name: "PAT Token" }).click();
    await page.fill("#pat", "pat-session-token");
    await page.getByRole("button", { name: "Sign in with PAT" }).click();

    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() => {
          const raw = localStorage.getItem("auth-storage");
          if (!raw) return null;
          return JSON.parse(raw).state.user?.roles;
        }),
      )
      .toEqual(["admin"]);
  });

  test("logout clears auth state and returns to login", async ({ page }) => {
    await page.route("http://localhost:8001/api/v1/authn/validate", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          subject: "admin@example.com",
          username: "admin",
          token_type: "jwt",
          claims: {
            preferred_username: "admin",
            email: "admin@example.com",
            roles: ["admin"],
          },
        }),
      });
    });
    await page.route("http://localhost:8000/api/v1/services", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ services: [] }),
      });
    });
    await page.route("http://localhost:8000/api/v1/compilations", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    });
    await page.route("http://localhost:8001/api/v1/audit/logs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    });

    await page.fill("#jwt-token", "jwt-session-token");
    await page.getByRole("button", { name: "Sign in with JWT" }).click();
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();

    await page.getByRole("button", { name: "Logout" }).click();

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "service2mcp" })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem("auth_token")))
      .toBeNull();
    await expect
      .poll(() =>
        page.evaluate(() => {
          const raw = localStorage.getItem("auth-storage");
          return raw ? JSON.parse(raw).state.isAuthenticated : null;
        }),
      )
      .toBe(false);
  });
});
