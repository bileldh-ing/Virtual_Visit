/**
 * Coordinate helpers shared by the 3D viewer and minimap. Pure functions (unit-tested).
 *
 * The reconstruction writes points and camera centres in the COLMAP world frame and a
 * `viewer_transform` {origin, rotation, extent}: aligned = rotation · (p − origin) puts the
 * estimated "up" on +Y. We additionally scale by VIEW_SIZE / extent so every scene has a
 * comparable on-screen size. Scale stays *relative* (monocular reconstruction): never metres.
 */

export type Vec3 = [number, number, number];
export type Mat3 = number[][];
export type ViewerTransform = { origin: Vec3; rotation: Mat3; extent: number };

export const VIEW_SIZE = 10;

export function mulMat3Vec(m: Mat3, v: Vec3): Vec3 {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ];
}

export function mulMat3(a: Mat3, b: Mat3): Mat3 {
  const out: Mat3 = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) out[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j];
  return out;
}

export function viewScale(tr: ViewerTransform): number {
  return VIEW_SIZE / Math.max(tr.extent, 1e-6);
}

/** COLMAP world point -> viewer (Three.js) coordinates. */
export function toViewer(p: Vec3, tr: ViewerTransform): Vec3 {
  const s = viewScale(tr);
  const a = mulMat3Vec(tr.rotation, [p[0] - tr.origin[0], p[1] - tr.origin[1], p[2] - tr.origin[2]]);
  return [a[0] * s, a[1] * s, a[2] * s];
}

/** Transform a whole Float32Array of xyz triplets in place. */
export function transformPositions(positions: Float32Array, tr: ViewerTransform): Float32Array {
  const s = viewScale(tr);
  const [o0, o1, o2] = tr.origin;
  const r = tr.rotation;
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i] - o0, y = positions[i + 1] - o1, z = positions[i + 2] - o2;
    positions[i] = (r[0][0] * x + r[0][1] * y + r[0][2] * z) * s;
    positions[i + 1] = (r[1][0] * x + r[1][1] * y + r[1][2] * z) * s;
    positions[i + 2] = (r[2][0] * x + r[2][1] * y + r[2][2] * z) * s;
  }
  return positions;
}

/**
 * Camera basis in viewer space for a Three.js camera (x right, y up, looking down −z).
 * COLMAP cameras are x right, y down, z forward, so flip y and z of the c2w basis.
 * Returns the three column vectors [right, up, back].
 */
export function cameraBasis(rotationC2w: Mat3, tr: ViewerTransform): { right: Vec3; up: Vec3; back: Vec3; forward: Vec3 } {
  const m = mulMat3(tr.rotation, rotationC2w);
  const col = (j: number): Vec3 => [m[0][j], m[1][j], m[2][j]];
  const x = col(0), y = col(1), z = col(2);
  return { right: x, up: [-y[0], -y[1], -y[2]], back: [-z[0], -z[1], -z[2]], forward: z };
}

/** Viewer-space (x, z) -> normalised minimap coordinates that match preview_topdown.png. */
export function minimapUV(p: Vec3, tr: ViewerTransform): { u: number; v: number } {
  const s = viewScale(tr);
  const ext = Math.max(tr.extent, 1e-6) * 1.15;
  return { u: (p[0] / s / ext) * 0.5 + 0.5, v: (p[2] / s / ext) * 0.5 + 0.5 };
}

/** Heading (radians, clockwise from minimap "up" = −z) of a forward vector projected on the floor. */
export function headingOnMap(forward: Vec3): number {
  return Math.atan2(forward[0], -forward[2]);
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function easeInOut(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/** Wrap an index into [0, n). */
export function wrap(i: number, n: number): number {
  return n <= 0 ? 0 : ((i % n) + n) % n;
}
