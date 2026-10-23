import { describe, expect, it } from "vitest";
import { coveringPhoto, trustRows } from "@/lib/world-trust";

const shots: { p: [number, number, number]; yaw: number }[] = [
  { p: [0, 1.5, 0], yaw: 0 },
  { p: [0, 1.5, 0], yaw: Math.PI / 2 },
  { p: [4, 1.5, 0], yaw: Math.PI },
];

describe("coveringPhoto", () => {
  it("picks the photo whose heading matches the view", () => {
    expect(coveringPhoto({ x: 0, z: 0, yaw: 0.2 }, shots)).toBe(0);
    expect(coveringPhoto({ x: 0, z: 0, yaw: Math.PI / 2 - 0.1 }, shots)).toBe(1);
  });
  it("wraps angles across ±π", () => {
    expect(coveringPhoto({ x: 4, z: 0, yaw: -Math.PI + 0.1 }, shots)).toBe(2);
  });
  it("returns null when looking where no photo looked (inferred area)", () => {
    expect(coveringPhoto({ x: 0, z: 0, yaw: -Math.PI / 2 }, shots)).toBeNull();
  });
  it("returns null when far from every capture position", () => {
    expect(coveringPhoto({ x: 2, z: 3, yaw: 0 }, shots)).toBeNull();
  });
});

describe("trustRows", () => {
  it("marks generated worlds inferred and never claims dimensions", () => {
    const rows = trustRows({ honesty: "ai_generated", photos: 8, placedPhotos: 7, hasCollider: true, metric: true });
    expect(rows.find((r) => r.key === "world")?.status).toBe("inferred");
    expect(rows.find((r) => r.key === "scale")?.status).toBe("not claimed");
    expect(rows.find((r) => r.key === "photos")?.detail).toContain("7 shown");
  });
});
