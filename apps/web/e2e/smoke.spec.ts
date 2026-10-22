import { expect, test } from "@playwright/test";

test("landing page renders hero, honesty section and CTAs", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("reconstructed in 3D");
  await expect(page.getByText("What’s real, and what isn’t")).toBeVisible();
  await expect(page.getByRole("link", { name: "Start free" })).toBeVisible();
});

test("unknown share token shows a clear message", async ({ page }) => {
  await page.goto("/tour/not-a-real-token-123456");
  await expect(page.getByRole("heading", { name: /Tour not found|no longer active/ })).toBeVisible();
});

test("dashboard redirects anonymous users to sign in", async ({ page }) => {
  await page.goto("/app");
  await expect(page).toHaveURL(/\/signin\?next=%2Fapp/);
});
