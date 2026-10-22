import { describe, expect, it } from "vitest";
import { cameraBasis, headingOnMap, minimapUV, toViewer, transformPositions, VIEW_SIZE, wrap, type ViewerTransform } from "@/lib/viewer-math";

const near = (a: number[], b: number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]));
const I: number[][] = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
const tr: ViewerTransform = { origin: [1, 1, 1], rotation: I, extent: 5 };

describe("viewer math", () => {
  it("centres on origin and scales extent to VIEW_SIZE", () => {
    expect(toViewer([6, 1, 1], tr)).toEqual([VIEW_SIZE, 0, 0]);
    const arr = new Float32Array([6, 1, 1, 1, 1, 1]);
    transformPositions(arr, tr);
    expect(Array.from(arr)).toEqual([10, 0, 0, 0, 0, 0]);
  });

  it("applies the alignment rotation", () => {
    const flipY = { ...tr, origin: [0, 0, 0] as [number, number, number], rotation: [[1, 0, 0], [0, -1, 0], [0, 0, -1]] };
    near(toViewer([0, 5, 0], flipY), [0, -10, 0]);
  });

  it("converts COLMAP camera axes (y down, z forward) to Three.js (y up, -z forward)", () => {
    const b = cameraBasis(I, { ...tr, rotation: I });
    near(b.right, [1, 0, 0]);
    near(b.up, [0, -1, 0]);
    near(b.forward, [0, 0, 1]);
    near(b.back, [0, 0, -1]);
  });

  it("maps viewer positions onto the preview image like render_preview does", () => {
    const centre = minimapUV([0, 0, 0], tr);
    expect(centre).toEqual({ u: 0.5, v: 0.5 });
    const edge = minimapUV(toViewer([1 + 5 * 1.15, 1, 1], tr), tr);
    expect(edge.u).toBeCloseTo(1);
  });

  it("computes headings and wraps indices", () => {
    expect(headingOnMap([0, 0, -1])).toBeCloseTo(0);
    expect(headingOnMap([1, 0, 0])).toBeCloseTo(Math.PI / 2);
    expect(wrap(-1, 5)).toBe(4);
    expect(wrap(7, 5)).toBe(2);
    expect(wrap(3, 0)).toBe(0);
  });
});
