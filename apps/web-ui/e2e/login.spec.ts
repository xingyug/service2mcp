import { test, expect } from "@playwright/test";

test.describe("Login Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
  });

  test("renders login page with title and description", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Tool Compiler v2" })).toBeVisible();
    await expect(page.getByText("Enterprise API-to-MCP Tool Compilation Platform")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("renders username and password fields on password tab", async ({ page }) => {
    await expect(page.locator("#username")).toBeVisible();
    await expect(page.locator("#password")).toBeVisible();
    await expect(page.getByLabel("Username")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
  });

  test("can switch between Password and PAT tabs", async ({ page }) => {
    // Password tab is active by default
    await expect(page.locator("#username")).toBeVisible();

    // Switch to PAT tab
    await page.getByRole("tab", { name: "PAT Token" }).click();
    await expect(page.locator("#pat")).toBeVisible();
    await expect(page.getByLabel("Personal Access Token")).toBeVisible();

    // Username/password fields should be hidden
    await expect(page.locator("#username")).not.toBeVisible();

    // Switch back to Password tab
    await page.getByRole("tab", { name: "Password Login" }).click();
    await expect(page.locator("#username")).toBeVisible();
    await expect(page.locator("#pat")).not.toBeVisible();
  });

  test("shows validation on submitting empty password form", async ({ page }) => {
    // HTML5 required validation prevents submission – fields have required attr
    const usernameInput = page.locator("#username");
    await expect(usernameInput).toHaveAttribute("required", "");

    const passwordInput = page.locator("#password");
    await expect(passwordInput).toHaveAttribute("required", "");
  });

  test("shows validation on submitting empty PAT form", async ({ page }) => {
    await page.getByRole("tab", { name: "PAT Token" }).click();
    const patInput = page.locator("#pat");
    await expect(patInput).toHaveAttribute("required", "");
  });

  test("can fill in and submit password form", async ({ page }) => {
    await page.fill("#username", "testuser");
    await page.fill("#password", "testpass");

    await expect(page.locator("#username")).toHaveValue("testuser");
    await expect(page.locator("#password")).toHaveValue("testpass");

    // Submit – will fail (no backend) but we verify the form submits and shows error
    await page.getByRole("button", { name: "Sign in" }).click();

    // Should show an error since backend is not running
    await expect(page.locator(".text-destructive").first()).toBeVisible({ timeout: 10000 });
  });

  test("can fill in and submit PAT form", async ({ page }) => {
    await page.getByRole("tab", { name: "PAT Token" }).click();
    await page.fill("#pat", "my-test-pat-token");
    await expect(page.locator("#pat")).toHaveValue("my-test-pat-token");

    await page.getByRole("button", { name: "Sign in with PAT" }).click();

    // Should show an error since backend is not running
    await expect(page.locator(".text-destructive").first()).toBeVisible({ timeout: 10000 });
  });
});
