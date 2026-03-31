import { expect, test } from "@playwright/test";

import { setupAuth } from "./helpers";

test.describe("Observe page", () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await page.goto("/observe");
    await page.waitForLoadState("networkidle");
  });

  test("renders dashboard tabs and the default Grafana target", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: "Observability Dashboards" }),
    ).toBeVisible();

    await expect(page.getByRole("tab", { name: "Compilation" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Runtime" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Access Control" })).toBeVisible();

    const openInGrafanaLink = page.locator('a[target="_blank"]').first();
    await expect(openInGrafanaLink).toHaveAttribute(
      "href",
      /\/d\/compilation\/compilation-dashboard$/,
    );
    await expect(page.locator('iframe[title="Grafana Dashboard"]').first()).toHaveAttribute(
      "src",
      /\/d\/compilation\/compilation-dashboard\?orgId=1&theme=(light|dark)&kiosk$/,
    );
  });

  test("switches Grafana targets when tabs change", async ({ page }) => {
    await page.getByRole("tab", { name: "Runtime" }).click();
    await expect(page.locator('a[target="_blank"]').first()).toHaveAttribute(
      "href",
      /\/d\/runtime\/runtime-dashboard$/,
    );
    await expect(page.locator('iframe[title="Grafana Dashboard"]').first()).toHaveAttribute(
      "src",
      /\/d\/runtime\/runtime-dashboard\?orgId=1&theme=(light|dark)&kiosk$/,
    );

    await page.getByRole("tab", { name: "Access Control" }).click();
    await expect(page.locator('a[target="_blank"]').first()).toHaveAttribute(
      "href",
      /\/d\/access-control\/access-control-dashboard$/,
    );
    await expect(page.locator('iframe[title="Grafana Dashboard"]').first()).toHaveAttribute(
      "src",
      /\/d\/access-control\/access-control-dashboard\?orgId=1&theme=(light|dark)&kiosk$/,
    );
  });
});
