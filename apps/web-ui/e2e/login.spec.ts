import { test, expect } from "@playwright/test";

test.describe("Login Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
  });

  test("renders login page with title and description", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Tool Compiler v2" })).toBeVisible();
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
});
