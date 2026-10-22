"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type ComponentRef } from "react";
import * as THREE from "three";
import { cameraBasis, easeInOut, markerVisible, toViewer, type ViewerTransform, type Vec3 } from "@/lib/viewer-math";
import type { Viewpoint } from "@/lib/schemas";

export type ViewMode = "orbit" | "walk";

export type Pose = { position: THREE.Vector3; quaternion: THREE.Quaternion; fov: number; forward: Vec3 };

export function viewpointPose(vp: Viewpoint, tr: ViewerTransform): Pose {
  const p = toViewer(vp.position, tr);
  const b = cameraBasis(vp.rotation_c2w, tr);
  const m = new THREE.Matrix4().makeBasis(new THREE.Vector3(...b.right), new THREE.Vector3(...b.up), new THREE.Vector3(...b.back));
  const q = new THREE.Quaternion().setFromRotationMatrix(m);
  const f = vp.focal_px ?? vp.height;
  const fov = THREE.MathUtils.clamp(THREE.MathUtils.radToDeg(2 * Math.atan(vp.height / 2 / f)), 35, 95);
  return { position: new THREE.Vector3(...p), quaternion: q, fov, forward: b.forward };
}

type SceneProps = {
  positions: Float32Array;
  colors: Float32Array | null;
  poses: Pose[];
  index: number;
  mode: ViewMode;
  pointSize: number;
  showFrustums: boolean;
  floorY: number;
  onSelect: (i: number) => void;
  onCamera: (pos: THREE.Vector3, forward: THREE.Vector3) => void;
  onArrive: () => void;
};

function Points({ positions, colors, size }: { positions: Float32Array; colors: Float32Array | null; size: number }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    if (colors) g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    g.computeBoundingSphere();
    return g;
  }, [positions, colors]);
  useEffect(() => () => geom.dispose(), [geom]);
  return (
    <points geometry={geom}>
      <pointsMaterial size={size} sizeAttenuation vertexColors={Boolean(colors)} color={colors ? "#ffffff" : "#7fe3d3"} transparent opacity={0.95} />
    </points>
  );
}

const FRUSTUM = (() => {
  // Pyramid in camera space (apex at origin, looking down −z), 0.35 units deep.
  const d = 0.35, w = 0.16, h = 0.22;
  const c = [[-w, h, -d], [w, h, -d], [w, -h, -d], [-w, -h, -d]];
  const seg: number[] = [];
  for (const p of c) seg.push(0, 0, 0, ...p);
  for (let i = 0; i < 4; i++) seg.push(...c[i], ...c[(i + 1) % 4]);
  return new Float32Array(seg);
})();

function Frustum({ pose, active, i, mode, onSelect }: { pose: Pose; active: boolean; i: number; mode: ViewMode; onSelect: (i: number) => void }) {
  const [hover, setHover] = useState(false);
  const group = useRef<THREE.Group>(null);
  // In walk mode the camera sits at a capture position; a marker of that (or a
  // co-located) camera would fill the whole view, so hide markers that are too close.
  useFrame(({ camera }) => {
    if (group.current) group.current.visible = markerVisible(camera.position.distanceTo(pose.position), mode);
  });
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(FRUSTUM, 3));
    return g;
  }, []);
  const color = active ? "#f4efe4" : hover ? "#7fe3d3" : "#45cfbb";
  return (
    <group ref={group} position={pose.position} quaternion={pose.quaternion}>
      <lineSegments geometry={geom}>
        <lineBasicMaterial color={color} transparent opacity={active ? 1 : 0.75} />
      </lineSegments>
      <mesh
        position={[0, 0, -0.18]}
        onClick={(e: ThreeEvent<MouseEvent>) => {
          e.stopPropagation();
          onSelect(i);
        }}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHover(true);
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          setHover(false);
          document.body.style.cursor = "";
        }}
      >
        <sphereGeometry args={[active ? 0.09 : 0.07, 12, 12]} />
        <meshBasicMaterial color={color} transparent opacity={active ? 0.95 : hover ? 0.9 : 0.55} />
      </mesh>
    </group>
  );
}

/** Animates the camera between viewpoints; supports orbit (dollhouse) and first-person walk. */
function Rig({ poses, index, mode, onCamera, onArrive }: Pick<SceneProps, "poses" | "index" | "mode" | "onCamera" | "onArrive">) {
  const { camera, gl } = useThree();
  const controls = useRef<ComponentRef<typeof OrbitControls> | null>(null);
  const anim = useRef<{ t: number; from: THREE.Vector3; to: THREE.Vector3; qf: THREE.Quaternion; qt: THREE.Quaternion; tf: THREE.Vector3; tt: THREE.Vector3; fov0: number; fov1: number } | null>(null);
  const look = useRef({ yaw: 0, pitch: 0, dragging: false, x: 0, y: 0 });
  const keys = useRef(new Set<string>());
  const persp = camera as THREE.PerspectiveCamera;
  const tmpF = useMemo(() => new THREE.Vector3(), []);
  const lastReport = useRef(0);

  // Start a transition whenever the target viewpoint or mode changes.
  useEffect(() => {
    const pose = poses[index];
    if (!pose) return;
    const to = new THREE.Vector3();
    const qt = new THREE.Quaternion();
    const tt = new THREE.Vector3();
    let fov1 = 55;
    if (mode === "walk") {
      to.copy(pose.position);
      qt.copy(pose.quaternion);
      tt.copy(pose.position).add(new THREE.Vector3(0, 0, -1).applyQuaternion(pose.quaternion));
      fov1 = pose.fov;
    } else {
      const back = new THREE.Vector3(0, 0, 1).applyQuaternion(pose.quaternion);
      back.y = 0;
      if (back.lengthSq() < 1e-6) back.set(0, 0, 1);
      back.normalize();
      tt.copy(pose.position);
      to.copy(pose.position).addScaledVector(back, 4.5).add(new THREE.Vector3(0, 3.2, 0));
      const m = new THREE.Matrix4().lookAt(to, tt, new THREE.Vector3(0, 1, 0));
      qt.setFromRotationMatrix(m);
    }
    const tf = controls.current ? controls.current.target.clone() : tt.clone();
    anim.current = { t: 0, from: camera.position.clone(), to, qf: camera.quaternion.clone(), qt, tf, tt, fov0: persp.fov, fov1 };
    look.current.yaw = 0;
    look.current.pitch = 0;
  }, [index, mode, poses, camera, persp]);

  // First-person look (drag) and WASD movement in walk mode.
  useEffect(() => {
    if (mode !== "walk") return;
    const el = gl.domElement;
    const down = (e: PointerEvent) => {
      look.current.dragging = true;
      look.current.x = e.clientX;
      look.current.y = e.clientY;
    };
    const move = (e: PointerEvent) => {
      if (!look.current.dragging || anim.current) return;
      const dx = e.clientX - look.current.x, dy = e.clientY - look.current.y;
      look.current.x = e.clientX;
      look.current.y = e.clientY;
      const euler = new THREE.Euler().setFromQuaternion(camera.quaternion, "YXZ");
      euler.y += dx * 0.0035;
      euler.x = THREE.MathUtils.clamp(euler.x + dy * 0.0035, -1.3, 1.3);
      camera.quaternion.setFromEuler(euler);
    };
    const up = () => (look.current.dragging = false);
    const kd = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      keys.current.add(e.key.toLowerCase());
    };
    const pressed = keys.current;
    const ku = (e: KeyboardEvent) => pressed.delete(e.key.toLowerCase());
    el.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);
    return () => {
      el.removeEventListener("pointerdown", down);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      pressed.clear();
    };
  }, [mode, gl, camera]);

  useFrame((state, dt) => {
    const a = anim.current;
    if (a) {
      a.t = Math.min(1, a.t + dt / 1.25);
      const k = easeInOut(a.t);
      camera.position.lerpVectors(a.from, a.to, k);
      camera.quaternion.slerpQuaternions(a.qf, a.qt, k);
      persp.fov = a.fov0 + (a.fov1 - a.fov0) * k;
      persp.updateProjectionMatrix();
      if (controls.current) controls.current.target.lerpVectors(a.tf, a.tt, k);
      if (a.t >= 1) {
        anim.current = null;
        onArrive();
      }
    } else if (mode === "walk" && keys.current.size) {
      const speed = 2.2 * dt;
      const f = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
      f.y = 0;
      f.normalize();
      const r = new THREE.Vector3().crossVectors(f, new THREE.Vector3(0, 1, 0));
      const k = keys.current;
      if (k.has("w")) camera.position.addScaledVector(f, speed);
      if (k.has("s")) camera.position.addScaledVector(f, -speed);
      if (k.has("a")) camera.position.addScaledVector(r, -speed);
      if (k.has("d")) camera.position.addScaledVector(r, speed);
    }
    if (state.clock.elapsedTime - lastReport.current > 0.1) {
      lastReport.current = state.clock.elapsedTime;
      camera.getWorldDirection(tmpF);
      onCamera(camera.position, tmpF);
    }
  });

  return mode === "orbit" ? (
    <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.08} minDistance={0.5} maxDistance={40} maxPolarAngle={Math.PI * 0.495} />
  ) : null;
}

export function Scene(props: SceneProps) {
  const { positions, colors, poses, index, mode, pointSize, showFrustums, floorY, onSelect, onCamera, onArrive } = props;
  return (
    <Canvas
      dpr={[1, 1.75]}
      camera={{ position: [6, 6, 8], fov: 55, near: 0.02, far: 400 }}
      gl={{ antialias: true, powerPreference: "high-performance", preserveDrawingBuffer: true }}
      onCreated={({ gl }) => gl.setClearColor("#070b12")}
      aria-label="3D reconstruction viewer"
      role="img"
    >
      <fog attach="fog" args={["#070b12", 18, 60]} />
      <Points positions={positions} colors={colors} size={pointSize} />
      {showFrustums && poses.map((p, i) => <Frustum key={i} pose={p} i={i} mode={mode} active={i === index} onSelect={onSelect} />)}
      <gridHelper args={[30, 30, "#1d2a3e", "#141e2e"]} position={[0, floorY, 0]} />
      <Rig poses={poses} index={index} mode={mode} onCamera={onCamera} onArrive={onArrive} />
    </Canvas>
  );
}
