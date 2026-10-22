"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, useLoader, useThree } from "@react-three/fiber";
import clsx from "clsx";
import { ChevronLeft, ChevronRight, Maximize, Pause, Play, Ruler } from "lucide-react";
import { Suspense, useEffect, useRef, useState } from "react";
import * as THREE from "three";

export type PanoNode = { id: string; label: string; image: string; thumb: string | null };

function Sphere({ url }: { url: string }) {
  const tex = useLoader(THREE.TextureLoader, url);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.mapping = THREE.EquirectangularReflectionMapping;
  return (
    <mesh scale={[-1, 1, 1]}>
      <sphereGeometry args={[50, 64, 40]} />
      <meshBasicMaterial map={tex} side={THREE.BackSide} toneMapped={false} />
    </mesh>
  );
}

function Fov() {
  const { camera, gl } = useThree();
  useEffect(() => {
    const el = gl.domElement;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const c = camera as THREE.PerspectiveCamera;
      c.fov = THREE.MathUtils.clamp(c.fov + e.deltaY * 0.03, 35, 95);
      c.updateProjectionMatrix();
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [camera, gl]);
  return null;
}

/** Equirectangular 360° tour: each node is one of the user's own panoramas, shown as captured. */
export function PanoramaViewer({ nodes, title, className }: { nodes: PanoNode[]; title: string; className?: string }) {
  const [i, setI] = useState(0);
  const [spin, setSpin] = useState(true);
  const root = useRef<HTMLDivElement>(null);
  const n = nodes.length;
  const node = nodes[i];
  if (!n) return null;
  return (
    <div ref={root} className={clsx("relative overflow-hidden rounded-3xl bg-ink-950 ring-1 ring-white/8", className)} data-testid="pano-viewer">
      <Canvas camera={{ position: [0, 0, 0.1], fov: 75 }} gl={{ preserveDrawingBuffer: true }} aria-label="360° panorama" role="img">
        <Suspense fallback={null}>
          <Sphere key={node.image} url={node.image} />
        </Suspense>
        <OrbitControls enableZoom={false} enablePan={false} rotateSpeed={-0.35} autoRotate={spin} autoRotateSpeed={0.4} />
        <Fov />
      </Canvas>
      <div className="glass absolute left-4 top-4 rounded-2xl px-4 py-2.5">
        <p className="font-display text-lg text-ivory-50">{title}</p>
        <p className="text-xs text-ink-300">360° panorama {i + 1} of {n} · {node.label}</p>
      </div>
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-2 p-4">
        <div className="flex max-w-full gap-1.5 overflow-x-auto scrollbar-thin">
          {nodes.map((p, k) => (
            <button key={p.id} onClick={() => setI(k)} aria-label={`Panorama ${k + 1}: ${p.label}`} aria-current={k === i ? "true" : undefined} className={clsx("h-12 w-24 shrink-0 overflow-hidden rounded-lg ring-2", k === i ? "ring-teal-300" : "ring-transparent opacity-60 hover:opacity-100")}>
              {p.thumb ? <img src={p.thumb} alt="" className="h-full w-full object-cover" /> : <span className="block h-full w-full bg-ink-800" />}
            </button>
          ))}
        </div>
        <div className="glass flex items-center gap-1 rounded-2xl p-1.5" role="toolbar" aria-label="Panorama controls">
          <button aria-label="Previous panorama" onClick={() => setI((i - 1 + n) % n)} className="flex h-11 w-11 items-center justify-center rounded-xl hover:bg-white/10"><ChevronLeft className="h-5 w-5" /></button>
          <button aria-label={spin ? "Stop rotation" : "Auto-rotate"} onClick={() => setSpin((s) => !s)} className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal-400 text-ink-950 hover:bg-teal-300">{spin ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}</button>
          <button aria-label="Next panorama" onClick={() => setI((i + 1) % n)} className="flex h-11 w-11 items-center justify-center rounded-xl hover:bg-white/10"><ChevronRight className="h-5 w-5" /></button>
          <button aria-label="Measurement unavailable for panoramas (no depth)" aria-disabled className="flex h-11 w-11 cursor-not-allowed items-center justify-center rounded-xl text-ink-400"><Ruler className="h-5 w-5" /></button>
          <button aria-label="Fullscreen" onClick={() => (document.fullscreenElement ? document.exitFullscreen() : root.current?.requestFullscreen?.())} className="flex h-11 w-11 items-center justify-center rounded-xl hover:bg-white/10"><Maximize className="h-5 w-5" /></button>
        </div>
      </div>
    </div>
  );
}
