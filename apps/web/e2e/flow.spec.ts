import path from "node:path";
import { expect, test } from "@playwright/test";

const api = process.env.E2E_API_URL ?? "http://localhost:8000";

test("sign up → verify → create project → upload → capture check", async ({ page, request }) => {
  const email = `e2e-${Date.now()}@example.com`;
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("E2E Agent");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill("E2e-Password-2026");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/app/);
  await expect(page.getByText("Verify your email")).toBeVisible();

  // Development outbox (enabled only when ROOMSCAPE_ENV=development/test).
  const mail = await (await request.get(`${api}/api/v1/dev/outbox?to=${encodeURIComponent(email)}`)).json();
  const body: string = mail.items.find((m: { kind: string }) => m.kind === "verify_email").body;
  const token = body.split("token=")[1].split(/\s/)[0];
  await page.goto(`/verify-email?token=${token}`);
  await expect(page.getByText("Email verified.")).toBeVisible();

  await page.goto("/app/projects/new");
  await page.getByLabel("Title").fill("E2E Test Flat");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: "E2E Test Flat" })).toBeVisible();
  await expect(page).toHaveURL(/tab=photos/);

  const files = [1, 2, 3].map((i) => path.join(__dirname, "fixtures", `room_${i}.jpg`));
  await page.locator("#file-input").setInputFiles(files);
  await expect(page.getByText("3 added")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "3 photos" })).toBeVisible();

  await page.getByRole("button", { name: "Run capture check" }).click();
  await expect(page.getByText(/ready|needs more images|ready with warnings|unsupported/i).first()).toBeVisible({ timeout: 45_000 });

  await page.getByRole("link", { name: "Reconstructions" }).click();
  await expect(page.getByRole("button", { name: "New reconstruction" })).toBeEnabled();
});
