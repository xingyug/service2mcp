import { test, expect } from "@playwright/test";
import { setupAuth } from "./helpers";

test.describe("Theme Toggle", () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
  });

  test("theme toggle button exists in sidebar", async ({ page }) => {
    // The toggle is a button with sr-only text "Toggle theme"
    const toggleButton = page.getByRole("button", { name: "Toggle theme" });
    await expect(toggleButton).toBeVisible();
  });

  test("can switch to dark theme", async ({ page }) => {
    // Click theme toggle to open dropdown
    await page.getByRole("button", { name: "Toggle theme" }).click();

    // Select Dark
    await page.getByRole("menuitem", { name: "Dark" }).click();

    // The <html> element should have class "dark"
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("can switch to light theme", async ({ page }) => {
    // First switch to dark
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);

    // Now switch to light
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Light" }).click();
    await expect(page.locator("html")).toHaveClass(/light/);
  });

  test("theme persists across page navigation", async ({ page }) => {
    // Switch to dark
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);

    // Navigate to compilations page
    await page.getByRole("link", { name: "Jobs" }).click();
    await expect(page).toHaveURL(/\/compilations$/);

    // Theme should still be dark
    await expect(page.locator("html")).toHaveClass(/dark/);

    // Navigate to another page
    await page.getByRole("link", { name: "Registry" }).click();
    await expect(page).toHaveURL(/\/services$/);
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("system theme option is available", async ({ page }) => {
    await page.getByRole("button", { name: "Toggle theme" }).click();

    await expect(page.getByRole("menuitem", { name: "Light" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Dark" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "System" })).toBeVisible();
  });
});
