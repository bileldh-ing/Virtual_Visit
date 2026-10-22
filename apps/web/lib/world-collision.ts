/**
 * Capsule collision + floor following against a collider mesh, using three-mesh-bvh (MIT).
 *
 * The player is a vertical capsule from the feet (radius above the floor) to just below the eye. Each step:
 * integrate horizontal velocity + gravity, push the capsule out of every intersecting triangle (shapecast),
 * then snap to the floor below when it is within a step height. Pure enough to unit-test with a box room.
 */

import * as THREE from "three";
import { MeshBVH } from "three-mesh-bvh";

export type Player = {
  eye: THREE.Vector3; // eye position (world)
  vel: THREE.Vector3; // m/s (y = vertical)
  onGround: boolean;
};

export type CollisionParams = {
  eyeHeight: number;
  radius: number;
  stepHeight: number;
  gravity: number;
  headroom: number; // capsule top is this far below the eye
};

export const DEFAULT_COLLISION: CollisionParams = { eyeHeight: 1.6, radius: 0.25, stepHeight: 0.35, gravity: 18, headroom: 0.1 };

export class WorldCollider {
  readonly bvh: MeshBVH;
  readonly geometry: THREE.BufferGeometry;
  private seg = new THREE.Line3();
  private box = new THREE.Box3();
  private triPoint = new THREE.Vector3();
  private capPoint = new THREE.Vector3();
  private dir = new THREE.Vector3();
  private ray = new THREE.Ray();

  constructor(geometry: THREE.BufferGeometry) {
    this.geometry = geometry;
    this.bvh = new MeshBVH(geometry, { targetLeafSize: 12 });
  }

  /** Merge all meshes under `root` (world transforms applied) into one non-indexed-safe geometry. */
  static fromObject(root: THREE.Object3D): WorldCollider {
    root.updateMatrixWorld(true);
    const positions: number[] = [];
    const v = new THREE.Vector3();
    root.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh || !m.geometry) return;
      const g = m.geometry.index ? m.geometry.toNonIndexed() : m.geometry;
      const pos = g.getAttribute("position");
      for (let i = 0; i < pos.count; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld);
        positions.push(v.x, v.y, v.z);
      }
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    return new WorldCollider(geo);
  }

  bounds(): THREE.Box3 {
    this.geometry.computeBoundingBox();
    return this.geometry.boundingBox!.clone();
  }

  /** First hit distance along a ray (any side), or null. */
  cast(origin: THREE.Vector3, direction: THREE.Vector3, far = 1e4): number | null {
    this.ray.origin.copy(origin);
    this.ray.direction.copy(direction).normalize();
    const hit = this.bvh.raycastFirst(this.ray, THREE.DoubleSide, 0, far);
    return hit ? hit.distance : null;
  }

  /** Floor height under a point (searching down from `p.y + up`), or null if there is no floor. */
  floorBelow(p: THREE.Vector3, up = 0.5, far = 50): number | null {
    const o = new THREE.Vector3(p.x, p.y + up, p.z);
    const d = this.cast(o, new THREE.Vector3(0, -1, 0), far);
    return d === null ? null : o.y - d;
  }

  /** Push a capsule segment out of the mesh. Returns the total correction applied. */
  resolveCapsule(start: THREE.Vector3, end: THREE.Vector3, radius: number, iterations = 3): THREE.Vector3 {
    const total = new THREE.Vector3();
    for (let it = 0; it < iterations; it++) {
      this.seg.start.copy(start);
      this.seg.end.copy(end);
      this.box.makeEmpty();
      this.box.expandByPoint(start);
      this.box.expandByPoint(end);
      this.box.min.addScalar(-radius);
      this.box.max.addScalar(radius);
      let moved = false;
      this.bvh.shapecast({
        intersectsBounds: (b) => b.intersectsBox(this.box),
        intersectsTriangle: (tri) => {
          const dist = tri.closestPointToSegment(this.seg, this.triPoint, this.capPoint);
          if (dist < radius) {
            const depth = radius - dist;
            this.dir.subVectors(this.capPoint, this.triPoint);
            if (this.dir.lengthSq() < 1e-12) tri.getNormal(this.dir);
            this.dir.normalize();
            this.seg.start.addScaledVector(this.dir, depth);
            this.seg.end.addScaledVector(this.dir, depth);
            moved = true;
          }
        },
      });
      const delta = new THREE.Vector3().subVectors(this.seg.start, start);
      start.copy(this.seg.start);
      end.copy(this.seg.end);
      total.add(delta);
      if (!moved) break;
    }
    return total;
  }

  /**
   * One physics step. `horizontal` is the desired horizontal velocity (x, z). Mutates `player`.
   * Sub-steps keep fast movement from tunnelling through thin walls.
   */
  step(player: Player, horizontal: [number, number], dt: number, p: CollisionParams = DEFAULT_COLLISION): void {
    const maxMove = p.radius * 0.5;
    const speed = Math.hypot(horizontal[0], horizontal[1]) + Math.abs(player.vel.y);
    const n = Math.min(12, Math.max(1, Math.ceil((speed * dt) / maxMove)));
    const h = dt / n;
    for (let i = 0; i < n; i++) {
      player.vel.x = horizontal[0];
      player.vel.z = horizontal[1];
      player.vel.y = player.onGround ? Math.min(0, player.vel.y) : player.vel.y - p.gravity * h;
      player.eye.addScaledVector(player.vel, h);
      const feet = player.eye.y - p.eyeHeight;
      const start = new THREE.Vector3(player.eye.x, player.eye.y - p.headroom - p.radius, player.eye.z);
      const end = new THREE.Vector3(player.eye.x, feet + p.radius + p.stepHeight, player.eye.z);
      if (end.y > start.y) end.y = start.y;
      const corr = this.resolveCapsule(start, end, p.radius);
      player.eye.x += corr.x;
      player.eye.z += corr.z;
      if (corr.y < 0) player.eye.y += corr.y; // head bumped a ceiling
      // floor following: snap down/up within the step height, else fall
      const floor = this.floorBelow(player.eye, 0.0, p.eyeHeight + p.stepHeight + 0.5);
      if (floor !== null && player.eye.y - p.eyeHeight - floor <= p.stepHeight + 1e-3 && player.vel.y <= 0) {
        player.eye.y = floor + p.eyeHeight;
        player.vel.y = 0;
        player.onGround = true;
      } else {
        player.onGround = false;
      }
    }
  }
}
