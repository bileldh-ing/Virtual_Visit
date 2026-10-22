import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parsePly } from "@/lib/ply";

function binaryPly(points: [number, number, number, number, number, number][]): ArrayBuffer {
  const header = `ply\nformat binary_little_endian 1.0\nelement vertex ${points.length}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n`;
  const h = new TextEncoder().encode(header);
  const buf = new ArrayBuffer(h.length + points.length * 15);
  new Uint8Array(buf).set(h, 0);
  const v = new DataView(buf, h.length);
  points.forEach((p, i) => {
    v.setFloat32(i * 15, p[0], true);
    v.setFloat32(i * 15 + 4, p[1], true);
    v.setFloat32(i * 15 + 8, p[2], true);
    v.setUint8(i * 15 + 12, p[3]);
    v.setUint8(i * 15 + 13, p[4]);
    v.setUint8(i * 15 + 14, p[5]);
  });
  return buf;
}

describe("parsePly", () => {
  it("reads binary little-endian xyz + rgb", () => {
    const pc = parsePly(binaryPly([[1, 2, 3, 255, 0, 0], [-1.5, 0, 4.25, 0, 128, 255]]));
    expect(pc.count).toBe(2);
    expect(Array.from(pc.positions)).toEqual([1, 2, 3, -1.5, 0, 4.25]);
    expect(pc.colors?.[0]).toBeCloseTo(1);
    expect(pc.colors?.[4]).toBeCloseTo(128 / 255);
  });

  it("reads ascii PLY without colour", () => {
    const txt = "ply\nformat ascii 1.0\nelement vertex 2\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n1 2 3\n";
    const pc = parsePly(new TextEncoder().encode(txt).buffer as ArrayBuffer);
    expect(pc.count).toBe(2);
    expect(pc.colors).toBeNull();
    expect(Array.from(pc.positions)).toEqual([0, 0, 0, 1, 2, 3]);
  });

  it("rejects non-PLY and truncated files", () => {
    expect(() => parsePly(new TextEncoder().encode("hello").buffer as ArrayBuffer)).toThrow(/Not a PLY/);
    const full = binaryPly([[1, 2, 3, 1, 2, 3]]);
    expect(() => parsePly(full.slice(0, full.byteLength - 4))).toThrow(/truncated/);
  });

  // Real artifact from the 48-photo COLMAP run, when the spike output is present locally.
  const real = path.resolve(__dirname, "../../../../../spike/cli48/out/sparse_points_web.ply");
  it.skipIf(!existsSync(real))("parses the real reconstruction output (3,448 web points)", () => {
    const b = readFileSync(real);
    const pc = parsePly(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer);
    expect(pc.count).toBe(3448);
    expect(Number.isFinite(pc.positions[0])).toBe(true);
  });
});
