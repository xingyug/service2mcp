import { test, expect } from "@playwright/test";
import { setupAuth } from "./helpers";

test.describe("Compilation Wizard", () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await page.goto("/compilations/new");
    await page.waitForLoadState("networkidle");
  });

  test("wizard renders with step indicators", async ({ page }) => {
    const progressNav = page.locator("nav[aria-label='Progress']");
    await expect(progressNav).toBeVisible();

    // All 4 step labels should be present
    for (const label of ["Source Input", "Protocol & Options", "Auth Configuration", "Review & Submit"]) {
      await expect(page.getByText(label).first()).toBeVisible();
    }

    // Step 1 indicator should be highlighted (current)
    const firstStepCircle = progressNav.locator("button").first();
    await expect(firstStepCircle).toBeVisible();
  });

  test("source input step renders with form fields", async ({ page }) => {
    // CardTitle renders as <div>, not heading
    await expect(page.locator("[data-slot='card-title']").getByText("Source Input")).toBeVisible();

    // Source type radio buttons (RadioGroupItem renders both span + hidden input)
    await expect(page.getByRole("radio", { name: "URL" })).toBeVisible();
    await expect(page.getByRole("radio", { name: "Paste Content" })).toBeVisible();
    await expect(page.getByRole("radio", { name: "Upload File" })).toBeVisible();

    // URL input (default source mode) — use type selector to avoid radio button with same id
    await expect(page.locator("input[type='url']#source-url")).toBeVisible();

    // Service name and created-by fields
    await expect(page.locator("#service-name")).toBeVisible();
    await expect(page.locator("#created-by")).toBeVisible();

    // Navigation buttons
    await expect(page.getByRole("button", { name: "Back" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Continue" })).toBeVisible();
  });

  test("can fill in source URL and created-by", async ({ page }) => {
    const sourceUrl = "https://api.example.com/openapi.yaml";
    const urlInput = page.locator("input[type='url']#source-url");
    await urlInput.fill(sourceUrl);
    await expect(urlInput).toHaveValue(sourceUrl);

    await page.fill("#created-by", "test-admin");
    await expect(page.locator("#created-by")).toHaveValue("test-admin");
  });

  test("shows validation error when required fields are empty", async ({ page }) => {
    // Clear the created-by field (may be pre-filled from auth store)
    await page.locator("#created-by").clear();

    // Try to advance without filling required fields
    await page.getByRole("button", { name: "Continue" }).click();

    // Should show a validation error
    await expect(page.getByText("Created by is required").first()).toBeVisible();
  });

  test("can navigate between wizard steps", async ({ page }) => {
    const cardTitle = page.locator("[data-slot='card-title']");

    // Fill required fields on step 0
    await page.locator("input[type='url']#source-url").fill("https://api.example.com/openapi.yaml");
    await page.fill("#created-by", "test-admin");

    // Go to step 1
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(cardTitle.getByText("Protocol & Options")).toBeVisible();

    // Go to step 2
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(cardTitle.getByText("Auth Configuration")).toBeVisible();

    // Go back to step 1
    await page.getByRole("button", { name: "Back" }).click();
    await expect(cardTitle.getByText("Protocol & Options")).toBeVisible();

    // Go back to step 0
    await page.getByRole("button", { name: "Back" }).click();
    await expect(cardTitle.getByText("Source Input")).toBeVisible();
  });

  test("review step shows entered data", async ({ page }) => {
    const sourceUrl = "https://api.example.com/petstore.yaml";

    // Step 0: fill source
    await page.locator("input[type='url']#source-url").fill(sourceUrl);
    await page.fill("#service-name", "petstore");
    await page.fill("#created-by", "test-admin");
    await page.getByRole("button", { name: "Continue" }).click();

    // Step 1: continue with defaults
    await page.getByRole("button", { name: "Continue" }).click();

    // Step 2: continue with no auth
    await page.getByRole("button", { name: "Continue" }).click();

    // Step 3: Review
    await expect(page.getByText("Review & Submit").first()).toBeVisible();
    await expect(page.getByText(sourceUrl)).toBeVisible();
    await expect(page.getByText("petstore", { exact: true })).toBeVisible();
    await expect(page.getByText("test-admin")).toBeVisible();
    await expect(page.getByText("generic", { exact: false })).toBeVisible();
    await expect(page.getByText("None").first()).toBeVisible();

    // Edit badges should exist
    const editBadges = page.getByText("Edit", { exact: true });
    expect(await editBadges.count()).toBeGreaterThanOrEqual(3);

    // Submit button should be visible
    await expect(page.getByRole("button", { name: "Create Compilation" })).toBeVisible();
  });

  test("can switch source mode to paste content", async ({ page }) => {
    await page.getByRole("radio", { name: "Paste Content" }).click();
    await expect(page.locator("#source-content")).toBeVisible();
    await expect(page.locator("input[type='url']#source-url")).not.toBeVisible();

    await page.fill("#source-content", "openapi: 3.0.0\ninfo:\n  title: Test");
    await expect(page.locator("#source-content")).toHaveValue("openapi: 3.0.0\ninfo:\n  title: Test");
  });
});
