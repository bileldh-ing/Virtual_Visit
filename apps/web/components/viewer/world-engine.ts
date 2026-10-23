/**
 * Imperative three.js engine behind <WorldViewer>: loads a Marble world (SPZ splats via Spark) or a depth-estimated
 * room (GLB), a collider mesh, and runs game-like first-person navigation with capsule collision.
 *
 * Browser-only (imported dynamically). React talks to it through the small public API and callbacks.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { WorldCollider, type CollisionParams, type Player } from "@/lib/world-collision";
import {
  clampFov,
  clampPitch,
  DEFAULT_MOVE,
  lerpPose,
  marbleMatrix,
  minimapFrame,
  pitchFromRotationC2W,
  stepVelocity,
  transitionKind,
  waypointInDirection,
  yawFromAzimuth,
  yawFromRotationC2W,
  yawTowards,
  type MoveParams,
  type Pose,
  type Vec3,
} from "@/lib/world-nav";
import type { World } from "@/lib/schemas";

export type WorldUrls = { spzLow?: string; spzHigh?: string; room?: string; collider?: string; pano?: string };
/** "auto" streams full detail on GPUs and stops at the preview on software renderers (SwiftShader, llvmpipe). */
export type DetailPref = "auto" | "preview" | "full";
export type Hotspot = { id: string; label: string; pose: Pose; uploadId: string | null; photoName: string; fovY?: number };
export type MinimapInfo = { image: string; toUV: (x: number, z: number) => { u: number; v: number } };
export type EngineEvents = {
  onProgress?: (label: string, fraction: number | null) => void;
  onReady?: (info: { hotspots: Hotspot[]; minimap: MinimapInfo | null; mode: "walk" | "waypoint"; eyeHeight: number }) => void;
  onQuality?: (label: string) => void;
  onError?: (message: string) => void;
  onPose?: (p: { x: number; z: number; yaw: number }) => void;
  onArrive?: (hotspot: number | null) => void;
  onTour?: (playing: boolean) => void;
  onFade?: (opacity: number) => void;
};

type Transition = { from: Pose; to: Pose; t: number; dur: number; kind: "glide" | "door"; hotspot: number | null; swapped: boolean };

async function fetchWithProgress(url: string, onFrac: (f: number | null) => void): Promise<ArrayBuffer> {
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const total = Number(res.headers.get("content-length")) || 0;
  if (!res.body || !total) {
    onFrac(null);
    return res.arrayBuffer();
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    onFrac(Math.min(1, got / total));
  }
  const out = new Uint8Array(got);
  let o = 0;
  for (const c of chunks) {
    out.set(c, o);
    o += c.length;
  }
  return out.buffer;
}

export class WorldEngine {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  private host: HTMLElement;
  private world: World;
  private urls: WorldUrls;
  private ev: EngineEvents;
  private worldRoot = new THREE.Group();
  private collider: WorldCollider | null = null;
  private player: Player = { eye: new THREE.Vector3(0, 1.6, 0), vel: new THREE.Vector3(), onGround: false };
  private params: CollisionParams = { eyeHeight: 1.6, radius: 0.25, stepHeight: 0.35, gravity: 18, headroom: 0.1 };
  private move: MoveParams = { ...DEFAULT_MOVE };
  private hvel: [number, number] = [0, 0];
  private yaw = 0;
  private pitch = 0;
  private keys = new Set<string>();
  private touchMove: [number, number] = [0, 0];
  private target: THREE.Vector3 | null = null;
  private transition: Transition | null = null;
  private tour: { stops: Pose[]; hot: (number | null)[]; i: number; dwell: number } | null = null;
  private clock = new THREE.Clock();
  private raf = 0;
  private disposed = false;
  private dragging: { x: number; y: number; moved: number } | null = null;
  private mode: "walk" | "waypoint" = "walk";
  private waypointIndex = 0;
  private lastPoseEmit = 0;
  private stuck = 0;
  private manual = false;
  hotspots: Hotspot[] = [];
  loaded = false;
  splatPoints = 0;
  private spark: THREE.Object3D | null = null;
  private panoTex: THREE.Texture | null = null;
  private hotspotMeshes: THREE.Object3D[] = [];
  private listeners: [EventTarget, string, EventListener, AddEventListenerOptions?][] = [];
  private resizeObs: ResizeObserver | null = null;
  /** true when WebGL runs on a CPU rasteriser: render at lower resolution and keep the preview splats. */
  readonly software: boolean;
  private detail: DetailPref;

  constructor(host: HTMLElement, world: World, urls: WorldUrls, ev: EngineEvents, detail: DetailPref = "auto") {
    this.host = host;
    this.world = world;
    this.urls = urls;
    this.ev = ev;
    this.renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance", preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(0x0b1016, 1);
    this.detail = detail;
    this.software = false;
    try {
      const gl = this.renderer.getContext();
      const dbg = gl.getExtension("WEBGL_debug_renderer_info");
      const name = dbg ? String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)) : "";
      this.software = /swiftshader|llvmpipe|softpipe|software/i.test(name);
    } catch {
      /* unknown renderer: assume hardware */
    }
    if (this.software) this.renderer.setPixelRatio(0.5);
    this.camera = new THREE.PerspectiveCamera(70, 1, 0.03, 400);
    this.camera.rotation.order = "YXZ";
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.touchAction = "none";
    this.renderer.domElement.setAttribute("data-testid", "world-canvas");
    this.scene.add(this.worldRoot);
    this.resize();
    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(host);
    this.bindInput();
  }

  // ------------------------------------------------------------------ loading
  async load(): Promise<void> {
    try {
      const isMarble = this.world.engine === "marble_world";
      if (isMarble) {
        const m = new THREE.Matrix4().fromArray(marbleMatrix(this.world.semantics_metadata as never));
        this.worldRoot.matrixAutoUpdate = false;
        this.worldRoot.matrix.copy(m);
        this.worldRoot.matrixWorldNeedsUpdate = true;
      }
      // 1. collider first: it defines the floor, eye height and minimap
      if (this.urls.collider) {
        this.ev.onProgress?.("Loading walls and floor", 0);
        const buf = await fetchWithProgress(this.urls.collider, (f) => this.ev.onProgress?.("Loading walls and floor", f));
        const gltf = await new GLTFLoader().parseAsync(buf, "");
        const holder = new THREE.Group();
        if (isMarble) {
          holder.matrixAutoUpdate = false;
          holder.matrix.copy(this.worldRoot.matrix);
        }
        holder.add(gltf.scene);
        this.collider = WorldCollider.fromObject(holder);
      }
      this.placeSpawn();
      // 2. panorama as an immediate backdrop while the splats stream (Marble only)
      if (isMarble && this.urls.pano) {
        try {
          const tex = await new THREE.TextureLoader().loadAsync(this.urls.pano);
          tex.mapping = THREE.EquirectangularReflectionMapping;
          tex.colorSpace = THREE.SRGBColorSpace;
          this.panoTex = tex;
          this.scene.background = tex;
        } catch {
          /* optional */
        }
      }
      this.buildHotspots();
      const minimap = this.collider ? this.renderMinimap() : null;
      this.loaded = true;
      this.ev.onReady?.({ hotspots: this.hotspots, minimap, mode: this.mode, eyeHeight: this.params.eyeHeight });
      this.start();
      // 3. the visible world
      if (isMarble) await this.loadSplats();
      else if (this.urls.room) await this.loadRoom();
      this.ev.onProgress?.("", 1);
    } catch (e) {
      this.ev.onError?.(e instanceof Error ? e.message : "Could not load the world.");
    }
  }

  private async loadSplats(): Promise<void> {
    const { SparkRenderer, SplatMesh } = await import("@sparkjsdev/spark");
    const spark = new SparkRenderer({ renderer: this.renderer });
    this.scene.add(spark);
    this.spark = spark;
    const previewOnly = this.urls.spzLow && (this.detail === "preview" || (this.detail === "auto" && this.software));
    const order = (previewOnly ? [this.urls.spzLow] : [this.urls.spzLow, this.urls.spzHigh]).filter(Boolean) as string[];
    let current: THREE.Object3D | null = null;
    for (let i = 0; i < order.length; i++) {
      const label = order.length > 1 && i === 0 ? "Streaming preview splats" : "Streaming full-detail splats";
      const bytes = await fetchWithProgress(order[i], (f) => this.ev.onProgress?.(label, f));
      if (this.disposed) return;
      const mesh = new SplatMesh({ fileBytes: new Uint8Array(bytes), fileName: "world.spz" });
      await mesh.initialized;
      if (this.disposed) return;
      this.worldRoot.add(mesh);
      if (current) {
        this.worldRoot.remove(current);
        (current as unknown as { dispose?: () => void }).dispose?.();
      }
      current = mesh;
      this.splatPoints = (mesh as unknown as { numSplats?: number }).numSplats ?? 0;
      this.scene.background = new THREE.Color(0x0b1016);
      const last = i === order.length - 1;
      this.ev.onQuality?.(previewOnly ? (this.software ? "Preview detail (software renderer)" : "Preview detail") : last ? "Full detail" : "Preview detail");
    }
  }

  private async loadRoom(): Promise<void> {
    const buf = await fetchWithProgress(this.urls.room!, (f) => this.ev.onProgress?.("Loading the depth-estimated room", f));
    const gltf = await new GLTFLoader().parseAsync(buf, "");
    if (this.disposed) return;
    gltf.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) {
        const mat = m.material as THREE.MeshBasicMaterial;
        mat.side = THREE.DoubleSide;
      }
    });
    this.worldRoot.add(gltf.scene);
    this.ev.onQuality?.("Depth-estimated mesh");
  }

  private placeSpawn(): void {
    const w = this.world;
    let spawn = new THREE.Vector3(0, 0, 0).applyMatrix4(this.worldRoot.matrix);
    let yaw = 0;
    if (w.spawn) {
      spawn = new THREE.Vector3(...(w.spawn.position as Vec3));
      yaw = (w.spawn.yaw_deg * Math.PI) / 180;
    }
    if (this.collider) {
      const floor = this.collider.floorBelow(spawn, 0.2, 60);
      const ceilD = this.collider.cast(spawn, new THREE.Vector3(0, 1, 0), 60);
      if (floor !== null) {
        const capture = spawn.y - floor;
        const room = ceilD !== null ? spawn.y + ceilD - floor : capture * 2.5;
        const metric = Boolean(w.semantics_metadata && (w.semantics_metadata as Record<string, number | null>).metric_scale_factor);
        let eye = w.eye_height ?? (metric ? Math.max(1.5, Math.min(1.7, capture)) : Math.max(capture, room * 0.4));
        eye = Math.min(eye, room - 0.15);
        this.params.eyeHeight = eye;
        // body proportions follow the room size when there is no metric scale
        const unit = metric || w.eye_height ? 1 : Math.max(0.4, room / 2.7);
        this.params.radius = 0.22 * unit;
        this.params.stepHeight = 0.3 * unit;
        this.params.gravity = 18 * unit;
        this.move.walkSpeed = 1.5 * unit;
        spawn.y = floor + eye;
      }
    } else {
      this.mode = "waypoint";
    }
    this.player.eye.copy(spawn);
    this.yaw = yaw;
    this.pitch = 0;
    this.player.onGround = Boolean(this.collider);
  }

  private buildHotspots(): void {
    const w = this.world;
    const hs: Hotspot[] = [];
    if (w.viewpoints?.length) {
      for (const v of w.viewpoints) {
        const r = v.rotation_c2w;
        hs.push({
          id: v.name,
          label: v.name.replace(/\.[a-z]+$/i, ""),
          pose: { p: v.position as Vec3, yaw: yawFromRotationC2W(r), pitch: clampPitch(pitchFromRotationC2W(r)) },
          uploadId: v.upload_id,
          photoName: v.name,
          fovY: v.fov_y_deg,
        });
      }
    } else if (w.input_photos?.length) {
      for (const ph of w.input_photos) {
        if (ph.azimuth_deg == null) continue; // position unknown: listed in the filmstrip, not placed
        hs.push({
          id: ph.name,
          label: ph.name.replace(/\.[a-z]+$/i, ""),
          pose: { p: [this.player.eye.x, this.player.eye.y, this.player.eye.z], yaw: yawFromAzimuth(ph.azimuth_deg), pitch: 0 },
          uploadId: ph.upload_id,
          photoName: ph.name,
        });
      }
    }
    this.hotspots = hs;
    if (!hs.length && this.mode === "waypoint") return;
    const geo = new THREE.RingGeometry(0.12, 0.17, 32);
    const mat = new THREE.MeshBasicMaterial({ color: 0x2dd4bf, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthTest: false });
    hs.forEach((h, i) => {
      const ring = new THREE.Mesh(geo, mat);
      const floor = this.collider?.floorBelow(new THREE.Vector3(...h.pose.p), 0.2, 30);
      ring.position.set(h.pose.p[0], (floor ?? h.pose.p[1] - this.params.eyeHeight) + 0.02, h.pose.p[2]);
      ring.rotation.x = -Math.PI / 2;
      ring.renderOrder = 10;
      ring.userData.hotspot = i;
      this.scene.add(ring);
      this.hotspotMeshes.push(ring);
    });
  }

  /** Top-down render of the collider (floor light, walls dark), ceiling clipped by the camera's near plane. */
  private renderMinimap(): MinimapInfo | null {
    if (!this.collider) return null;
    const b = this.collider.bounds();
    const floorY = this.player.eye.y - this.params.eyeHeight;
    // frame the space around reachable floor, not the outer shell: sample floor along rays from the spawn
    const pts: [number, number][] = [];
    for (let a = 0; a < 64; a++) {
      const dir = new THREE.Vector3(Math.cos((a / 64) * Math.PI * 2), 0, Math.sin((a / 64) * Math.PI * 2));
      const d = this.collider.cast(this.player.eye, dir, 80) ?? 4;
      pts.push([this.player.eye.x + dir.x * d, this.player.eye.z + dir.z * d]);
    }
    const xs = pts.map((p) => p[0]);
    const zs = pts.map((p) => p[1]);
    const frame = minimapFrame({
      minX: Math.max(b.min.x, Math.min(...xs)),
      maxX: Math.min(b.max.x, Math.max(...xs)),
      minZ: Math.max(b.min.z, Math.min(...zs)),
      maxZ: Math.min(b.max.z, Math.max(...zs)),
    });
    const size = 384;
    const rt = new THREE.WebGLRenderTarget(size, size);
    const cam = new THREE.OrthographicCamera(-frame.half, frame.half, frame.half, -frame.half, 0.01, 200);
    cam.position.set(frame.cx, floorY + this.params.eyeHeight * 1.05, frame.cz);
    cam.up.set(0, 0, -1);
    cam.lookAt(frame.cx, floorY - 10, frame.cz);
    const mat = new THREE.ShaderMaterial({
      side: THREE.DoubleSide,
      uniforms: { floorY: { value: floorY }, eye: { value: this.params.eyeHeight } },
      vertexShader: "varying float vy; void main(){ vec4 w = modelMatrix * vec4(position,1.0); vy = w.y; gl_Position = projectionMatrix * viewMatrix * w; }",
      fragmentShader:
        "uniform float floorY; uniform float eye; varying float vy; void main(){ float h = clamp((vy - floorY) / max(eye, 1e-3), 0.0, 1.0); vec3 floorC = vec3(0.80,0.85,0.90); vec3 wallC = vec3(0.20,0.24,0.30); gl_FragColor = vec4(mix(floorC, wallC, smoothstep(0.08, 0.35, h)), 1.0); }",
    });
    const mesh = new THREE.Mesh(this.collider.geometry, mat);
    const sc = new THREE.Scene();
    sc.background = new THREE.Color(0x0b1016);
    sc.add(mesh);
    this.renderer.setRenderTarget(rt);
    this.renderer.render(sc, cam);
    const px = new Uint8Array(size * size * 4);
    this.renderer.readRenderTargetPixels(rt, 0, 0, size, size, px);
    this.renderer.setRenderTarget(null);
    rt.dispose();
    mat.dispose();
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext("2d")!;
    const img = ctx.createImageData(size, size);
    for (let y = 0; y < size; y++) img.data.set(px.subarray((size - 1 - y) * size * 4, (size - y) * size * 4), y * size * 4);
    ctx.putImageData(img, 0, 0);
    return { image: canvas.toDataURL("image/png"), toUV: frame.toUV };
  }

  // ------------------------------------------------------------------ public API
  goTo(i: number): void {
    const h = this.hotspots[i];
    if (!h) return;
    this.startTransition({ ...h.pose, p: this.groundedEye(h.pose.p) }, i);
  }

  /** Move to a uv position on the minimap (click-to-move on the map). */
  walkToMapPoint(x: number, z: number): void {
    this.target = new THREE.Vector3(x, this.player.eye.y, z);
    this.stopTour();
  }

  lookBy(dx: number, dy: number): void {
    this.yaw -= dx * 0.0032;
    this.pitch = clampPitch(this.pitch - dy * 0.0032);
    this.cancelAutopilot();
  }

  setTouchMove(forward: number, right: number): void {
    this.touchMove = [forward, right];
    if (forward || right) this.cancelAutopilot();
  }

  zoom(delta: number): void {
    this.camera.fov = clampFov(this.camera.fov + delta);
    this.camera.updateProjectionMatrix();
  }

  get fov(): number {
    return this.camera.fov;
  }

  lockPointer(): void {
    void this.renderer.domElement.requestPointerLock?.();
  }

  startTour(): void {
    const stops: Pose[] = [];
    const hot: (number | null)[] = [];
    if (this.hotspots.length) {
      this.hotspots.forEach((h, i) => {
        stops.push({ ...h.pose, p: this.groundedEye(h.pose.p) });
        hot.push(i);
      });
    } else {
      // no placed photos: a collision-safe loop — look around, then visit open space in 4 directions and return
      const c: Vec3 = [this.player.eye.x, this.player.eye.y, this.player.eye.z];
      for (let k = 0; k < 4; k++) {
        stops.push({ p: c, yaw: this.yaw + (k * Math.PI) / 2, pitch: 0 });
        hot.push(null);
      }
      for (let k = 0; k < 4; k++) {
        const a = this.yaw + (k * Math.PI) / 2;
        const dir = new THREE.Vector3(-Math.sin(a), 0, -Math.cos(a));
        const d = this.collider?.cast(this.player.eye, dir, 40) ?? 0;
        const go = Math.max(0, d - this.params.radius * 3) * 0.6;
        if (go < 0.4) continue;
        const p: Vec3 = [c[0] + dir.x * go, c[1], c[2] + dir.z * go];
        stops.push({ p, yaw: a, pitch: 0 }, { p, yaw: a + Math.PI, pitch: 0 });
        hot.push(null, null);
      }
      stops.push({ p: c, yaw: this.yaw, pitch: 0 });
      hot.push(null);
    }
    if (!stops.length) return;
    this.tour = { stops, hot, i: -1, dwell: 0 };
    this.ev.onTour?.(true);
    this.nextTourStop();
  }

  stopTour(): void {
    if (this.tour) {
      this.tour = null;
      this.ev.onTour?.(false);
    }
  }

  state() {
    return {
      loaded: this.loaded,
      x: this.player.eye.x,
      y: this.player.eye.y,
      z: this.player.eye.z,
      yaw: this.yaw,
      pitch: this.pitch,
      onGround: this.player.onGround,
      eyeHeight: this.params.eyeHeight,
      mode: this.mode,
      fov: this.camera.fov,
      splatPoints: this.splatPoints,
      hasCollider: Boolean(this.collider),
      touring: Boolean(this.tour),
      software: this.software,
    };
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    for (const [t, n, f, o] of this.listeners) t.removeEventListener(n, f, o);
    this.resizeObs?.disconnect();
    if (document.pointerLockElement === this.renderer.domElement) document.exitPointerLock();
    this.panoTex?.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  // ------------------------------------------------------------------ internals
  private groundedEye(p: Vec3): Vec3 {
    if (!this.collider) return p;
    const floor = this.collider.floorBelow(new THREE.Vector3(...p), 0.3, 30);
    return floor === null ? p : [p[0], floor + this.params.eyeHeight, p[2]];
  }

  private cancelAutopilot(): void {
    this.target = null;
  }

  private startTransition(to: Pose, hotspot: number | null, dur?: number): void {
    const from: Pose = { p: [this.player.eye.x, this.player.eye.y, this.player.eye.z], yaw: this.yaw, pitch: this.pitch };
    const kind = transitionKind(from.p, to.p);
    const dist = Math.hypot(to.p[0] - from.p[0], to.p[2] - from.p[2]);
    this.transition = { from, to, t: 0, dur: dur ?? (kind === "door" ? 1.4 : Math.max(0.9, dist / 1.2)), kind, hotspot, swapped: false };
    this.target = null;
    this.ev.onArrive?.(null);
  }

  private nextTourStop(): void {
    if (!this.tour) return;
    this.tour.i = (this.tour.i + 1) % this.tour.stops.length;
    this.startTransition(this.tour.stops[this.tour.i], this.tour.hot[this.tour.i], undefined);
  }

  private bindInput(): void {
    const el = this.renderer.domElement;
    const on = (t: EventTarget, n: string, f: EventListener, o?: AddEventListenerOptions) => {
      t.addEventListener(n, f, o);
      this.listeners.push([t, n, f, o]);
    };
    on(window, "keydown", ((e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const k = e.key.toLowerCase();
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright", "shift"].includes(k)) {
        this.keys.add(k);
        if (k.startsWith("arrow")) e.preventDefault();
        this.stopTour();
        this.cancelAutopilot();
        if (this.mode === "waypoint" && (k === "w" || k === "arrowup")) this.waypointStep();
      }
      if (k === "+" || k === "=") this.zoom(-5);
      if (k === "-") this.zoom(5);
    }) as EventListener);
    on(window, "keyup", ((e: KeyboardEvent) => {
      this.keys.delete(e.key.toLowerCase());
    }) as EventListener);
    on(window, "blur", () => this.keys.clear());
    on(el, "wheel", ((e: WheelEvent) => {
      e.preventDefault();
      this.zoom(e.deltaY > 0 ? 3 : -3);
    }) as EventListener, { passive: false });
    on(document, "mousemove", ((e: MouseEvent) => {
      if (document.pointerLockElement === el) {
        this.lookBy(e.movementX, e.movementY);
        return;
      }
      if (this.dragging) {
        const dx = e.clientX - this.dragging.x;
        const dy = e.clientY - this.dragging.y;
        this.dragging.moved += Math.abs(dx) + Math.abs(dy);
        this.dragging.x = e.clientX;
        this.dragging.y = e.clientY;
        this.lookBy(-dx * 1.4, -dy * 1.4); // drag the world (grab-to-look)
        this.stopTour();
      }
    }) as EventListener);
    on(el, "mousedown", ((e: MouseEvent) => {
      if (document.pointerLockElement === el) return;
      this.dragging = { x: e.clientX, y: e.clientY, moved: 0 };
    }) as EventListener);
    on(document, "mouseup", ((e: MouseEvent) => {
      const d = this.dragging;
      this.dragging = null;
      if (d && d.moved < 6 && e.target === el) this.clickAt(e.clientX, e.clientY);
    }) as EventListener);
    on(el, "dblclick", () => this.lockPointer());
    // touch: one finger drag = look, tap = click-to-move (the joystick component handles movement)
    let touch: { id: number; x: number; y: number; moved: number } | null = null;
    on(el, "touchstart", ((e: TouchEvent) => {
      const t = e.changedTouches[0];
      touch = { id: t.identifier, x: t.clientX, y: t.clientY, moved: 0 };
    }) as EventListener, { passive: true });
    on(el, "touchmove", ((e: TouchEvent) => {
      if (!touch) return;
      for (const t of Array.from(e.changedTouches)) {
        if (t.identifier !== touch.id) continue;
        const dx = t.clientX - touch.x;
        const dy = t.clientY - touch.y;
        touch.moved += Math.abs(dx) + Math.abs(dy);
        touch.x = t.clientX;
        touch.y = t.clientY;
        this.lookBy(-dx * 2, -dy * 2);
        this.stopTour();
      }
    }) as EventListener, { passive: true });
    on(el, "touchend", ((e: TouchEvent) => {
      if (touch && touch.moved < 8) {
        const t = e.changedTouches[0];
        this.clickAt(t.clientX, t.clientY);
      }
      touch = null;
    }) as EventListener, { passive: true });
  }

  /** Click-to-move: hotspot rings first, then a point on the walkable floor. */
  clickAt(clientX: number, clientY: number): void {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const rc = new THREE.Raycaster();
    rc.setFromCamera(ndc, this.camera);
    const hit = rc.intersectObjects(this.hotspotMeshes, false)[0];
    if (hit) {
      this.stopTour();
      this.goTo(hit.object.userData.hotspot as number);
      return;
    }
    if (this.mode === "waypoint" || !this.collider) return;
    const d = this.collider.cast(rc.ray.origin, rc.ray.direction, 60);
    if (d === null) return;
    const p = rc.ray.origin.clone().addScaledVector(rc.ray.direction, d);
    const floor = this.player.eye.y - this.params.eyeHeight;
    if (p.y > floor + this.params.stepHeight * 1.5) {
      // clicked a wall or furniture: walk up to it, not into it
      p.addScaledVector(rc.ray.direction, -this.params.radius * 2);
    }
    this.stopTour();
    this.target = new THREE.Vector3(p.x, this.player.eye.y, p.z);
    this.stuck = 0;
  }

  private waypointStep(): void {
    const pts = this.hotspots.map((h) => h.pose.p);
    if (!pts.length) return;
    const next = waypointInDirection(pts, this.waypointIndex, this.yaw);
    if (next !== null) {
      this.waypointIndex = next;
      this.goTo(next);
    }
  }

  private start(): void {
    this.clock.start();
    const loop = () => {
      if (this.disposed) return;
      this.raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, this.clock.getDelta());
      if (!this.manual) this.tick(dt);
    };
    loop();
  }

  /**
   * Fixed-timestep control for tests and offline capture on machines without a GPU: while manual, the render loop
   * stops advancing time and `advance()` simulates `seconds` in `1/hz` steps (input, collision, transitions), then
   * renders one frame. Movement is identical to the realtime loop; only wall-clock pacing differs.
   */
  setManual(on: boolean): void {
    this.manual = on;
    this.clock.getDelta();
  }

  advance(seconds: number, hz = 60): void {
    const n = Math.max(1, Math.round(seconds * hz));
    for (let i = 0; i < n; i++) this.tick(seconds / n, i === n - 1);
  }

  private tick(dt: number, render = true): void {
    // 1. scripted transition (hotspot glide / door fade / tour)
    if (this.transition) {
      const tr = this.transition;
      tr.t += dt / tr.dur;
      if (tr.kind === "door") {
        const fade = tr.t < 0.5 ? tr.t * 2 : (1 - tr.t) * 2;
        this.ev.onFade?.(Math.max(0, Math.min(1, fade)));
        const pose = tr.t < 0.5 ? tr.from : tr.to;
        this.player.eye.set(...pose.p);
        this.yaw = pose.yaw;
        this.pitch = pose.pitch;
      } else {
        const pose = lerpPose(tr.from, tr.to, tr.t);
        this.player.eye.set(...pose.p);
        this.yaw = pose.yaw;
        this.pitch = pose.pitch;
      }
      if (tr.t >= 1) {
        this.transition = null;
        this.ev.onFade?.(0);
        this.player.vel.set(0, 0, 0);
        this.hvel = [0, 0];
        this.ev.onArrive?.(tr.hotspot);
        if (this.tour) this.tour.dwell = this.tour.hot[this.tour.i] !== null ? 2.6 : 1.2;
      }
    } else if (this.tour) {
      this.tour.dwell -= dt;
      if (this.tour.dwell <= 0) this.nextTourStop();
    } else if (this.mode === "walk" && this.collider) {
      // 2. free walk with collision
      let input = { forward: 0, right: 0, sprint: this.keys.has("shift") };
      const k = this.keys;
      input.forward = (k.has("w") || k.has("arrowup") ? 1 : 0) - (k.has("s") || k.has("arrowdown") ? 1 : 0) + this.touchMove[0];
      input.right = (k.has("d") || k.has("arrowright") ? 1 : 0) - (k.has("a") || k.has("arrowleft") ? 1 : 0) + this.touchMove[1];
      if (this.target) {
        const dx = this.target.x - this.player.eye.x;
        const dz = this.target.z - this.player.eye.z;
        const d = Math.hypot(dx, dz);
        if (d < 0.12 || this.stuck > 0.6) {
          this.target = null;
        } else {
          const want = yawTowards([0, 0, 0], [dx, 0, dz]);
          this.yaw += Math.atan2(Math.sin(want - this.yaw), Math.cos(want - this.yaw)) * Math.min(1, dt * 4);
          input = { forward: Math.min(1, d / 0.6), right: 0, sprint: false };
        }
      }
      this.hvel = stepVelocity(this.hvel, input, this.yaw, dt, this.move);
      const before = this.player.eye.clone();
      this.collider.step(this.player, this.hvel, dt, this.params);
      if (this.target) {
        const progressed = Math.hypot(this.player.eye.x - before.x, this.player.eye.z - before.z);
        this.stuck = progressed < Math.hypot(...this.hvel) * dt * 0.2 ? this.stuck + dt : 0;
      }
    }
    // 3. camera
    this.camera.position.copy(this.player.eye);
    this.camera.rotation.set(this.pitch, this.yaw, 0);
    for (const m of this.hotspotMeshes) (m as THREE.Mesh).visible = !this.transition;
    if (!render) return;
    this.renderer.render(this.scene, this.camera);
    const now = performance.now();
    if (this.manual || now - this.lastPoseEmit > 66) {
      this.lastPoseEmit = now;
      this.ev.onPose?.({ x: this.player.eye.x, z: this.player.eye.z, yaw: this.yaw });
    }
  }

  private resize(): void {
    const w = this.host.clientWidth || 1;
    const h = this.host.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }
}
