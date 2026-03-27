import { test, expect } from "@playwright/test";
import { setupAuth } from "./helpers";

test.describe("Responsive Layout", () => {
  test("sidebar is visible on desktop viewport", async ({ page }) => {
    await setupAuth(page);
    // Desktop viewport (default from chromium project config is 1280x720)
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Sidebar should be visible with navigation links
    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar).toBeVisible();

    // Navigation items should be visible
    await expect(page.getByRole("link", { name: "Dashboard" }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Jobs" }).first()).toBeVisible();
  });

  test("sidebar is collapsed on mobile viewport", async ({ page }) => {
    await setupAuth(page);
    // Mobile viewport (below 768px breakpoint from use-mobile hook)
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // On mobile, the sidebar should not be visible by default (it becomes a sheet)
    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar).not.toBeVisible();
  });

  test("sidebar trigger button appears on mobile", async ({ page }) => {
    await setupAuth(page);
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // The SidebarTrigger button should be visible in the header
    const trigger = page.locator("button[data-sidebar='trigger']");
    await expect(trigger).toBeVisible();
  });

  test("mobile sidebar opens when trigger is clicked", async ({ page }) => {
    await setupAuth(page);
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Click the sidebar trigger
    const trigger = page.locator("button[data-sidebar='trigger']");
    await trigger.click();

    // Sidebar should now be visible (as a sheet/overlay)
    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar).toBeVisible({ timeout: 5000 });

    // Navigation links should be visible
    await expect(page.getByRole("link", { name: "Dashboard" }).first()).toBeVisible();
  });

  test("pages render correctly on mobile viewport", async ({ page }) => {
    await setupAuth(page);
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/compilations/new");
    await page.waitForLoadState("networkidle");

    // Wizard should still render on mobile
    await expect(page.getByRole("heading", { name: "Source Input" })).toBeVisible();
    await expect(page.locator("#created-by")).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue" })).toBeVisible();
  });
});
