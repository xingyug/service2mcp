import type { Page } from "@playwright/test";

/**
 * Sets up fake auth state in localStorage so the AuthGuard lets us through.
 * Must be called before navigating to any protected (dashboard) route.
 */
export async function setupAuth(page: Page) {
  // Visit login first so we have an origin to set localStorage on
  await page.goto("/login");
  await page.evaluate(() => {
    localStorage.setItem(
      "auth-storage",
      JSON.stringify({
        state: {
          token: "test-token",
          user: { username: "admin", email: "admin@test.com", roles: ["admin"] },
          isAuthenticated: true,
        },
        version: 0,
      }),
    );
  });
}
