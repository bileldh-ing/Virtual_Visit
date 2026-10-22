import { describe, expect, it } from "vitest";
import { planBatches } from "@/lib/batches";

const MB = 1024 * 1024;
const f = (mb: number) => ({ size: Math.round(mb * MB) });

describe("planBatches", () => {
  it("returns no batches for no files", () => {
    expect(planBatches([])).toEqual([]);
  });

  it("caps each batch by total bytes", () => {
    const files = Array.from({ length: 10 }, () => f(1.4)); // ~14 MB total
    const batches = planBatches(files);
    expect(batches.length).toBeGreaterThan(1);
    for (const b of batches) expect(b.reduce((s, x) => s + x.size, 0)).toBeLessThanOrEqual(8 * MB);
    expect(batches.flat()).toHaveLength(10);
  });

  it("caps each batch by file count", () => {
    const batches = planBatches(Array.from({ length: 25 }, () => f(0.1)));
    expect(batches.map((b) => b.length)).toEqual([10, 10, 5]);
  });

  it("sends an oversized file alone", () => {
    const batches = planBatches([f(1), f(20), f(1)]);
    expect(batches.map((b) => b.length)).toEqual([1, 1, 1]);
  });

  it("preserves order", () => {
    const files = [f(3), f(3), f(3), f(1)].map((x, i) => ({ ...x, i }));
    expect(planBatches(files).flat().map((x) => x.i)).toEqual([0, 1, 2, 3]);
  });
});
