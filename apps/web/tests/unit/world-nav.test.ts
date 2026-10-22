import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  applyMatrix,
  clampFov,
  clampPitch,
  forwardOf,
  lerpPose,
  marbleMatrix,
  minimapFrame,
  pitchFromRotationC2W,
  stepVelocity,
  transitionKind,
  waypointInDirection,
  wrapAngle,
  yawFromAzimuth,
  yawFromRotationC2W,
  yawTowards,
  type Vec3,
} from "@/lib/world-nav";
import { DEFAULT_COLLISION, WorldCollider, type Player } from "@/lib/world-collision";

describe("movement integration", () => {
  it("accelerates smoothly toward walk speed and damps to rest", () => {
    let v: [number, number] = [0, 0];
    const speeds: number[] = [];
    for (let i = 0; i < 60; i++) {
      v = stepVelocity(v, { forward: 1, right: 0, sprint: false }, 0, 1 / 60);
      speeds.push(Math.hypot(...v));
    }
    expect(speeds[0]).toBeLessThan(0.5); // no instant jump
    expect(speeds.at(-1)!).toBeCloseTo(1.6, 1);
    expect(v[1]).toBeLessThan(0); // yaw 0 looks down -Z
    for (let i = 0; i < 120; i++) v = stepVelocity(v, { forward: 0, right: 0, sprint: false }, 0, 1 / 60);
    expect(Math.hypot(...v)).toBeLessThan(0.01);
  });

  it("sprint is faster and diagonals are not", () => {
    let a: [number, number] = [0, 0];
    let b: [number, number] = [0, 0];
    let c: [number, number] = [0, 0];
    for (let i = 0; i < 200; i++) {
      a = stepVelocity(a, { forward: 1, right: 0, sprint: true }, 0, 1 / 60);
      b = stepVelocity(b, { forward: 1, right: 1, sprint: false }, 0, 1 / 60);
      c = stepVelocity(c, { forward: 1, right: 0, sprint: false }, 0, 1 / 60);
    }
    expect(Math.hypot(...a)).toBeGreaterThan(Math.hypot(...c) * 2);
    expect(Math.hypot(...b)).toBeCloseTo(Math.hypot(...c), 2);
  });

  it("right strafe is +X at yaw 0", () => {
    let v: [number, number] = [0, 0];
    for (let i = 0; i < 100; i++) v = stepVelocity(v, { forward: 0, right: 1, sprint: false }, 0, 1 / 60);
    expect(v[0]).toBeGreaterThan(1.5);
  });
});

describe("angles and poses", () => {
  it("clamps pitch, fov and wraps yaw", () => {
    expect(clampPitch(3)).toBeLessThan(Math.PI / 2);
    expect(clampFov(10)).toBe(35);
    expect(clampFov(200)).toBe(95);
    expect(wrapAngle(3 * Math.PI)).toBeCloseTo(Math.PI);
  });
  it("yawTowards agrees with forwardOf", () => {
    const yaw = yawTowards([0, 0, 0], [3, 0, -3]);
    const [fx, fz] = forwardOf(yaw);
    expect(fx).toBeCloseTo(Math.SQRT1_2);
    expect(fz).toBeCloseTo(-Math.SQRT1_2);
  });
  it("reads yaw/pitch from an OpenCV-style camera rotation in a Y-up frame", () => {
    // camera looking along +X, level: x_cam(right) = +Z?  right = forward x up => (1,0,0) x (0,1,0) = (0,0,1)
    const r = [[0, 0, 1], [0, -1, 0], [1, 0, 0]];
    const yaw = yawFromRotationC2W(r);
    expect(forwardOf(yaw)[0]).toBeCloseTo(1);
    expect(pitchFromRotationC2W(r)).toBeCloseTo(0);
  });
  it("Marble azimuth 90 (right) turns the view to +X", () => {
    expect(forwardOf(yawFromAzimuth(90))[0]).toBeCloseTo(1);
    expect(forwardOf(yawFromAzimuth(0))[1]).toBeCloseTo(-1);
  });
  it("lerpPose takes the short way round", () => {
    const p = lerpPose({ p: [0, 0, 0], yaw: 3, pitch: 0 }, { p: [2, 0, 0], yaw: -3, pitch: 0 }, 0.5);
    expect(Math.abs(wrapAngle(p.yaw))).toBeGreaterThan(3);
    expect(p.p[0]).toBeCloseTo(1);
  });
  it("long moves become door transitions", () => {
    expect(transitionKind([0, 0, 0], [1, 0, 1])).toBe("glide");
    expect(transitionKind([0, 0, 0], [5, 0, 0])).toBe("door");
  });
});

describe("Marble frame", () => {
  it("flips Y/Z (180° about X), applies metric scale and puts the ground at y=0", () => {
    const m = marbleMatrix({ metric_scale_factor: 2, ground_plane_offset: 0.5 });
    // raw floor point (OpenCV +y down): y = (0.5 + 0) / 2 = 0.25 raw -> metric 0.5 -> minus offset 0 -> flipped 0
    expect(applyMatrix(m, [0, 0.25, 0])[1]).toBeCloseTo(0);
    expect(applyMatrix(m, [0, -1, 0])[1]).toBeGreaterThan(0); // raw "up" (negative y) is up in the viewer
    expect(applyMatrix(m, [0, 0, 1])[2]).toBeCloseTo(-2); // raw forward becomes -Z (camera default forward)
    const id = marbleMatrix(null);
    expect(applyMatrix(id, [1, 2, 3])).toEqual([1, -2, -3]);
  });
});

describe("minimap + waypoints", () => {
  it("maps bounds to a square frame and back", () => {
    const f = minimapFrame({ minX: -2, maxX: 2, minZ: -1, maxZ: 7 });
    const uv = f.toUV(0, 3);
    expect(uv.u).toBeCloseTo(0.5);
    expect(uv.v).toBeCloseTo(0.5);
    const p = f.fromUV(uv.u, uv.v);
    expect(p.x).toBeCloseTo(0);
    expect(p.z).toBeCloseTo(3);
  });
  it("waypoint navigation only goes to viewpoints in front", () => {
    const pts: Vec3[] = [[0, 0, 0], [0, 0, -3], [3, 0, 0], [0, 0, 3]];
    expect(waypointInDirection(pts, 0, 0)).toBe(1); // looking -Z
    expect(waypointInDirection(pts, 0, -Math.PI / 2)).toBe(2); // turned right -> +X
    expect(waypointInDirection([[0, 0, 0], [0, 0, 3]], 0, 0)).toBeNull(); // nothing ahead: stay put
  });
});

function boxRoom(w = 4, d = 6, h = 2.6): THREE.Object3D {
  const g = new THREE.Group();
  const add = (geo: THREE.BufferGeometry, x: number, y: number, z: number) => {
    const m = new THREE.Mesh(geo);
    m.position.set(x, y, z);
    g.add(m);
  };
  add(new THREE.BoxGeometry(w, 0.1, d), 0, -0.05, 0); // floor
  add(new THREE.BoxGeometry(0.1, h, d), -w / 2, h / 2, 0);
  add(new THREE.BoxGeometry(0.1, h, d), w / 2, h / 2, 0);
  add(new THREE.BoxGeometry(w, h, 0.1), 0, h / 2, -d / 2);
  add(new THREE.BoxGeometry(w, h, 0.1), 0, h / 2, d / 2);
  add(new THREE.BoxGeometry(1, 0.2, 1), 1, 0.1, 1); // a low step (0.2 high)
  add(new THREE.BoxGeometry(1, 1.0, 1), -1, 0.5, -1.5); // a table you cannot walk through
  return g;
}

describe("capsule collision", () => {
  const col = WorldCollider.fromObject(boxRoom());
  const P = { ...DEFAULT_COLLISION, eyeHeight: 1.6 };
  const player = (x: number, z: number): Player => ({ eye: new THREE.Vector3(x, 1.6, z), vel: new THREE.Vector3(), onGround: true });

  it("finds the floor and keeps the eye height", () => {
    expect(col.floorBelow(new THREE.Vector3(0, 1.6, 0))).toBeCloseTo(0, 2);
    const pl = player(0, 0);
    for (let i = 0; i < 30; i++) col.step(pl, [0, 0], 1 / 60, P);
    expect(pl.eye.y).toBeCloseTo(1.6, 2);
    expect(pl.onGround).toBe(true);
  });

  it("walls stop the player (never walk through)", () => {
    const pl = player(0, 0);
    for (let i = 0; i < 240; i++) col.step(pl, [3, 0], 1 / 60, P); // run at +X for 4 s
    expect(pl.eye.x).toBeLessThan(2 - P.radius + 0.06);
    expect(pl.eye.x).toBeGreaterThan(1.5);
  });

  it("does not tunnel at sprint speed with large frames", () => {
    const pl = player(0, 0);
    for (let i = 0; i < 20; i++) col.step(pl, [0, 12], 1 / 10, P);
    expect(pl.eye.z).toBeLessThan(3);
  });

  it("steps up low obstacles and follows the floor", () => {
    const pl = player(1, -0.5);
    for (let i = 0; i < 90; i++) col.step(pl, [0, 1.2], 1 / 60, P);
    expect(pl.eye.z).toBeGreaterThan(0.6);
    expect(pl.eye.y).toBeGreaterThan(1.6 + 0.1); // standing on the 0.2 step
  });

  it("is blocked by a table taller than the step height", () => {
    const pl = player(-1, 0);
    for (let i = 0; i < 180; i++) col.step(pl, [0, -1.5], 1 / 60, P);
    expect(pl.eye.z).toBeGreaterThan(-1.0 + P.radius - 0.06);
  });

  it("falls with gravity when spawned in the air", () => {
    const pl = player(0, 2);
    pl.eye.y = 3;
    pl.onGround = false;
    for (let i = 0; i < 120; i++) col.step(pl, [0, 0], 1 / 60, P);
    expect(pl.eye.y).toBeCloseTo(1.6, 1);
  });
});
