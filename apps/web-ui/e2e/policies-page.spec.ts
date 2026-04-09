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
    localStorage.setItem("auth_token", "test-token");
  }, user);
}

test.describe("Policies page", () => {
  test("sends subject_type filters and explicit evaluation risk levels", async ({
    page,
  }) => {
    let requestedSubjectType: string | null = null;
    let evaluationPayload: Record<string, unknown> | null = null;

    await page.route(
      /http:\/\/localhost:8001\/api\/v1\/authz\/policies.*/,
      async (route: Route) => {
        const url = new URL(route.request().url());
        requestedSubjectType = url.searchParams.get("subject_type");
        const items =
          requestedSubjectType === "role"
            ? [
                {
                  id: "pol-role-1",
                  subject_type: "role",
                  subject_id: "editor",
                  resource_id: "svc-1",
                  action_pattern: "read",
                  risk_threshold: "dangerous",
                  decision: "allow",
                  created_at: "2026-03-30T00:00:00Z",
                },
              ]
            : [
                {
                  id: "pol-user-1",
                  subject_type: "user",
                  subject_id: "alice",
                  resource_id: "svc-1",
                  action_pattern: "read",
                  risk_threshold: "safe",
                  decision: "allow",
                  created_at: "2026-03-29T00:00:00Z",
                },
                {
                  id: "pol-role-1",
                  subject_type: "role",
                  subject_id: "editor",
                  resource_id: "svc-1",
                  action_pattern: "read",
                  risk_threshold: "dangerous",
                  decision: "allow",
                  created_at: "2026-03-30T00:00:00Z",
                },
              ];

        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items }),
        });
      },
    );

    await page.route(
      "http://localhost:8001/api/v1/authz/evaluate",
      async (route: Route) => {
        evaluationPayload = route.request().postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            decision: "allow",
            matched_policy_id: "pol-role-1",
            reason: "Matched policy",
          }),
        });
      },
    );

    await setupAuth(page, {
      username: "alice",
      subject: "alice",
      roles: ["admin"],
    });

    await page.goto("/policies");
    await expect(
      page.getByRole("heading", { name: "Authorization Policies" }),
    ).toBeVisible();

    await page.getByPlaceholder("Any subject type").fill("role");
    await expect.poll(() => requestedSubjectType).toBe("role");

    await page.getByText("Test Policy Evaluation").click();
    await page.locator('input[placeholder="e.g. user, group, role"]').first().fill("role");
    await page.getByPlaceholder("e.g. alice").fill("editor");
    await page.getByPlaceholder("e.g. read").fill("read");
    await page.getByPlaceholder("e.g. service-123").fill("svc-1");

    await page.locator('[data-slot="select-trigger"]').filter({ hasText: "safe" }).click();
    await page.locator('[data-slot="select-item"]').filter({ hasText: "dangerous" }).click();

    await page.getByRole("button", { name: "Evaluate" }).click();

    await expect(page.getByText("pol-role-1")).toBeVisible();
    expect(evaluationPayload).toMatchObject({
      subject_type: "role",
      subject_id: "editor",
      action: "read",
      resource_id: "svc-1",
      risk_level: "dangerous",
    });
  });
});
