import { test, expect, type Page } from "@playwright/test";
import { setupAuth } from "./helpers";

/** Remove the Next.js dev overlay that intercepts pointer events */
async function dismissDevOverlay(page: Page) {
  await page.evaluate(() => {
    document.querySelectorAll("nextjs-portal").forEach((el) => el.remove());
  });
}

test.describe("Theme Toggle", () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await dismissDevOverlay(page);
  });

  test("theme toggle button exists in sidebar", async ({ page }) => {
    const toggleButton = page.getByRole("button", { name: "Toggle theme" });
    await expect(toggleButton).toBeVisible();
  });

  test("can switch to dark theme", async ({ page }) => {
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("can switch to light theme", async ({ page }) => {
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);

    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Light" }).click();
    await expect(page.locator("html")).toHaveClass(/light/);
  });

  test("theme persists across page navigation", async ({ page }) => {
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);

    await page.getByRole("link", { name: "Jobs" }).click();
    await expect(page).toHaveURL(/\/compilations$/);
    await dismissDevOverlay(page);
    await expect(page.locator("html")).toHaveClass(/dark/);

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
