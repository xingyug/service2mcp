import { test, expect } from "@playwright/test";
import { setupAuth } from "./helpers";

test.describe("Navigation", () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  test("sidebar renders with all navigation groups", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Navigation group labels
    for (const group of ["Overview", "Compile", "Services", "Access Control", "Gateway", "Observe"]) {
      await expect(page.getByText(group, { exact: true }).first()).toBeVisible();
    }
  });

  test("sidebar contains all navigation links", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    const links = [
      "Dashboard",
      "New Compilation",
      "Jobs",
      "Registry",
      "Policies",
      "PAT Tokens",
      "Audit Log",
      "Routes",
      "Dashboards",
    ];
    for (const label of links) {
      await expect(page.getByRole("link", { name: label }).first()).toBeVisible();
    }
  });

  test("navigate to compilations page", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await page.getByRole("link", { name: "Jobs" }).click();
    await expect(page).toHaveURL(/\/compilations$/);
    // Page should render without crashing – check for breadcrumb
    await expect(page.getByText("Compilations").first()).toBeVisible();
  });

  test("navigate to new compilation page", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await page.getByRole("link", { name: "New Compilation" }).click();
    await expect(page).toHaveURL(/\/compilations\/new$/);
    await expect(page.getByText("Source Input").first()).toBeVisible();
  });

  test("navigate to services page", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await page.getByRole("link", { name: "Registry" }).click();
    await expect(page).toHaveURL(/\/services$/);
    await expect(page.getByText("Services").first()).toBeVisible();
  });

  test("navigate to access control pages", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // PAT Tokens
    await page.getByRole("link", { name: "PAT Tokens" }).click();
    await expect(page).toHaveURL(/\/pats$/);

    // Audit Log
    await page.getByRole("link", { name: "Audit Log" }).click();
    await expect(page).toHaveURL(/\/audit$/);

    // Policies
    await page.getByRole("link", { name: "Policies" }).click();
    await expect(page).toHaveURL(/\/policies$/);
  });

  test("navigate to gateway and observe pages", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await page.getByRole("link", { name: "Routes" }).click();
    await expect(page).toHaveURL(/\/gateway$/);

    await page.getByRole("link", { name: "Dashboards" }).click();
    await expect(page).toHaveURL(/\/observe$/);
  });

  test("breadcrumbs update on navigation", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Dashboard breadcrumb on root
    const breadcrumbNav = page.locator("nav[aria-label='breadcrumb']");
    await expect(breadcrumbNav.getByText("Dashboard").first()).toBeVisible();

    // Navigate to compilations
    await page.getByRole("link", { name: "Jobs" }).click();
    await expect(breadcrumbNav.getByText("Compilations")).toBeVisible();

    // Navigate to new compilation – breadcrumb should show Compilations > New
    await page.getByRole("link", { name: "New Compilation" }).click();
    await expect(breadcrumbNav.getByText("New")).toBeVisible();
  });
});
