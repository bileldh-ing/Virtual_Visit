import { expect, test } from "@playwright/test";

// The walkable world viewer on a tiny committed fixture (4 x 2.6 x 5 m room, SPZ v2 splats + 12-triangle collider).
// No provider and no API key: CI never calls World Labs. Movement is frame-stepped through the viewer's
// setManual()/advance() hooks, so the test is deterministic on software WebGL.
type Pose = { loaded: boolean; x: number; y: number; z: number; yaw: number; onGround: boolean; splatPoints: number; hasCollider: boolean; mode: string };
type EngineHandle = { setManual: (on: boolean) => void; advance: (s: number) => void; yaw: number };
declare global {
  interface Window {
    __roomscapeWorld?: () => Pose | null;
    __roomscapeWorldEngine?: () => EngineHandle | null;
  }
}

test("world viewer: loads splats + collider, walks, and stops at the wall", async ({ page }) => {
  test.setTimeout(150_000); // software WebGL in CI renders ~1 fps
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/e2e/world");
  await expect(page.getByTestId("world-viewer")).toBeVisible();
  await expect.poll(async () => page.evaluate(() => window.__roomscapeWorld?.()?.splatPoints ?? 0), { timeout: 45_000 }).toBe(4000);
  const start = (await page.evaluate(() => window.__roomscapeWorld?.()))!;
  expect(start.loaded && start.hasCollider && start.onGround).toBe(true);
  expect(start.mode).toBe("walk");
  expect(start.y).toBeCloseTo(1.5, 1); // eye height above the fixture floor (metric scale)
  await expect(page.getByTestId("minimap")).toBeVisible();
  await expect(page.getByTestId("honesty-badge")).toContainText("AI-generated");

  // face +x (yaw -90°) and walk: the room's x walls are at ±2 m, so we must stop short of x = 2
  await page.evaluate(() => {
    const e = window.__roomscapeWorldEngine!()!;
    e.setManual(true);
    e.yaw = -Math.PI / 2;
    e.advance(0.1);
  });
  await page.keyboard.down("w");
  await page.evaluate(() => window.__roomscapeWorldEngine!()!.advance(0.6));
  const mid = (await page.evaluate(() => window.__roomscapeWorld?.()))!;
  expect(mid.x).toBeGreaterThan(0.3); // moved
  await page.evaluate(() => window.__roomscapeWorldEngine!()!.advance(6));
  await page.keyboard.up("w");
  const end = (await page.evaluate(() => window.__roomscapeWorld?.()))!;
  expect(end.x).toBeGreaterThan(1.4); // reached the wall…
  expect(end.x).toBeLessThan(2.0); // …but did not pass through it
  expect(Math.abs(end.z)).toBeLessThan(0.2);
  expect(end.onGround).toBe(true);
  expect(end.y).toBeCloseTo(1.5, 1);

  // the canvas actually rendered splats (not a blank frame); Spark sorts in a worker, so give it a few frames
  const luminance = () =>
    page.evaluate(() => {
      window.__roomscapeWorldEngine!()!.advance(1 / 60);
      const c = document.querySelector("[data-testid=world-canvas]") as HTMLCanvasElement;
      const t = document.createElement("canvas");
      t.width = 32;
      t.height = 18;
      const g = t.getContext("2d")!;
      g.drawImage(c, 0, 0, 32, 18);
      const d = g.getImageData(0, 0, 32, 18).data;
      let s = 0;
      for (let i = 0; i < d.length; i += 4) s += d[i] + d[i + 1] + d[i + 2];
      return s / (d.length / 4) / 3;
    });
  await expect.poll(luminance, { timeout: 30_000, intervals: [500] }).toBeGreaterThan(30);
  expect(errors).toEqual([]);
});
