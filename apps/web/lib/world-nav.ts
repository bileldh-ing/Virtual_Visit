/**
 * Pure navigation math for the explorable-world viewer (no three.js, unit-tested).
 *
 * Conventions: Y up, the camera looks down -Z at yaw 0, yaw grows counter-clockwise seen from above
 * (turning right decreases yaw), pitch is clamped to avoid flipping.
 */

export type Vec3 = [number, number, number];

export type MoveInput = { forward: number; right: number; sprint: boolean };
export type MoveParams = { walkSpeed: number; sprintMultiplier: number; accel: number; damping: number };
export const DEFAULT_MOVE: MoveParams = { walkSpeed: 1.6, sprintMultiplier: 2.2, accel: 10, damping: 8 };

/** Horizontal velocity integration with smooth acceleration toward the desired speed and damping when idle. */
export function stepVelocity(vel: [number, number], input: MoveInput, yaw: number, dt: number, p: MoveParams = DEFAULT_MOVE): [number, number] {
  let f = input.forward;
  let r = input.right;
  const len = Math.hypot(f, r);
  if (len > 1) {
    f /= len;
    r /= len;
  }
  const speed = p.walkSpeed * (input.sprint ? p.sprintMultiplier : 1);
  // forward = (-sin yaw, -cos yaw), right = (cos yaw, -sin yaw)
  const tx = (-Math.sin(yaw) * f + Math.cos(yaw) * r) * speed;
  const tz = (-Math.cos(yaw) * f - Math.sin(yaw) * r) * speed;
  const moving = len > 1e-3;
  const k = 1 - Math.exp(-(moving ? p.accel : p.damping) * dt);
  return [vel[0] + (tx - vel[0]) * k, vel[1] + (tz - vel[1]) * k];
}

export function clampPitch(pitch: number, limit = Math.PI / 2 - 0.05): number {
  return Math.max(-limit, Math.min(limit, pitch));
}

export function wrapAngle(a: number): number {
  let x = a % (2 * Math.PI);
  if (x > Math.PI) x -= 2 * Math.PI;
  if (x < -Math.PI) x += 2 * Math.PI;
  return x;
}

/** Yaw that looks from `from` toward `to` (horizontal). */
export function yawTowards(from: Vec3, to: Vec3): number {
  return Math.atan2(-(to[0] - from[0]), -(to[2] - from[2]));
}

/** Forward unit vector (horizontal) for a yaw. */
export function forwardOf(yaw: number): [number, number] {
  return [-Math.sin(yaw), -Math.cos(yaw)];
}

/** Yaw of a camera-to-world rotation in a Y-up frame (columns = camera x right, y down, z forward). */
export function yawFromRotationC2W(r: number[][]): number {
  return Math.atan2(-r[0][2], -r[2][2]);
}

/** Pitch (positive = looking up) of a camera-to-world rotation in a Y-up frame. */
export function pitchFromRotationC2W(r: number[][]): number {
  return Math.asin(Math.max(-1, Math.min(1, r[1][2])));
}

/** Marble azimuth (degrees, 0 = front, 90 = right) to viewer yaw. */
export function yawFromAzimuth(deg: number): number {
  return wrapAngle((-deg * Math.PI) / 180);
}

/**
 * Column-major 4x4 matrix taking raw Marble SPZ/collider coordinates (OpenCV-style, +y down) to the viewer frame:
 * metric scale, ground plane at y = 0, then the 180° rotation about X that Marble's own viewer applies.
 * Without metadata (draft worlds) the scale is 1 and the floor is found later from the collider.
 */
export function marbleMatrix(semantics: { metric_scale_factor?: number | null; ground_plane_offset?: number | null } | null | undefined): number[] {
  const s = semantics?.metric_scale_factor ?? 1;
  const off = semantics?.ground_plane_offset ?? 0;
  // M = Rx(pi) * T(0, -off, 0) * S(s);  Rx(pi) = diag(1, -1, -1)
  return [s, 0, 0, 0, 0, -s, 0, 0, 0, 0, -s, 0, 0, off, 0, 1];
}

export function applyMatrix(m: number[], p: Vec3): Vec3 {
  return [m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13], m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]];
}

export type Bounds2D = { minX: number; maxX: number; minZ: number; maxZ: number };

/** Square minimap frame around XZ bounds (with margin); maps world XZ <-> uv in [0,1] (north = -Z = top). */
export function minimapFrame(b: Bounds2D, margin = 0.06) {
  const cx = (b.minX + b.maxX) / 2;
  const cz = (b.minZ + b.maxZ) / 2;
  const half = (Math.max(b.maxX - b.minX, b.maxZ - b.minZ) / 2) * (1 + margin * 2) || 1;
  return {
    cx,
    cz,
    half,
    toUV: (x: number, z: number) => ({ u: (x - (cx - half)) / (2 * half), v: (z - (cz - half)) / (2 * half) }),
    fromUV: (u: number, v: number) => ({ x: cx - half + u * 2 * half, z: cz - half + v * 2 * half }),
  };
}

/** CSS rotation (radians, clockwise on screen) of a heading arrow for a yaw on a north-up (-Z up) map. */
export function headingOnMinimap(yaw: number): number {
  return -yaw;
}

/** Smoothstep easing used for glides and door-style transitions. */
export function ease(t: number): number {
  const x = Math.max(0, Math.min(1, t));
  return x * x * (3 - 2 * x);
}

export type Pose = { p: Vec3; yaw: number; pitch: number };

/** Interpolate between two poses; yaw takes the short way round. */
export function lerpPose(a: Pose, b: Pose, t: number): Pose {
  const k = ease(t);
  const dy = wrapAngle(b.yaw - a.yaw);
  return {
    p: [a.p[0] + (b.p[0] - a.p[0]) * k, a.p[1] + (b.p[1] - a.p[1]) * k, a.p[2] + (b.p[2] - a.p[2]) * k],
    yaw: a.yaw + dy * k,
    pitch: a.pitch + (b.pitch - a.pitch) * k,
  };
}

/** Glides longer than this use a fade ("door") transition instead of flying through space. */
export const DOOR_DISTANCE = 3.0;

export function transitionKind(a: Vec3, b: Vec3): "glide" | "door" {
  return Math.hypot(b[0] - a[0], b[2] - a[2]) > DOOR_DISTANCE ? "door" : "glide";
}

export function nearestIndex(points: Vec3[], p: Vec3): number {
  let best = -1;
  let bd = Infinity;
  points.forEach((q, i) => {
    const d = (q[0] - p[0]) ** 2 + (q[2] - p[2]) ** 2;
    if (d < bd) {
      bd = d;
      best = i;
    }
  });
  return best;
}

/**
 * Waypoint-only navigation when no collider exists: "forward" goes to the known viewpoint best aligned with the
 * view direction (within ±60°), never anywhere else — so the visitor can never fly through a wall.
 */
export function waypointInDirection(points: Vec3[], current: number, yaw: number, maxAngle = Math.PI / 3): number | null {
  const [fx, fz] = forwardOf(yaw);
  let best: number | null = null;
  let bestScore = -Infinity;
  points.forEach((q, i) => {
    if (i === current) return;
    const dx = q[0] - points[current][0];
    const dz = q[2] - points[current][2];
    const d = Math.hypot(dx, dz);
    if (d < 1e-6) return;
    const cos = (dx * fx + dz * fz) / d;
    if (Math.acos(Math.max(-1, Math.min(1, cos))) > maxAngle) return;
    const score = cos - d * 0.05;
    if (score > bestScore) {
      bestScore = score;
      best = i;
    }
  });
  return best;
}

export function clampFov(fov: number): number {
  return Math.max(35, Math.min(95, fov));
}
